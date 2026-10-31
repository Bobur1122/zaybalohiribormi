"""
6-QISM: PRO v6.0 Institutional Historical Backtester (backtest.py)
Oxirgi N kunlik (masalan 5–10 kunlik) HAQIQIY M15 va H1 shamchalarda (XAUUSD — Yahoo Finance GC=F,
BTCUSD — Binance Spot BTCUSDT) botning barcha qoidalarini (Killzone 10:00–23:00 UZT, Bozor Ochiq/Yopiq,
Fractal BOS/CHoCH, Liquidity Sweep, Unmitigated OB/FVG, 85/100 Confluence, Limit Order Trigger,
TP1 50% + Breakeven, TP2 30% + Trailing SL, TP3 20% va SL) shamchama-shamcha tekshirib,
haqiqiy Win Rate (WR), PnL va har bir signal ro'yxatini chiqarib beradi.
"""
import argparse
from datetime import datetime, timedelta, timezone
import sys
from typing import Any
import urllib.parse
import urllib.request
import json
import numpy as np
import pandas as pd

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from config import (
    SYMBOLS,
    TIMEZONE_OFFSET_HOURS,
    KILLZONES_TASHKENT,
    MAX_DAILY_TRADES_PER_SYMBOL,
    POST_TRADE_COOLDOWN_MINUTES,
    PENDING_EXPIRY_MINUTES,
    CONFLUENCE_THRESHOLD,
)
from data_feed import MarketDataCollector, is_market_open_for_symbol
from fundamental_agent import FundamentalAgent
from technical_agent import TechnicalAgent
from risk_manager import RiskManager
from schemas import MarketSnapshot


