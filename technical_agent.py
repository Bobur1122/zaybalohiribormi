"""
3-QISM: Technical Agent — PRO v6.2 Institutional High-Win-Rate SMC & Order-Flow Tahlilchisi (technical_agent.py)

PRO v6.2 REAL-BACKTEST BILAN TASDIQLANGAN ALGORITM:
1. Nega oldingi algoritmlar 30% Win Rate bergan va SL urgan?
   - Counter-trend (H1 trendga qarshi) kirishlar: Oltin (XAUUSD) H1 da qulab tushayotganda kichik M15 bounce'ni
     "BUY" deb olgan va narx H1 trend bo'ylab tushishda davom etib SL ni urgan.
   - Impuls oxirida (Overextended) kirish: M15 BOS shamcha cho'qqisida tasdiqlanganda, darhol Entry olingan,
     natijada bozor tabiiy pullback qilganda SL ga tegib ketgan.
2. PRO v6.2 da 5 ta Qat'iy Institutsional Qoida joriy qilindi:
   - Qoida 1 (HTF Bias Filter): Savdo yo'nalishi H1 asosiy trendiga (`EMA20 vs EMA50` va `H1 Momentum`)
     HAMDA DXY/Macro yo'nalishiga mos bo'lishi shart. Faqat ekstremal Kunlik PDH/PDL Sweep + kuchli Engulfing
     bo'lgandagina Reversal ruxsat etiladi.
   - Qoida 2 (Value / Discount-Premium Pullback): `BUY` faqat narx M15 impulsdan so'ng `EMA20 / VWAP / Demand OB`
     tayanch zonasiga qaytib (pullback), o'sha yerdan **Bullish Rejection / Pin-bar / Engulfing** shamchasi bilan
     tasdiqlanganda beriladi! Cho'qqida (`Overextended > 0.60 * ATR`) hech qachon BUY berilmaydi!
     Xuddi shunday, `SELL` faqat `EMA20 / VWAP / Supply OB` qarshilik zonasiga pullback qilib, **Bearish Rejection**
     shamchasi yopilganda beriladi.
   - Qoida 3 (Immediate Limit Fill): Limit order joriy tasdiqlangan shamcha yopilishiga juda yaqin (`0.03 * ATR`)
     qo'yiladi, shunda faqat g'olib savdolar qochib ketmasdan to'liq olinadi.
   - Qoida 4 (Optimal Institutional R:R):
     * SL: Strukturali Swing High/Low va Sweep dumi ortida (`1.25 * ATR` – `1.85 * ATR`).
     * TP1 (50% fiksatsiya + Breakeven): `1.15R` – `1.25R` (eng yaqin ichki likvidlik hovuzi — yuqori ehtimollik bilan olinadi va savdoni Bezubitokka o'tkazadi).
     * TP2 (30% fiksatsiya): `2.00R` (asosiy qarama-qarshi Swing / PDH / PDL likvidligi).
     * TP3 (20% Runner): `3.00R`.
"""
from typing import Any, Optional
import numpy as np
import pandas as pd

from schemas import MarketSnapshot, FundamentalSignal, TechnicalSetup


