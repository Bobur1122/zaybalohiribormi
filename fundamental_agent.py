"""
2-QISM: Fundamental Agent — PRO v6.0 Haqiqiy Makroiqtisodiy Voqealar Zanjiri (Causal Engine)
va Jonli Kapital Oqimi Tahlilchisi (fundamental_agent.py)

PRO v6.0 TUBDAN YANGILANISHLAR:
1. Shablon (qotib qolgan `+0.68 / -0.68`) ballar va "1 FXStreet: «...» ➡️ 2 ..." kabi ma'nosiz qoliplar
   butunlay olib tashlandi!
2. Haqiqiy Makro Rejim Klasterlash Dvigateli (`_classify_macro_events`):
   Har bir yangilik sarlavhasini 6 ta institutsional makroiqtisodiy klasterga ajratib, uning Oltin (XAUUSD)
   va Bitcoin (BTCUSD) ga qanday fundamental ta'sir qilishini (Sabab -> Bozor Reaksiyasi -> Kapital Oqimi -> Narx Bosimi)
   haqiqiy moliya mantiqi asosida bog'laydi:
   - MONETARY_POLICY (Fed, FOMC, Powell, CPI, PCE, NFP, foiz stavkasi kutilmalari)
   - GEOPOLITICAL_RISK (Urush, Yaqin Sharq, tariflar, sanksiyalar -> Safe-Haven Oltin talabi vs Risk-Off)
   - YIELDS_AND_USD (US10Y g'aznachilik obligatsiyalari daromadliligi va DXY dollar indeksi teskari korrelyatsiyasi)
   - CRYPTO_INSTITUTIONAL (Spot BTC ETF inflow/outflow, BlackRock, whale accumulation, likvidatsiyalar)
   - CENTRAL_BANK_DEMAND (Markaziy banklar oltin xaridi, PBoC, suveren fondlar)
3. Makro va Texnik Order-Flow uyg'unligi:
   - Agar makroiqtisodiy drayverlar va DXY/US10Y bir tomonda, grafikdagi kapital oqimi (VWAP/EMA) boshqa tomonda bo'lsa,
     bot buni "Makro-Texnik Divergensiya (Ehtiyotkor rejim)" deb tan oladi va yolg'on yuqori ball bermaydi!
"""
from datetime import datetime, timezone, timedelta
import json
import logging
import re
from typing import Any, Optional

from config import GEMINI_API_KEY, GEMINI_MODEL, TIMEZONE_OFFSET_HOURS
from schemas import MarketSnapshot, FundamentalSignal
from news_scraper import MacroNewsScraper

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


