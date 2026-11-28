# OPS — چرخشِ راز (`BOT_TOKEN` و `SECRETS_KEY`)

سندِ عملیاتیِ فاز ۱۰ (بستنِ **AUD-001**). این سند را **مالکِ سیستم** اجرا می‌کند؛
عامل فقط ابزار را تحویل داده است. هیچ مقدارِ رازی در این سند، در log، در commit یا در
artifact نیست — فقط **نامِ** متغیرها و **شمارش**.

---

## ۱. دامنه و آنچه انجام **نمی‌شود**

- **در دامنه:** فقط `BOT_TOKEN` و `SECRETS_KEY`.
- **خارج از دامنه:** کلیدهای Provider (`ai_providers.api_key` / `ai_models.api_key` از
  دیدِ مالک) **بازصدور نمی‌شوند**. ریسکِ باقی‌مانده با شناسهٔ **D-13** در فایلِ وضعیت
  (`docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md` §8) ثبت شده است.
- **خارج از دامنه:** رمزنگاریِ `sources.api_key` (متنی) — **D-12**.
- چرخشِ راز **پیش‌شرطِ حذفِ فایلِ متنی** است: تا وقتی چرخش کامل و تأیید نشده، فایلِ
  `newsbot-deploy-pkg\newsbot.env` **دست‌نخورده** می‌ماند (تصمیم **C8**).

## ۲. پیش‌شرط‌ها

| # | پیش‌شرط | بررسی |
|---|---|---|
| 1 | پنجرهٔ نگهداری (maintenance window) اعلام شده و downtime پذیرفته شده است | تأییدِ مالک |
| 2 | بکاپِ کاملِ دیتابیس گرفته شده (گام ۳) | `PRAGMA integrity_check` = `ok` |
| 3 | venvِ سرویس: `/opt/newsbot/.venv` (همان venvِ اجرای سرویس) | `ls /opt/newsbot/.venv/bin/python` |
| 4 | `cryptography` در همان venv نصب است | `/opt/newsbot/.venv/bin/python -c "import cryptography"` |
| 5 | `OLD_SECRETS_KEY` (کلیدِ کنونی) و `SECRETS_KEY` (کلیدِ نو) در shell **بدون echo** صادر شده‌اند | `echo ${#SECRETS_KEY}` فقط **طول** را نشان می‌دهد |
| 6 | checkoutِ مخزن با `tools/` روی همان میزبان موجود است | `ls /opt/newsbot/tools/rotate_secrets_key.py` |

> ابزار **فقط** از محیط می‌خواند: `OLD_SECRETS_KEY` و `SECRETS_KEY`. هرگز کلید را به
> argv ندهید، هرگز در فایل بنویسید، هرگز paste نکنید.

## ۳. توقفِ سرویس و بکاپِ دیتابیس

```bash
# ۱) سرویس را متوقف کنید تا هیچ نوشتنی در جریان نباشد
cd /opt/newsbot
sudo systemctl stop newsbot      # یا: docker compose stop newsbot

# ۲) بکاپ (شامل WAL/SHM)
DB=/opt/newsbot/data/newsbot.db          # مسیر واقعی را با محیط تطبیق دهید
BAK=/opt/newsbot/backups/newsbot-$(date -u +%Y%m%dT%H%M%SZ).db
mkdir -p "$(dirname "$BAK")"
sqlite3 "$DB" ".backup '$BAK'"            # بکاپِ سازگار، حتی با WAL فعال
ls -l "$DB" "$DB-wal" "$DB-shm" 2>/dev/null || true   # وجودِ فایل‌های WAL/SHM را ثبت کنید

# ۳) صحتِ بکاپ
sqlite3 "$BAK" "PRAGMA integrity_check;"     # باید دقیقاً «ok» باشد
```

- اگر `sqlite3` نصب نیست، از همان venv استفاده کنید:
  `/opt/newsbot/.venv/bin/python -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('PRAGMA integrity_check').fetchone()[0])" "$BAK"`
- **بکاپ باید تازه‌تر از دیتابیسِ زنده باشد**؛ ابزار این را خودش می‌سنجد (وگرنه exit 2).
- بکاپ را **خارجِ** دیتابیسِ زنده نگه دارید (همان فایل قابلِ قبول نیست ⇒ exit 2).

## ۴. چرخش با ابزار (`--check` سپس `--apply`)