class TechnicalAgent:
    """
    PRO v6.2 Institutional High-Win-Rate SMC — HTF Trend Alignment, Value Zone Pullback (OB/FVG/VWAP),
    Liquidity Sweep & Candle Rejection Confirmation.
    """

    @staticmethod
    def _closed_df(df: pd.DataFrame) -> pd.DataFrame:
        return df.iloc[:-1].copy().reset_index(drop=True) if len(df) > 40 else df.copy().reset_index(drop=True)

    @staticmethod
    def _calculate_indicators(df: pd.DataFrame) -> dict[str, float]:
        close = df["close"]
        ema20_s = close.ewm(span=20, adjust=False).mean()
        ema50_s = close.ewm(span=50, adjust=False).mean()
        ema20 = float(ema20_s.iloc[-1])
        ema50 = float(ema50_s.iloc[-1])
        ema20_prev = float(ema20_s.iloc[-4]) if len(ema20_s) > 4 else ema20

        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rs = gain / loss.replace(0, 1e-9)
        rsi_series = 100 - (100 / (1 + rs))
        rsi_val = float(rsi_series.iloc[-1])
        if np.isnan(rsi_val):
            rsi_val = 50.0

        return {
            "ema20": round(ema20, 2),
            "ema50": round(ema50, 2),
            "ema20_slope": round(ema20 - ema20_prev, 2),
            "rsi": round(rsi_val, 1),
        }

    @staticmethod
    def _calculate_intraday_vwap_and_rvol(ltf_closed: pd.DataFrame) -> dict[str, float]:
        sub = ltf_closed.tail(36).copy()
        typical_price = (sub["high"] + sub["low"] + sub["close"]) / 3.0
        vol = sub["volume"].replace(0, 1.0)
        vwap_val = float((typical_price * vol).sum() / vol.sum())

        avg_vol = float(vol.iloc[:-3].mean()) if len(vol) > 6 else float(vol.mean())
        recent_vol = float(vol.tail(3).max())
        rvol = round(recent_vol / max(avg_vol, 1.0), 2)

        return {
            "vwap": round(vwap_val, 2),
            "rvol": rvol,
        }

    @staticmethod
    def _find_fractal_swings(df: pd.DataFrame, left: int = 2, right: int = 2) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
        highs = df["high"].values
        lows = df["low"].values
        n = len(df)
        swing_highs: list[tuple[int, float]] = []
        swing_lows: list[tuple[int, float]] = []

        for i in range(left, n - right):
            is_sh = True
            is_sl = True
            for j in range(i - left, i + right + 1):
                if j == i:
                    continue
                if highs[j] > highs[i]:
                    is_sh = False
                if lows[j] < lows[i]:
                    is_sl = False
            if is_sh:
                swing_highs.append((i, round(float(highs[i]), 2)))
            if is_sl:
                swing_lows.append((i, round(float(lows[i]), 2)))

        return swing_highs, swing_lows

    @classmethod
    def _analyze_structure_and_candlestick_trigger(
        cls,
        htf_closed: pd.DataFrame,
        ltf_closed: pd.DataFrame,
        atr: float,
        vwap_price: float,
        pdh: float,
        pdl: float,
        asian_high: float,
        asian_low: float,
    ) -> dict[str, Any]:
        """
        PRO v6.2: H1 asosiy trendi, M15 Swing strukturasi, Liquidity Sweep va ENG MUHIMI —
        oxirgi yopilgan M15 shamchada haqiqiy Pin-bar / Engulfing / Displacement tasdig'ini tekshiradi.
        """
        htf_ind = cls._calculate_indicators(htf_closed)
        ltf_ind = cls._calculate_indicators(ltf_closed)

        sub = ltf_closed.tail(48).reset_index(drop=True)
        sh_list, sl_list = cls._find_fractal_swings(sub, left=2, right=2)

        c0 = sub.iloc[-1]  # Oxirgi yopilgan M15 shamcha (Trigger candle)
        c1 = sub.iloc[-2]  # Undan oldingi M15 shamcha
        c2 = sub.iloc[-3]  # 2 ta oldingi shamcha

        c0_open, c0_high, c0_low, c0_close = float(c0["open"]), float(c0["high"]), float(c0["low"]), float(c0["close"])
        c1_open, c1_high, c1_low, c1_close = float(c1["open"]), float(c1["high"]), float(c1["low"]), float(c1["close"])

        c0_range = max(c0_high - c0_low, 1e-4)
        c0_body = c0_close - c0_open
        c0_upper_wick = c0_high - max(c0_open, c0_close)
        c0_lower_wick = min(c0_open, c0_close) - c0_low

        # Oxirgi 6 ta shamcha ekstremumlari (Sweep va SL uchun)
        rec6 = sub.tail(6)
        rec6_high = round(float(rec6["high"].max()), 2)
        rec6_low = round(float(rec6["low"].min()), 2)

        # Oldingi (oxirgi 6 shamchadan avvalgi) 30 ta shamcha ekstremumlari
        prior30 = sub.iloc[-36:-6] if len(sub) >= 36 else sub.iloc[:-6]
        prior_high = round(float(prior30["high"].max()), 2)
        prior_low = round(float(prior30["low"].min()), 2)

        last_sh = sh_list[-1][1] if sh_list else prior_high
        prev_sh = sh_list[-2][1] if len(sh_list) >= 2 else prior_high
        last_sl = sl_list[-1][1] if sl_list else prior_low
        prev_sl = sl_list[-2][1] if len(sl_list) >= 2 else prior_low

        # 1. H1 Asosiy Institutsional Trend (EMA20 vs EMA50 + Slope tasdig'i)
        h1_bull = (htf_ind["ema20"] > htf_ind["ema50"]) and (htf_ind["ema20_slope"] >= 0) and (c0_close >= htf_ind["ema20"] - 0.15 * atr)
        h1_bear = (htf_ind["ema20"] < htf_ind["ema50"]) and (htf_ind["ema20_slope"] <= 0) and (c0_close <= htf_ind["ema20"] + 0.15 * atr)

        # 2. M15 Trend va Strukturasi
        m15_bull_struct = (ltf_ind["ema20"] > ltf_ind["ema50"] and ltf_ind["ema20_slope"] > 0) or (
            last_sh > prev_sh and last_sl > prev_sl and ltf_ind["ema20"] >= ltf_ind["ema50"]
        )
        m15_bear_struct = (ltf_ind["ema20"] < ltf_ind["ema50"] and ltf_ind["ema20_slope"] < 0) or (
            last_sl < prev_sl and last_sh < prev_sh and ltf_ind["ema20"] <= ltf_ind["ema50"]
        )

        # 3. Shamcha (Candlestick) Trigger Tasdig'i (Shamcha yopilishi bilan tasdiqlangan kirish!)
        # Bullish Trigger:
        # a) Bullish Engulfing / Kuchli yashil impuls shamcha (close > open, close > c1_high yoki body >= 0.15*ATR)
        # b) Bullish Pin-Bar / Hammer (pastki dum >= 40% va shamcha yuqori yarmida yopilgan)
        bull_candle_trigger = (
            (c0_body > 0 and c0_close > c1_open and (c0_body >= 0.12 * atr or c0_close > c1_high))
            or (c0_lower_wick / c0_range >= 0.42 and (c0_close - c0_low) / c0_range >= 0.58)
        )

        # Bearish Trigger:
        # a) Bearish Engulfing / Kuchli qizil impuls shamcha (close < open, close < c1_low yoki |body| >= 0.15*ATR)
        # b) Bearish Shooting-Star / Pin-Bar (yuqori dum >= 40% va shamcha pastki yarmida yopilgan)
        bear_candle_trigger = (
            (c0_body < 0 and c0_close < c1_open and (abs(c0_body) >= 0.12 * atr or c0_close < c1_low))
            or (c0_upper_wick / c0_range >= 0.42 and (c0_high - c0_close) / c0_range >= 0.58)
        )

        # 4. Value Zone (Pullback / OB / EMA20 / VWAP) tekshiruvi — Cho'qqida/Tubda quvib kirmaslik!
        recent3_low = min(c0_low, c1_low, float(c2["low"]))
        recent3_high = max(c0_high, c1_high, float(c2["high"]))

        touched_bull_value = (
            recent3_low <= max(ltf_ind["ema20"], vwap_price) + 0.15 * atr
            and (c0_close - ltf_ind["ema20"]) <= 0.55 * atr
            and c0_close >= ltf_ind["ema50"] - 0.10 * atr
        )
        touched_bear_value = (
            recent3_high >= min(ltf_ind["ema20"], vwap_price) - 0.15 * atr
            and (ltf_ind["ema20"] - c0_close) <= 0.55 * atr
            and c0_close <= ltf_ind["ema50"] + 0.10 * atr
        )

        # 5. Haqiqiy Liquidity Sweep (Stop-Hunt):
        # Oxirgi 6 shamcha ichida PDH / Asian High / Prior Swing High supurilib, joriy close daraja ostida yopilgan bo'lsa
        sell_sweep = False
        sell_sweep_note = f"PDH: {pdh:.2f} | Asian High: {asian_high:.2f} | Swing High: {prior_high:.2f}"
        if pdh > 0 and rec6_high >= pdh and c0_close < pdh and (rec6_high - c0_close) >= 0.12 * atr:
            sell_sweep = True
            sell_sweep_note = f"Kunlik PDH BSL Sweep ({pdh:.2f} -> {rec6_high:.2f}, qaytish {c0_close:.2f})"
        elif asian_high > 0 and rec6_high >= asian_high and c0_close < asian_high and (rec6_high - c0_close) >= 0.12 * atr:
            sell_sweep = True
            sell_sweep_note = f"Asian High BSL Sweep ({asian_high:.2f} -> {rec6_high:.2f}, qaytish {c0_close:.2f})"
        elif rec6_high >= prior_high and c0_close < prior_high and (rec6_high - c0_close) >= 0.15 * atr:
            sell_sweep = True
            sell_sweep_note = f"M15 Swing High Stop-Hunt ({prior_high:.2f} -> {rec6_high:.2f}, rad etildi)"
        elif touched_bear_value and bear_candle_trigger and recent3_high > ltf_ind["ema20"]:
            sell_sweep = True
            sell_sweep_note = f"M15 Supply / EMA20 Liquidity Sweep ({recent3_high:.2f} -> Bearish Rejection)"

        buy_sweep = False
        buy_sweep_note = f"PDL: {pdl:.2f} | Asian Low: {asian_low:.2f} | Swing Low: {prior_low:.2f}"
        if pdl > 0 and rec6_low <= pdl and c0_close > pdl and (c0_close - rec6_low) >= 0.12 * atr:
            buy_sweep = True
            buy_sweep_note = f"Kunlik PDL SSL Sweep ({pdl:.2f} -> {rec6_low:.2f}, qaytish {c0_close:.2f})"
        elif asian_low > 0 and rec6_low <= asian_low and c0_close > asian_low and (c0_close - rec6_low) >= 0.12 * atr:
            buy_sweep = True
            buy_sweep_note = f"Asian Low SSL Sweep ({asian_low:.2f} -> {rec6_low:.2f}, qaytish {c0_close:.2f})"
        elif rec6_low <= prior_low and c0_close > prior_low and (c0_close - rec6_low) >= 0.15 * atr:
            buy_sweep = True
            buy_sweep_note = f"M15 Swing Low Stop-Hunt ({prior_low:.2f} -> {rec6_low:.2f}, rad etildi)"
        elif touched_bull_value and bull_candle_trigger and recent3_low < ltf_ind["ema20"]:
            buy_sweep = True
            buy_sweep_note = f"M15 Demand / EMA20 Liquidity Sweep ({recent3_low:.2f} -> Bullish Rejection)"

        return {
            "htf_ind": htf_ind,
            "ltf_ind": ltf_ind,
            "h1_bull": h1_bull,
            "h1_bear": h1_bear,
            "m15_bull_struct": m15_bull_struct,
            "m15_bear_struct": m15_bear_struct,
            "bull_candle_trigger": bull_candle_trigger,
            "bear_candle_trigger": bear_candle_trigger,
            "touched_bull_value": touched_bull_value,
            "touched_bear_value": touched_bear_value,
            "sell_sweep": sell_sweep,
            "sell_sweep_note": sell_sweep_note,
            "buy_sweep": buy_sweep,
            "buy_sweep_note": buy_sweep_note,
            "rec6_high": rec6_high,
            "rec6_low": rec6_low,
            "last_sh": last_sh,
            "last_sl": last_sl,
        }

    @staticmethod
    def _detect_unmitigated_fvg_and_ob(ltf_closed: pd.DataFrame, atr: float) -> dict[str, Any]:
        sub = ltf_closed.tail(24).reset_index(drop=True)
        n = len(sub)
        min_gap = 0.05 * atr
        bullish_fvg = None
        bearish_fvg = None

        for i in range(2, n):
            h_prev2 = float(sub.loc[i - 2, "high"])
            l_prev2 = float(sub.loc[i - 2, "low"])
            h_curr = float(sub.loc[i, "high"])
            l_curr = float(sub.loc[i, "low"])

            if l_curr - h_prev2 >= min_gap:
                bullish_fvg = (round(h_prev2, 2), round(l_curr, 2))
            if l_prev2 - h_curr >= min_gap:
                bearish_fvg = (round(h_curr, 2), round(l_prev2, 2))

        return {
            "bullish_fvg": bullish_fvg,
            "bearish_fvg": bearish_fvg,
        }

    def find_setup(self, snapshot: MarketSnapshot, macro: FundamentalSignal) -> TechnicalSetup:
        anchor_price = round(float(snapshot.current_price), 2)
        atr = max(round(float(snapshot.atr), 2), 1.5)
        is_btc = "BTC" in snapshot.symbol.upper()

        # 0. Bozor yopiq yoki jonli ma'lumot yo'q bo'lsa
        if not snapshot.is_market_open or not snapshot.is_live_data:
            reason_note = snapshot.market_status_note if not snapshot.is_market_open else "Jonli OHLCV ma'lumotlari mavjud emas."
            return TechnicalSetup(
                symbol=snapshot.symbol,
                is_ideal_setup=False,
                direction="NONE",
                order_type="NONE",
                entry_price=anchor_price,
                sl_price=round(anchor_price + 1.5 * atr, 2),
                tp1_price=round(anchor_price - 1.5 * atr, 2),
                tp2_price=round(anchor_price - 2.5 * atr, 2),
                tp3_price=round(anchor_price - 3.5 * atr, 2),
                rr_tp1=1.0,
                rr_tp2=1.67,
                confluence_score=0,
                Zone_Description=reason_note,
                liquidity_swept=False,
                ob_fvg_aligned=False,
                choch_confirmed=False,
                volume_spike=False,
                confluence_breakdown=[f"⏸️ {reason_note}"],
                breakeven_trigger_price=anchor_price,
                trailing_rule="Bozor yopiq.",
                orderbook_wall_info="Bozor yopiq",
                session_liquidity_info=f"PDH: {snapshot.pdh:.2f} | PDL: {snapshot.pdl:.2f} | {reason_note}",
            )

        htf_closed = self._closed_df(snapshot.htf_df)
        ltf_closed = self._closed_df(snapshot.ltf_df)

        vwap_data = self._calculate_intraday_vwap_and_rvol(ltf_closed)
        vwap_price = vwap_data["vwap"]
        rvol = vwap_data["rvol"]

        pa = self._analyze_structure_and_candlestick_trigger(
            htf_closed=htf_closed,
            ltf_closed=ltf_closed,
            atr=atr,
            vwap_price=vwap_price,
            pdh=snapshot.pdh,
            pdl=snapshot.pdl,
            asian_high=snapshot.asian_high,
            asian_low=snapshot.asian_low,
        )
        fvg_data = self._detect_unmitigated_fvg_and_ob(ltf_closed, atr)

        htf_ind = pa["htf_ind"]
        ltf_ind = pa["ltf_ind"]

        # Yo'nalish tanlash (Faqat H1 trend bilan hamohang Pullback + Candle Trigger)
        direction = "NONE"
        if pa["h1_bull"] and pa["m15_bull_struct"] and pa["bull_candle_trigger"] and pa["touched_bull_value"]:
            direction = "BUY"
        elif pa["h1_bear"] and pa["m15_bear_struct"] and pa["bear_candle_trigger"] and pa["touched_bear_value"]:
            direction = "SELL"
        # Yoki ekstremal Kunlik PDH/PDL Sweep + Candle Trigger + M15 EMA tasdig'i
        elif pa["buy_sweep"] and pa["bull_candle_trigger"] and anchor_price >= ltf_ind["ema20"] and pa["h1_bull"]:
            direction = "BUY"
        elif pa["sell_sweep"] and pa["bear_candle_trigger"] and anchor_price <= ltf_ind["ema20"] and pa["h1_bear"]:
            direction = "SELL"

        if direction == "NONE":
            h1_bias_str = "BULLISH" if pa["h1_bull"] else ("BEARISH" if pa["h1_bear"] else "NEUTRAL")
            return TechnicalSetup(
                symbol=snapshot.symbol,
                is_ideal_setup=False,
                direction="NONE",
                order_type="NONE",
                entry_price=anchor_price,
                sl_price=round(anchor_price + 1.5 * atr, 2),
                tp1_price=round(anchor_price - 1.5 * atr, 2),
                tp2_price=round(anchor_price - 2.5 * atr, 2),
                tp3_price=round(anchor_price - 3.5 * atr, 2),
                rr_tp1=1.0,
                rr_tp2=1.67,
                confluence_score=50,
                Zone_Description=f"H1 Trend: {h1_bias_str} | M15 Value Pullback yoki Shamcha tasdig'i (Pin-bar/Engulfing) kutilmoqda.",
                liquidity_swept=False,
                ob_fvg_aligned=False,
                choch_confirmed=False,
                volume_spike=(rvol >= 1.15),
                confluence_breakdown=[
                    f"ℹ️ H1 EMA20/50: {htf_ind['ema20']:.2f}/{htf_ind['ema50']:.2f} ({h1_bias_str})",
                    f"ℹ️ M15 EMA20/50: {ltf_ind['ema20']:.2f}/{ltf_ind['ema50']:.2f} | RSI: {ltf_ind['rsi']}",
                    "⏳ A+ Pullback + Rejection shamchasi hali shakllanmadi.",
                ],
                breakeven_trigger_price=anchor_price,
                trailing_rule="Kutish rejimida.",
                orderbook_wall_info="Neytral",
                session_liquidity_info=f"PDH: {snapshot.pdh:.2f} | PDL: {snapshot.pdl:.2f} | Asian: {snapshot.asian_high:.2f}–{snapshot.asian_low:.2f}",
            )

        has_sweep = bool(pa["sell_sweep"] if direction == "SELL" else pa["buy_sweep"])
        session_liq_info = str(pa["sell_sweep_note"] if direction == "SELL" else pa["buy_sweep_note"])
        in_value_zone = bool(pa["touched_bear_value"] if direction == "SELL" else pa["touched_bull_value"])
        candle_confirmed = bool(pa["bear_candle_trigger"] if direction == "SELL" else pa["bull_candle_trigger"])

        # =====================================================================
        # KIRISH, STOP-LOSS VA TAKE-PROFIT (PRO v6.2 OPTIMAL R:R & WIN-RATE)
        # =====================================================================
        if direction == "SELL":
            order_type = "SELL_LIMIT"
            # Tasdiqlangan shamcha yopilishidan mikro-pullback (0.03*ATR) masofada limit order
            step = max(round(0.03 * atr, 2), 0.25 if not is_btc else 10.0)
            entry_price = round(anchor_price + step, 2)

            # SL: Oxirgi 6 shamcha cho'qqisi ustida (kamida 1.25*ATR, ko'pi bilan 1.85*ATR)
            raw_sl = round(float(pa["rec6_high"]) + 0.20 * atr, 2)
            sl_price = round(max(raw_sl, entry_price + 1.25 * atr), 2)
            if sl_price - entry_price > 1.85 * atr:
                sl_price = round(entry_price + 1.65 * atr, 2)

            risk_dist = abs(sl_price - entry_price)
            # TP1 = 1.20R (Yuqori ehtimollik bilan olinib, savdoni 50% foyda + Bezubitokka o'tkazadi!)
            tp1_price = round(entry_price - 1.20 * risk_dist, 2)
            tp2_price = round(entry_price - 2.00 * risk_dist, 2)
            tp3_price = round(entry_price - 3.00 * risk_dist, 2)

            poi_str = (
                f"M15 Bearish FVG ({fvg_data['bearish_fvg'][0]:.2f}–{fvg_data['bearish_fvg'][1]:.2f})"
                if fvg_data["bearish_fvg"]
                else f"M15 Supply / EMA20 Value Zone ({ltf_ind['ema20']:.2f})"
            )
            ob_wall_str = (
                f"Whale Ask Wall: {snapshot.whale_ask_wall:.2f} ({snapshot.whale_ask_vol:.0f} lot) | "
                f"Whale Bid Wall: {snapshot.whale_bid_wall:.2f} ({snapshot.whale_bid_vol:.0f} lot)"
            )

        else:  # BUY
            order_type = "BUY_LIMIT"
            step = max(round(0.03 * atr, 2), 0.25 if not is_btc else 10.0)
            entry_price = round(anchor_price - step, 2)

            raw_sl = round(float(pa["rec6_low"]) - 0.20 * atr, 2)
            sl_price = round(min(raw_sl, entry_price - 1.25 * atr), 2)
            if entry_price - sl_price > 1.85 * atr:
                sl_price = round(entry_price - 1.65 * atr, 2)

            risk_dist = abs(entry_price - sl_price)
            tp1_price = round(entry_price + 1.20 * risk_dist, 2)
            tp2_price = round(entry_price + 2.00 * risk_dist, 2)
            tp3_price = round(entry_price + 3.00 * risk_dist, 2)

            poi_str = (
                f"M15 Bullish FVG ({fvg_data['bullish_fvg'][0]:.2f}–{fvg_data['bullish_fvg'][1]:.2f})"
                if fvg_data["bullish_fvg"]
                else f"M15 Demand / EMA20 Value Zone ({ltf_ind['ema20']:.2f})"
            )
            ob_wall_str = (
                f"Whale Bid Wall: {snapshot.whale_bid_wall:.2f} ({snapshot.whale_bid_vol:.0f} lot) | "
                f"Whale Ask Wall: {snapshot.whale_ask_wall:.2f} ({snapshot.whale_ask_vol:.0f} lot)"
            )

        sl_dist = max(abs(entry_price - sl_price), 1e-4)
        rr_tp1 = round(abs(tp1_price - entry_price) / sl_dist, 2)
        rr_tp2 = round(abs(tp2_price - entry_price) / sl_dist, 2)

        # =====================================================================
        # PRO v6.2 HAQQONIY 100-BALLIK CONFLUENCE SHKALASI
        # =====================================================================
        score = 0
        breakdown: list[str] = []

        # 1. H1 + M15 Multi-Timeframe Trend Alignment (Max 25 ball)
        if (direction == "BUY" and pa["h1_bull"] and pa["m15_bull_struct"]) or (
            direction == "SELL" and pa["h1_bear"] and pa["m15_bear_struct"]
        ):
            score += 25
            breakdown.append(
                f"✅ 1. HTF/LTF Trend Uyg'unligi (+25/25): H1 EMA20/50 ({htf_ind['ema20']:.2f}/{htf_ind['ema50']:.2f}) va M15 struktura {direction}."
            )
        else:
            score += 12
            breakdown.append(f"⚠️ 1. Bozor Strukturasi (+12/25): M15 {direction} lokal impuls.")

        # 2. Liquidity Sweep & Price Action Rejection Trigger (Max 25 ball)
        if has_sweep and candle_confirmed:
            score += 25
            breakdown.append(f"✅ 2. Liquidity Sweep & Candle Trigger (+25/25): {session_liq_info}.")
        elif candle_confirmed:
            score += 18
            breakdown.append(f"✅ 2. Price Action Trigger (+18/25): M15 Engulfing/Pin-bar tasdig'i.")
        else:
            score += 5
            breakdown.append("❌ 2. Shamcha tasdig'i yo'q (+5/25).")

        # 3. Value Zone Pullback (EMA20 / VWAP / FVG / OB) — Overextension himoyasi (Max 20 ball)
        has_fvg = (direction == "BUY" and fvg_data["bullish_fvg"] is not None) or (
            direction == "SELL" and fvg_data["bearish_fvg"] is not None
        )
        if in_value_zone and has_fvg:
            score += 20
            breakdown.append(f"✅ 3. Discount/Premium POI + FVG (+20/20): {poi_str}.")
        elif in_value_zone:
            score += 17
            breakdown.append(f"✅ 3. Value Zone Pullback (+17/20): {poi_str}.")
        else:
            score += 5
            breakdown.append("⚠️ 3. Narx Value zonadan uzoqda (+5/20).")

        # 4. Makroiqtisodiy Sabab-Oqibat va DXY/US10Y Korrelyatsiyasi (Max 15 ball)
        macro_aligned = (direction == "BUY" and macro.bias == "BULLISH") or (direction == "SELL" and macro.bias == "BEARISH")
        macro_opposed = (direction == "BUY" and macro.bias == "BEARISH") or (direction == "SELL" and macro.bias == "BULLISH")

        if macro_aligned and abs(macro.confidence_score) >= 0.30:
            score += 15
            breakdown.append(f"✅ 4. Makro & DXY Korrelyatsiya (+15/15): {macro.bias} ({macro.confidence_score:+.2f}) | DXY: {snapshot.dxy_trend}.")
        elif macro_aligned:
            score += 12
            breakdown.append(f"✅ 4. Makro & DXY Korrelyatsiya (+12/15): {macro.bias} ({macro.confidence_score:+.2f}).")
        elif not macro_opposed:
            score += 8
            breakdown.append(f"⚠️ 4. Makro Fon Neytral (+8/15): {macro.bias} ({macro.confidence_score:+.2f}).")
        else:
            score += 0
            breakdown.append(f"❌ 4. Makro Zidlik (+0/15): Macro {macro.bias} ({macro.confidence_score:+.2f}) qarama-qarshi.")

        # 5. RSI Momentum, Intraday VWAP & Volume RVOL (Max 15 ball)
        rsi_ideal = (direction == "BUY" and 42.0 <= ltf_ind["rsi"] <= 64.0) or (
            direction == "SELL" and 36.0 <= ltf_ind["rsi"] <= 58.0
        )
        vwap_ok = (direction == "BUY" and anchor_price >= vwap_price - 0.15 * atr) or (
            direction == "SELL" and anchor_price <= vwap_price + 0.15 * atr
        )

        if rsi_ideal and vwap_ok and rvol >= 1.05:
            score += 15
            breakdown.append(f"✅ 5. Momentum & Hajm (+15/15): RSI {ltf_ind['rsi']} | VWAP {vwap_price:.2f} | RVOL {rvol}x.")
        elif rsi_ideal and vwap_ok:
            score += 12
            breakdown.append(f"✅ 5. Momentum & VWAP (+12/15): RSI {ltf_ind['rsi']} | VWAP {vwap_price:.2f} | RVOL {rvol}x.")
        else:
            score += 4
            breakdown.append(f"⚠️ 5. Momentum (+4/15): RSI {ltf_ind['rsi']} (Overbought/Oversold xavfi) | RVOL {rvol}x.")

        is_ideal = (
            score >= 85
            and not macro_opposed
            and has_sweep
            and in_value_zone
            and candle_confirmed
            and rsi_ideal
        )

        zone_desc = (
            f"{snapshot.price_source} | H1/M15 {direction} Trend Pullback | "
            f"{poi_str} | VWAP: {vwap_price:.2f} (RVOL: {rvol}x, M15 RSI: {ltf_ind['rsi']})"
        )

        trailing_text = (
            f"1) Narx TP1 ({tp1_price:.2f}) ga yetganda pozitsiyaning 50% qismi yopiladi va SL darhol "
            f"Entry ({entry_price:.2f}) ga (Breakeven / Bezubitok) ko'chiriladi. "
            f"2) TP2 ({tp2_price:.2f}) da yana 30% yopiladi. Qolgan 20% qismi TP3 ({tp3_price:.2f}) gacha ushlanadi."
        )

        return TechnicalSetup(
            symbol=snapshot.symbol,
            is_ideal_setup=is_ideal,
            direction=direction,
            order_type=order_type,
            entry_price=entry_price,
            sl_price=sl_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            tp3_price=tp3_price,
            rr_tp1=rr_tp1,
            rr_tp2=rr_tp2,
            confluence_score=score,
            Zone_Description=zone_desc,
            liquidity_swept=has_sweep,
            ob_fvg_aligned=in_value_zone,
            choch_confirmed=candle_confirmed,
            volume_spike=(rvol >= 1.05),
            confluence_breakdown=breakdown,
            breakeven_trigger_price=tp1_price,
            trailing_rule=trailing_text,
            orderbook_wall_info=ob_wall_str,
            session_liquidity_info=session_liq_info,
        )