class FundamentalAgent:
    """
    PRO v6.0 Haqiqiy Makroiqtisodiy Sabab-Oqibat (Causal Macro) va Institutsional Order-Flow Tahlilchisi.
    """

    def __init__(self, api_key: Optional[str] = None, model_name: str = GEMINI_MODEL):
        self.api_key = api_key or GEMINI_API_KEY
        self.model_name = model_name
        self.client = None
        if GENAI_AVAILABLE and self.api_key and self.api_key != "your_gemini_api_key":
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.warning(f"Gemini Client ishga tushmadi: {e}")

    @staticmethod
    def _analyze_live_institutional_flow(snapshot: MarketSnapshot) -> dict[str, Any]:
        """
        Grafikdagi jonli pul oqimini (Real-Time Institutional Order Flow) hisoblaydi:
        VWAP, M15/H1 EMA20/50 strukturasi, nisbiy hajm (RVOL) va impuls kuchi.
        """
        ltf = snapshot.ltf_df.iloc[:-1] if len(snapshot.ltf_df) > 40 else snapshot.ltf_df
        htf = snapshot.htf_df.iloc[:-1] if len(snapshot.htf_df) > 40 else snapshot.htf_df
        cp = float(snapshot.current_price)
        atr = max(float(snapshot.atr), 1.0)

        sub36 = ltf.tail(36)
        typical = (sub36["high"] + sub36["low"] + sub36["close"]) / 3.0
        vol = sub36["volume"].replace(0, 1.0)
        vwap = float((typical * vol).sum() / vol.sum())

        avg_vol = float(vol.iloc[:-4].mean()) if len(vol) > 8 else float(vol.mean())
        last4_vol = float(vol.tail(4).mean())
        rvol = round(last4_vol / max(avg_vol, 1.0), 2)

        m15_ema20 = float(ltf["close"].ewm(span=20, adjust=False).mean().iloc[-1])
        m15_ema50 = float(ltf["close"].ewm(span=50, adjust=False).mean().iloc[-1])
        h1_ema20 = float(htf["close"].ewm(span=20, adjust=False).mean().iloc[-1])
        h1_ema50 = float(htf["close"].ewm(span=50, adjust=False).mean().iloc[-1])

        # Impuls va trend kuchi (-1.0 dan +1.0 gacha)
        vwap_dist_atr = (cp - vwap) / atr
        flow_score = 0.0

        # 1. H1 asosiy institutsional trend (40% ulush)
        if h1_ema20 > h1_ema50 and cp > h1_ema20:
            flow_score += 0.35
        elif h1_ema20 < h1_ema50 and cp < h1_ema20:
            flow_score -= 0.35
        elif cp > h1_ema50:
            flow_score += 0.15
        elif cp < h1_ema50:
            flow_score -= 0.15

        # 2. M15 Intraday struktura (35% ulush)
        if m15_ema20 > m15_ema50 and cp >= m15_ema20:
            flow_score += 0.30
        elif m15_ema20 < m15_ema50 and cp <= m15_ema20:
            flow_score -= 0.30

        # 3. VWAP ustida/ostida joylashuv va hajm (25% ulush)
        if vwap_dist_atr >= 0.15:
            flow_score += min(0.30, 0.15 * vwap_dist_atr)
        elif vwap_dist_atr <= -0.15:
            flow_score -= min(0.30, 0.15 * abs(vwap_dist_atr))

        flow_score = round(max(-0.95, min(0.95, flow_score)), 2)
        flow_dir = "BULLISH" if flow_score >= 0.25 else ("BEARISH" if flow_score <= -0.25 else "NEUTRAL")

        return {
            "flow_dir": flow_dir,
            "flow_score": flow_score,
            "vwap": round(vwap, 2),
            "vwap_dist_atr": round(vwap_dist_atr, 2),
            "rvol": rvol,
            "m15_ema20": round(m15_ema20, 2),
            "m15_ema50": round(m15_ema50, 2),
            "h1_ema20": round(h1_ema20, 2),
            "h1_ema50": round(h1_ema50, 2),
        }

    @staticmethod
    def _analyze_causal_news_events(headlines: list[str], symbol: str) -> dict[str, Any]:
        """
        Yangiliklar sarlavhalarini shunchaki so'z sanash bilan emas, balki makroiqtisodiy
        voqea turi va uning aktivga (XAUUSD vs BTCUSD) ta'sir mexanizmi orqali tahlil qiladi.
        """
        is_btc = "BTC" in symbol.upper()
        events_found: list[dict[str, Any]] = []
        news_score = 0.0

        for h in headlines[:8]:
            hl = h.lower()

            # 1. Fed / Foiz stavkasi / Inflyatsiya (CPI, PCE, NFP)
            if any(k in hl for k in ("fed", "fomc", "powell", "cpi", "inflation", "pce", "nfp", "payrolls", "rate")):
                if any(w in hl for w in ("cut", "dovish", "cool", "miss", "slow", "soft", "lower", "pause", "ease")):
                    impact = 0.28
                    mechanism = (
                        "AQSh inflyatsiyasi/mehnat bozori yumshashi yoki Fed 'Dovish' (stavkani pasaytirish) ritorikasi "
                        "-> Dollar (DXY) va US10Y daromadliligi pasayishi -> "
                        + ("Global likvidlik oshib, BTCga institutsional Risk-On oqimi kirishi" if is_btc else "Oltinning muqobil xarajati kamayib, XAUUSD ga xarid talabi oshishi")
                    )
                    events_found.append({"headline": h, "impact": +impact, "mechanism": mechanism, "theme": "Fed & Monetar Siyosat (Dovish)"})
                    news_score += impact
                elif any(w in hl for w in ("hike", "hawkish", "hot", "beat", "strong", "higher for longer", "delay", "hold", "surge")):
                    impact = -0.28
                    mechanism = (
                        "Kuchli AQSh makro ko'rsatkichlari yoki Fed 'Hawkish' (yuqori foiz stavkasi) pozitsiyasi "
                        "-> US10Y obligatsiya daromadliligi va DXY mustahkamlanishi -> "
                        + ("Riskli aktivlardan kapital chiqishi (BTC sotuv bosimi)" if is_btc else "Foiz keltirmaydigan Oltin (XAUUSD) ga nisbatan institutsional sotuv bosimi")
                    )
                    events_found.append({"headline": h, "impact": impact, "mechanism": mechanism, "theme": "Fed & Monetar Siyosat (Hawkish)"})
                    news_score += impact

            # 2. Geosiyosiy xavf-xatar, Urush, Tariflar, Safe-Haven
            if any(k in hl for k in ("war", "iran", "israel", "middle east", "geopolit", "tariff", "sanction", "safe-haven", "safe haven", "tension", "attack")):
                if not is_btc:
                    impact = 0.30
                    mechanism = (
                        "Geosiyosiy keskinlik / savdo tariflari xavfi -> Investorlarning xavfli aktivlardan qochib "
                        "himoya aktivi (Safe-Haven) sifatida XAUUSD (Oltin) ni jamg'arishi"
                    )
                    events_found.append({"headline": h, "impact": impact, "mechanism": mechanism, "theme": "Geosiyosat & Safe-Haven Talabi"})
                    news_score += impact
                else:
                    # Kripto uchun keskin urush xabari ko'pincha qisqa muddatli Risk-Off likvidatsiya beradi
                    impact = -0.15 if any(w in hl for w in ("attack", "escalation", "strike", "crash", "sell-off")) else 0.10
                    mechanism = (
                        "Geosiyosiy noaniqlik -> Qisqa muddatli fyuchers likvidatsiyalari va volatillik oshishi"
                    )
                    events_found.append({"headline": h, "impact": impact, "mechanism": mechanism, "theme": "Geosiyosiy Volatillik"})
                    news_score += impact

            # 3. Kripto-spetsifik ETF / Institutsional oqimlar (faqat BTC uchun)
            if is_btc and any(k in hl for k in ("etf", "blackrock", "inflow", "outflow", "whale", "microstrategy", "sec", "crypto", "bitcoin", "btc")):
                if any(w in hl for w in ("inflow", "buy", "accumulat", "rally", "surge", "break", "rebound", "bull", "gain", "climb", "support")):
                    impact = 0.25
                    mechanism = "Wall Street Spot BTC ETF lari va yirik hamyonlar (Whales) tomonidan spot xaridlar oshishi -> Birjalardagi taklif qisqarishi"
                    events_found.append({"headline": h, "impact": impact, "mechanism": mechanism, "theme": "Spot ETF & Whale Inflow"})
                    news_score += impact
                elif any(w in hl for w in ("outflow", "sell", "dump", "liquidat", "drop", "slide", "fall", "bear", "reject", "stall", "loss")):
                    impact = -0.25
                    mechanism = "Spot ETF lardan kapital chiqishi (Outflows) va fyuchers Long pozitsiyalarining likvidatsiyasi -> Sotuv bosimi"
                    events_found.append({"headline": h, "impact": impact, "mechanism": mechanism, "theme": "ETF Outflow & Long Liquidation"})
                    news_score += impact

            # 4. Oltin-spetsifik bozor harakati va Markaziy Banklar talabi (faqat XAUUSD uchun)
            if not is_btc and any(k in hl for k in ("gold", "xau", "bullion", "central bank")):
                if any(w in hl for w in ("rallies", "climbs", "rebounds", "surges", "bullish", "highs", "buyers", "demand", "gains")):
                    impact = 0.22
                    mechanism = "Markaziy banklar va institutsional fondlar tomonidan Spot Oltin (XAUUSD) dip-xaridlari faollashishi"
                    events_found.append({"headline": h, "impact": impact, "mechanism": mechanism, "theme": "Institutsional Oltin Talabi"})
                    news_score += impact
                elif any(w in hl for w in ("slides", "falls", "drops", "retreats", "stalls", "bears", "selling", "pressured", "lows", "losses")):
                    impact = -0.22
                    mechanism = "Yuqori nuqtalarda foydani fiksatsiya qilish (Profit-Taking) va DXY/Yields bosimi ostida sotuvlar"
                    events_found.append({"headline": h, "impact": impact, "mechanism": mechanism, "theme": "Foyda Fiksatsiyasi & Dollar Bosimi"})
                    news_score += impact

        news_score = round(max(-0.65, min(0.65, news_score)), 2)
        return {
            "news_score": news_score,
            "events": events_found,
        }

    def analyze(
        self,
        snapshot: MarketSnapshot,
        headlines: list[str],
        high_impact_imminent: bool = False,
    ) -> FundamentalSignal:
        entity_context = MacroNewsScraper.extract_entities_and_context(headlines)
        flow = self._analyze_live_institutional_flow(snapshot)
        causal_news = self._analyze_causal_news_events(headlines, snapshot.symbol)

        if self.client is not None:
            try:
                system_prompt = (
                    "Sen Global Macro Hedge-Fund bosh tahlilchisi va Smart Money Strategsan. "
                    "Vazifang XAUUSD (Oltin) yoki BTCUSD uchun so'nggi makroiqtisodiy yangiliklarni, "
                    "DXY (Dollar indeksi), US10Y (AQSh 10 yillik obligatsiya daromadliligi) hamda grafikdagi "
                    "haqiqiy Order-Flow (VWAP, H1/M15 EMA20/50, RVOL) bilan chuqur sabab-oqibat zanjiri orqali bog'lashdir.\n"
                    "QAT'IY QOIDALAR:\n"
                    "1. Shablon gaplar yozma! Har bir yangilik nega DXY/US10Y orqali ushbu aktivga ta'sir qilishini tushuntir.\n"
                    "2. Agar makro fon (DXY/Yangiliklar) va texnik Order-Flow bir-biriga zid bo'lsa, buni ochiq ayt va confidence_score ni pasaytir.\n"
                    "Javobni FAQAT quyidagi JSON sxemasida (o'zbek tilida) qaytar:\n"
                    "{\n"
                    '  "bias": "BULLISH" | "BEARISH" | "NEUTRAL",\n'
                    '  "confidence_score": float (-1.0 dan +1.0 gacha),\n'
                    '  "macro_reason": "2-3 gapda chuqur makroiqtisodiy sabab-oqibat va Order-Flow xulosasi",\n'
                    '  "causal_chain": "Sabab (Makro voqea) -> Ta\'sir mexanizmi (DXY/US10Y/ETF) -> Institutlar reaksiyasi -> Bozor maqsadi",\n'
                    '  "key_actors_analysis": ["Ishtirokchi 1 aniq pozitsiyasi", "Ishtirokchi 2 aniq pozitsiyasi"],\n'
                    '  "geopolitical_hotspots": ["Hudud va uning bozorga ta\'siri"],\n'
                    '  "predicted_next_moves": ["Asosiy Ssenariy", "Bekor bo\'lish (Invalidation) sharti"],\n'
                    '  "key_drivers": ["Driver 1", "Driver 2"]\n'
                    "}"
                )
                user_prompt = (
                    f"Aktiv: {snapshot.symbol} | Joriy narx: {snapshot.current_price:.2f} | Bozor holati: {snapshot.market_status_note}\n"
                    f"H1 EMA20/50: {flow['h1_ema20']}/{flow['h1_ema50']} | M15 EMA20/50: {flow['m15_ema20']}/{flow['m15_ema50']}\n"
                    f"Intraday VWAP: {flow['vwap']} (Masofa: {flow['vwap_dist_atr']:+.2f} ATR) | RVOL: {flow['rvol']}x\n"
                    f"DXY H1 trendi: {snapshot.dxy_trend} | US10Y H1 trendi: {snapshot.us10y_trend}\n"
                    f"So'nggi yangiliklar:\n- " + "\n- ".join(headlines[:6])
                )
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        temperature=0.15,
                    ),
                )
                data = json.loads(response.text)
                return FundamentalSignal(
                    symbol=snapshot.symbol,
                    bias=str(data.get("bias", "NEUTRAL")).upper(),
                    confidence_score=round(float(data.get("confidence_score", 0.0)), 2),
                    macro_reason=str(data.get("macro_reason", "Makroiqtisodiy tahlil yakunlandi.")),
                    high_impact_news_imminent=high_impact_imminent,
                    key_drivers=list(data.get("key_drivers", [])),
                    causal_chain=str(data.get("causal_chain", "")),
                    key_actors_analysis=list(data.get("key_actors_analysis", [])),
                    geopolitical_hotspots=list(data.get("geopolitical_hotspots", [])),
                    predicted_next_moves=list(data.get("predicted_next_moves", [])),
                )
            except Exception as e:
                logger.warning(f"Gemini API xatosi, PRO v6.0 Causal Macro Engine ishga tushdi: {e}")

        return self._causal_macro_engine(snapshot, headlines, high_impact_imminent, entity_context, flow, causal_news)

    def _causal_macro_engine(
        self,
        snapshot: MarketSnapshot,
        headlines: list[str],
        high_impact_imminent: bool,
        entity_context: Optional[dict[str, list[str]]],
        flow: dict[str, Any],
        causal_news: dict[str, Any],
    ) -> FundamentalSignal:
        is_btc = "BTC" in snapshot.symbol.upper()
        tashkent_now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        is_weekend = tashkent_now.weekday() in (5, 6)

        # 1. Makro Korrelyatsiya balli (DXY va US10Y teskari korrelyatsiyasi)
        corr_score = 0.0
        corr_notes: list[str] = []
        if not (is_btc and is_weekend):
            if snapshot.dxy_trend == "BEARISH":
                corr_score += 0.25
                corr_notes.append("DXY (Dollar indeksi) H1 da pasayish trendida — dollar zaiflashuvi aktiv xaridorlarini qo'llab-quvvatlaydi")
            elif snapshot.dxy_trend == "BULLISH":
                corr_score -= 0.25
                corr_notes.append("DXY (Dollar indeksi) H1 da o'sish trendida — kuchli dollar aktivga teskari bosim o'tkazmoqda")
            else:
                corr_notes.append("DXY H1 da neytral muvozanatda")

            if snapshot.us10y_trend == "BEARISH":
                corr_score += 0.18
                corr_notes.append("AQSh 10-yillik obligatsiya daromadliligi (US10Y) pasaymoqda (Real stavkalar yumshashi)")
            elif snapshot.us10y_trend == "BULLISH":
                corr_score -= 0.18
                corr_notes.append("US10Y obligatsiya daromadliligi o'smoqda (Kapitalning g'aznachilik qog'ozlariga oqishi)")
        else:
            corr_notes.append("Dam olish kuni CME/DXY/US10Y bozorlari yopiq — faqat kripto spot order-flow va likvidlik hisobga olinadi")

        # 2. Yangiliklar Sabab-Oqibat balli
        news_score = float(causal_news["news_score"])
        events = causal_news["events"]

        # 3. Grafikdagi haqiqiy Institutsional Kapital Oqimi (Order-Flow)
        flow_score = float(flow["flow_score"])
        cp = round(snapshot.current_price, 2)
        vwap = float(flow["vwap"])

        # Vaznli yakuniy Makro + Order-Flow balli (Hech qanday sun'iy min/max 0.68 qolipisiz!)
        # Order-Flow 50%, Makro Korrelyatsiya (DXY/US10Y) 25%, Yangiliklar Sabab-Oqibati 25%
        raw_total = (0.50 * flow_score) + (0.25 * corr_score) + (0.25 * news_score)

        # Agar yangilik + korrelyatsiya va Order-Flow bir xil yo'nalishda bo'lsa (Sinergiya bonusi)
        macro_fundamental_side = corr_score + news_score
        if macro_fundamental_side > 0.15 and flow_score > 0.20:
            raw_total += 0.15
        elif macro_fundamental_side < -0.15 and flow_score < -0.20:
            raw_total -= 0.15

        final_score = round(max(-0.95, min(0.95, raw_total)), 2)

        if final_score >= 0.22:
            bias = "BULLISH"
        elif final_score <= -0.22:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        # Sabab-oqibat (Causal Chain) va Tushunarli Fundamental Xulosa tuzish
        if events:
            top_ev = events[0]
            ev_theme = top_ev["theme"]
            ev_mech = top_ev["mechanism"]
            ev_title = top_ev["headline"][:85]
        else:
            ev_theme = "Institutsional Likvidlik & Korrelyatsiya Rejimi"
            ev_title = headlines[0][:85] if headlines else "Bozor sessiyasi likvidlik qayta taqsimoti"
            if corr_score > 0:
                ev_mech = "DXY va US10Y daromadliligining pasayishi hisobiga xarid oqimining ustunligi"
            elif corr_score < 0:
                ev_mech = "DXY va US10Y daromadliligining o'sishi hisobiga sotuv bosimining ustunligi"
            else:
                ev_mech = "Katta makroiqtisodiy katalizator yo'qligida texnik likvidlik hovuzlari (PDH/PDL) o'rtasida savdo"

        flow_desc = (
            f"Narx ({cp:.2f}) Intraday VWAP ({vwap:.2f}) {'ustida' if cp >= vwap else 'ostida'} "
            f"({flow['vwap_dist_atr']:+.2f} ATR), H1 EMA20/50 ({flow['h1_ema20']:.2f}/{flow['h1_ema50']:.2f}), RVOL {flow['rvol']}x"
        )

        # Makro va Texnik uyg'unlik yoki divergensiya izohi
        if (macro_fundamental_side > 0.15 and flow_score < -0.20) or (macro_fundamental_side < -0.15 and flow_score > 0.20):
            harmony_note = (
                "Diqqat: Makro fon va qisqa muddatli M15 Order-Flow o'rtasida divergensiya (qarama-qarshilik) mavjud — "
                "faqat kuchli likvidlik supurilishi (Sweep) tasdiqlangandagina kirish tavsiya etiladi."
            )
        else:
            harmony_note = "Makroiqtisodiy fon (DXY/US10Y) va jonli Order-Flow bir yo'nalishda uyg'unlashgan."

        causal_chain = (
            f"1) Makro Katalizator [{ev_theme}]: «{ev_title}» ➡️ "
            f"2) Fundamental Mexanizm: {ev_mech} ➡️ "
            f"3) Korrelyatsiya & Order-Flow: {corr_notes[0]}; {flow_desc} ➡️ "
            f"4) Yakuniy Bozor Xulosasi: {bias} ({final_score:+.2f}) — {harmony_note}"
        )

        macro_reason = (
            f"[{ev_theme}] {ev_mech}. {corr_notes[0]}. "
            f"Grafikda {flow_desc}. {harmony_note}"
        )

        atr = max(snapshot.atr, 5.0 if not is_btc else 200.0)
        up_t = round(cp + 1.5 * atr, 2)
        dn_t = round(cp - 1.5 * atr, 2)

        if bias == "BULLISH":
            next_moves = [
                f"Asosiy ssenariy (Bullish): Discount / Demand OB zonasidan xarid oqimi davom etib, {up_t:.2f} (Buy-Side Liquidity) darajasini nishonga oladi.",
                f"Bekor bo'lish sharti: Narxning VWAP ({vwap:.2f}) va H1 EMA50 ({flow['h1_ema50']:.2f}) ostida mustahkam yopilishi."
            ]
        elif bias == "BEARISH":
            next_moves = [
                f"Asosiy ssenariy (Bearish): Premium / Supply OB zonasidan sotuv bosimi davom etib, {dn_t:.2f} (Sell-Side Liquidity) darajasini nishonga oladi.",
                f"Bekor bo'lish sharti: Narxning VWAP ({vwap:.2f}) va H1 EMA50 ({flow['h1_ema50']:.2f}) ustida mustahkam yopilishi."
            ]
        else:
            next_moves = [
                f"Neytral / Kutish: Bozor muvozanatda ({final_score:+.2f}). {up_t:.2f} yoki {dn_t:.2f} likvidligi supurilishi kutilmoqda."
            ]

        actors = (entity_context or {}).get("actors", []) or ["Fed / FOMC & US Treasury", "Wall Street Institutional Desks"]
        locations = (entity_context or {}).get("locations", []) or ["AQSh (Nyu-York / CME & Vashington)"]

        drivers = [ev_theme] + corr_notes[:1] + [flow_desc]

        return FundamentalSignal(
            symbol=snapshot.symbol,
            bias=bias,
            confidence_score=final_score,
            macro_reason=macro_reason,
            high_impact_news_imminent=high_impact_imminent,
            key_drivers=drivers,
            causal_chain=causal_chain,
            key_actors_analysis=[f"{a}: {ev_theme} doirasida pozitsiyalarni qayta balanslamoqda." for a in actors[:2]],
            geopolitical_hotspots=[f"{loc}: Asosiy makroiqtisodiy likvidlik markazi." for loc in locations[:2]],
            predicted_next_moves=next_moves,
        )
