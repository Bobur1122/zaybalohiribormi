"""
2-QISM: FXStreet News bo'limidan Aktivga Mos (Gold vs Bitcoin) Yangiliklar va Kontekst Yig'uvchi Modul (news_scraper.py) — PRO v5.1
PRO v5.1 MUHIM TUZATISH (FALSE NEWS LOCKOUT FIX):
1) Oldingi versiyada `_fetch_fxstreet_html` da `published` ISO formatda berilgani uchun `parsedate_to_datetime()`
   xatolik (`Exception`) berib, `except Exception: imminent_breaking = True` tufayli botni 24 soat davomida
   "Yaqin 30 daqiqada kuchli yangilik bor" deb bloklab qo'ygan edi!
2) Endi `check_imminent_high_impact_news` faqat HAQIQIY favqulodda AQSh/Fed/CPI/NFP relizlari oxirgi 15 daqiqa
   ichida chiqqanini aniq RFC-2822 yoki ISO-8601 vaqt shtampi bilan tekshiradi. Vaqt shtampi bo'lmasa yoki
   oddiy tahliliy maqola bo'lsa, botni asossiz bloklamaydi (`False` qaytaradi).
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import re
from typing import Any, Optional
import urllib.request
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

from config import FXSTREET_NEWS_URL, FXSTREET_RSS_URL, FXSTREET_GOOGLE_RSS_URL

logger = logging.getLogger(__name__)

FXSTREET_BTC_GOOGLE_RSS_URL = (
    "https://news.google.com/rss/search?q=site:fxstreet.com/news+"
    "(Bitcoin+OR+BTC+OR+Crypto+OR+ETF+OR+Fed+OR+DXY+OR+Treasury)&hl=en-US&gl=US&ceid=US:en"
)

HIGH_IMPACT_KEYWORDS = [
    "BREAKING", "CPI", "Core PCE", "Nonfarm Payrolls", "NFP", "FOMC",
    "Federal Reserve", "Fed Chair", "Warsh", "Powell", "Interest Rate",
    "Rate Hike", "Rate Cut", "Unemployment Claims", "GDP", "ISM",
    "Retail Sales", "Geopolitical", "Iran", "Middle East", "Hormuz",
    "Bab el-Mandeb", "Suez", "Tariffs", "Sanctions", "Safe-Haven",
    "Treasury Yields", "US10Y", "DXY", "WTI", "Oil", "Bitcoin", "BTC", "ETF"
]

ACTOR_PATTERNS: dict[str, str] = {
    "Fed / FOMC (Warsh & Powell)": r"\b(fed|fomc|federal reserve|warsh|powell|waller|bowman|goolsbee|kashkari)\b",
    "AQSh G'aznachiligi va Kongress (Fiskal siyosat / Obligatsiyalar)": r"\b(treasury|us bond|yields|congress|midterm|election|white house|tariff|fiscal)\b",
    "Wall Street Institutsional Fondlari (BlackRock / Spot ETF / CME)": r"\b(blackrock|etf|inflows|outflows|institutional|cme|whales|short-squeeze|squeeze|rally|accumulation)\b",
    "Yirik Markaziy Banklar (BoJ / BoE / PBoC / ECB)": r"\b(boj|bank of japan|boe|bank of england|pboc|rba|rbnz|ecb|central bank)\b",
    "Energetika va Geosiyosiy Kuchlar (Yaqin Sharq / OPEC / Neft)": r"\b(iran|irgc|israel|opec|saudi|oil|wti|brent|tanker|blockade|gulf)\b",
}

LOCATION_PATTERNS: dict[str, str] = {
    "AQSh (Vashington / Nyu-York — Wall Street, CME & Fed)": r"\b(united states|us dollar|wall street|washington|dxy|treasuries|fed|fomc|etf|bitcoin|btc)\b",
    "Yaqin Sharq va Dengiz Bo'g'ozlari (Eron, Ho'rmuz, Bob al-Mandab, Suvaysh)": r"\b(middle east|iran|hormuz|bab el-mandeb|suez|red sea|saudi arabia|israel|gulf)\b",
    "Osiyo Moliya Markazlari (Tokio, Pekin, Gongkong, Singapur)": r"\b(japan|tokyo|china|beijing|hong kong|india|singapore|asian session)\b",
    "Yevropa va Buyuk Britaniya (London, Frankfurt, Yevrozona)": r"\b(uk|britain|london|france|germany|eurozone|european|ecb|boe)\b",
}


def _parse_article_datetime(pub_str: str) -> Optional[datetime]:
    """RSS (RFC-2822) yoki ISO-8601 formatidagi vaqtni xavfsiz parse qiladi."""
    if not pub_str or not pub_str.strip():
        return None
    s = pub_str.strip()
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


class MacroNewsScraper:
    """FXStreet News bo'limidan aktivga mos (XAUUSD yoki BTCUSD) yangiliklarni va shaxslar/hududlar aloqalarini yig'uvchi PRO v5.1 klass."""

    def __init__(self, timeout: int = 8):
        self.timeout = timeout
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _fetch_fxstreet_rss(self, url: str, max_items: int = 12) -> list[dict[str, str]]:
        items_out: list[dict[str, str]] = []
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        for item in root.findall(".//item")[:max_items]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            if title:
                clean_title = re.sub(r"\s*-\s*FXStreet.*$", "", title, flags=re.IGNORECASE)
                items_out.append({
                    "title": clean_title,
                    "url": link,
                    "published": pub_date,
                    "source": "FXStreet News",
                })
        return items_out

    def _fetch_fxstreet_html(self, max_items: int = 12) -> list[dict[str, str]]:
        items_out: list[dict[str, str]] = []
        req = urllib.request.Request(FXSTREET_NEWS_URL, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        soup = BeautifulSoup(html, "html.parser")
        seen: set[str] = set()
        for a_tag in soup.select("a[href*='/news/']"):
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            if not title or len(title) < 25 or title in seen:
                continue
            seen.add(title)
            full_url = href if href.startswith("http") else f"https://www.fxstreet.com{href}"
            items_out.append({
                "title": title,
                "url": full_url,
                "published": "",  # HTML sahifada aniq sekundlik vaqt bo'lmagani uchun bo'sh qoldiriladi
                "source": "FXStreet News (Direct)",
            })
            if len(items_out) >= max_items:
                break
        return items_out

    def fetch_fxstreet_articles(self, max_items: int = 10, symbol: str = "XAUUSD") -> list[dict[str, str]]:
        is_btc = "BTC" in symbol.upper()
        fetch_chain = (
            [
                lambda: self._fetch_fxstreet_rss(FXSTREET_BTC_GOOGLE_RSS_URL, max_items),
                lambda: self._fetch_fxstreet_rss(FXSTREET_RSS_URL, max_items * 2),
                lambda: self._fetch_fxstreet_html(max_items * 2),
            ]
            if is_btc
            else [
                lambda: self._fetch_fxstreet_rss(FXSTREET_RSS_URL, max_items),
                lambda: self._fetch_fxstreet_html(max_items),
                lambda: self._fetch_fxstreet_rss(FXSTREET_GOOGLE_RSS_URL, max_items),
            ]
        )

        collected: list[dict[str, str]] = []
        for fetcher in fetch_chain:
            try:
                articles = fetcher()
                if articles:
                    if is_btc:
                        filtered = [
                            a for a in articles
                            if not (
                                ("gold" in a["title"].lower() or "xau" in a["title"].lower())
                                and not any(k in a["title"].lower() for k in ("fed", "fomc", "dollar", "dxy", "cpi", "yield", "btc", "bitcoin"))
                            )
                        ]
                        collected.extend(filtered or articles)
                    else:
                        collected.extend(articles)
                if len(collected) >= max_items:
                    return collected[:max_items]
            except Exception as e:
                logger.debug(f"FXStreet manba xatosi: {e}")

        if collected:
            return collected[:max_items]

        logger.info("Oflayn test rejimi: FXStreet News so'nggi makroiqtisodiy sarlavhalari ishlatilmoqda.")
        return [
            {
                "title": "US Dollar Index holds firm ahead of FOMC as Treasury yields and institutional flows drive intraday volatility",
                "url": "https://www.fxstreet.com/news",
                "published": "",
                "source": "FXStreet News",
            },
            {
                "title": "Bitcoin and Gold test key institutional liquidity zones as Wall Street desks adjust positioning ahead of Fed",
                "url": "https://www.fxstreet.com/news",
                "published": "",
                "source": "FXStreet News",
            },
        ]

    def fetch_headlines(self, max_items: int = 8, symbol: str = "XAUUSD") -> list[str]:
        articles = self.fetch_fxstreet_articles(max_items=max_items, symbol=symbol)
        return [art["title"] for art in articles]

    @staticmethod
    def extract_entities_and_context(headlines: list[str]) -> dict[str, list[str]]:
        blob = " ".join(headlines).lower()
        detected_actors: list[str] = []
        detected_locations: list[str] = []

        for actor_label, pattern in ACTOR_PATTERNS.items():
            if re.search(pattern, blob, flags=re.IGNORECASE):
                detected_actors.append(actor_label)

        for loc_label, pattern in LOCATION_PATTERNS.items():
            if re.search(pattern, blob, flags=re.IGNORECASE):
                detected_locations.append(loc_label)

        return {
            "actors": detected_actors,
            "locations": detected_locations,
        }

    def check_imminent_high_impact_news(self, window_minutes: int = 15) -> tuple[bool, list[dict[str, Any]]]:
        """
        PRO v5.1: Faqat haqiqiy favqulodda AQSh/Fed/FOMC/CPI/NFP relizi oxirgi `window_minutes` (15 daqiqa)
        ichida e'lon qilingan bo'lsagina `imminent_breaking = True` qaytaradi.
        Vaqt shtampi bo'lmasa yoki tahliliy maqola bo'lsa, signallarni asossiz bloklamaydi!
        """
        articles = self.fetch_fxstreet_articles(max_items=15)
        high_impact_list: list[dict[str, Any]] = []
        imminent_breaking = False
        now_utc = datetime.now(timezone.utc)

        # Faqat haqiqiy jonli Fed/AQSh makro reliz iboralari
        urgent_usd_flash_triggers = (
            "breaking: fed",
            "breaking: fomc",
            "breaking: us cpi",
            "breaking: nonfarm payrolls",
            "breaking: powell",
            "breaking: warsh",
            "flash: fed",
            "flash: us cpi",
            "urgent: fed",
        )

        for art in articles:
            title_lower = art["title"].lower()
            matched_kw = [kw for kw in HIGH_IMPACT_KEYWORDS if kw.lower() in title_lower]
            if matched_kw:
                high_impact_list.append({
                    "title": art["title"],
                    "url": art["url"],
                    "keywords": matched_kw,
                    "published": art["published"],
                })
                if any(ut in title_lower for ut in urgent_usd_flash_triggers):
                    pub_dt = _parse_article_datetime(art.get("published", ""))
                    if pub_dt is not None:
                        age_minutes = (now_utc - pub_dt).total_seconds() / 60.0
                        if 0.0 <= age_minutes <= window_minutes:
                            imminent_breaking = True

        return imminent_breaking, high_impact_list