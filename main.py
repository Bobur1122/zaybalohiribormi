"""
5-QISM: Asosiy Orkestrator va 24/7 Avtonom Ishga Tushirish Moduli (main.py) — PRO v5.1
PRO v5.1 INSTITUTIONAL DIAMOND SNIPER:
1. 5 ta "Signal Bloklanish" (Catch-22) xatosi to'liq bartaraf etildi.
2. Bekor qilingan (`CANCELLED`) limit orderlardan keyin 90 daqiqalik Cooldown emas, atigi 6 daqiqalik
   yangilanish pauzasi qo'llaniladi — shunda bot yangi kirish nuqtasini o'tkazib yubormaydi!
3. Har 3 daqiqalik siklda konsol va log faylga aniq diagnostika (`[SCAN] Narx | Yo'nalish | Confluence Ball | Sabab`)
   chiqariladi.
"""
import argparse
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import sys
import time
import urllib.request
from typing import Any, Optional

from config import (
    SYMBOLS,
    TIMEZONE_OFFSET_HOURS,
    MAX_DAILY_TRADES_PER_SYMBOL,
    POST_TRADE_COOLDOWN_MINUTES,
    PENDING_EXPIRY_MINUTES,
)
from data_feed import MarketDataCollector
from news_scraper import MacroNewsScraper
from fundamental_agent import FundamentalAgent
from technical_agent import TechnicalAgent
from risk_manager import RiskManager
from telegram_bot import TelegramSignalDispatcher, PerformanceStatsTracker, clean_md
from schemas import FinalTradeSignal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot_execution.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("AI_Gold_Sniper_Main")

ACTIVE_STATE_FILE = "active_trades_state.json"


