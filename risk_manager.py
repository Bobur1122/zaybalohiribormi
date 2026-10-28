"""
3-QISM: Risk Manager va Yakuniy Qaror Qabul Qiluvchi Modul (risk_manager.py) — PRO v5.1
PRO v5.1 INSTITUTIONAL DIAMOND SNIPER:
- Confluence >= 85/100 bo'lgan A+ Setuplarni tasdiqlaydi (Liquidity Sweep Reversal VA Order Block + FVG Pullback).
- Kunlik savdo limiti: har bir aktiv uchun maksimal 4 ta haqiqiy ishga tushgan savdo (`MAX_DAILY_TRADES_PER_SYMBOL = 4`).
- Bekor qilingan (`CANCELLED`) pending orderlar kunlik limitni kamaytirmaydi.
- Savdo yopilgach 30 daqiqalik Cooldown (`POST_TRADE_COOLDOWN_MINUTES = 30`).
- Faqat London + Nyu-York Killzone (`10:00–23:00 UZT`) vaqtida savdo ochadi.
"""
from datetime import datetime, timezone, timedelta
import logging
from typing import Optional

from config import (
    ACCOUNT_BALANCE,
    RISK_PER_TRADE_PCT,
    MIN_RR_RATIO,
    MIN_TP1_RR_RATIO,
    MAX_SL_ATR_MULTIPLIER,
    CONFLUENCE_THRESHOLD,
    TIMEZONE_OFFSET_HOURS,
    KILLZONES_TASHKENT,
    ENFORCE_KILLZONE,
    MAX_DAILY_TRADES_PER_SYMBOL,
    POST_TRADE_COOLDOWN_MINUTES,
)
from schemas import MarketSnapshot, FundamentalSignal, TechnicalSetup, FinalTradeSignal

logger = logging.getLogger(__name__)


