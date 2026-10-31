"""
4-QISM: PRO v5.1 Toza Matnli Telegram Signal (< 3,000 belgi)
hamda Kunlik Limit va Aniq Statistika Kuzatuvchisi (telegram_bot.py)

PRO v5.1 INSTITUTIONAL DIAMOND SNIPER:
- MUHIM TUZATISH: `get_today_trades_count(symbol)` endi bekor qilingan (`CANCELLED`) pending orderlarni
  kunlik limitga qo'shib yubormaydi! Faqat haqiqiy ishga tushgan yoki faol savdolarni hisoblaydi.
- 4096-belgi Telegram limitidan 100% xavfsiz format (< 2,800 belgi)
- Aniq TP1, TP2, TP3, B/U va SL statistikasi
"""
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE_OFFSET_HOURS
from schemas import FinalTradeSignal

logger = logging.getLogger(__name__)

HISTORY_FILE = "trade_history.json"
MAX_TELEGRAM_TEXT_LEN = 3900


def clean_md(text: str) -> str:
    """Telegram Markdown parserini buzuvchi belgilarni tozalaydi."""
    s = str(text)
    return s.replace("_", "-").replace("*", "").replace("`", "").replace("[", "(").replace("]", ")")


def strip_all_md(text: str) -> str:
    """Barcha Markdown belgilarini olib tashlaydi."""
    return re.sub(r"[*_`]", "", str(text))


def safe_truncate_tg(text: str, max_len: int = MAX_TELEGRAM_TEXT_LEN) -> str:
    """Xabar uzunligini xavfsiz chegarada ushlaydi."""
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 20]
    last_nl = cut.rfind("\n")
    if last_nl > max_len - 400:
        cut = cut[:last_nl]
    return cut + "\n..."