class MultiAgentTradingOrchestrator:
    """
    PRO v5.1 Institutional Diamond Sniper — 4 ta AI-agentni boshqaruvchi va
    ochiq savdolarni 24/7 kuzatuvchi markaziy klass.
    """

    def __init__(self, use_mt5: bool = False, enforce_killzone: bool = True):
        self.data_collector = MarketDataCollector(use_mt5=use_mt5)
        self.news_scraper = MacroNewsScraper()
        self.fundamental_agent = FundamentalAgent()
        self.technical_agent = TechnicalAgent()
        self.risk_manager = RiskManager(enforce_killzone=enforce_killzone)
        self.stats_tracker = PerformanceStatsTracker()
        self.telegram_bot = TelegramSignalDispatcher(stats_tracker=self.stats_tracker)

        self.active_trades: dict[str, dict[str, Any]] = {}
        self.last_trade_closed_time: dict[str, datetime] = {}
        self.last_alert_time: dict[str, datetime] = {}
        self.last_update_id: int = 0

        self._load_active_state()
        purged = self.stats_tracker.purge_unsent_ghost_trades(set(self.active_trades.keys()))
        if purged > 0:
            logger.info(f"Tarixdan {purged} ta yetib bormagan (ghost) PENDING yozuvlar tozalandi.")

    def _load_active_state(self) -> None:
        if os.path.exists(ACTIVE_STATE_FILE):
            try:
                with open(ACTIVE_STATE_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for sym, state in raw.get("active_trades", {}).items():
                    if "opened_at" in state and isinstance(state["opened_at"], str):
                        state["opened_at"] = datetime.fromisoformat(state["opened_at"])
                    self.active_trades[sym] = state
                for sym, ts_str in raw.get("last_trade_closed_time", {}).items():
                    if isinstance(ts_str, str):
                        self.last_trade_closed_time[sym] = datetime.fromisoformat(ts_str)
            except Exception as e:
                logger.warning(f"Holat faylini o'qishda xatolik: {e}")

    def _save_active_state(self) -> None:
        try:
            serializable_active = {}
            for sym, st in self.active_trades.items():
                copy_st = dict(st)
                if isinstance(copy_st.get("opened_at"), datetime):
                    copy_st["opened_at"] = copy_st["opened_at"].isoformat()
                serializable_active[sym] = copy_st

            serializable_closed = {
                sym: dt.isoformat() for sym, dt in self.last_trade_closed_time.items()
            }
            payload = {
                "active_trades": serializable_active,
                "last_trade_closed_time": serializable_closed,
            }
            with open(ACTIVE_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Holat faylini saqlashda xatolik: {e}")

    def _check_smart_pending_invalidation(
        self,
        symbol: str,
        trade: dict[str, Any],
        bar_high: float,
        bar_low: float,
        cp: float,
        imminent_news: bool,
    ) -> Optional[str]:
        direction = trade["direction"]
        entry = trade["entry"]
        tp1 = trade["tp1"]
        opened_at: datetime = trade["opened_at"]
        now_utc = datetime.now(timezone.utc)
        age_minutes = (now_utc - opened_at).total_seconds() / 60.0

        # 1. Narx limit orderga tegmasdan TP1 ga yetib borsa -> Missed Setup
        missed_cond = (direction == "BUY" and bar_high >= tp1) or (direction == "SELL" and bar_low <= tp1)
        if missed_cond:
            return (
                f"⚠️ *LIMIT ORDER BEKOR QILINDI (MISSED SETUP): {clean_md(symbol)}*\n"
                f"• *Sabab:* Narx `{entry:.2f}` limitga qaytmasdan TP1 (`{tp1:.2f}`) maqsadiga yetib bordi.\n"
                f"• *Harakat:* Eskirgan `{trade['order_type']}` orderni o'chiring (`$0.00`). Yangi setup qidiriladi."
            )

        # 2. Favqulodda jonli Fed/CPI/NFP relizi boshlansa
        if imminent_news:
            return (
                f"🚨 *YANGILIK HIMOYASI: {clean_md(symbol)} LIMIT ORDER BEKOR QILINDI*\n"
                f"• *Sabab:* Ayni damda favqulodda AQSh makroiqtisodiy relizi chiqmoqda.\n"
                f"• *Harakat:* `{entry:.2f}` dagi `{trade['order_type']}` orderni o'chiring (`$0.00`)."
            )

        # 3. 120 daqiqa (2 soat) ichida narx limitga kelmasa
        if age_minutes >= PENDING_EXPIRY_MINUTES:
            return (
                f"⌛ *VAQT TUGADI (EXPIRED): {clean_md(symbol)} LIMIT ORDER YANGILANADI*\n"
                f"• *Sabab:* `{entry:.2f}` dagi `{trade['order_type']}` order {int(age_minutes)} daqiqa ichida ishga tushmadi (Joriy narx: `{cp:.2f}`).\n"
                f"• *Harakat:* Eskirgan limit orderni o'chiring (`$0.00`). Yangi bozor strukturasi tahlil qilinadi."
            )

        return None

    def check_and_manage_active_trades(self) -> None:
        if not self.active_trades:
            return

        imminent_news, _ = self.news_scraper.check_imminent_high_impact_news(window_minutes=15)

        for symbol in list(self.active_trades.keys()):
            trade = self.active_trades[symbol]
            try:
                live_q = self.data_collector.fetch_tradingview_live_quote(symbol)
                if live_q and live_q.get("close"):
                    cp = round(float(live_q["close"]), 2)
                else:
                    snap = self.data_collector.get_snapshot(symbol)
                    cp = round(float(snap.current_price), 2)

                bar_high = cp
                bar_low = cp
            except Exception as e:
                logger.warning(f"{symbol} jonli narxini tekshirishda xato: {e}")
                continue

            direction = trade["direction"]
            entry = trade["entry"]
            sl = trade["sl"]
            tp1 = trade["tp1"]
            tp2 = trade["tp2"]
            tp3 = trade.get("tp3", round(entry + 1.45 * (tp2 - entry), 2))
            status = trade["status"]

            # 1-BOSQICH: PENDING LIMIT ORDER
            if status == "PENDING":
                triggered = (direction == "BUY" and bar_low <= entry) or (
                    direction == "SELL" and bar_high >= entry
                )
                if triggered:
                    trade["status"] = "TRIGGERED"
                    sig_id = self.stats_tracker.record_triggered(symbol) or trade.get("id", 1)
                    self._save_active_state()
                    msg = (
                        f"⚡ *ORDER ISHGA TUSHDI (#{sig_id}): {clean_md(symbol)} ({direction})*\n"
                        f"• *Kirish:* `{entry:.2f}` | *Joriy narx:* `{cp:.2f}`\n"
                        f"• *Stop-Loss:* `{sl:.2f}` | *TP1 (50% + B/U):* `{tp1:.2f}` | *TP2:* `{tp2:.2f}`"
                    )
                    self.telegram_bot.send_text_message(msg)
                    continue
                else:
                    cancel_msg = self._check_smart_pending_invalidation(
                        symbol, trade, bar_high, bar_low, cp, imminent_news
                    )
                    if cancel_msg:
                        sig_id, _ = self.stats_tracker.record_outcome(symbol, "CANCELLED")
                        stats_footer = self.stats_tracker.format_stats_block()
                        self.telegram_bot.send_text_message(f"{cancel_msg}\n\n{stats_footer}")
                        # PRO v5.1: Bekor qilingan PENDING orderdan keyin 30 daqiqa emas, atigi 6 daqiqa kutamiz!
                        self.last_trade_closed_time[symbol] = datetime.now(timezone.utc) - timedelta(
                            minutes=max(0, POST_TRADE_COOLDOWN_MINUTES - 6)
                        )
                        del self.active_trades[symbol]
                        self._save_active_state()
                    continue

            # 2-BOSQICH: TRIGGERED -> TP1 (50% FIKSATSIYA VA BREAKEVEN)
            if status == "TRIGGERED":
                hit_tp1 = (direction == "BUY" and bar_high >= tp1) or (
                    direction == "SELL" and bar_low <= tp1
                )
                if hit_tp1:
                    trade["status"] = "TP1_HIT"
                    trade["sl"] = entry
                    trade["max_tp_stage"] = 1
                    sig_id, locked_pnl = self.stats_tracker.record_stage_progress(symbol, "TP1_HIT", 1)
                    self._save_active_state()
                    stats_footer = self.stats_tracker.format_stats_block()
                    msg = (
                        f"🎯 *TP1 OLINDI (50% FOYDA + BEZUBITOK) #{sig_id}: {clean_md(symbol)}*\n"
                        f"• *Joriy narx:* `{cp:.2f}` (TP1: `{tp1:.2f}`)\n"
                        f"• *50% Fiksatsiya:* `+$" + f"{locked_pnl:.2f}` naqd qilindi!\n"
                        f"• *Majburiy Amal:* Qolgan 50% pozitsiya Stop-Lossini `{entry:.2f}` (Entry / B/U) ga ko'chiring!\n\n"
                        f"{stats_footer}"
                    )
                    self.telegram_bot.send_text_message(msg)
                    status = "TP1_HIT"
                    sl = entry

            # 3-BOSQICH: TP1_HIT -> TP2 (YANA 30% FIKSATSIYA)
            if status == "TP1_HIT":
                hit_tp2 = (direction == "BUY" and bar_high >= tp2) or (
                    direction == "SELL" and bar_low <= tp2
                )
                if hit_tp2:
                    trade["status"] = "TP2_HIT"
                    trade["sl"] = tp1
                    trade["max_tp_stage"] = 2
                    sig_id, locked_pnl = self.stats_tracker.record_stage_progress(symbol, "TP2_HIT", 2)
                    self._save_active_state()
                    stats_footer = self.stats_tracker.format_stats_block()
                    msg = (
                        f"🎯🎯 *TP2 OLINDI (80% FOYDA NAQD) #{sig_id}: {clean_md(symbol)}*\n"
                        f"• *Joriy narx:* `{cp:.2f}` (TP2: `{tp2:.2f}`)\n"
                        f"• *Jami Fiksatsiya (80%):* `+$" + f"{locked_pnl:.2f}`\n"
                        f"• *Trailing SL:* Qolgan 20% Runner Stop-Lossi `{tp1:.2f}` (TP1) ga ko'chirildi! Maqsad TP3 (`{tp3:.2f}`).\n\n"
                        f"{stats_footer}"
                    )
                    self.telegram_bot.send_text_message(msg)
                    status = "TP2_HIT"
                    sl = tp1

            # 4-BOSQICH: TP3 YAKUNIY MAQSAD
            hit_tp3 = (direction == "BUY" and bar_high >= tp3) or (
                direction == "SELL" and bar_low <= tp3
            )
            if hit_tp3:
                sig_id, pnl = self.stats_tracker.record_outcome(symbol, "TP3_FULL")
                stats_footer = self.stats_tracker.format_stats_block()
                msg = (
                    f"🚀🏆 *TP3 FULL WIN (100% MAQSAD) #{sig_id}: {clean_md(symbol)}*\n"
                    f"• *Joriy narx:* `{cp:.2f}` (TP3: `{tp3:.2f}`)\n"
                    f"• *Yakuniy Sof Foyda:* `*+${pnl:.2f}*`\n\n"
                    f"{stats_footer}"
                )
                self.telegram_bot.send_text_message(msg)
                self.last_trade_closed_time[symbol] = datetime.now(timezone.utc)
                del self.active_trades[symbol]
                self._save_active_state()
                continue

            # 5-BOSQICH: STOP-LOSS YOKI TRAILING SL TEKSHIRUVI
            hit_sl = (direction == "BUY" and bar_low <= sl) or (
                direction == "SELL" and bar_high >= sl
            )
            if hit_sl:
                max_stage = int(trade.get("max_tp_stage", 0))
                if max_stage == 2:
                    sig_id, pnl = self.stats_tracker.record_outcome(symbol, "TP2_REACHED")
                    title = f"✅ *SAVDO YOPILDI (TP1 + TP2 FOYDA BILAN) #{sig_id}: {clean_md(symbol)}*"
                    detail = f"Qolgan 20% qism `{sl:.2f}` da yopildi. *Sof Foyda: +${pnl:.2f}*"
                elif max_stage == 1:
                    sig_id, pnl = self.stats_tracker.record_outcome(symbol, "TP1_ONLY")
                    title = f"🛡️ *TP1 FOYDA + BREAKEVEN HIMOYASI #{sig_id}: {clean_md(symbol)}*"
                    detail = f"Narx `{entry:.2f}` (B/U) ga qaytdi, lekin TP1 dagi 50% foyda hisobiga savdo *+${pnl:.2f} foyda* bilan yopildi!"
                else:
                    sig_id, pnl = self.stats_tracker.record_outcome(symbol, "SL_HIT")
                    title = f"❌ *STOP-LOSS ISHLADI (#{sig_id}): {clean_md(symbol)}*"
                    detail = f"Narx `{sl:.2f}` dagi himoya Stop-Lossga tegdi (`-${abs(pnl):.2f}`). Keyingi savdogacha {POST_TRADE_COOLDOWN_MINUTES} daqiqa Cooldown."

                stats_footer = self.stats_tracker.format_stats_block()
                self.telegram_bot.send_text_message(f"{title}\n• {detail}\n\n{stats_footer}")
                self.last_trade_closed_time[symbol] = datetime.now(timezone.utc)
                del self.active_trades[symbol]
                self._save_active_state()

    def build_live_status_report(self) -> str:
        tashkent_now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        _, kz_label = self.risk_manager.get_current_killzone()
        lines = [
            f"📡 *PRO v5.1 SNIPER — JONLI HOLAT ({tashkent_now.strftime('%H:%M')} UZT)*",
            f"🕒 *Sessiya:* `{clean_md(kz_label)}`\n",
        ]
        for asset_key in ("GOLD", "BTC"):
            sym = SYMBOLS[asset_key]
            snap = self.data_collector.get_snapshot(sym)
            headlines = self.news_scraper.fetch_headlines(max_items=5, symbol=sym)
            macro = self.fundamental_agent.analyze(snap, headlines, False)
            tech = self.technical_agent.find_setup(snap, macro)
            today_cnt = self.stats_tracker.get_today_trades_count(sym)
            active_st = self.active_trades.get(sym, {}).get("status", "Yo'q")
            lines.append(
                f"🔹 *{sym}:* `{snap.current_price:.2f}` | Yo'nalish: *{tech.direction}* | "
                f"Confluence: *{tech.confluence_score}/100*\n"
                f"   • Holat: `{active_st}` | Bugungi savdolar: `{today_cnt}/{MAX_DAILY_TRADES_PER_SYMBOL}`\n"
                f"   • Likvidlik: {clean_md(tech.session_liquidity_info)}"
            )
        lines.append("\n" + self.stats_tracker.format_stats_block())
        return "\n".join(lines)

    def poll_telegram_commands(self) -> None:
        if not self.telegram_bot.bot_token:
            return
        url = (
            f"https://api.telegram.org/bot{self.telegram_bot.bot_token}/getUpdates"
            f"?offset={self.last_update_id + 1}&timeout=1"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for upd in data.get("result", []):
                self.last_update_id = int(upd["update_id"])
                msg = upd.get("message") or upd.get("channel_post") or {}
                text = (msg.get("text") or "").strip().lower()
                if not text:
                    continue
                if text.startswith("/stat"):
                    self.telegram_bot.send_text_message(self.stats_tracker.format_stats_block())
                elif text.startswith("/status") or text.startswith("/holat"):
                    self.telegram_bot.send_text_message(self.build_live_status_report())
                elif text.startswith("/reset_stat"):
                    self.stats_tracker.clear_history()
                    self.active_trades.clear()
                    self.last_trade_closed_time.clear()
                    self._save_active_state()
                    self.telegram_bot.send_text_message(
                        "🧹 *PRO v5.1 Statistika va ochiq savdolar tarixi 0 dan yangilandi!*"
                    )
        except Exception as e:
            logger.debug(f"Telegram command polling xatosi: {e}")

    def run_pipeline_for_symbol(self, symbol: str) -> FinalTradeSignal:
        # Agar ushbu aktivda ochiq yoki pending savdo bo'lsa, yangi signal ochmaymiz
        if symbol in self.active_trades:
            st = self.active_trades[symbol]["status"]
            logger.info(f"[{symbol}] Ochiq savdo mavjud (Holati: {st}) — yangi signal kutish rejimida.")
            snap = self.data_collector.get_snapshot(symbol)
            headlines = self.news_scraper.fetch_headlines(max_items=5, symbol=symbol)
            macro = self.fundamental_agent.analyze(snap, headlines, False)
            tech = self.technical_agent.find_setup(snap, macro)
            return self.risk_manager._reject(snap, macro, tech, "Active Trade", f"Aktiv savdo mavjud ({st}).")

        snapshot = self.data_collector.get_snapshot(symbol)
        imminent_news, _ = self.news_scraper.check_imminent_high_impact_news(window_minutes=15)
        headlines = self.news_scraper.fetch_headlines(max_items=8, symbol=symbol)
        macro_signal = self.fundamental_agent.analyze(snapshot, headlines, imminent_news)
        tech_setup = self.technical_agent.find_setup(snapshot, macro_signal)

        today_trades = self.stats_tracker.get_today_trades_count(symbol)
        last_closed = self.last_trade_closed_time.get(symbol)

        final_signal = self.risk_manager.evaluate_trade(
            snapshot=snapshot,
            macro=macro_signal,
            tech=tech_setup,
            today_trades_count=today_trades,
            last_closed_time=last_closed,
        )

        logger.info(
            f"[PRO v5.1 SCAN] {symbol} | Narx={snapshot.current_price:.2f} | "
            f"Macro={macro_signal.bias} ({macro_signal.confidence_score:+.2f}) | "
            f"Tech={tech_setup.direction} | Score={tech_setup.confluence_score}/100 | "
            f"Sweep={tech_setup.liquidity_swept} | Approved={final_signal.is_approved}"
        )

        if final_signal.is_approved:
            sent_ok = self.telegram_bot.send_signal(final_signal)
            if sent_ok:
                tp3_val = final_signal.tp3_price if final_signal.tp3_price > 0 else round(
                    final_signal.entry_price + 1.45 * (final_signal.tp2_price - final_signal.entry_price), 2
                )
                self.active_trades[symbol] = {
                    "id": self.stats_tracker.next_signal_id() - 1,
                    "symbol": symbol,
                    "direction": final_signal.direction,
                    "order_type": final_signal.order_type,
                    "entry": final_signal.entry_price,
                    "sl": final_signal.sl_price,
                    "tp1": final_signal.tp1_price,
                    "tp2": final_signal.tp2_price,
                    "tp3": tp3_val,
                    "status": "TRIGGERED" if "MARKET" in final_signal.order_type else "PENDING",
                    "max_tp_stage": 0,
                    "opened_at": datetime.now(timezone.utc),
                }
                self._save_active_state()
                logger.info(f"✅ [{symbol}] A+ Signal (#{self.active_trades[symbol]['id']}) Telegramga muvaffaqiyatli yuborildi!")

        return final_signal

    def run_once(self) -> dict[str, FinalTradeSignal]:
        self.poll_telegram_commands()
        self.check_and_manage_active_trades()
        results: dict[str, FinalTradeSignal] = {}
        for asset_key in ("GOLD", "BTC"):
            sym = SYMBOLS[asset_key]
            results[sym] = self.run_pipeline_for_symbol(sym)
        return results

    def run_forever(self, interval_seconds: int = 180) -> None:
        logger.info("🚀 PRO v5.1 Institutional Diamond Sniper 24/7 rejimida ishga tushdi!")
        while True:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Asosiy siklda xatolik: {e}", exc_info=True)
            time.sleep(interval_seconds)


import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Starting health check server on port {port}")
    server.serve_forever()

def main() -> None:
    # Start health server for Render
    if os.environ.get("RENDER") or os.environ.get("PORT"):
        t = threading.Thread(target=start_health_server, daemon=True)
        t.start()

    parser = argparse.ArgumentParser(description="PRO v5.1 Institutional Diamond Sniper")
    parser.add_argument("--loop", action="store_true", help="24/7 uzluksiz monitoring rejimi")
    parser.add_argument("--interval", type=int, default=180, help="Sikllar orasidagi vaqt (sekund, default=180)")
    parser.add_argument("--mt5", action="store_true", help="MetaTrader 5 terminalidan ma'lumot olish")
    parser.add_argument("--ignore-killzone", action="store_true", help="Killzone vaqt filtrini o'chirish")
    parser.add_argument("--reset-stats", action="store_true", help="Statistika tarixini 0 dan tozalash")
    args = parser.parse_args()

    orchestrator = MultiAgentTradingOrchestrator(
        use_mt5=args.mt5,
        enforce_killzone=not args.ignore_killzone,
    )
    if args.reset_stats:
        orchestrator.stats_tracker.clear_history()
        orchestrator.active_trades.clear()
        orchestrator.last_trade_closed_time.clear()
        orchestrator._save_active_state()
        logger.info("🧹 Statistika va faol savdolar tarixi 0 dan yangilandi.")

    if args.loop:
        orchestrator.run_forever(interval_seconds=args.interval)
    else:
        orchestrator.run_once()


if __name__ == "__main__":
    main()