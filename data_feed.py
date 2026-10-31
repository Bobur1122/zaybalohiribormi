"""
1-QISM: Bozor ma'lumotlarini, Kunlik/Osiyo Likvidlik Xaritasini (`PDH/PDL`, `Asian High/Low`),
Bozor Ochiq/Yopiq Kalendarini va Order-Book Kitlar Devorini yig'uvchi modul (data_feed.py) — PRO v6.0
PRO v6.0 HAQIQIY BOZOR YANGILANISHLARI:
1. Shanba va Yakshanba kunlari (Juma 23:00 UZT dan Dushanba 04:00 UZT gacha) XAUUSD (Oltin) Forex bozori
   yopiq ekanligini aniq tekshiradi (`is_market_open_for_symbol`) va dam olish kunlari Oltindan signal chiqishini
   100% bloklaydi!
2. Soxta (sun'iy `sweep_idx = bars - 5`) shamchalar yasaydigan eski sandbox mantiqi BUTUNLAY OLIB TASHLANDI:
   agar jonli API (Yahoo Finance / Binance / MT5) vaqtincha javob bermasa, `is_live_data=False` belgilanadi va
   yolg'on signal chiqarilmaydi.
3. BTCUSD uchun Binance Spot REST API (`api.binance.com/api/v3/klines`) birlamchi va Yahoo Finance ikkilamchi
   jonli manba sifatida ulandi.
"""
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Optional
import urllib.parse
import urllib.request
import numpy as np
import pandas as pd

from config import (
    SYMBOLS,
    TV_TICKERS,
    HTF,
    LTF,
    MT5_LOGIN,
    MT5_PASSWORD,
    MT5_SERVER,
    MT5_PATH,
    TIMEZONE_OFFSET_HOURS,
)
from schemas import MarketSnapshot

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False

TV_SCANNER_ENDPOINTS = [
    "https://scanner.tradingview.com/global/scan",
    "https://scanner.tradingview.com/forex/scan",
    "https://scanner.tradingview.com/cfd/scan",
]


def is_market_open_for_symbol(symbol: str, now_utc: Optional[datetime] = None) -> tuple[bool, str]:
    """
    Aktiv bo'yicha xalqaro bozor ochiq yoki yopiqligini tekshiradi (Toshkent vaqti UTC+5 asosida).
    - BTCUSD (Kripto): 24/7 ochiq.
    - XAUUSD, USDX, US10Y (Forex / Spot Gold / CME):
      * Shanba (weekday == 5): Kun bo'yi YOPIQ.
      * Yakshanba (weekday == 6): Kun bo'yi YOPIQ (Dushanba soat 03:00-04:00 UZT da Osiyo sessiyasi bilan ochiladi).
      * Juma (weekday == 4): Soat 23:00 UZT dan keyin haftalik yopilish oldidan yangi savdo ochilmaydi.
      * Dushanba (weekday == 0): Soat 03:00 UZT gacha bozor hali ochilmagan bo'ladi.
      * Har kuni 02:00 - 03:05 UZT: Kunlik CME rollover / texnik tanaffus.
    """
    sym = symbol.upper()
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    tz_uz = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))
    now_uz = now_utc.astimezone(tz_uz)
    wd = now_uz.weekday()  # 0=Dushanba ... 4=Juma, 5=Shanba, 6=Yakshanba
    hm = now_uz.hour + now_uz.minute / 60.0

    if "BTC" in sym:
        if wd in (5, 6):
            return True, f"Kripto 24/7 Ochiq ({now_uz.strftime('%A %H:%M')} UZT — Dam olish kuni CME yopiq, faqat yuqori hajmli setup olinadi)"
        return True, f"Kripto 24/7 Faol ({now_uz.strftime('%H:%M')} UZT)"

    # Forex / XAUUSD / DXY / US10Y uchun dam olish kunlari tekshiruvi
    if wd == 5:
        return False, f"Bozor Yopiq (Shanba dam olish kuni — {now_uz.strftime('%H:%M')} UZT. Oltin bozori Dushanba 03:00 UZT da ochiladi)"
    if wd == 6:
        return False, f"Bozor Yopiq (Yakshanba dam olish kuni — {now_uz.strftime('%H:%M')} UZT. Oltin bozori Dushanba 03:00 UZT da ochiladi)"
    if wd == 4 and hm >= 23.0:
        return False, f"Bozor Yopilmoqda (Juma kechki {now_uz.strftime('%H:%M')} UZT — Haftalik yopilish oldidan savdo to'xtatilgan)"
    if wd == 0 and hm < 3.0:
        return False, f"Bozor Hali Ochilmadi (Dushanba {now_uz.strftime('%H:%M')} UZT — Oltin bozori 03:00 UZT da ochiladi)"
    if 2.0 <= hm < 3.1:
        return False, f"Kunlik Rollover Tanaffusi ({now_uz.strftime('%H:%M')} UZT — CME spred kengayishi vaqti)"

    return True, f"Forex/Gold Bozor Ochiq ({now_uz.strftime('%H:%M')} UZT)"


