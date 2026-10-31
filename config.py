"""
PRO v6.0 INSTITUTIONAL REAL-MARKET SNIPER — Konfiguratsiya va API kalitlari (config.py)
PRO v6.0 TUBDAN YANGILANISHLAR:
1. Render Web Service (PORT binding + 24/7 Loop): Render'da "No open ports detected / Application exited early"
   xatosi bartaraf etildi.
2. Shanba-Yakshanba va Juma kechki Oltin (XAUUSD) Forex bozori yopiq vaqtlarini aniq filtrlash:
   - XAUUSD, USDX, US10Y: Shanba va Yakshanba kunlari 100% yopiq (Juma 23:00 UZT dan Dushanba 04:00 UZT gacha).
   - BTCUSD: 24/7 ochiq, ammo dam olish kunlari faqat yuqori hajmli haqiqiy struktura buzilgandagina signal beradi.
3. Soxta (fake) sandbox/shablon ma'lumotlar olib tashlandi: jonli OHLCV olinmasa, yolg'on signal chiqarilmaydi.
4. Haqiqiy SMC (Swing High/Low Fractal BOS/CHoCH, FVG, Order Block) va sabab-oqibatli Makro tahlil.
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

# Risk Boshqaruvi sozlamalari (PRO v6.0 Institutional Standard)
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "10000.0"))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))  # Har bir savdoda depozitning 1% ($100) qismi
MIN_RR_RATIO = 1.00               # Asosiy TP2 uchun minimal Risk:Reward nisbati (1:1.00+, TP3=1.30R+)
MIN_TP1_RR_RATIO = 0.70           # TP1 (fiksatsiya va Smart B/U) uchun minimal R:R nisbati (1:0.70+)
MAX_SL_ATR_MULTIPLIER = 2.8       # SL masofasi ATR ning 2.8 barobaridan oshmasligi shart
MIN_SL_ATR_MULTIPLIER = 0.85      # SL masofasi ATR ning 0.85 barobaridan kam bo'lmasligi shart
CONFLUENCE_THRESHOLD = 85         # Savdoga ruxsat berish uchun minimal Confluence balli (85/100)

# PRO v6.8 Kunlik Limit va Cooldown (Sifat va Faollik muvozanati — 1 London + 1 NY savdo)
MAX_DAILY_TRADES_PER_SYMBOL = 2   # Har bir aktiv bo'yicha kuniga maksimal 2 ta haqiqiy A+ savdo
POST_TRADE_COOLDOWN_MINUTES = 120 # Savdo yopilgach (SL/TP), keyingi savdogacha 2 soat kutish
PENDING_EXPIRY_MINUTES = 120      # Limit order 120 daqiqa (8 ta M15 shamcha) ichida ishga tushmasa yangilanadi

# AI (LLM) sozlamalari (Fundamental tahlil uchun)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# FXStreet va Makro News manbalari
FXSTREET_NEWS_URL = "https://www.fxstreet.com/news"
FXSTREET_RSS_URL = "https://www.fxstreet.com/rss/news"
FXSTREET_GOOGLE_RSS_URL = (
    "https://news.google.com/rss/search?q=site:fxstreet.com/news+"
    "(XAUUSD+OR+Gold+OR+Fed+OR+FOMC+OR+CPI+OR+Powell+OR+NFP+OR+Yields+OR+Geopolitics)&hl=en-US&gl=US&ceid=US:en"
)

# Telegram Bot sozlamalari
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "8522051984:AAFUNietKZGCJ6kvoy2dgYVHOgCVT3Sh56w",
)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6637983416")

# Toshkent vaqti (UTC+5) bo'yicha institutsional Killzone soatlari
TIMEZONE_OFFSET_HOURS = 5
KILLZONES_TASHKENT = {
    "LONDON": (11, 16),    # 11:00 - 16:00 UZT (London Open & Mid-Session)
    "NEW_YORK": (16, 23),  # 16:00 - 23:00 UZT (London-NY Overlap, 16:30 US Macro Release & NY Session)
}
ENFORCE_KILLZONE = os.getenv("ENFORCE_KILLZONE", "true").lower() == "true"

# Render / Cloud Health-Check HTTP Port
PORT = int(os.getenv("PORT", "10000"))

# MetaTrader 5 (agar Windows VPS da MT5 terminal ulangan bo'lsa)
MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PATH = os.getenv("MT5_PATH", "")
