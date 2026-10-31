# 💎 PRO v5.1 INSTITUTIONAL DIAMOND SNIPER — HOTFIX AUDIT VA YANGILANISH HUJJATI

## 🚨 Nega kecha (27-Okt) va bugun (28-Okt) botdan birorta ham signal chiqmadi? (Root-Cause Audit)

Kod bazasi (`schemas.py`, `technical_agent.py`, `news_scraper.py`, `risk_manager.py`, `telegram_bot.py`, `config.py`) satrma-satr tekshirilganda, **PRO v5.0** versiyasida signallarni **100% bloklab qo'ygan 5 ta texnik va mantiqiy "qulf" (Deadlock / Bug)** aniqlandi:

1. **1-Xato (`schemas.py` vs `risk_manager.py` / `telegram_bot.py` nomuvofiqligi — Asosiy To'xtash Sababi):**
   - `PRO v5.0` da `risk_manager.py` va `telegram_bot.py` yangilangan, ammo `schemas.py` eski `PRO v4.5` holatida qolib ketgan edi (`FinalTradeSignal` va `TechnicalSetup` maydon nomlari `tp2_price`, `rr_tp1`, `rr_tp2`, `Zone_Description` bir-biriga mos kelmagan).
   - Oqibatda har 3 daqiqada `main.py` ishlaganda `TypeError` / `AttributeError` yuz berib, sikl xatolik bilan tugagan va Telegram'ga xabar yuborilmagan.
   - **PRO v5.1 Yechimi:** `schemas.py` to'liq yangilandi va barcha `PRO v5.1` hamda `v4.5/v5.0` atributlari (`@property` ko'priklari bilan) 100% moslashtirildi.

2. **2-Xato (`technical_agent.py` dagi "BOS vs Liquidity Sweep" Matematik Catch-22 Qulfi):**
   - `PRO v5.0` da `SELL` chiqishi uchun oxirgi 8 ta M15 shamcha (`w3`) **bir vaqtning o'zida** ham oxirgi 9 soatlik eng past nuqtani (`w3_low < min(w1_low, w2_low)`) yangilashi, **ham** 24 soatlik/Osiyo cho'qqisini (`w3_high >= pdh/asian_high`) 35% dum bilan supurib o'tishi va `EMA20 < EMA50` bo'lishi talab qilingan edi.
   - Haqiqiy grafikda narx tepani supurganda (Liquidity Sweep) u 9 soatlik cho'qqida bo'ladi, 9 soatlik tubda emas! Trend bo'ylab Order Block'ga qaytganda (Pullback) esa oxirgi 8 ta shamcha yuqoriga korreksiya qilayotgan bo'ladi. Shu sababli ikkala shart bir vaqtda **hech qachon (`0%`)** bajarilmagan.
   - **PRO v5.1 Yechimi:** Haqiqiy Smart Money Concepts (SMC) ning **2 ta mustaqil institutsional kirish arxetipi** ajratildi:
     - **Arxetip A (`SWEEP_REVERSAL`):** Oxirgi 14 ta M15 shamcha ichida `PDH/PDL`, `Asian High/Low` yoki `Swing High/Low` supurilib (bitta shamcha dumi YOKI 2–3 shamchali Turtle-Soup qaytishi bilan) rad etilganda.
     - **Arxetip B (`TREND_OB_PULLBACK`):** Trend va Order-Flow (`VWAP` va `EMA20/50`) aniq yo'nalishda bo'lib, narx M15 Order Block / FVG zonasiga (`in_setup_zone`) qaytganda.

3. **3-Xato (`news_scraper.py` dagi Doimiy `imminent_breaking = True` Blokirovkasi):**
   - `_fetch_fxstreet_html` funksiyasida yangilik vaqti ISO formatda (`datetime.now().isoformat()`) yozilgan, `check_imminent_high_impact_news` esa uni `parsedate_to_datetime()` (RFC-2822) orqali o'qishga uringan.
   - Har safar xatolik (`Exception`) chiqqanda `except Exception: imminent_breaking = True` ishlagan va botni **24 soat davomida** *"Yaqin 30 daqiqa ichida kuchli USD yangiligi bor"* deb bloklab qo'ygan!
   - **PRO v5.1 Yechimi:** Vaqt parseri RFC-2822 va ISO-8601 formatlarini xavfsiz o'qiydigan qilindi; vaqt shtampi bo'lmasa asossiz `True` qaytarmaydi (`False`). Faqat oxirgi 15 daqiqada chiqqan haqiqiy favqulodda AQSh/Fed/CPI/NFP flash relizlaridagina himoya yoqiladi.

4. **4-Xato (`telegram_bot.py` va `main.py` da Bekor Qilingan Limit Orderlar Kunlik Limitni Yeb Qo'yishi):**
   - `get_today_trades_count()` funksiyasi ishga tushmasdan bekor bo'lgan (`CANCELLED`) limit orderlarni ham kunlik `2/2` limitga qo'shib hisoblagan va ustiga 90 daqiqalik Cooldown bergan.
   - **PRO v5.1 Yechimi:** `CANCELLED` bo'lgan pending orderlar kunlik limitga kiritilmaydi, kunlik haqiqiy savdolar limiti `4 ta` (`MAX_DAILY_TRADES_PER_SYMBOL = 4`) etib belgilandi, va bekor bo'lgan pending orderdan keyin atigi 6 daqiqalik yangilanish pauzasi beriladi.

5. **5-Xato (`config.py` dagi `16:00–17:00 UZT` va `10:00–12:00 UZT` Killzone Uzilishi):**
   - `16:00–17:00 UZT` oralig'ida (aynan `16:30 UZT` da AQSh makroiqtisodiy relizlari va Nyu-York Pre-Market likvidlik sweeplari bo'ladigan eng muhim soatda!) bot `Off-Session` bo'lib o'chib qolgan.
   - **PRO v5.1 Yechimi:** `LONDON` (`10:00–16:00 UZT`) va `NEW_YORK` (`16:00–23:00 UZT`) uzluksiz ulandi.

---

## 🛠️ VPS SERVERDA 30 SONIYADA YANGILASH BO'YICHA QO'LLANMA

Quyidagi `ai_gold_sniper_bot.zip` arxivini yoki yangilangan `.py` fayllarni VPS serveringizdagi `/opt/ai_gold_sniper` (yoki bot turgan) papkaga yuklab, quyidagi buyruqlarni bering:

```bash
cd /opt/ai_gold_sniper
unzip -o ai_gold_sniper_bot.zip
python3 main.py --reset-stats
sudo systemctl restart gold-sniper
sudo journalctl -u gold-sniper -f
```
*(Agar botni `nohup` yoki `screen`/`tmux` orqali ishlatayotgan bo'lsangiz: `pkill -f main.py && nohup python3 main.py --loop > bot_output.log 2>&1 &`)*
