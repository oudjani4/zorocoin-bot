# Zoro Airdrop Bot 🥷

بوت تليجرام + Mini App لتجميع نقاط Zoro (نظام Idle Mining) على شبكة TON، مع توزيع فعلي للتوكن لاحقًا حسب رصيد كل مستخدم.

## المكونات
- `bot/` — بوت تليجرام (aiogram) اللي بيفتح الـ Mini App.
- `backend/` — API بـ FastAPI + PostgreSQL: يدير المستخدمين، المهام، التعدين، الإحالة.
- `webapp/` — واجهة الـ Mini App (HTML/CSS/JS) مع TonConnect لربط المحفظة.
- `scripts/distribute_tokens.py` — سكريبت التوزيع الفعلي للتوكن (يشتغل بعد الإطلاق).

## خطوات التشغيل

### 1. جهّز قاعدة البيانات
```bash
# مثال باستخدام PostgreSQL محلي
createdb zoro_airdrop
```

### 2. إعداد المتغيرات البيئية
```bash
cp .env.example .env
```
عدّل القيم في `.env`:
- `BOT_TOKEN`: خده من [@BotFather](https://t.me/BotFather)
- `DATABASE_URL`: بيانات اتصال Postgres بتاعتك
- `WEBAPP_URL`: رابط HTTPS للـ webapp (استخدم [ngrok](https://ngrok.com) وقت التجربة المحلية: `ngrok http 8080`)
- `REQUIRED_CHANNELS`: يوزرات القنوات المطلوب الاشتراك فيها (مفصولة بفاصلة)
- `TONCONNECT_MANIFEST_URL`: رابط ملف `tonconnect-manifest.json` بعد رفعه

**مهم جدًا:** البوت لازم يكون **أدمن** في كل قناة مطلوب التحقق منها، وإلا مش هيقدر يتحقق من اشتراك المستخدمين.

### 3. تثبيت المكتبات
```bash
python -m venv venv
source venv/bin/activate  # أو venv\Scripts\activate على ويندوز
pip install -r requirements.txt
```

### 4. تشغيل الـ Backend
```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 5. استضافة ملفات الـ webapp
لازم تتاح على HTTPS (مطلب أساسي من تليجرام وTonConnect). محليًا:
```bash
cd webapp
python -m http.server 8080
# افتح تانل عليه بـ ngrok: ngrok http 8080
```
حدّث `WEBAPP_URL` و`TONCONNECT_MANIFEST_URL` في `.env` بالرابط اللي طلع من ngrok.

في `app.js`، عدّل:
```js
const API_BASE = "https://your-backend-domain.com"; // رابط الـ backend
const MANIFEST_URL = "https://yourdomain.com/webapp/tonconnect-manifest.json";
```
وفي نفس الملف عدّل `botUsername` في دالة `render()` لاسم يوزر بوتك (لروابط الإحالة).

### 6. تشغيل البوت
```bash
cd bot
python bot.py
```

### 7. جرّب
افتح البوت في تليجرام واضغط `/start`.

## خطوة التوزيع الفعلي (بعد إطلاق التوكن)

السكريبت شغال بنظام "دفعات" (batches) قابلة للاستئناف — كل دفعة ليها اسم
(`--label`)، وبيتم تجميد المبالغ في قاعدة البيانات وقت المراجعة، عشان أي
تنفيذ فعلي بعد كده يوزع بالظبط نفس الأرقام اللي راجعتها.

1. اطلق الـ Jetton بتاعك على TON وسجّل الـ Master Address.
2. جهّز محفظة التوزيع، واحفظ الـ mnemonic بتاعها في `.env` كـ
   `DISTRIBUTION_WALLET_MNEMONIC` (لحظة تشغيل فعلي بس، امسحه بعدها لو ينفع).
3. **جرّب على testnet الأول** بمبالغ تافهة وتأكد إن كل حاجة شغالة قبل ما تلمس
   مبالغ حقيقية.
4. شغّل وضع المراجعة (dry-run):
   ```bash
   python scripts/distribute_tokens.py --label "2026-08-26" --jetton-master EQxxx...
   ```
   ده هيطلعلك ملف `scripts/distribution_list_2026-08-26.csv` فيه كل
   المستحقين ومبالغهم، من غير أي تحويل فعلي.
5. راجع القايمة بالكامل، بالعين.
6. نفّذ فعليًا بنفس الـ label:
   ```bash
   python scripts/distribute_tokens.py --label "2026-08-26" --jetton-master EQxxx... --execute
   ```
   - كل تحويل بينسجل حاله ("success"/"failed") فورًا في قاعدة البيانات.
   - لو السكريبت وقع أو اتقفل في النص، **شغّله تاني بنفس `--label`**: هيتخطى
     المستخدمين اللي خلصوا فعلًا ويكمل من اللي فاضل بس.
   - لما تخلص، شيك على `scripts/distribution.log` وعلى أي سجلات `status=failed`
     عشان تعرف مين محتاج مراجعة يدوية (مثلاً عنوان محفظة غلط).

## تبويب Miner (نظام المستويات)
100 مستوى، كل ترقية بتتدفع TON حقيقي عبر TonConnect لمحفظة الخزينة
(`TREASURY_WALLET_ADDRESS` في `.env` — لازم تكون محفظتك انت، **مختلفة** عن
محفظة التوزيع اللي في `scripts/distribute_tokens.py`).

- سعر الترقية من مستوى L لـ L+1 = `LEVEL_BASE_PRICE_TON + (L-1) × LEVEL_PRICE_INCREMENT_TON`
  → بالديفولت: 1، 1.5، 2، 2.5... لحد 50 TON عند آخر مستوى.
- معدل التعدين بيزيد `LEVEL_MINING_RATE_INCREMENT` (ديفولت 10 ZORO/ساعة) كل مستوى.
- الآلية: الواجهة بتاخد بيانات الدفع من `/api/levels/upgrade/start`، تبعت معاملة
  TON حقيقية بتعليق فريد، وبعدين `/api/levels/upgrade/verify` بيتأكد إن المعاملة
  دي فعلاً وصلت على شبكة TON (عن طريق toncenter) قبل ما يرفّع مستوى المستخدم —
  عشان محدش يقدر يزوّر ترقية من غير ما يدفع فعليًا.
- ⚠️ **جرّب على testnet الأول** (`TONCENTER_BASE_URL=https://testnet.toncenter.com/api/v2`)
  وتأكد إن شكل استجابة toncenter (مكان الـ comment جوه `in_msg`) لسه مطابق
  للكود قبل ما تشغّله على مبالغ حقيقية — توثيق الـ API بيتغير بين الحين والتاني.

## نقاط أمان مهمة
- التحقق من `initData` (في `backend/auth.py`) بيمنع أي حد يزور هويته كمستخدم تاني.
- في حماية بسيطة من السبام (فاصل زمني بين كل تجميع، وسقف يومي) — لو حبيت تشدد الحماية أكتر ضيف rate limiting على مستوى IP كمان.
- **متنساش:** التوزيع الفعلي للتوكن عملية لا رجعة فيها. اعمل مراجعة كاملة للقايمة قبل التنفيذ.