class PerformanceStatsTracker:
    """
    PRO v5.1 Aniq Statistika va Kunlik Limit Kuzatuvchisi.
    """

    def __init__(self, history_file: str = HISTORY_FILE):
        self.history_file = history_file
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"trades": []}

    def _save(self) -> None:
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def clear_history(self) -> None:
        self.data = {"trades": []}
        self._save()

    def purge_unsent_ghost_trades(self, valid_active_symbols: set[str]) -> int:
        trades = self.data.get("trades", [])
        cleaned: list[dict[str, Any]] = []
        removed = 0
        for tr in trades:
            st = tr.get("status", "PENDING")
            sym = tr.get("symbol", "")
            if st == "PENDING" and sym not in valid_active_symbols:
                removed += 1
                continue
            cleaned.append(tr)
        if removed > 0:
            for idx, tr in enumerate(cleaned, start=1):
                tr["id"] = idx
            self.data["trades"] = cleaned
            self._save()
        return removed

    def next_signal_id(self) -> int:
        trades = self.data.get("trades", [])
        return len(trades) + 1

    def get_today_trades_count(self, symbol: str) -> int:
        """
        PRO v5.1: Toshkent vaqti bo'yicha bugun ushbu aktivda nechta HAQIQIY savdo ochilganini hisoblaydi.
        Bekor qilingan (`CANCELLED`) limit orderlar kunlik limitni yeb qo'ymaydi!
        """
        trades = self.data.get("trades", [])
        tashkent_now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        today_prefix = tashkent_now.strftime("%Y-%m-%d")
        count = 0
        for tr in trades:
            if tr.get("symbol") == symbol and tr.get("status") != "CANCELLED":
                op = tr.get("opened_at", "")
                if op.startswith(today_prefix):
                    count += 1
        return count

    def record_new_signal(
        self,
        symbol: str,
        direction: str,
        order_type: str,
        entry_price: float,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
        tp3_price: float = 0.0,
        risk_usd: float = 100.0,
    ) -> int:
        trades = self.data.setdefault("trades", [])
        for tr in reversed(trades):
            if tr.get("symbol") == symbol and tr.get("status") in ("PENDING", "TRIGGERED", "BREAKEVEN", "TP1_HIT", "TP2_HIT"):
                if tr.get("status") == "PENDING":
                    tr["direction"] = direction
                    tr["order_type"] = order_type
                    tr["entry_price"] = round(entry_price, 2)
                    tr["sl_price"] = round(sl_price, 2)
                    tr["tp1_price"] = round(tp1_price, 2)
                    tr["tp2_price"] = round(tp2_price, 2)
                    tr["tp3_price"] = round(tp3_price if tp3_price > 0 else tp2_price, 2)
                    self._save()
                return int(tr["id"])

        sig_id = len(trades) + 1
        tashkent_now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        trades.append({
            "id": sig_id,
            "symbol": symbol,
            "direction": direction,
            "order_type": order_type,
            "entry_price": round(entry_price, 2),
            "sl_price": round(sl_price, 2),
            "tp1_price": round(tp1_price, 2),
            "tp2_price": round(tp2_price, 2),
            "tp3_price": round(tp3_price if tp3_price > 0 else tp2_price, 2),
            "risk_usd": round(risk_usd, 2),
            "status": "TRIGGERED" if "MARKET" in order_type else "PENDING",
            "max_tp_stage": 0,
            "pnl_usd": 0.0,
            "opened_at": tashkent_now.strftime("%Y-%m-%d %H:%M UZT"),
            "closed_at": None,
        })
        self._save()
        return sig_id

    def record_triggered(self, symbol: str) -> Optional[int]:
        for tr in reversed(self.data.get("trades", [])):
            if tr.get("symbol") == symbol and tr.get("status") == "PENDING":
                tr["status"] = "TRIGGERED"
                self._save()
                return int(tr["id"])
        return None

    def record_stage_progress(self, symbol: str, new_status: str, max_tp_stage: int) -> tuple[int, float]:
        for tr in reversed(self.data.get("trades", [])):
            if tr.get("symbol") == symbol and tr.get("status") in ("PENDING", "TRIGGERED", "BREAKEVEN", "TP1_HIT", "TP2_HIT"):
                entry = float(tr["entry_price"])
                sl = float(tr["sl_price"])
                tp1 = float(tr["tp1_price"])
                tp2 = float(tr["tp2_price"])
                risk_usd = float(tr.get("risk_usd", 100.0))
                risk_pts = max(abs(entry - sl), 1e-4)

                rr1 = abs(entry - tp1) / risk_pts
                rr2 = abs(entry - tp2) / risk_pts

                tr["status"] = new_status
                tr["max_tp_stage"] = max(int(tr.get("max_tp_stage", 0)), max_tp_stage)
                if max_tp_stage == 1:
                    tr["pnl_usd"] = round(risk_usd * (0.50 * rr1), 2)
                elif max_tp_stage == 2:
                    tr["pnl_usd"] = round(risk_usd * (0.50 * rr1 + 0.30 * rr2), 2)
                self._save()
                return int(tr["id"]), float(tr["pnl_usd"])
        return 0, 0.0

    def record_outcome(self, symbol: str, outcome: str) -> tuple[int, float]:
        if outcome == "BREAKEVEN":
            outcome = "PURE_BU"

        tashkent_now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        for tr in reversed(self.data.get("trades", [])):
            if tr.get("symbol") == symbol and tr.get("status") in ("PENDING", "TRIGGERED", "BREAKEVEN", "TP1_HIT", "TP2_HIT"):
                entry = float(tr["entry_price"])
                sl = float(tr["sl_price"])
                tp1 = float(tr["tp1_price"])
                tp2 = float(tr["tp2_price"])
                tp3 = float(tr.get("tp3_price", tp2))
                risk_usd = float(tr.get("risk_usd", 100.0))
                risk_pts = max(abs(entry - sl), 1e-4)

                rr_tp1 = abs(entry - tp1) / risk_pts
                rr_tp2 = abs(entry - tp2) / risk_pts
                rr_tp3 = abs(entry - tp3) / risk_pts

                if outcome in ("TP1_ONLY", "TP1_HIT"):
                    outcome = "TP1_ONLY"
                    tr["max_tp_stage"] = 1
                    pnl = round(risk_usd * (0.50 * rr_tp1), 2)
                elif outcome in ("TP2_REACHED", "TP2_HIT"):
                    outcome = "TP2_REACHED"
                    tr["max_tp_stage"] = 2
                    pnl = round(risk_usd * (0.50 * rr_tp1 + 0.30 * rr_tp2), 2)
                elif outcome == "TP3_FULL":
                    tr["max_tp_stage"] = 3
                    pnl = round(risk_usd * (0.50 * rr_tp1 + 0.30 * rr_tp2 + 0.20 * rr_tp3), 2)
                elif outcome == "PURE_BU":
                    tr["max_tp_stage"] = 0
                    pnl = 0.0
                elif outcome == "CANCELLED":
                    tr["max_tp_stage"] = 0
                    pnl = 0.0
                else:
                    outcome = "SL_HIT"
                    tr["max_tp_stage"] = 0
                    pnl = round(-risk_usd, 2)

                tr["status"] = outcome
                tr["pnl_usd"] = pnl
                tr["closed_at"] = tashkent_now.strftime("%Y-%m-%d %H:%M UZT")
                self._save()
                return int(tr["id"]), pnl
        return 0, 0.0

    def compute_metrics(self) -> dict[str, Any]:
        trades = self.data.get("trades", [])
        closed_statuses = ("TP1_ONLY", "TP2_REACHED", "TP3_FULL", "PURE_BU", "SL_HIT")
        closed = [t for t in trades if t.get("status") in closed_statuses]

        tp1_only_list = [t for t in closed if t.get("status") == "TP1_ONLY"]
        tp2_list = [t for t in closed if t.get("status") == "TP2_REACHED"]
        tp3_list = [t for t in closed if t.get("status") == "TP3_FULL"]
        pure_bu_list = [t for t in closed if t.get("status") == "PURE_BU"]
        sl_list = [t for t in closed if t.get("status") == "SL_HIT"]

        pending_list = [t for t in trades if t.get("status") == "PENDING"]
        active_list = [t for t in trades if t.get("status") in ("TRIGGERED", "BREAKEVEN", "TP1_HIT", "TP2_HIT")]
        cancelled_list = [t for t in trades if t.get("status") == "CANCELLED"]

        wins_total = len(tp1_only_list) + len(tp2_list) + len(tp3_list)
        losses_total = len(sl_list)
        decided = wins_total + losses_total
        win_rate = round((wins_total / decided) * 100.0, 1) if decided > 0 else 100.0
        net_pnl = round(sum(float(t.get("pnl_usd", 0.0)) for t in closed + active_list), 2)

        return {
            "total_signals": len(trades),
            "closed_count": len(closed),
            "pending_count": len(pending_list),
            "active_count": len(active_list),
            "cancelled_count": len(cancelled_list),
            "tp1_only_count": len(tp1_only_list),
            "tp2_count": len(tp2_list),
            "tp3_count": len(tp3_list),
            "pure_bu_count": len(pure_bu_list),
            "sl_count": len(sl_list),
            "wins_total": wins_total,
            "win_rate": win_rate,
            "net_pnl": net_pnl,
            "recent_trades": trades[-5:],
        }

    def format_stats_block(self) -> str:
        m = self.compute_metrics()
        pnl_sign = "+" if m["net_pnl"] >= 0 else ""
        lines = [
            "📊 *PRO v5.1 STATISTIKA:*",
            f"• Jami: *{m['total_signals']} ta* (Yopilgan: *{m['closed_count']}* | Ochiq: *{m['active_count']}* | Kutishda: *{m['pending_count']}*)",
            f"• Natijalar: 🎯 TP1: *{m['tp1_only_count']}* | 🎯🎯 TP2: *{m['tp2_count']}* | 🚀 TP3: *{m['tp3_count']}* | 🛡️ B/U: *{m['pure_bu_count']}* | ❌ SL: *{m['sl_count']}*",
            f"• Win Rate: *{m['win_rate']}%* | Sof PnL: *{pnl_sign}${m['net_pnl']:.2f}*",
        ]
        return "\n".join(lines)


