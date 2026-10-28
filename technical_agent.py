"""
3-QISM: Technical Agent — PRO v5.1 Institutional Diamond Sniper (technical_agent.py)
PRO v5.1 MUHIM TUZATISHLAR (CATCH-22 BUG YECHIMI):
1. Oldingi PRO v5.0 da matematik "Catch-22" xatosi bor edi:
   - `SELL` chiqishi uchun oxirgi 8 ta M15 shamcha (`w3`) BIR VAQTNING O'ZIDA ham oxirgi 9 soatlik
     eng past nuqtani (`w3_low < min(w1_low, w2_low)`) yangilashi, HAM 24 soatlik/Osiyo cho'qqisini
     (`w3_high >= pdh/asian_high`) supurib o'tishi va `EMA20 < EMA50` bo'lishi talab qilingan edi!
   - Haqiqiy grafikda bitta 8-shamchalik oynada ham tepani supurib, ham 9 soatlik tubni sindirish
     deyarli 0% holatda uchraydi — shu sababli bot 2 kun davomida 0 ta signal bergan!
2. PRO v5.1 da haqiqiy Smart Money Concepts (SMC) ning IKKITA institutsional kirish arxetipi to'liq qo'llab-quvvatlanadi:
   - 1-Arxetip (`SWEEP_REVERSAL` — Liquidity Sweep & Rejection / CHoCH):
     Narx oxirgi 16 ta M15 shamcha (4 soat) ichida `PDH`, `PDL`, `Asian High/Low` yoki lokal Swing High/Low
     likvidligini supurib (bitta shamcha dumi bilan YOKI 2-3 shamchali Turtle-Soup qaytishi bilan) rad etsa.
   - 2-Arxetip (`OB_PULLBACK` — Trend BOS + Order Block / FVG Retest):
     Bozor aniq H1/M15 trendda (`EMA20 vs EMA50` yoki `VWAP` impulsida) bo'lib, narx M15 Order Block / FVG
     zonasiga (`in_setup_zone`) qaytib (pullback) institutsional limit zonani test qilayotgan bo'lsa.
3. Limit Order (`SELL_LIMIT` / `BUY_LIMIT`) masofasi joriy narxdan realistik M15 mikro-retest masofasiga
   (`0.04 * ATR` dan `0.18 * ATR` gacha) joylashtiriladi, shunda order havoda qolib ketmasdan keyingi
   shamchalarda aniq ishga tushadi!
"""
import numpy as np
import pandas as pd

from schemas import MarketSnapshot, FundamentalSignal, TechnicalSetup