class RiskManager:
    """
    PRO v5.1 Institutsional Risk va Sifat Nazoratchisi.
    """

    def __init__(
        self,
        account_balance: float = ACCOUNT_BALANCE,
        risk_pct: float = RISK_PER_TRADE_PCT,
        enforce_killzone: bool = ENFORCE_KILLZONE,
    ):
        self.account_balance = account_balance
        self.risk_pct = risk_pct
        self.enforce_killzone = enforce_killzone

    @staticmethod
    def get_current_killzone() -> tuple[bool, str]:
        tz_tashkent = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))
        now_uz = datetime.now(tz_tashkent)
        hour = now_uz.hour

        for kz_name, (start_h, end_h) in KILLZONES_TASHKENT.items():
            if start_h <= hour < end_h:
                return True, f"{kz_name} Killzone ({now_uz.strftime('%H:%M')} UZT)"
        return False, f"Off-Session ({now_uz.strftime('%H:%M')} UZT — Tungi / Osiyo sessiyasida savdo qilinmaydi)"

    def calculate_lot_size(self, symbol: str, entry_price: float, sl_price: float) -> tuple[float, float]:
        risk_usd = round(self.account_balance * (self.risk_pct / 100.0), 2)
        sl_distance = abs(entry_price - sl_price)
        if sl_distance <= 1e-6:
            return 0.01, risk_usd

        if "XAU" in symbol.upper():
            raw_lot = risk_usd / (sl_distance * 100.0)
        elif "BTC" in symbol.upper():
            raw_lot = risk_usd / sl_distance
        else:
            raw_lot = risk_usd / (sl_distance * 100.0)

        lot_size = max(0.01, round(raw_lot, 2))
        return lot_size, risk_usd

    def evaluate_trade(
        self,
        snapshot: MarketSnapshot,
        macro: FundamentalSignal,
        tech: TechnicalSetup,
        today_trades_count: int = 0,
        last_closed_time: Optional[datetime] = None,
    ) -> FinalTradeSignal:
        in_kz, kz_label = self.get_current_killzone()
        lot_size, risk_usd = self.calculate_lot_size(snapshot.symbol, tech.entry_price, tech.sl_price)
        sl_distance = abs(tech.entry_price - tech.sl_price)

        # 1. Killzone tekshiruvi (10:00–23:00 UZT London & NY)
        if self.enforce_killzone and not in_kz:
            return self._reject(snapshot, macro, tech, kz_label, f"Killzone tashqarisida: {kz_label}.")

        # 2. Kunlik savdo limiti (faqat haqiqiy ishga tushgan savdolar bo'yicha)
        if today_trades_count >= MAX_DAILY_TRADES_PER_SYMBOL:
            return self._reject(
                snapshot, macro, tech, kz_label,
                f"Kunlik savdo limiti to'ldi: bugun {snapshot.symbol} bo'yicha {today_trades_count}/{MAX_DAILY_TRADES_PER_SYMBOL} ta savdo bajarildi."
            )

        # 3. Cooldown tekshiruvi (Savdo yopilgach 30 daqiqa)
        if last_closed_time is not None:
            now_utc = datetime.now(timezone.utc)
            elapsed_min = (now_utc - last_closed_time).total_seconds() / 60.0
            if elapsed_min < POST_TRADE_COOLDOWN_MINUTES:
                rem = int(POST_TRADE_COOLDOWN_MINUTES - elapsed_min)
                return self._reject(
                    snapshot, macro, tech, kz_label,
                    f"Cooldown rejimi: oxirgi savdodan keyin yana {rem} daqiqa kutiladi."
                )

        # 4. Favqulodda jonli yangilik filtri (faqat 15 daqiqalik haqiqiy Fed/CPI/NFP flash vaqtida)
        if macro.high_impact_news_imminent:
            return self._reject(
                snapshot, macro, tech, kz_label,
                "Ayni damda favqulodda (Flash) Fed/CPI/NFP relizi chiqmoqda — spred kengayishidan himoya."
            )

        # 5. Texnik setup va Confluence >= 85/100 tekshiruvi
        if not tech.is_ideal_setup or tech.direction == "NONE" or tech.confluence_score < CONFLUENCE_THRESHOLD:
            return self._reject(
                snapshot, macro, tech, kz_label,
                f"Confluence ball yetarli emas ({tech.confluence_score}/{CONFLUENCE_THRESHOLD}) yoki A+ zona shakllanmadi."
            )

        # 6. SL masofasi ATR ga nisbatan xavfsiz chegarada ekanligi
        if snapshot.atr > 0 and sl_distance > MAX_SL_ATR_MULTIPLIER * snapshot.atr:
            return self._reject(
                snapshot, macro, tech, kz_label,
                f"SL masofasi ({sl_distance:.2f}) haddan tashqari katta (> {MAX_SL_ATR_MULTIPLIER}x ATR)."
            )

        # 7. Risk:Reward tekshiruvi (TP1 >= 1.20 va TP2 >= 1.80)
        if tech.rr_tp1 < MIN_TP1_RR_RATIO or tech.rr_tp2 < MIN_RR_RATIO:
            return self._reject(
                snapshot, macro, tech, kz_label,
                f"R:R nisbati past: TP1={tech.rr_tp1} (min {MIN_TP1_RR_RATIO}), TP2={tech.rr_tp2} (min {MIN_RR_RATIO})."
            )

        return FinalTradeSignal(
            is_approved=True,
            rejection_reason=None,
            symbol=snapshot.symbol,
            order_type=tech.order_type,
            direction=tech.direction,
            current_price=round(snapshot.current_price, 2),
            entry_price=tech.entry_price,
            sl_price=tech.sl_price,
            tp1_price=tech.tp1_price,
            tp2_price=tech.tp2_price,
            tp3_price=tech.tp3_price,
            lot_size=lot_size,
            risk_usd=risk_usd,
            confluence_score=tech.confluence_score,
            killzone_active=kz_label,
            macro_summary=macro.macro_reason,
            technical_summary=tech.Zone_Description,
            dxy_correlation=f"DXY H1: {snapshot.dxy_trend} | US10Y H1: {snapshot.us10y_trend}",
            causal_chain=macro.causal_chain,
            key_actors_analysis=macro.key_actors_analysis,
            geopolitical_hotspots=macro.geopolitical_hotspots,
            predicted_next_moves=macro.predicted_next_moves,
            confluence_breakdown=tech.confluence_breakdown,
            breakeven_trigger_price=tech.breakeven_trigger_price,
            trailing_rule=tech.trailing_rule,
            orderbook_wall_info=tech.orderbook_wall_info,
            session_liquidity_info=tech.session_liquidity_info,
        )

    def _reject(
        self,
        snapshot: MarketSnapshot,
        macro: FundamentalSignal,
        tech: TechnicalSetup,
        kz_label: str,
        reason: str,
    ) -> FinalTradeSignal:
        logger.info(f"[{snapshot.symbol}] Signal filtrlandi: {reason}")
        return FinalTradeSignal(
            is_approved=False,
            rejection_reason=reason,
            symbol=snapshot.symbol,
            order_type="NONE",
            direction="NONE",
            current_price=round(snapshot.current_price, 2),
            entry_price=tech.entry_price,
            sl_price=tech.sl_price,
            tp1_price=tech.tp1_price,
            tp2_price=tech.tp2_price,
            tp3_price=tech.tp3_price,
            lot_size=0.0,
            risk_usd=0.0,
            confluence_score=tech.confluence_score,
            killzone_active=kz_label,
            macro_summary=macro.macro_reason,
            technical_summary=tech.Zone_Description,
            dxy_correlation=f"DXY H1: {snapshot.dxy_trend} | US10Y H1: {snapshot.us10y_trend}",
            causal_chain=macro.causal_chain,
            key_actors_analysis=macro.key_actors_analysis,
            geopolitical_hotspots=macro.geopolitical_hotspots,
            predicted_next_moves=macro.predicted_next_moves,
            confluence_breakdown=tech.confluence_breakdown,
            breakeven_trigger_price=tech.breakeven_trigger_price,
            trailing_rule=tech.trailing_rule,
            orderbook_wall_info=tech.orderbook_wall_info,
            session_liquidity_info=tech.session_liquidity_info,
        )