class MarketDataCollector:
    """
    Haqiqiy tarixiy shamchalar, TradingView FOREXCOM:XAUUSD, Binance BTCUSDT, DXY, US10Y,
    PDH/PDL, Asian Range va Order-Book Whale Walls ma'lumotlarini yig'uvchi PRO v6.0 klass.
    """

    def __init__(self, use_mt5: bool = False):
        self.use_mt5 = use_mt5 and MT5_AVAILABLE
        self.mt5_connected = False
        self.exchange = ccxt.binance({"enableRateLimit": True}) if CCXT_AVAILABLE else None
        self._stable_basis_cache: dict[str, tuple[int, float]] = {}

        if self.use_mt5:
            self._init_mt5()

    def _init_mt5(self) -> None:
        kwargs = {}
        if MT5_PATH:
            kwargs["path"] = MT5_PATH
        if mt5.initialize(**kwargs):
            authorized = mt5.login(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
            if authorized:
                self.mt5_connected = True
                logger.info("MT5 terminaliga muvaffaqiyatli ulandi.")

    def fetch_tradingview_live_quote(self, symbol: str) -> Optional[dict[str, Any]]:
        tv_ticker_map = {
            **TV_TICKERS,
            "US10Y": "TVC:US10Y",
        }
        tv_ticker = tv_ticker_map.get(symbol, "FOREXCOM:XAUUSD")
        payload = {
            "symbols": {"tickers": [tv_ticker], "query": {"types": []}},
            "columns": [
                "close", "open", "high", "low", "EMA20", "EMA50",
                "RSI", "ATR", "Recommend.All", "Pivot.M.Classic.Middle",
                "Pivot.M.Classic.R1", "Pivot.M.Classic.S1"
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        for endpoint in TV_SCANNER_ENDPOINTS:
            try:
                req = urllib.request.Request(
                    endpoint,
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                rows = res.get("data", [])
                if rows and "d" in rows[0] and rows[0]["d"][0] is not None:
                    d = rows[0]["d"]
                    return {
                        "ticker": tv_ticker,
                        "close": float(d[0]),
                        "open": float(d[1]) if d[1] is not None else None,
                        "high": float(d[2]) if d[2] is not None else None,
                        "low": float(d[3]) if d[3] is not None else None,
                        "ema20": float(d[4]) if d[4] is not None else None,
                        "ema50": float(d[5]) if d[5] is not None else None,
                        "rsi": float(d[6]) if d[6] is not None else None,
                        "atr": float(d[7]) if d[7] is not None else None,
                    }
            except Exception as e:
                logger.debug(f"TradingView Scanner endpoint ({endpoint}) xatosi: {e}")
        return None

    def fetch_orderbook_walls(
        self,
        symbol: str,
        current_price: float,
        atr: float,
        ltf_df: pd.DataFrame,
    ) -> dict[str, float]:
        binance_sym = "PAXGUSDT" if "XAU" in symbol.upper() else "BTCUSDT"
        url = f"https://api.binance.com/api/v3/depth?symbol={binance_sym}&limit=100"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                depth = json.loads(resp.read().decode("utf-8"))

            bids = [(float(p), float(q)) for p, q in depth.get("bids", [])]
            asks = [(float(p), float(q)) for p, q in depth.get("asks", [])]
            if bids and asks:
                mid_ob = (bids[0][0] + asks[0][0]) / 2.0
                basis_shift = current_price - mid_ob

                best_bid_p, best_bid_v = max(bids, key=lambda x: x[1])
                best_ask_p, best_ask_v = max(asks, key=lambda x: x[1])

                total_bid_v = sum(q for _, q in bids)
                total_ask_v = sum(q for _, q in asks)
                imbalance = round(total_ask_v / max(total_bid_v, 1e-6), 2)

                return {
                    "whale_ask_wall": round(best_ask_p + basis_shift, 2),
                    "whale_ask_vol": round(best_ask_v, 2),
                    "whale_bid_wall": round(best_bid_p + basis_shift, 2),
                    "whale_bid_vol": round(best_bid_v, 2),
                    "ob_imbalance_ratio": imbalance,
                }
        except Exception as e:
            logger.debug(f"Order-Book API oflayn ({symbol}), hajm profilidan hisoblanmoqda: {e}")

        closed = ltf_df.iloc[:-1].tail(36) if len(ltf_df) > 10 else ltf_df
        upper_df = closed[closed["high"] > current_price]
        lower_df = closed[closed["low"] < current_price]

        if not upper_df.empty:
            max_ask_idx = upper_df["volume"].idxmax()
            ask_wall = round(float(upper_df.loc[max_ask_idx, "high"]), 2)
            ask_vol = round(float(upper_df.loc[max_ask_idx, "volume"]), 1)
        else:
            ask_wall = round(current_price + 0.85 * atr, 2)
            ask_vol = 0.0

        if not lower_df.empty:
            max_bid_idx = lower_df["volume"].idxmax()
            bid_wall = round(float(lower_df.loc[max_bid_idx, "low"]), 2)
            bid_vol = round(float(lower_df.loc[max_bid_idx, "volume"]), 1)
        else:
            bid_wall = round(current_price - 0.85 * atr, 2)
            bid_vol = 0.0

        imbalance = round(ask_vol / max(bid_vol, 1.0), 2) if bid_vol > 0 else 1.0
        return {
            "whale_ask_wall": ask_wall,
            "whale_ask_vol": ask_vol,
            "whale_bid_wall": bid_wall,
            "whale_bid_vol": bid_vol,
            "ob_imbalance_ratio": imbalance,
        }

    @staticmethod
    def _compute_session_liquidity_levels(ltf_df: pd.DataFrame, htf_df: pd.DataFrame) -> dict[str, float]:
        """
        PRO v6.0: Kechagi kun / oldingi 24 soatlik High-Low (`PDH` va `PDL`) hamda
        Osiyo sessiyasi (`03:00–10:00 UZT`) High-Low darajalarini hisoblaydi.
        """
        closed_ltf = ltf_df.iloc[:-1] if len(ltf_df) > 20 else ltf_df
        closed_htf = htf_df.iloc[:-1] if len(htf_df) > 20 else htf_df

        if len(closed_ltf) >= 40:
            ref_window = closed_ltf.iloc[-108:-12] if len(closed_ltf) > 108 else closed_ltf.iloc[:-12]
            pdh = round(float(ref_window["high"].max()), 2)
            pdl = round(float(ref_window["low"].min()), 2)
        else:
            ref_h = closed_htf.iloc[:-3] if len(closed_htf) > 6 else closed_htf
            pdh = round(float(ref_h["high"].max()), 2)
            pdl = round(float(ref_h["low"].min()), 2)

        try:
            times_utc = pd.to_datetime(closed_ltf["time"], utc=True)
            hours_uzt = (times_utc.dt.hour + TIMEZONE_OFFSET_HOURS) % 24
            asian_mask = (hours_uzt >= 3) & (hours_uzt < 10)
            prior_ltf = closed_ltf.iloc[:-6] if len(closed_ltf) > 24 else closed_ltf
            prior_mask = asian_mask.iloc[:-6] if len(closed_ltf) > 24 else asian_mask
            asian_bars = prior_ltf[prior_mask].tail(28)
            if not asian_bars.empty and len(asian_bars) >= 4:
                asian_high = round(float(asian_bars["high"].max()), 2)
                asian_low = round(float(asian_bars["low"].min()), 2)
            else:
                sub24 = closed_ltf.iloc[-40:-8] if len(closed_ltf) > 40 else closed_ltf.iloc[:-6]
                asian_high = round(float(sub24["high"].max()), 2)
                asian_low = round(float(sub24["low"].min()), 2)
        except Exception:
            sub24 = closed_ltf.iloc[-40:-8] if len(closed_ltf) > 40 else closed_ltf.tail(24)
            asian_high = round(float(sub24["high"].max()), 2)
            asian_low = round(float(sub24["low"].min()), 2)

        return {
            "pdh": pdh,
            "pdl": pdl,
            "asian_high": asian_high,
            "asian_low": asian_low,
        }

    def fetch_binance_ohlcv(
        self,
        symbol: str,
        timeframe_str: str,
        bars: int = 200,
    ) -> Optional[pd.DataFrame]:
        """BTCUSD uchun Binance Spot API dan 100% haqiqiy shamchalarni va real hajmni oladi."""
        if "BTC" not in symbol.upper():
            return None
        interval_map = {"M5": "5m", "M15": "15m", "H1": "1h", "H4": "4h", "D1": "1d"}
        interval = interval_map.get(timeframe_str, "15m")
        url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={interval}&limit={bars}"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0"},
            )
            with urllib.request.urlopen(req, timeout=7) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            if not raw or len(raw) < 30:
                return None
            df = pd.DataFrame(
                [
                    {
                        "time": pd.to_datetime(int(row[0]), unit="ms", utc=True),
                        "open": round(float(row[1]), 2),
                        "high": round(float(row[2]), 2),
                        "low": round(float(row[3]), 2),
                        "close": round(float(row[4]), 2),
                        "volume": round(float(row[5]), 4),
                    }
                    for row in raw
                ]
            )
            return df
        except Exception as e:
            logger.debug(f"Binance OHLCV xatosi ({symbol} {timeframe_str}): {e}")
            return None

    def fetch_live_intraday_ohlcv(
        self,
        symbol: str,
        timeframe_str: str,
        bars: int = 200,
        live_quote: Optional[dict[str, Any]] = None,
    ) -> Optional[pd.DataFrame]:
        # BTCUSD uchun avval Binance haqiqiy Spot shamchalarini tekshiramiz
        if "BTC" in symbol.upper():
            bin_df = self.fetch_binance_ohlcv(symbol, timeframe_str, bars)
            if bin_df is not None and not bin_df.empty:
                return bin_df

        yf_symbol_map = {
            "XAUUSD": "GC=F",
            "USDX": "DX-Y.NYB",
            "US10Y": "^TNX",
            "BTCUSD": "BTC-USD",
        }
        yf_sym = yf_symbol_map.get(symbol.upper(), "GC=F")
        interval = "15m" if timeframe_str == "M15" else ("5m" if timeframe_str == "M5" else "1h")
        range_str = "5d" if interval in ("5m", "15m") else "1mo"

        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yf_sym)}"
            f"?interval={interval}&range={range_str}"
        )
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8"))

            result = payload["chart"]["result"][0]
            timestamps = result["timestamp"]
            quote = result["indicators"]["quote"][0]

            df = pd.DataFrame({
                "time": pd.to_datetime(timestamps, unit="s", utc=True),
                "open": quote["open"],
                "high": quote["high"],
                "low": quote["low"],
                "close": quote["close"],
                "volume": quote["volume"],
            }).dropna().tail(bars).reset_index(drop=True)

            if df.empty or len(df) < 25:
                return None

            closed_candle_ts = int(pd.Timestamp(df["time"].iloc[-2]).timestamp())
            cache_key = f"{symbol}_{timeframe_str}"

            if live_quote and live_quote.get("close") is not None:
                tv_close = float(live_quote["close"])
                cached = self._stable_basis_cache.get(cache_key)
                if cached is not None and cached[0] == closed_candle_ts:
                    basis_offset = cached[1]
                else:
                    last_yf_close = float(df["close"].iloc[-1])
                    basis_offset = round(tv_close - last_yf_close, 2)
                    # Basis faqat XAUUSD (Fyuchers va Spot farqi) uchun qo'llaniladi va +/-80 pt bilan chegaralanadi
                    if "XAU" in symbol.upper() and abs(basis_offset) <= 80.0:
                        self._stable_basis_cache[cache_key] = (closed_candle_ts, basis_offset)
                    else:
                        basis_offset = 0.0
            else:
                basis_offset = 0.0

            if basis_offset != 0.0:
                for col in ("open", "high", "low", "close"):
                    df[col] = np.round(df[col] + basis_offset, 2)

            if live_quote and live_quote.get("close") is not None:
                tv_close = round(float(live_quote["close"]), 2)
                # Faqat oxirgi shamcha yangi (so'nggi 2 soat ichida) bo'lsa jonli narx bilan yangilaymiz
                last_bar_time = pd.Timestamp(df["time"].iloc[-1]).to_pydatetime()
                if (datetime.now(timezone.utc) - last_bar_time).total_seconds() < 7200:
                    df.loc[df.index[-1], "close"] = tv_close
                    df.loc[df.index[-1], "high"] = max(float(df.loc[df.index[-1], "high"]), tv_close)
                    df.loc[df.index[-1], "low"] = min(float(df.loc[df.index[-1], "low"]), tv_close)

            return df
        except Exception as e:
            logger.debug(f"Live Intraday OHLCV API oflayn ({symbol} {timeframe_str}): {e}")
            return None

    @staticmethod
    def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        if "tick_volume" in df.columns and "volume" not in df.columns:
            df.rename(columns={"tick_volume": "volume"}, inplace=True)
        required = ["time", "open", "high", "low", "close", "volume"]
        return df[required]

    def _fetch_mt5_ohlcv(self, symbol: str, timeframe_str: str, bars: int = 200) -> pd.DataFrame:
        tf_map = {
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        tf = tf_map.get(timeframe_str, mt5.TIMEFRAME_H1)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"MT5 dan {symbol} ({timeframe_str}) ma'lumotlari olinmadi.")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return self._normalize_columns(df)

    @staticmethod
    def _build_neutral_fallback_ohlcv(
        symbol: str,
        timeframe_str: str,
        bars: int = 200,
        live_quote: Optional[dict[str, Any]] = None,
    ) -> pd.DataFrame:
        """
        DIQQAT (PRO v6.0): Agar barcha tashqi OHLCV API lar uzilib qolsa, HECH QANDAY SOXTA SWEEP
        YOKI SUN'IY TREND YASALMAYDI! Tekis (flat) neytral shamchalar qaytariladi va `is_live_data=False`
        bo'lgani uchun RiskManager har qanday signalni bloklaydi.
        """
        step_minutes = 60 if timeframe_str == "H1" else 15
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        times = [now - timedelta(minutes=step_minutes * (bars - i)) for i in range(bars)]

        if live_quote and live_quote.get("close"):
            base_p = round(float(live_quote["close"]), 2)
        elif "XAU" in symbol.upper():
            base_p = 4040.00
        elif "BTC" in symbol.upper():
            base_p = 80000.00
        else:
            base_p = 102.00

        delta = 0.50 if "XAU" in symbol.upper() else (20.0 if "BTC" in symbol.upper() else 0.05)
        return pd.DataFrame({
            "time": times,
            "open": [base_p] * bars,
            "high": [round(base_p + delta, 2)] * bars,
            "low": [round(base_p - delta, 2)] * bars,
            "close": [base_p] * bars,
            "volume": [1000.0] * bars,
        })

    def get_ohlcv_with_status(
        self, symbol: str, timeframe_str: str, bars: int = 200
    ) -> tuple[pd.DataFrame, bool]:
        live_quote = self.fetch_tradingview_live_quote(symbol)
        if self.mt5_connected:
            try:
                return self._fetch_mt5_ohlcv(symbol, timeframe_str, bars), True
            except Exception as e:
                logger.warning(f"MT5 xatosi ({symbol}): {e}")

        live_df = self.fetch_live_intraday_ohlcv(symbol, timeframe_str, bars, live_quote)
        if live_df is not None and not live_df.empty:
            return live_df, True

        logger.warning(f"[{symbol} {timeframe_str}] Jonli OHLCV API javob bermadi — savdo xavfsizlik uchun bloklanadi.")
        return self._build_neutral_fallback_ohlcv(symbol, timeframe_str, bars, live_quote), False

    def get_ohlcv(self, symbol: str, timeframe_str: str, bars: int = 200) -> pd.DataFrame:
        df, _ = self.get_ohlcv_with_status(symbol, timeframe_str, bars)
        return df

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
        closed_df = df.iloc[:-1] if len(df) > period + 2 else df
        high = closed_df["high"]
        low = closed_df["low"]
        prev_close = closed_df["close"].shift(1)
        tr = pd.concat([
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(window=period).mean()
        val = float(atr_series.iloc[-1])
        return round(val if not np.isnan(val) else float(tr.mean()), 4)

    def determine_dxy_trend(self) -> str:
        dxy_symbol = SYMBOLS.get("DXY", "USDX")
        quote = self.fetch_tradingview_live_quote(dxy_symbol)
        if quote and quote.get("close") and quote.get("ema20") and quote.get("ema50"):
            c = float(quote["close"])
            e20 = float(quote["ema20"])
            e50 = float(quote["ema50"])
            if e20 > e50 and c >= e20:
                return "BULLISH"
            elif e20 < e50 and c <= e20:
                return "BEARISH"

        df, ok = self.get_ohlcv_with_status(dxy_symbol, HTF, bars=120)
        if not ok:
            return "NEUTRAL"
        close = df["close"]
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        last_close = float(close.iloc[-1])

        if ema20 > ema50 and last_close >= ema20:
            return "BULLISH"
        elif ema20 < ema50 and last_close <= ema20:
            return "BEARISH"
        return "NEUTRAL"

    def determine_us10y_trend(self) -> str:
        quote = self.fetch_tradingview_live_quote("US10Y")
        if quote and quote.get("close") and quote.get("ema20") and quote.get("ema50"):
            c = float(quote["close"])
            e20 = float(quote["ema20"])
            e50 = float(quote["ema50"])
            if e20 > e50 and c >= e20:
                return "BULLISH"
            elif e20 < e50 and c <= e20:
                return "BEARISH"

        df, ok = self.get_ohlcv_with_status("US10Y", HTF, bars=80)
        if not ok:
            return "NEUTRAL"
        close = df["close"]
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        last_c = float(close.iloc[-1])
        if ema20 > ema50 and last_c >= ema20:
            return "BULLISH"
        elif ema20 < ema50 and last_c <= ema20:
            return "BEARISH"
        return "NEUTRAL"

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        market_open, market_note = is_market_open_for_symbol(symbol)
        htf_df, htf_live = self.get_ohlcv_with_status(symbol, HTF, bars=200)
        ltf_df, ltf_live = self.get_ohlcv_with_status(symbol, LTF, bars=200)
        is_live = htf_live and ltf_live

        dxy_trend = self.determine_dxy_trend()
        us10y_trend = self.determine_us10y_trend()
        atr_val = self.calculate_atr(htf_df, period=14)
        current_price = float(ltf_df["close"].iloc[-1])
        tv_ticker = TV_TICKERS.get(symbol, symbol)

        ob_walls = self.fetch_orderbook_walls(symbol, current_price, atr_val, ltf_df)
        session_lvls = self._compute_session_liquidity_levels(ltf_df, htf_df)

        return MarketSnapshot(
            symbol=symbol,
            htf_df=htf_df,
            ltf_df=ltf_df,
            dxy_trend=dxy_trend,
            atr=atr_val,
            current_price=current_price,
            price_source=f"TradingView ({tv_ticker})" if is_live else f"Offline ({tv_ticker})",
            us10y_trend=us10y_trend,
            whale_ask_wall=ob_walls["whale_ask_wall"],
            whale_ask_vol=ob_walls["whale_ask_vol"],
            whale_bid_wall=ob_walls["whale_bid_wall"],
            whale_bid_vol=ob_walls["whale_bid_vol"],
            ob_imbalance_ratio=ob_walls["ob_imbalance_ratio"],
            pdh=session_lvls["pdh"],
            pdl=session_lvls["pdl"],
            asian_high=session_lvls["asian_high"],
            asian_low=session_lvls["asian_low"],
            is_live_data=is_live,
            is_market_open=market_open,
            market_status_note=market_note,
        )