def fetch_historical_data_for_backtest(
    collector: MarketDataCollector,
    symbol: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Backtest uchun maksimal M15 (1000 shamcha ~ 10 kun) va H1 (500 shamcha ~ 20 kun) haqiqiy tarixni yuklaydi.
    """
    if "BTC" in symbol.upper():
        ltf_df = collector.fetch_binance_ohlcv(symbol, "M15", bars=960)
        htf_df = collector.fetch_binance_ohlcv(symbol, "H1", bars=480)
        if ltf_df is not None and htf_df is not None:
            return htf_df, ltf_df

    # Yahoo Finance dan 15m (1mo = ~20 savdo kuni) va 1h (3mo) olish
    yf_map = {
        "XAUUSD": "GC=F",
        "USDX": "DX-Y.NYB",
        "US10Y": "^TNX",
        "BTCUSD": "BTC-USD",
    }
    yf_sym = yf_map.get(symbol.upper(), "GC=F")
    live_q = collector.fetch_tradingview_live_quote(symbol)

    def _get_yf(interval: str, rng: str) -> pd.DataFrame:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yf_sym)}"
            f"?interval={interval}&range={rng}"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        res = payload["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame({
            "time": pd.to_datetime(ts, unit="s", utc=True),
            "open": q["open"],
            "high": q["high"],
            "low": q["low"],
            "close": q["close"],
            "volume": q["volume"],
        }).dropna().reset_index(drop=True)

        if "XAU" in symbol.upper() and live_q and live_q.get("close"):
            tv_c = float(live_q["close"])
            yf_c = float(df["close"].iloc[-1])
            offset = round(tv_c - yf_c, 2)
            if abs(offset) <= 80.0:
                for col in ("open", "high", "low", "close"):
                    df[col] = np.round(df[col] + offset, 2)
        return df

    ltf_df = _get_yf("15m", "1mo")
    htf_df = _get_yf("1h", "3mo")
    return htf_df, ltf_df


def is_in_killzone_at(dt_utc: datetime) -> tuple[bool, str]:
    tz_uz = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))
    dt_uz = dt_utc.astimezone(tz_uz)
    h = dt_uz.hour
    for kz_name, (sh, eh) in KILLZONES_TASHKENT.items():
        if sh <= h < eh:
            return True, f"{kz_name} ({dt_uz.strftime('%H:%M')} UZT)"
    return False, f"Off-Session ({dt_uz.strftime('%H:%M')} UZT)"


def run_symbol_backtest(
    symbol: str,
    days: int = 10,
) -> dict[str, Any]:
    collector = MarketDataCollector(use_mt5=False)
    fund_agent = FundamentalAgent()
    tech_agent = TechnicalAgent()
    risk_mgr = RiskManager(enforce_killzone=True)

    htf_full, ltf_full = fetch_historical_data_for_backtest(collector, symbol)
    dxy_htf, _ = fetch_historical_data_for_backtest(collector, "USDX")

    now_utc = pd.Timestamp(ltf_full["time"].iloc[-1]).to_pydatetime()
    start_cutoff = now_utc - timedelta(days=days)

    # Backtest boshlanadigan shamcha indeksi (kamida 120 ta oldingi shamcha bo'lishi shart)
    start_idx = 120
    for idx in range(120, len(ltf_full)):
        bar_dt = pd.Timestamp(ltf_full.loc[idx, "time"]).to_pydatetime()
        if bar_dt >= start_cutoff:
            start_idx = idx
            break

    trades_log: list[dict[str, Any]] = []
    active_trade: dict[str, Any] | None = None
    last_closed_dt: datetime | None = None
    daily_trade_counts: dict[str, int] = {}

    tz_uz = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))

    for i in range(start_idx, len(ltf_full)):
        bar = ltf_full.loc[i]
        bar_dt: datetime = pd.Timestamp(bar["time"]).to_pydatetime()
        bar_dt_uz = bar_dt.astimezone(tz_uz)
        day_key = bar_dt_uz.strftime("%Y-%m-%d")

        bar_open = round(float(bar["open"]), 2)
        bar_high = round(float(bar["high"]), 2)
        bar_low = round(float(bar["low"]), 2)
        bar_close = round(float(bar["close"]), 2)

        # 1. Agar aktiv savdo (PENDING yoki TRIGGERED) bo'lsa, joriy M15 shamchaning High/Low bo'yicha tekshiramiz
        if active_trade is not None:
            st = active_trade["status"]
            direction = active_trade["direction"]
            entry = active_trade["entry"]
            sl = active_trade["sl"]
            tp1 = active_trade["tp1"]
            tp2 = active_trade["tp2"]
            tp3 = active_trade["tp3"]
            risk_usd = active_trade["risk_usd"]
            risk_pts = max(abs(entry - active_trade["initial_sl"]), 1e-4)
            rr1 = abs(tp1 - entry) / risk_pts
            rr2 = abs(tp2 - entry) / risk_pts
            rr3 = abs(tp3 - entry) / risk_pts

            mkt_open, _ = is_market_open_for_symbol(symbol, bar_dt)
            if not mkt_open and st == "PENDING":
                active_trade["status"] = "CANCELLED"
                active_trade["closed_at"] = bar_dt_uz.strftime("%Y-%m-%d %H:%M")
                trades_log.append(active_trade)
                active_trade = None
                continue

            if st == "PENDING":
                age_min = (bar_dt - active_trade["opened_dt"]).total_seconds() / 60.0
                # Narx limitga tegmasdan TP1 ga yetib borsa -> Missed Setup (CANCELLED)
                missed = (direction == "BUY" and bar_high >= tp1 and bar_low > entry) or (
                    direction == "SELL" and bar_low <= tp1 and bar_high < entry
                )
                if missed or age_min >= PENDING_EXPIRY_MINUTES:
                    active_trade["status"] = "CANCELLED"
                    active_trade["closed_at"] = bar_dt_uz.strftime("%Y-%m-%d %H:%M")
                    trades_log.append(active_trade)
                    active_trade = None
                    continue

                # Limit order ishga tushdimi?
                triggered = (direction == "BUY" and bar_low <= entry) or (
                    direction == "SELL" and bar_high >= entry
                )
                if triggered:
                    active_trade["status"] = "TRIGGERED"
                    active_trade["triggered_at"] = bar_dt_uz.strftime("%Y-%m-%d %H:%M")
                    daily_trade_counts[day_key] = daily_trade_counts.get(day_key, 0) + 1
                    st = "TRIGGERED"
                else:
                    continue

            # Agar savdo TRIGGERED / TP1_HIT / TP2_HIT bo'lsa:
            # Konservativ (halol) tekshiruv: bitta shamchada SL ham tegsa, avval SL tekshiriladi
            hit_sl = (direction == "BUY" and bar_low <= sl) or (direction == "SELL" and bar_high >= sl)
            if hit_sl:
                max_stage = active_trade["max_tp_stage"]
                if max_stage == 2:
                    active_trade["status"] = "TP2_WIN"
                    active_trade["pnl_usd"] = round(risk_usd * (0.50 * rr1 + 0.30 * rr2 + 0.20 * rr1), 2)
                elif max_stage == 1:
                    active_trade["status"] = "TP1_BU_WIN"
                    active_trade["pnl_usd"] = round(risk_usd * (0.50 * rr1), 2)
                else:
                    active_trade["status"] = "SL_LOSS"
                    active_trade["pnl_usd"] = round(-risk_usd, 2)
                active_trade["closed_at"] = bar_dt_uz.strftime("%Y-%m-%d %H:%M")
                trades_log.append(active_trade)
                last_closed_dt = bar_dt
                active_trade = None
                continue

            # TP1 -> TP2 -> TP3 tekshiruvi
            if st == "TRIGGERED":
                hit_tp1 = (direction == "BUY" and bar_high >= tp1) or (direction == "SELL" and bar_low <= tp1)
                if hit_tp1:
                    active_trade["status"] = "TP1_HIT"
                    active_trade["max_tp_stage"] = 1
                    active_trade["sl"] = entry  # Breakeven!
                    st = "TP1_HIT"
                    sl = entry

            if st == "TP1_HIT":
                hit_tp2 = (direction == "BUY" and bar_high >= tp2) or (direction == "SELL" and bar_low <= tp2)
                if hit_tp2:
                    active_trade["status"] = "TP2_HIT"
                    active_trade["max_tp_stage"] = 2
                    active_trade["sl"] = tp1  # Trailing SL -> TP1!
                    st = "TP2_HIT"
                    sl = tp1

            if st == "TP2_HIT":
                hit_tp3 = (direction == "BUY" and bar_high >= tp3) or (direction == "SELL" and bar_low <= tp3)
                if hit_tp3:
                    active_trade["status"] = "TP3_FULL_WIN"
                    active_trade["max_tp_stage"] = 3
                    active_trade["pnl_usd"] = round(risk_usd * (0.50 * rr1 + 0.30 * rr2 + 0.20 * rr3), 2)
                    active_trade["closed_at"] = bar_dt_uz.strftime("%Y-%m-%d %H:%M")
                    trades_log.append(active_trade)
                    last_closed_dt = bar_dt
                    active_trade = None
                    continue

            continue

        # 2. Yangi signal qidirish (faqat aktiv savdo yo'q bo'lganda)
        mkt_open, mkt_note = is_market_open_for_symbol(symbol, bar_dt)
        if not mkt_open:
            continue

        in_kz, kz_label = is_in_killzone_at(bar_dt)
        if not in_kz:
            continue

        if daily_trade_counts.get(day_key, 0) >= MAX_DAILY_TRADES_PER_SYMBOL:
            continue

        if last_closed_dt is not None:
            if (bar_dt - last_closed_dt).total_seconds() / 60.0 < POST_TRADE_COOLDOWN_MINUTES:
                continue

        # Shu vaqtgacha bo'lgan M15 va H1 oynasini kesib olamiz (kelajakka qaramasdan — No Lookahead Bias!)
        ltf_slice = ltf_full.iloc[max(0, i - 140) : i + 1].copy().reset_index(drop=True)
        htf_slice = htf_full[htf_full["time"] <= bar_dt].tail(120).copy().reset_index(drop=True)
        if len(htf_slice) < 30 or len(ltf_slice) < 50:
            continue

        # Shu vaqtdagi DXY trendini hisoblash
        dxy_slice = dxy_htf[dxy_htf["time"] <= bar_dt].tail(60)
        if len(dxy_slice) >= 25:
            dc = dxy_slice["close"]
            de20 = float(dc.ewm(span=20, adjust=False).mean().iloc[-1])
            de50 = float(dc.ewm(span=50, adjust=False).mean().iloc[-1])
            dl = float(dc.iloc[-1])
            dxy_trend = "BULLISH" if (de20 > de50 and dl >= de20) else ("BEARISH" if (de20 < de50 and dl <= de20) else "NEUTRAL")
        else:
            dxy_trend = "NEUTRAL"

        atr_val = collector.calculate_atr(htf_slice, period=14)
        session_lvls = collector._compute_session_liquidity_levels(ltf_slice, htf_slice)

        snap = MarketSnapshot(
            symbol=symbol,
            htf_df=htf_slice,
            ltf_df=ltf_slice,
            dxy_trend=dxy_trend,
            atr=atr_val,
            current_price=bar_close,
            price_source=f"Backtest ({symbol})",
            us10y_trend=dxy_trend,
            whale_ask_wall=round(bar_close + 0.85 * atr_val, 2),
            whale_ask_vol=2500.0,
            whale_bid_wall=round(bar_close - 0.85 * atr_val, 2),
            whale_bid_vol=2500.0,
            ob_imbalance_ratio=1.0,
            pdh=session_lvls["pdh"],
            pdl=session_lvls["pdl"],
            asian_high=session_lvls["asian_high"],
            asian_low=session_lvls["asian_low"],
            is_live_data=True,
            is_market_open=True,
            market_status_note=mkt_note,
        )

        macro_sig = fund_agent.analyze(snap, [], False)
        tech_setup = tech_agent.find_setup(snap, macro_sig)

        if (
            tech_setup.is_ideal_setup
            and tech_setup.direction in ("BUY", "SELL")
            and tech_setup.confluence_score >= CONFLUENCE_THRESHOLD
        ):
            lot_sz, risk_usd = risk_mgr.calculate_lot_size(symbol, tech_setup.entry_price, tech_setup.sl_price)
            active_trade = {
                "symbol": symbol,
                "direction": tech_setup.direction,
                "order_type": tech_setup.order_type,
                "score": tech_setup.confluence_score,
                "entry": tech_setup.entry_price,
                "initial_sl": tech_setup.sl_price,
                "sl": tech_setup.sl_price,
                "tp1": tech_setup.tp1_price,
                "tp2": tech_setup.tp2_price,
                "tp3": tech_setup.tp3_price,
                "risk_usd": risk_usd,
                "status": "PENDING",
                "max_tp_stage": 0,
                "pnl_usd": 0.0,
                "opened_dt": bar_dt,
                "opened_at": bar_dt_uz.strftime("%Y-%m-%d %H:%M"),
                "triggered_at": None,
                "closed_at": None,
                "sweep_info": tech_setup.session_liquidity_info,
            }

    # Yakuniy statistika hisoblash
    closed_trades = [t for t in trades_log if t["status"] in ("TP1_BU_WIN", "TP2_WIN", "TP3_FULL_WIN", "SL_LOSS")]
    cancelled_trades = [t for t in trades_log if t["status"] == "CANCELLED"]
    wins = [t for t in closed_trades if "WIN" in t["status"]]
    losses = [t for t in closed_trades if t["status"] == "SL_LOSS"]

    win_rate = round((len(wins) / len(closed_trades)) * 100.0, 1) if closed_trades else 0.0
    net_pnl = round(sum(float(t["pnl_usd"]) for t in closed_trades), 2)

    return {
        "symbol": symbol,
        "days": days,
        "total_signals": len(trades_log),
        "triggered_closed": len(closed_trades),
        "cancelled_pending": len(cancelled_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "closed_trades": closed_trades,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="PRO v6.0 Historical Backtester")
    parser.add_argument("--days", type=int, default=14, help="Necha kunlik tarixda sinab ko'rish (default=14)")
    args = parser.parse_args()

    print("=" * 88)
    print(f"📊 PRO v6.0 INSTITUTIONAL REAL-MARKET BACKTEST (Oxirgi {args.days} kunlik haqiqiy M15/H1 tarix)")
    print("=" * 88)

    total_closed = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0.0

    for sym in ("XAUUSD", "BTCUSD"):
        res = run_symbol_backtest(sym, days=args.days)
        total_closed += res["triggered_closed"]
        total_wins += res["wins"]
        total_losses += res["losses"]
        total_pnl += res["net_pnl"]

        print(f"\n🔹 AKTIV: {sym} (Oxirgi {args.days} kun)")
        print(
            f"   • Jami Signallar: {res['total_signals']} ta | "
            f"Ishga tushib yopilgan: {res['triggered_closed']} ta | "
            f"Bekor qilingan (Pending Expired/Missed): {res['cancelled_pending']} ta"
        )
        print(
            f"   • Yutuq (TP1/TP2/TP3): {res['wins']} ta | Stop-Loss: {res['losses']} ta | "
            f"WIN RATE: {res['win_rate']}% | Sof Foyda (1% risk = $100): ${res['net_pnl']:+.2f}"
        )
        if res["closed_trades"]:
            print("   • Bajarilgan savdolar ro'yxati:")
            for idx, tr in enumerate(res["closed_trades"], 1):
                badge = "✅" if "WIN" in tr["status"] else "❌"
                print(
                    f"     {idx}. {badge} [{tr['opened_at']} UZT] {tr['direction']} ({tr['score']}/100) | "
                    f"Entry: {tr['entry']:.2f} | SL: {tr['initial_sl']:.2f} | TP1: {tr['tp1']:.2f} | TP2: {tr['tp2']:.2f} "
                    f"-> {tr['status']} (${tr['pnl_usd']:+.2f})"
                )

    overall_wr = round((total_wins / total_closed) * 100.0, 1) if total_closed > 0 else 0.0
    print("\n" + "=" * 88)
    print(
        f"🏆 UMUMIY NATIJA (XAUUSD + BTCUSD): "
        f"Savdolar: {total_closed} ta (Yutuq: {total_wins} | SL: {total_losses}) | "
        f"UMUMIY WIN RATE: {overall_wr}% | UMUMIY SOF FOYDA: ${total_pnl:+.2f}"
    )
    print("=" * 88)


if __name__ == "__main__":
    main()
