"""
PRO v5.1 INSTITUTIONAL DIAMOND SNIPER — Konfiguratsiya va API kalitlari (config.py)
PRO v5.1 MUHIM TUZATISHLAR (SIGNAL BLOKLANISHI YECHIMI):
1. KILLZONES_TASHKENT: 16:00–17:00 UZT dagi "ko'r zona" (16:30 UZT AQSh yangiliklari va NY pre-market)
   hamda 10:00–12:00 UZT Frankfurt/London ochilishi to'liq qamrab olindi:
   - LONDON: 10:00 – 16:00 UZT
   - NEW_YORK: 16:00 – 23:00 UZT
   (Faqat tungi past likvidli Osiyo/Whipsaw soatlari 23:00–10:00 UZT filtrlanadi).
2. MAX_DAILY_TRADES_PER_SYMBOL = 4 (faqat haqiqiy ishga tushgan savdolar hisoblanadi;
   bekor qilingan PENDING limit orderlar kunlik limitni yeb qo'ymaydi!).
3. POST_TRADE_COOLDOWN_MINUTES = 30 (haqiqiy yopilgan savdodan keyin 30 daqiqa tanaffus).
4. CONFLUENCE_THRESHOLD = 85 (100 ballik muvozanatli SMC + Order-Flow shkalasi bo'yicha).
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Bozor aktivlari (Oltin XAUUSD, Bitcoin BTCUSD, Dollar indeksi DXY va AQSh 10Y Obligatsiya daromadliligi)
SYMBOLS = {
    "GOLD": "XAUUSD",
    "BTC": "BTCUSD",
    "DXY": "USDX",
    "US10Y": "US10Y",
}

# TradingView rasmiy tickerlari (1:1 narx sinxronizatsiyasi uchun)
TV_TICKERS = {
    "XAUUSD": "FOREXCOM:XAUUSD",
    "BTCUSD": "BINANCE:BTCUSDT",
    "USDX": "TVC:DXY",
    "US10Y": "TVC:US10Y",
}

# Taymfreym sozlamalari (Multi-Timeframe)
HTF = "H1"      # Katta taymfreym: Bozor strukturasi va asosiy likvidlik darajalari
LTF = "M15"     # Kichik taymfreym: Liquidity Sweep, Order Block, FVG va aniq kirish nuqtasi
M5_TF = "M5"    # Mikro taymfreym: CHoCH va Volume Spike tasdig'i

# Risk Boshqaruvi sozlamalari (PRO v5.1 Institutional Standard)
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "10000.0"))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))  # Har bir savdoda depozitning 1% ($100) qismi
MIN_RR_RATIO = 1.8                # Asosiy TP2 uchun minimal Risk:Reward nisbati (1:1.8+)
MIN_TP1_RR_RATIO = 1.20           # TP1 (50% fiksatsiya va B/U) uchun minimal R:R nisbati (1:1.20+)
MAX_SL_ATR_MULTIPLIER = 2.8       # SL masofasi ATR ning 2.8 barobaridan oshmasligi shart
MIN_SL_ATR_MULTIPLIER = 1.15      # SL masofasi ATR ning 1.15 barobaridan kam bo'lmasligi shart (Stop-Hunt himoyasi)
CONFLUENCE_THRESHOLD = 85         # Savdoga ruxsat berish uchun minimal Confluence balli (85/100)

# PRO v5.1 Kunlik Limit va Cooldown (Sifat va Faollik muvozanati)
MAX_DAILY_TRADES_PER_SYMBOL = 4   # Har bir aktiv bo'yicha kuniga maksimal 4 ta haqiqiy savdo (London + NY)
POST_TRADE_COOLDOWN_MINUTES = 30  # Savdo yopilgach (SL/TP), keyingi savdogacha 30 daqiqa kutish
PENDING_EXPIRY_MINUTES = 120      # Limit order 120 daqiqa (8 ta M15 shamcha) ichida ishga tushmasa yangilanadi

# AI (LLM) sozlamalari (Fundamental tahlil uchun)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# FXStreet News manbalari
FXSTREET_NEWS_URL = "https://www.fxstreet.com/news"
FXSTREET_RSS_URL = "https://www.fxstreet.com/rss/news"
FXSTREET_GOOGLE_RSS_URL = (
    "https://news.google.com/rss/search?q=site:fxstreet.com/news+"
    "(XAUUSD+OR+Gold+OR+Fed+OR+FOMC+OR+CPI+OR+Powell+OR+Warsh+OR+Yields+OR+Iran)&hl=en-US&gl=US&ceid=US:en"
)

# Telegram Bot sozlamalari
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "8796032287:AAHYutAk7yI1WINFHJpz5983bxQZca-rbhQ",
)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6637983416")

# Toshkent vaqti (UTC+5) bo'yicha institutsional Killzone soatlari
# PRO v5.1: 10:00 dan 23:00 gacha uzluksiz London + NY Overlap + NY sessiyasi (16:00-17:00 uzilishsiz!)
TIMEZONE_OFFSET_HOURS = 5
KILLZONES_TASHKENT = {
    "LONDON": (10, 16),    # 10:00 - 16:00 UZT (Frankfurt + London Open & Mid-Session)
    "NEW_YORK": (16, 23),  # 16:00 - 23:00 UZT (London-NY Overlap, 16:30 US Macro Release & NY Session)
}
ENFORCE_KILLZONE = os.getenv("ENFORCE_KILLZONE", "true").lower() == "true"

# MetaTrader 5 (agar Windows VPS da MT5 terminal ulangan bo'lsa)
MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PATH = os.getenv("MT5_PATH", "")