class TechnicalAgent:
    """
    PRO v5.1 Institutional Diamond Sniper — Smart Money Concepts (SMC), Liquidity Sweep,
    Order Block + FVG va Order-Book Tahlilchisi.
    """

    @staticmethod
    def _closed_df(df: pd.DataFrame) -> pd.DataFrame:
        return df.iloc[:-1].copy() if len(df) > 40 else df.copy()

    @staticmethod
    def _calculate_ema_and_rsi(df: pd.DataFrame) -> dict[str, float]:
        close = df["close"]
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(window=14).mean()
        loss = (-delta.clip(upper=0)).rolling(window=14).mean()
        rs = gain / loss.replace(0, 1e-9)
        rsi_series = 100 - (100 / (1 + rs))
        rsi_val = float(rsi_series.iloc[-1])
        if np.isnan(rsi_val):
            rsi_val = 50.0

        return {
            "ema20": round(ema20, 2),
            "ema50": round(ema50, 2),
            "rsi": round(rsi_val, 1),
        }

    @staticmethod
    def _calculate_intraday_vwap_and_rvol(ltf_closed: pd.DataFrame) -> dict[str, float]:
        sub = ltf_closed.tail(36).copy()
        typical_price = (sub["high"] + sub["low"] + sub["close"]) / 3.0
        vol = sub["volume"].replace(0, 1.0)
        vwap_val = float((typical_price * vol).sum() / vol.sum())

        avg_vol = float(vol.mean())
        recent_peak_vol = float(vol.tail(8).max())
        rvol = round(recent_peak_vol / max(avg_vol, 1.0), 2)

        return {
            "vwap": round(vwap_val, 2),
            "rvol": rvol,
        }

    @staticmethod
    def _detect_fvg_zones(ltf_closed: pd.DataFrame, atr: float) -> dict[str, object]:
        sub = ltf_closed.tail(24).reset_index(drop=True)
        bullish_fvg = None
        bearish_fvg = None
        min_gap = 0.04 * atr

        for i in range(2, len(sub)):
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

    @staticmethod
    def _detect_liquidity_sweep_and_rejection(
        ltf_closed: pd.DataFrame,
        pdh: float,
        pdl: float,
        asian_high: float,
        asian_low: float,
        direction: str,
        atr: float,
    ) -> tuple[str, bool, float]:
        """
        PRO v5.1 Haqiqiy Institutsional Liquidity Sweep Tekshiruvi:
        Oxirgi 16 ta M15 shamcha (4 soat) ichida:
        1) Bitta shamcha dumi bilan (Single-Candle Wick Rejection >= 22%) PDH/PDL, Asian High/Low yoki Swing High/Low supurilsa, YOKI
        2) 2–4 shamchali Institutsional "Turtle-Soup / Stop-Hunt" bo'lsa (ya'ni shamcha darajani tepaga/pastga yorib o'tib,
           keyingi shamchalar daraja ichiga >= 0.12*ATR qaytib yopilsa), YOKI
        3) M15 lokal likvidlik cho'qqisi/tubidan kuchli impulsli rad etish (`>= 0.25*ATR`) kuzatilsa.
        """
        sub = ltf_closed.tail(36).reset_index(drop=True)
        recent_bars = sub.iloc[-14:]
        prior_bars = sub.iloc[:-14] if len(sub) > 18 else sub.iloc[:10]

        prior_swing_high = round(float(prior_bars["high"].max()), 2)
        prior_swing_low = round(float(prior_bars["low"].min()), 2)
        current_close = float(sub["close"].iloc[-1])

        max_high_idx = recent_bars["high"].idxmax()
        min_low_idx = recent_bars["low"].idxmin()

        high_bar = sub.loc[max_high_idx]
        low_bar = sub.loc[min_low_idx]

        # Yuqori shamcha dumi (Upper Wick Rejection Ratio)
        h_range = max(float(high_bar["high"]) - float(high_bar["low"]), 1e-4)
        h_upper_wick = float(high_bar["high"]) - max(float(high_bar["open"]), float(high_bar["close"]))
        upper_wick_ratio = round(h_upper_wick / h_range, 2)

        # Pastki shamcha dumi (Lower Wick Rejection Ratio)
        l_range = max(float(low_bar["high"]) - float(low_bar["low"]), 1e-4)
        l_lower_wick = min(float(low_bar["open"]), float(low_bar["close"])) - float(low_bar["low"])
        lower_wick_ratio = round(l_lower_wick / l_range, 2)

        swept_high_val = round(float(high_bar["high"]), 2)
        swept_low_val = round(float(low_bar["low"]), 2)

        # Multi-candle rejection (narx cho'qqidan qancha pastga qaytdi yoki tubdan qancha tepaga qaytdi)
        drop_from_recent_high = round(swept_high_val - current_close, 2)
        bounce_from_recent_low = round(current_close - swept_low_val, 2)

        if direction == "SELL":
            # 1) Kunlik PDH Sweep (yoki PDH ga 0.15*ATR yaqinlikdan qaytish)
            if swept_high_val >= pdh - 0.10 * atr and (
                upper_wick_ratio >= 0.22 or (current_close < pdh and drop_from_recent_high >= 0.15 * atr)
            ):
                return (
                    f"Kunlik PDH BSL Sweep ({pdh:.2f} likvidligi {swept_high_val:.2f} da olindi, qaytish -{drop_from_recent_high:.2f})",
                    True,
                    swept_high_val,
                )
            # 2) Osiyo High Sweep
            if swept_high_val >= asian_high - 0.08 * atr and (
                upper_wick_ratio >= 0.22 or (current_close < asian_high and drop_from_recent_high >= 0.15 * atr)
            ):
                return (
                    f"Asian High BSL Sweep ({asian_high:.2f} cho'qqisi {swept_high_val:.2f} da supurildi, rad etish dumi {int(upper_wick_ratio*100)}%)",
                    True,
                    swept_high_val,
                )
            # 3) M15/H1 Swing High Sweep
            if swept_high_val >= prior_swing_high - 0.08 * atr and (
                upper_wick_ratio >= 0.22 or drop_from_recent_high >= 0.18 * atr
            ):
                return (
                    f"M15 Swing High BSL Sweep ({prior_swing_high:.2f} -> {swept_high_val:.2f} Stop-Hunt & Bearish Rejection)",
                    True,
                    swept_high_val,
                )
            # 4) Intraday Supply Rejection (oxirgi 14 shamcha ichida cho'qqidan >= 0.25*ATR impulsli sotuv qaytishi)
            if drop_from_recent_high >= 0.25 * atr and (upper_wick_ratio >= 0.20 or float(high_bar["close"]) <= float(high_bar["open"])):
                return (
                    f"Intraday Supply Sweep & Rejection ({swept_high_val:.2f} cho'qqidan -{drop_from_recent_high:.2f} Bearish Displacement)",
                    True,
                    swept_high_val,
                )

        elif direction == "BUY":
            # 1) Kunlik PDL Sweep
            if swept_low_val <= pdl + 0.10 * atr and (
                lower_wick_ratio >= 0.22 or (current_close > pdl and bounce_from_recent_low >= 0.15 * atr)
            ):
                return (
                    f"Kunlik PDL SSL Sweep ({pdl:.2f} likvidligi {swept_low_val:.2f} da olindi, qaytish +{bounce_from_recent_low:.2f})",
                    True,
                    swept_low_val,
                )
            # 2) Osiyo Low Sweep
            if swept_low_val <= asian_low + 0.08 * atr and (
                lower_wick_ratio >= 0.22 or (current_close > asian_low and bounce_from_recent_low >= 0.15 * atr)
            ):
                return (
                    f"Asian Low SSL Sweep ({asian_low:.2f} tubi {swept_low_val:.2f} da supurildi, xarid dumi {int(lower_wick_ratio*100)}%)",
                    True,
                    swept_low_val,
                )
            # 3) M15/H1 Swing Low Sweep
            if swept_low_val <= prior_swing_low + 0.08 * atr and (
                lower_wick_ratio >= 0.22 or bounce_from_recent_low >= 0.18 * atr
            ):
                return (
                    f"M15 Swing Low SSL Sweep ({prior_swing_low:.2f} -> {swept_low_val:.2f} Stop-Hunt & Bullish Rejection)",
                    True,
                    swept_low_val,
                )
            # 4) Intraday Demand Rejection (oxirgi 14 shamcha ichida tubdan >= 0.25*ATR impulsli xarid qaytishi)
            if bounce_from_recent_low >= 0.25 * atr and (lower_wick_ratio >= 0.20 or float(low_bar["close"]) >= float(low_bar["open"])):
                return (
                    f"Intraday Demand Sweep & Rejection ({swept_low_val:.2f} tubidan +{bounce_from_recent_low:.2f} Bullish Displacement)",
                    True,
                    swept_low_val,
                )

        return (
            f"Kunlik diapazon: PDH {pdh:.2f} / PDL {pdl:.2f} | Asian: {asian_high:.2f}–{asian_low:.2f} (faol Order Block retesti)",
            False,
            swept_high_val if direction == "SELL" else swept_low_val,
        )

    @staticmethod
    def _detect_dynamic_smc_structure(
        htf_closed: pd.DataFrame,
        ltf_closed: pd.DataFrame,
        anchor_price: float,
        atr: float,
        vwap_price: float,
        macro_bias: str = "NEUTRAL",
        macro_conf: float = 0.0,
        pdh: float = 0.0,
        pdl: float = 0.0,
        asian_high: float = 0.0,
        asian_low: float = 0.0,
    ) -> dict[str, float | str | bool]:
        """
        PRO v5.1 Ikki Arxetipli Institutsional Bozor Strukturasi Tahlili:
        1) SWEEP_REVERSAL: Yuqori likvidlik (PDH / Asian High / Swing High) supurilib pastga rad etilganda -> SELL;
           Pastki likvidlik (PDL / Asian Low / Swing Low) supurilib tepaga rad etilganda -> BUY.
        2) TREND_OB_PULLBACK: Trend va Order-Flow (EMA20/50 yoki VWAP) aniq yo'nalishda bo'lib,
           narx M15 Order Block / FVG zonasida bo'lganda.
        """
        sub = ltf_closed.tail(36).reset_index(drop=True)
        w1 = sub.iloc[:12]
        w2 = sub.iloc[12:26]
        w3 = sub.iloc[26:]  # Oxirgi 10 ta yopilgan M15 shamcha

        w1_high, w1_low = float(w1["high"].max()), float(w1["low"].min())
        w2_high, w2_low = float(w2["high"].max()), float(w2["low"].min())
        w3_high, w3_low = float(w3["high"].max()), float(w3["low"].min())

        h1_close = htf_closed["close"]
        h1_ema20 = float(h1_close.ewm(span=20, adjust=False).mean().iloc[-1])
        h1_ema50 = float(h1_close.ewm(span=50, adjust=False).mean().iloc[-1])

        m15_close = ltf_closed["close"]
        m15_ema20 = float(m15_close.ewm(span=20, adjust=False).mean().iloc[-1])
        m15_ema50 = float(m15_close.ewm(span=50, adjust=False).mean().iloc[-1])

        # Oxirgi 14 shamchadagi ekstremumlar
        rec14 = sub.iloc[-14:]
        rec_high = float(rec14["high"].max())
        rec_low = float(rec14["low"].min())

        # 1-Arxetip: Top BSL Sweep Reversal (SELL) yoki Bottom SSL Sweep Reversal (BUY)
        top_sweep_rejection = (
            (
                (pdh > 0 and rec_high >= pdh - 0.10 * atr)
                or (asian_high > 0 and rec_high >= asian_high - 0.08 * atr)
                or (rec_high >= max(w1_high, w2_high) - 0.05 * atr)
            )
            and (rec_high - anchor_price >= 0.15 * atr)
        )

        bottom_sweep_rejection = (
            (
                (pdl > 0 and rec_low <= pdl + 0.10 * atr)
                or (asian_low > 0 and rec_low <= asian_low + 0.08 * atr)
                or (rec_low <= min(w1_low, w2_low) + 0.05 * atr)
            )
            and (anchor_price - rec_low >= 0.15 * atr)
        )

        # 2-Arxetip: Trend BOS / Order-Flow Continuation
        bearish_flow = (
            (m15_ema20 <= m15_ema50 and anchor_price <= vwap_price + 0.25 * atr)
            or (anchor_price < vwap_price and w3_low <= w2_low)
            or (h1_ema20 < h1_ema50 and anchor_price <= m15_ema20 + 0.30 * atr)
        )
        bullish_flow = (
            (m15_ema20 >= m15_ema50 and anchor_price >= vwap_price - 0.25 * atr)
            or (anchor_price > vwap_price and w3_high >= w2_high)
            or (h1_ema20 > h1_ema50 and anchor_price >= m15_ema20 - 0.30 * atr)
        )

        dist_to_ema20 = abs(anchor_price - m15_ema20)
        dist_to_vwap = abs(anchor_price - vwap_price)
        in_ob_zone = (dist_to_ema20 <= 1.35 * atr) or (dist_to_vwap <= 1.25 * atr)

        # Tor konsolidatsiya (No-Man's Land) tekshiruvi:
        recent_range = rec_high - rec_low
        is_dead_chop = (recent_range < 0.45 * atr) and (macro_bias == "NEUTRAL") and (abs(macro_conf) < 0.25)

        direction = "NONE"
        in_setup_zone = False

        if not is_dead_chop:
            if macro_bias == "BEARISH":
                if top_sweep_rejection or bearish_flow or in_ob_zone:
                    direction = "SELL"
                    in_setup_zone = top_sweep_rejection or in_ob_zone
            elif macro_bias == "BULLISH":
                if bottom_sweep_rejection or bullish_flow or in_ob_zone:
                    direction = "BUY"
                    in_setup_zone = bottom_sweep_rejection or in_ob_zone
            else:
                # Agar Macro NEUTRAL bo'lsa, faqat aniq Sweep Reversal yoki kuchli Trend BOS bo'lgandagina yo'nalish beriladi
                if top_sweep_rejection and (anchor_price <= vwap_price or m15_ema20 <= m15_ema50):
                    direction = "SELL"
                    in_setup_zone = True
                elif bottom_sweep_rejection and (anchor_price >= vwap_price or m15_ema20 >= m15_ema50):
                    direction = "BUY"
                    in_setup_zone = True
                elif bearish_flow and in_ob_zone and (w3_low < w2_low):
                    direction = "SELL"
                    in_setup_zone = True
                elif bullish_flow and in_ob_zone and (w3_high > w2_high):
                    direction = "BUY"
                    in_setup_zone = True

        # Order Block va Likvidlik darajalari
        bear_candles = sub[sub["close"] < sub["open"]]
        bull_candles = sub[sub["close"] > sub["open"]]

        if not bull_candles.empty:
            last_up_idx = bull_candles["high"].idxmax()
            bear_ob_high = round(float(sub.loc[last_up_idx, "high"]), 2)
            bear_ob_low = round(float(sub.loc[last_up_idx, "open"]), 2)
            if bear_ob_high - bear_ob_low < 0.15 * atr:
                bear_ob_low = round(bear_ob_high - 0.28 * atr, 2)
        else:
            bear_ob_high = round(w3_high, 2)
            bear_ob_low = round(w3_high - 0.28 * atr, 2)

        if not bear_candles.empty:
            last_dn_idx = bear_candles["low"].idxmin()
            bull_ob_low = round(float(sub.loc[last_dn_idx, "low"]), 2)
            bull_ob_high = round(float(sub.loc[last_dn_idx, "open"]), 2)
            if bull_ob_high - bull_ob_low < 0.15 * atr:
                bull_ob_high = round(bull_ob_low + 0.28 * atr, 2)
        else:
            bull_ob_low = round(w3_low, 2)
            bull_ob_high = round(w3_low + 0.28 * atr, 2)

        recent_8 = sub.tail(8)
        recent_bull_pull = recent_8[recent_8["high"] >= anchor_price]
        if not recent_bull_pull.empty:
            retest_high = round(float(recent_bull_pull["high"].max()), 2)
            retest_low = round(max(anchor_price + 0.04 * atr, min(retest_high - 0.12 * atr, anchor_price + 0.16 * atr)), 2)
        else:
            retest_low = round(anchor_price + 0.08 * atr, 2)
            retest_high = round(anchor_price + 0.28 * atr, 2)

        recent_bear_pull = recent_8[recent_8["low"] <= anchor_price]
        if not recent_bear_pull.empty:
            buy_zone_low = round(float(recent_bear_pull["low"].min()), 2)
            buy_zone_high = round(min(anchor_price - 0.04 * atr, max(buy_zone_low + 0.12 * atr, anchor_price - 0.16 * atr)), 2)
        else:
            buy_zone_high = round(anchor_price - 0.08 * atr, 2)
            buy_zone_low = round(anchor_price - 0.28 * atr, 2)

        major_high = round(float(sub["high"].max()), 2)
        major_low = round(float(sub["low"].min()), 2)

        return {
            "direction": direction,
            "in_setup_zone": in_setup_zone,
            "top_sweep_rejection": top_sweep_rejection,
            "bottom_sweep_rejection": bottom_sweep_rejection,
            "w1_high": round(w1_high, 2),
            "w1_low": round(w1_low, 2),
            "w2_high": round(w2_high, 2),
            "w2_low": round(w2_low, 2),
            "w3_high": round(w3_high, 2),
            "w3_low": round(w3_low, 2),
            "major_high": major_high,
            "major_low": major_low,
            "bear_ob_low": bear_ob_low,
            "bear_ob_high": bear_ob_high,
            "bull_ob_low": bull_ob_low,
            "bull_ob_high": bull_ob_high,
            "retest_low": retest_low,
            "retest_high": retest_high,
            "buy_zone_low": buy_zone_low,
            "buy_zone_high": buy_zone_high,
            "h1_ema20": round(h1_ema20, 2),
            "h1_ema50": round(h1_ema50, 2),
            "m15_ema20": round(m15_ema20, 2),
            "m15_ema50": round(m15_ema50, 2),
        }

    def find_setup(self, snapshot: MarketSnapshot, macro: FundamentalSignal) -> TechnicalSetup:
        htf_closed = self._closed_df(snapshot.htf_df)
        ltf_closed = self._closed_df(snapshot.ltf_df)

        anchor_price = round(float(snapshot.current_price), 2)
        atr = max(round(float(snapshot.atr), 2), 1.5)
        is_btc = "BTC" in snapshot.symbol.upper()

        htf_ind = self._calculate_ema_and_rsi(htf_closed)
        ltf_ind = self._calculate_ema_and_rsi(ltf_closed)
        vwap_data = self._calculate_intraday_vwap_and_rvol(ltf_closed)
        fvg_data = self._detect_fvg_zones(ltf_closed, atr)

        vwap_price = vwap_data["vwap"]
        rvol = vwap_data["rvol"]

        smc = self._detect_dynamic_smc_structure(
            htf_closed,
            ltf_closed,
            anchor_price,
            atr,
            vwap_price,
            macro_bias=macro.bias,
            macro_conf=macro.confidence_score,
            pdh=snapshot.pdh,
            pdl=snapshot.pdl,
            asian_high=snapshot.asian_high,
            asian_low=snapshot.asian_low,
        )

        direction = str(smc["direction"])
        in_setup_zone = bool(smc["in_setup_zone"])

        # Agar direction NONE bo'lsa, Neytral diagnostika qaytaramiz
        if direction == "NONE":
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
                confluence_score=40,
                Zone_Description="Bozor tor konsolidatsiyada (No-Man's Land) — A+ impuls kutilmoqda.",
                liquidity_swept=False,
                ob_fvg_aligned=False,
                choch_confirmed=False,
                volume_spike=False,
                confluence_breakdown=["❌ Bozor tor konsolidatsiyada (A+ Liquidity Sweep yoki Order Block retesti yo'q)."],
                breakeven_trigger_price=anchor_price,
                trailing_rule="Faol savdo yo'q.",
                orderbook_wall_info="Neytral",
                session_liquidity_info=f"PDH: {snapshot.pdh:.2f} | PDL: {snapshot.pdl:.2f} | Asian: {snapshot.asian_high:.2f}–{snapshot.asian_low:.2f}",
            )

        # Liquidity Sweep va Rejection tekshiruvi
        session_liq_info, has_sweep, sweep_extreme = self._detect_liquidity_sweep_and_rejection(
            ltf_closed=ltf_closed,
            pdh=snapshot.pdh,
            pdl=snapshot.pdl,
            asian_high=snapshot.asian_high,
            asian_low=snapshot.asian_low,
            direction=direction,
            atr=atr,
        )

        # =====================================================================
        # KIRISH, STOP-LOSS VA TAKE-PROFIT HISOBLASH (PRO v5.1 REALISTIK LIMIT)
        # =====================================================================
        if direction == "SELL":
            retest_low = float(smc["retest_low"])
            retest_high = float(smc["retest_high"])

            if snapshot.whale_ask_wall > anchor_price and (snapshot.whale_ask_wall - anchor_price) <= 0.25 * atr:
                retest_low = round(min(retest_low, snapshot.whale_ask_wall - (0.20 if not is_btc else 15.0)), 2)

            order_type = "SELL_LIMIT"
            # Limit order joriy narxdan 0.04*ATR – 0.16*ATR masofada qo'yiladi (shamcha dumi aniq oladi)
            entry_price = round(max(anchor_price + (0.40 if not is_btc else 15.0), min(retest_low, anchor_price + 0.18 * atr)), 2)

            min_sl_dist = max(1.20 * atr, 11.50 if not is_btc else 350.0)
            structural_peak = max(sweep_extreme if has_sweep else retest_high, float(smc["w3_high"]))
            sl_structural = round(structural_peak + 0.22 * atr, 2)
            sl_price = round(max(sl_structural, entry_price + min_sl_dist), 2)
            if sl_price - entry_price > 2.45 * atr:
                sl_price = round(entry_price + 2.15 * atr, 2)

            risk_dist = abs(sl_price - entry_price)

            raw_tp1 = round(entry_price - max(1.25 * risk_dist, 1.35 * atr), 2)
            if anchor_price > snapshot.whale_bid_wall > raw_tp1 and (entry_price - snapshot.whale_bid_wall) >= 1.20 * risk_dist:
                tp1_price = round(snapshot.whale_bid_wall + (0.50 if not is_btc else 25.0), 2)
            else:
                tp1_price = raw_tp1

            tp2_price = round(entry_price - max(2.10 * risk_dist, 2.30 * atr), 2)
            tp3_price = round(entry_price - max(3.00 * risk_dist, 3.40 * atr), 2)

            fvg_str = (
                f"M15 Bearish FVG ({fvg_data['bearish_fvg'][0]:.2f}–{fvg_data['bearish_fvg'][1]:.2f})"
                if fvg_data["bearish_fvg"]
                else f"M15 Supply OB ({retest_low:.2f}–{retest_high:.2f})"
            )
            ob_wall_str = (
                f"Whale Ask Wall: {snapshot.whale_ask_wall:.2f} ({snapshot.whale_ask_vol:.0f} lot) | "
                f"Whale Bid Wall: {snapshot.whale_bid_wall:.2f} ({snapshot.whale_bid_vol:.0f} lot)"
            )
            zone_desc = (
                f"TradingView ({snapshot.price_source}) H1/M15 Bearish SMC Setup | "
                f"{session_liq_info} | Kirish: {entry_price:.2f} ({fvg_str}) | "
                f"Intraday VWAP: {vwap_price:.2f} (RVOL: {rvol}x)"
            )

        else:  # BUY
            buy_zone_low = float(smc["buy_zone_low"])
            buy_zone_high = float(smc["buy_zone_high"])

            if 0 < snapshot.whale_bid_wall < anchor_price and (anchor_price - snapshot.whale_bid_wall) <= 0.25 * atr:
                buy_zone_high = round(max(buy_zone_high, snapshot.whale_bid_wall + (0.20 if not is_btc else 15.0)), 2)

            order_type = "BUY_LIMIT"
            entry_price = round(min(anchor_price - (0.40 if not is_btc else 15.0), max(buy_zone_high, anchor_price - 0.18 * atr)), 2)

            min_sl_dist = max(1.20 * atr, 11.50 if not is_btc else 350.0)
            structural_bottom = min(sweep_extreme if has_sweep else buy_zone_low, float(smc["w3_low"]))
            sl_structural = round(structural_bottom - 0.22 * atr, 2)
            sl_price = round(min(sl_structural, entry_price - min_sl_dist), 2)
            if entry_price - sl_price > 2.45 * atr:
                sl_price = round(entry_price - 2.15 * atr, 2)

            risk_dist = abs(entry_price - sl_price)

            raw_tp1 = round(entry_price + max(1.25 * risk_dist, 1.35 * atr), 2)
            if anchor_price < snapshot.whale_ask_wall < raw_tp1 and (snapshot.whale_ask_wall - entry_price) >= 1.20 * risk_dist:
                tp1_price = round(snapshot.whale_ask_wall - (0.50 if not is_btc else 25.0), 2)
            else:
                tp1_price = raw_tp1

            tp2_price = round(entry_price + max(2.10 * risk_dist, 2.30 * atr), 2)
            tp3_price = round(entry_price + max(3.00 * risk_dist, 3.40 * atr), 2)

            fvg_str = (
                f"M15 Bullish FVG ({fvg_data['bullish_fvg'][0]:.2f}–{fvg_data['bullish_fvg'][1]:.2f})"
                if fvg_data["bullish_fvg"]
                else f"M15 Demand OB ({buy_zone_low:.2f}–{buy_zone_high:.2f})"
            )
            ob_wall_str = (
                f"Whale Bid Wall: {snapshot.whale_bid_wall:.2f} ({snapshot.whale_bid_vol:.0f} lot) | "
                f"Whale Ask Wall: {snapshot.whale_ask_wall:.2f} ({snapshot.whale_ask_vol:.0f} lot)"
            )
            zone_desc = (
                f"TradingView ({snapshot.price_source}) H1/M15 Bullish SMC Setup | "
                f"{session_liq_info} | Kirish: {entry_price:.2f} ({fvg_str}) | "
                f"Intraday VWAP: {vwap_price:.2f} (RVOL: {rvol}x)"
            )

        # R:R hisoblash
        sl_dist = max(abs(entry_price - sl_price), 1e-4)
        rr_tp1 = round(abs(tp1_price - entry_price) / sl_dist, 2)
        rr_tp2 = round(abs(tp2_price - entry_price) / sl_dist, 2)

        # =====================================================================
        # PRO v5.1 MUVOZANATLI 100-BALLIK CONFLUENCE SHKALASI
        # =====================================================================
        score = 0
        breakdown: list[str] = []

        # 1-Mezon: Liquidity Sweep yoki Strukturali Order Block Rejection (Max 25 ball)
        if has_sweep:
            score += 25
            breakdown.append(f"✅ 1. Liquidity Sweep & Rejection (+25/25): {session_liq_info}.")
        elif in_setup_zone:
            score += 20
            breakdown.append(f"✅ 1. M15 Strukturali Order Block Mitigation (+20/25): {session_liq_info}.")
        else:
            score += 10
            breakdown.append(f"⚠️ 1. Likvidlik zonasi (+10/25): {session_liq_info}.")

        # 2-Mezon: Order Block + FVG / Supply-Demand mosligi (Max 25 ball)
        has_fvg = (direction == "SELL" and fvg_data["bearish_fvg"] is not None) or (
            direction == "BUY" and fvg_data["bullish_fvg"] is not None
        )
        if in_setup_zone and has_fvg:
            score += 25
            breakdown.append(f"✅ 2. Order Block + FVG Imbalance (+25/25): {fvg_str} tasdiqlandi.")
        elif in_setup_zone or has_fvg:
            score += 22
            breakdown.append(f"✅ 2. M15 Institutsional Order Block (+22/25): {fvg_str} aktiv zonada.")
        else:
            score += 15
            breakdown.append(f"✅ 2. Dinamik Limit Zona (+15/25): {fvg_str}.")

        # 3-Mezon: Fundamental & Jonli Order-Flow Bias (Max 20 ball)
        macro_aligned = (direction == "SELL" and macro.bias == "BEARISH") or (
            direction == "BUY" and macro.bias == "BULLISH"
        )
        if macro_aligned and abs(macro.confidence_score) >= 0.45:
            score += 20
            breakdown.append(
                f"✅ 3. Macro & Order-Flow (+20/20): {macro.bias} ({macro.confidence_score:+.2f}) | DXY: {snapshot.dxy_trend}."
            )
        elif macro_aligned or (macro.bias == "NEUTRAL" and has_sweep):
            score += 16
            breakdown.append(
                f"✅ 3. Macro & Order-Flow (+16/20): {macro.bias} ({macro.confidence_score:+.2f}) | DXY: {snapshot.dxy_trend}."
            )
        else:
            score += 8
            breakdown.append(
                f"⚠️ 3. Macro Bias (+8/20): {macro.bias} ({macro.confidence_score:+.2f})."
            )

        # 4-Mezon: Intraday VWAP & Institutional Volume RVOL (Max 15 ball)
        vwap_aligned = (direction == "SELL" and anchor_price <= vwap_price + 0.30 * atr) or (
            direction == "BUY" and anchor_price >= vwap_price - 0.30 * atr
        )
        if rvol >= 1.15 or (vwap_aligned and rvol >= 0.95):
            score += 15
            breakdown.append(
                f"✅ 4. Intraday VWAP & Volume (+15/15): VWAP {vwap_price:.2f} | RVOL {rvol}x institutsional oqim."
            )
        else:
            score += 11
            breakdown.append(
                f"✅ 4. Intraday VWAP (+11/15): VWAP {vwap_price:.2f} | RVOL {rvol}x."
            )

        # 5-Mezon: Strukturali SL Xavfsizligi & R:R Nisbati (Max 15 ball)
        if rr_tp1 >= 1.20 and rr_tp2 >= 1.80:
            score += 15
            breakdown.append(
                f"✅ 5. Structural SL & R:R (+15/15): SL {sl_dist:.2f} pt ({round(sl_dist/atr, 2)}x ATR) | TP1 1:{rr_tp1} | TP2 1:{rr_tp2}."
            )
        else:
            score += 10
            breakdown.append(
                f"⚠️ 5. R:R Nisbati (+10/15): TP1 1:{rr_tp1} | TP2 1:{rr_tp2}."
            )

        # A+ Ideal Setup: Score >= 85 VA (Liquidity Sweep YOKI Order Block Setup Zone)
        is_ideal = (score >= 85) and (has_sweep or in_setup_zone)

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
            ob_fvg_aligned=in_setup_zone or has_fvg,
            choch_confirmed=True,
            volume_spike=(rvol >= 1.10),
            confluence_breakdown=breakdown,
            breakeven_trigger_price=tp1_price,
            trailing_rule=trailing_text,
            orderbook_wall_info=ob_wall_str,
            session_liquidity_info=session_liq_info,
        )