ابزار **روی میزبان اجرا می‌شود، نه داخلِ ایمیج** (ایمیج `tools/` ندارد):

```bash
cd /opt/newsbot                       # checkoutِ مخزن
export OLD_SECRETS_KEY='...'          # بدون echo
export SECRETS_KEY='...'              # بدون echo

# ۱) اعتبارسنجی بدونِ نوشتن
/opt/newsbot/.venv/bin/python tools/rotate_secrets_key.py \
    --db "$DB" --check --backup "$BAK"

# ۲) چرخشِ اتمیک
/opt/newsbot/.venv/bin/python tools/rotate_secrets_key.py \
    --db "$DB" --apply --backup "$BAK"
```

خروجی فقط **شمارش** است (بدونِ هیچ مقدار):

```json
{"mode":"apply","rows_total":2,"rows_reencrypted":2,"rows_encrypted_plain":0,
 "rows_already_new":0,"rows_failed":0,"other_enc_v1_tables":[]}
```

| Exit | معنی | اقدام |
|---|---|---|
| `0` | موفق | ادامه به گام ۵ |
| `2` | پیش‌شرط نامعتبر (بکاپِ کهنه/همان‌فایل/نبودِ کلید/مقدارِ `enc:v1:` بیرون از دو جدول) | **هیچ چیز نوشته نشده**؛ علت را از پیامِ خطا بخوانید و اصلاح کنید |
| `3` | داده با کلیدِ قدیم باز نمی‌شود | **هیچ چیز نوشته نشده**؛ کلیدِ `OLD_SECRETS_KEY` را بررسی کنید |

- نوشتن در یک تراکنشِ `BEGIN IMMEDIATE` انجام می‌شود: یا همهٔ ردیف‌ها با کلیدِ نو، یا هیچ.
- اجرای دوباره **idempotent** است (ردیف‌های ازپیش‌جدیدشده دست‌نخورده می‌مانند).
- اگر `rows_encrypted_plain` بزرگ بود، یعنی کلیدهای متنیِ ذخیره‌شده هم رمز شده‌اند (خوب است).

## ۵. به‌روزرسانیِ env، راه‌اندازی و بررسی

```bash
# فایلِ envِ production را **بدون چاپ** ویرایش کنید (مثلاً /etc/newsbot/env)
sudoedit /etc/newsbot/env
# مقدارِ SECRETS_KEY را با کلیدِ نو جایگزین کنید؛ BOT_TOKEN را فعلاً دست نزنید (گام ۶).

sudo systemctl start newsbot
# یا: docker compose up -d newsbot

# بررسیِ پیکربندی (بدونِ چاپِ راز)
/opt/newsbot/.venv/bin/python run.py --check

# یک تستِ اتصالِ Provider (فقطِ نتیجه؛ نه کلید)
/opt/newsbot/.venv/bin/python -c "from newsbot import providers; print(providers.test_connection(<provider_id>))"
```

- اگر `run.py --check` خطا داد: **متوقف شوید** و به گام ۹ (rollback) بروید.
- اگر Provider باز نشد ولی `run.py --check` سبز بود: احتمالاً `SECRETS_KEY` اشتباه است؛
  بازگردانی کنید (گام ۹) و بعد دوباره تلاش کنید.

## ۶. چرخشِ `BOT_TOKEN` در BotFather

1. در BotFather: `/revoke` روی همان بات ⇒ **توکنِ جدید** صادر می‌شود (توکنِ قبلی از کار
   می‌افتد ⇒ ربات تا به‌روزرسانیِ env پاسخ نمی‌دهد).
2. مقدارِ جدید را در فایلِ env جایگزین کنید (`BOT_TOKEN`) — **بدون چاپ**.
3. سرویس را ری‌استارت کنید و مطمئن شوید ربات پاسخ می‌دهد (یک پیامِ `/start` از حسابِ آزمایشی).
4. **مدتِ downtime را ثبت کنید** (زمانِ `/revoke` تا زمانِ پاسخِ ربات) — این عدد در گزارشِ
   نهاییِ فاز ۱۰ می‌آید.
5. اگر ربات پاسخ نداد ⇒ rollback (گام ۹) و **BotFather** را دوباره بررسی کنید.

> کلیدهای Provider **در این گام بازصدور نمی‌شوند** (D-13).

## ۷. پاک‌سازیِ کش‌های QA و اجرای بعدی