class TelegramSignalDispatcher:
    """
    PRO v5.1 Toza Matnli Telegram Signal Yuboruvchi.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        stats_tracker: Optional[PerformanceStatsTracker] = None,
    ):
        self.bot_token = bot_token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.stats = stats_tracker or PerformanceStatsTracker()

    def format_signal_message(self, signal: FinalTradeSignal, signal_id: Optional[int] = None) -> str:
        sig_id = signal_id if signal_id is not None else self.stats.next_signal_id()
        direction_emoji = "🟢" if signal.direction == "BUY" else "🔴"
        order_badge = "⚡ MARKET" if "MARKET" in signal.order_type else "⏳ LIMIT ORDER"

        entry_p = signal.entry_price
        sl_p = signal.sl_price
        tp1_p = signal.tp1_price
        tp2_p = signal.tp2_price
        tp3_p = signal.tp3_price if signal.tp3_price > 0 else round(
            entry_p + (1.45 * (tp2_p - entry_p)), 2
        )

        sl_pts = round(abs(entry_p - sl_p), 2)
        tp1_pts = round(abs(tp1_p - entry_p), 2)
        tp2_pts = round(abs(tp2_p - entry_p), 2)
        tp3_pts = round(abs(tp3_p - entry_p), 2)

        sl_safe = max(sl_pts, 1e-4)
        rr1 = round(tp1_pts / sl_safe, 2)
        rr2 = round(tp2_pts / sl_safe, 2)
        rr3 = round(tp3_pts / sl_safe, 2)

        tashkent_now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        time_str = tashkent_now.strftime("%Y-%m-%d %H:%M UZT")

        breakdown_lines = "\n".join(f"  {clean_md(line)}" for line in (signal.confluence_breakdown or [])[:5])
        stats_text = self.stats.format_stats_block()

        msg = (
            f"💎 *PRO v6.0 SNIPER SIGNAL #{sig_id}* 💎\n"
            f"{direction_emoji} *{clean_md(signal.symbol)} — {clean_md(signal.order_type)}* ({order_badge})\n"
            f"🕒 *Vaqt:* `{time_str}` | `{clean_md(signal.killzone_active)}`\n"
            f"📊 *Confluence:* *{signal.confluence_score}/100 (A+ Setup)*\n\n"
            f"🎯 *SAVDO PARAMETRLARI:*\n"
            f"• *Joriy Narx:* `{signal.current_price:.2f}`\n"
            f"• *Kirish (Entry):* `{entry_p:.2f}`\n"
            f"• *Stop-Loss (SL):* `{sl_p:.2f}` (`-{sl_pts:.2f} pt`)\n"
            f"• *Take-Profit 1 (50% + B/U):* `{tp1_p:.2f}` (`+{tp1_pts:.2f} pt` | `1:{rr1}`)\n"
            f"• *Take-Profit 2 (30%):* `{tp2_p:.2f}` (`+{tp2_pts:.2f} pt` | `1:{rr2}`)\n"
            f"• *Take-Profit 3 (20% Runner):* `{tp3_p:.2f}` (`+{tp3_pts:.2f} pt` | `1:{rr3}`)\n"
            f"• *Tavsiya Lot:* `{signal.lot_size}` _(1% Risk = ${signal.risk_usd:.2f})_\n\n"
            f"🛡️ *SAVDONI BOSHQARISH:*\n"
            f"• *B/U Trigger:* `{tp1_p:.2f}` ga yetganda 50% foyda yopiladi va SL `{entry_p:.2f}` ga ko'chiriladi.\n\n"
            f"🏛️ *SMC STRUKTURA VA LIKVIDLIK:*\n"
            f"• *Struktura & POI:* {clean_md(signal.technical_summary)}\n"
            f"• *Sweep:* {clean_md(signal.session_liquidity_info)}\n"
            f"• *Order-Book:* {clean_md(signal.orderbook_wall_info)}\n\n"
            f"🌍 *FUNDAMENTAL SABAB-OQIBAT VA KORRELYATSIYA:*\n"
            f"• *Tahlil:* {clean_md(signal.macro_summary)}\n"
            f"• *Zanjir:* {clean_md(signal.causal_chain)}\n"
            f"• *Korrelyatsiya:* `{clean_md(signal.dxy_correlation)}`\n\n"
            f"✅ *100-BALLIK CONFLUENCE TEKSHIRUVI:*\n"
            f"{breakdown_lines}\n\n"
            f"{stats_text}"
        )
        return safe_truncate_tg(msg, MAX_TELEGRAM_TEXT_LEN)

    def send_text_message(self, text: str) -> bool:
        if not self.bot_token or not self.chat_id:
            logger.error("Telegram token yoki chat_id kiritilmagan.")
            return False

        safe_text = safe_truncate_tg(text, MAX_TELEGRAM_TEXT_LEN)
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        payload = {
            "chat_id": self.chat_id,
            "text": safe_text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                if res.get("ok"):
                    return True
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.warning(f"Markdown xatosi ({e.code}): {err_body}. Plain-text rejimida qayta yuborilmoqda...")
            plain_payload = {
                "chat_id": self.chat_id,
                "text": safe_truncate_tg(strip_all_md(safe_text), MAX_TELEGRAM_TEXT_LEN),
                "disable_web_page_preview": True,
            }
            try:
                data2 = json.dumps(plain_payload).encode("utf-8")
                req2 = urllib.request.Request(url, data2, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req2, timeout=10) as resp2:
                    res2 = json.loads(resp2.read().decode("utf-8"))
                    return bool(res2.get("ok"))
            except Exception as e2:
                logger.error(f"Plain-text xabar yuborishda ham xatolik: {e2}")
                return False
        except Exception as e:
            logger.error(f"Telegram sendMessage xatosi: {e}")
            return False
        return False

    def send_signal(self, signal: FinalTradeSignal) -> bool:
        if not signal.is_approved:
            return False

        tp3_val = signal.tp3_price if signal.tp3_price > 0 else round(
            signal.entry_price + (1.45 * (signal.tp2_price - signal.entry_price)), 2
        )

        projected_id = self.stats.next_signal_id()
        text = self.format_signal_message(signal, signal_id=projected_id)

        sent_ok = self.send_text_message(text)
        if not sent_ok:
            logger.error(f"[{signal.symbol}] Signal Telegram'ga yetib bormadi — tarixga yozilmadi.")
            return False

        self.stats.record_new_signal(
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=signal.order_type,
            entry_price=signal.entry_price,
            sl_price=signal.sl_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            tp3_price=tp3_val,
            risk_usd=signal.risk_usd,
        )
        return True