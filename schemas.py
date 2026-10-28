"""
1-QISM: Barcha modullarni bog'lovchi yagona ma'lumot qoliplari (Data Contracts - schemas.py) — PRO v5.1
PRO v5.1 MUHIM TUZATISH (SCHEMA MISMATCH FIX):
- Oldingi versiyada `schemas.py` (v4.5) va `risk_manager.py` / `telegram_bot.py` / `technical_agent.py` (v5.0)
  o'rtasida maydon nomlari (`tp2_price` vs `tp_price`, `FinalTradeSignal` argumentlari) mos kelmay qolgani
  sababli `AttributeError` / `TypeError` yuzaga kelgan edi!
- Endi `TechnicalSetup` va `FinalTradeSignal` barcha PRO v5.1 va v4.x/v5.0 atributlarini to'liq qo'llab-quvvatlaydi!
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional
import pandas as pd


BiasType = Literal["BULLISH", "BEARISH", "NEUTRAL"]
DirectionType = Literal["BUY", "SELL", "NONE"]
OrderExecutionType = Literal["BUY_LIMIT", "SELL_LIMIT", "MARKET_BUY", "MARKET_SELL", "WAIT_FOR_ZONE", "NONE"]


@dataclass
class MarketSnapshot:
    """Bozorning joriy texnik, korrelyatsion, Sessiya Likvidlik va Order-Book holati."""
    symbol: str
    htf_df: pd.DataFrame
    ltf_df: pd.DataFrame
    dxy_trend: str  # "BULLISH" | "BEARISH" | "NEUTRAL"
    atr: float
    current_price: float
    price_source: str = "TradingView (FOREXCOM:XAUUSD)"
    us10y_trend: str = "NEUTRAL"  # "BULLISH" | "BEARISH" | "NEUTRAL"
    whale_ask_wall: float = 0.0
    whale_ask_vol: float = 0.0
    whale_bid_wall: float = 0.0
    whale_bid_vol: float = 0.0
    ob_imbalance_ratio: float = 1.0
    pdh: float = 0.0          # Previous Day / 24h High
    pdl: float = 0.0          # Previous Day / 24h Low
    asian_high: float = 0.0   # Osiyo sessiyasi (03:00-10:00 UZT) High
    asian_low: float = 0.0    # Osiyo sessiyasi (03:00-10:00 UZT) Low
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def validate(self) -> bool:
        required_cols = {"time", "open", "high", "low", "close", "volume"}
        htf_ok = required_cols.issubset(set(self.htf_df.columns)) and not self.htf_df.empty
        ltf_ok = required_cols.issubset(set(self.ltf_df.columns)) and not self.ltf_df.empty
        return htf_ok and ltf_ok and self.current_price > 0 and self.atr > 0


@dataclass
class FundamentalSignal:
    """
    Fundamental Agent (DXY + US10Y + Jonli Order-Flow + FXStreet Voqealar Zanjiri tahlilchisi) natijasi.
    """
    symbol: str
    bias: str  # "BULLISH" | "BEARISH" | "NEUTRAL"
    confidence_score: float  # -1.0 (Kuchli Bearish) dan +1.0 (Kuchli Bullish) gacha
    macro_reason: str
    high_impact_news_imminent: bool
    key_drivers: list[str] = field(default_factory=list)
    causal_chain: str = ""
    key_actors_analysis: list[str] = field(default_factory=list)
    geopolitical_hotspots: list[str] = field(default_factory=list)
    predicted_next_moves: list[str] = field(default_factory=list)


@dataclass
class TechnicalSetup:
    """
    PRO v5.1 Texnik Agent (Liquidity Sweep + PDH/PDL + Asian Range + OB/FVG + VWAP + Whale Walls) natijasi.
    """
    symbol: str
    is_ideal_setup: bool = False
    direction: str = "NONE"  # "BUY" | "SELL" | "NONE"
    order_type: str = "NONE"  # "BUY_LIMIT" | "SELL_LIMIT" | "MARKET_BUY" | "MARKET_SELL" | "NONE"
    entry_price: float = 0.0
    sl_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    tp3_price: float = 0.0
    rr_tp1: float = 1.25
    rr_tp2: float = 2.10
    confluence_score: int = 0
    Zone_Description: str = ""
    liquidity_swept: bool = False
    ob_fvg_aligned: bool = False
    choch_confirmed: bool = False
    volume_spike: bool = False
    confluence_breakdown: list[str] = field(default_factory=list)
    breakeven_trigger_price: float = 0.0
    trailing_rule: str = ""
    orderbook_wall_info: str = ""
    session_liquidity_info: str = ""

    # Orqaga moslik (v4.5/v5.0 atribut nomlari bilan 100% moslik)
    @property
    def is_valid(self) -> bool:
        return self.is_ideal_setup

    @property
    def tp_price(self) -> float:
        return self.tp2_price

    @property
    def rr_ratio(self) -> float:
        return self.rr_tp2

    @property
    def liquidity_sweep_detected(self) -> bool:
        return self.liquidity_swept

    @property
    def sweep_note(self) -> str:
        return self.session_liquidity_info

    @property
    def confluence_Checklist(self) -> list[str]:
        return self.confluence_breakdown


@dataclass
class FinalTradeSignal:
    """PRO v5.1 Risk Manager tekshiruvidan o'tgan yakuniy savdo signali."""
    is_approved: bool
    rejection_reason: Optional[str]
    symbol: str
    order_type: str
    direction: str
    current_price: float
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float = 0.0
    lot_size: float = 0.01
    risk_usd: float = 100.0
    confluence_score: int = 0
    killzone_active: str = "LONDON/NY"
    macro_summary: str = ""
    technical_summary: str = ""
    dxy_correlation: str = ""
    causal_chain: str = ""
    key_actors_analysis: list[str] = field(default_factory=list)
    geopolitical_hotspots: list[str] = field(default_factory=list)
    predicted_next_moves: list[str] = field(default_factory=list)
    confluence_breakdown: list[str] = field(default_factory=list)
    breakeven_trigger_price: float = 0.0
    trailing_rule: str = ""
    orderbook_wall_info: str = ""
    session_liquidity_info: str = ""
    signal_id: int = 1

    @property
    def signal_key(self) -> str:
        """Takroriy spam yubormaslik uchun unikal signal kaliti."""
        return f"{self.symbol}_{self.order_type}_{round(self.entry_price, 0)}"