- کش‌های قدیمیِ دیتابیسِ تست با پیشوندِ `qa-live-db-v1-` هنوز ممکن است یک کلیدِ
  رمزشده‌ی قدیمی داشته باشند ⇒ در پنجرهٔ مالک حذف شوند
  (Settings → Actions → Caches → `qa-live-db-v1-*` → Delete).
- از این پس پیشوند **`qa-live-db-v2-`** است (PR #109).
- هر اجرای بعدیِ `qa-live` که پس از چرخش انجام می‌شود باید با
  `db_mode=fresh` باشد (کشِ v1 دیگر قابلِ رمزگشایی نیست).

## ۸. حذفِ فایلِ متنی — فقط بعد از چرخشِ کامل

```text
newsbot-deploy-pkg\newsbot.env
```

- **فقط** وقتی: گام ۴ با exit 0 تمام شده، سرویس با کلیدِ نو بالا آمده، Provider کار می‌کند،
  `BOT_TOKEN` در BotFather بازصدور شده و ربات پاسخ می‌دهد.
- پیش از حذف: یک بکاپِ رمزگذاری‌شده از خودِ فایل بردارید یا آن را به یک انبارِ رمزدارِ
  موردِ اعتماد منتقل کنید (در صورتِ نیاز به بازگردانی).
- تاریخِ حذف را در فایلِ وضعیت (§8) ثبت کنید — فقط **تاریخ**، نه محتوا.
- اگر هر گامی شکست خورد ⇒ فایل **می‌ماند** و rollback انجام می‌شود.

## ۹. Rollback

```bash
sudo systemctl stop newsbot
# ۱) envِ قبلی را برگردانید (SECRETS_KEY و BOT_TOKENِ قدیمی)
sudoedit /etc/newsbot/env
# ۲) دیتابیسِ پشتِ‌بان را برگردانید
cp "$BAK" "$DB"      # پشتِ‌بانِ گام ۳
# ۳) بالا بیاورید و بررسی کنید
sudo systemctl start newsbot
/opt/newsbot/.venv/bin/python run.py --check
```

- اگر `BOT_TOKEN` در BotFather بازصدور شده باشد، توکنِ قدیمی **برگشت‌پذیر نیست**؛ در این
  حالت rollback فقط روی `SECRETS_KEY` و دیتابیس ممکن است و توکن باید **دوباره** بازصدور شود.
- پس از rollback: علت را ثبت کنید و از همان نقطه دوباره شروع کنید (نیم‌روزِ چرخش).

## ۱۰. قاعدهٔ «هرگز چاپ / paste / commit»

- مقدارِ هیچ رازی **نباید** در ترمینال چاپ شود (`echo`, `set`, `env`, `cat` روی فایلِ env).
- مقدارِ هیچ رازی **نباید** در `git`, PR, issue، log، artifact یا این سند کپی شود.
- اگر لازم است از وجودِ یک متغیر مطمئن شوید، فقط **طول** یا **نام** را گزارش کنید:
  `echo ${#SECRETS_KEY}`.
- ابزار خودش این قاعده را رعایت می‌کند: کلیدها فقط از محیط خوانده می‌شوند و خروجی‌اش
  فقط شمارش است.

---

## پیوست — مرجعِ سریع

| گام | فرمان/کنش | موفقیت |
|---|---|---|
| ۳ | `sqlite3 "$DB" ".backup '$BAK'"` + `PRAGMA integrity_check` | `ok` |
| ۴ | `tools/rotate_secrets_key.py --db "$DB" --check --backup "$BAK"` | exit 0 |
| ۴ | `tools/rotate_secrets_key.py --db "$DB" --apply --backup "$BAK"` | exit 0 |
| ۵ | `run.py --check` | exit 0 |
| ۵ | تستِ اتصالِ Provider | موفق |
| ۶ | BotFather `/revoke` + به‌روزرسانیِ env + ری‌استارت | ربات پاسخ می‌دهد |
| ۷ | حذفِ کش‌های `qa-live-db-v1-*` | کش‌ها حذف شدند |
| ۸ | حذفِ `newsbot-deploy-pkg\newsbot.env` | فقط بعد از موفقیتِ همهٔ گام‌ها |

سندِ مرتبط: `docs/RELEASE_RUNBOOK.md` (دروازهٔ انتشار) · `docs/QA_LIVE_ACCEPTANCE.md`
(اجرای زنده) · `docs/PHASE10_FINAL_REPORT.md` (گزارشِ نهایی).
