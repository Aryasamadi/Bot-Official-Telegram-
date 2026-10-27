# راهنمای آپدیت روی VPS — NewsBot v4

این سند مسیر **رسمی و بازگشت‌پذیر** آپدیت روی VPS را می‌دهد. کوتاه‌ترین حالت: **روش ۱ (خودکار)**.
اگر می‌خواهید کنترل دستی داشته باشید: **روش ۲**.

> **مسیر نصب پیش‌فرض:** `/opt/bot-official-telegram` · **سرویس systemd:** `newsbot`
> اگر مسیر شما فرق دارد، در دستورات جایگزین کنید.

---

## ⚠️ پیش‌نیاز طلایی: بکاپ قبل از هر چیز

کد تغییرناپذیر است ولی **دادهٔ شما نیست**. اگر بک‌اند SQLite دارید، اول بکاپ بگیرید:

```bash
cd /opt/bot-official-telegram
mkdir -p /opt/backups
# مسیر دیتابیس از DB_FILE در .env خوانده می‌شود (همان منبعی که ربات و deploy.sh می‌خوانند)
DB="$(sed -n 's/^DB_FILE=//p' .env | tail -n1)"; DB="${DB:-newsbot.db}"
case "$DB" in /*) ;; *) DB="/opt/bot-official-telegram/$DB" ;; esac
python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' \
  "$DB" "/opt/backups/newsbot-manual-$(date +%Y%m%d-%H%M%S).db"
ls -lh /opt/backups/ | tail -3
```

> مسیر دیتابیس **حدس زده نمی‌شود**: `DB_FILE` در `.env` منبعِ واحد است (روی هاست فعلی:
> `/opt/bot-official-telegram/data/newsbot.db`). اگر بک‌اند **D1** است اصلاً فایل محلی
> SQLite وجود ندارد (داده روی Cloudflare است)؛ این مرحله را رد کنید.
> D1 خودش نسخه‌بندی/point-in-time recovery دارد.

---

## روش ۱ — آپدیت خودکار (توصیه‌شده) ✅

با یک `push` به `main`، ورک‌فلوی `deploy-vps.yml` این کارها را **به‌ترتیب** انجام می‌دهد:

1. کپی `deploy/deploy.sh` روی VPS و اجرای `deploy.sh deploy <SHA>`
2. بررسی تمیز بودن working tree؛ تغییر محلی (tracked/staged/untracked) ⇒ توقف قبل از هر تغییری
3. بکاپ آنلاین دیتابیس (۷ نسخهٔ آخر نگه داشته می‌شود)
4. همگام‌سازی ایمن با `git reset --keep` + نصب وابستگی‌ها
5. **گیت سلامت:** `run.py --check` — اگر پیکربندی/اسکیما خراب باشد، **سرویس قدیمی دست‌نخورده می‌ماند**
6. restart + دروازهٔ سلامت؛ اگر پس از sync شکستی رخ دهد، کد به SHA قبلی برمی‌گردد و سرویس روی کد قبلی restart می‌شود

پس فقط این را اجرا کنید (از روی ماشین خودتان):

```bash
git push origin main
```

و بعد وضعیت را ببینید:

```bash
# روی VPS
systemctl status newsbot --no-pager
journalctl -u newsbot -n 50 --no-pager
```

### تنظیم یک‌بارهٔ Secrets (فقط اگر قبلاً نکرده‌اید)

در GitHub → **Settings → Secrets and variables → Actions** این دو را بسازید:

| نام Secret | مقدار |
|---|---|
| `VPS_HOST` | IP یا دامنهٔ VPS |
| `VPS_SSH_PRIVATE_KEY` | کلید خصوصی SSH (robots با دسترسی root) |

کلید عمومی متناظر باید در `/root/.ssh/authorized_keys` روی VPS باشد.

---

## روش ۲ — آپدیت دستی روی VPS

اگر CI/CD را نمی‌خواهید، همان اسکریپت رسمی را دستی و با دسترسی **root** روی VPS اجرا کنید:

```bash
cd /opt/bot-official-telegram

# ۰) بکاپ دستی اختیاری — deploy.sh خودش بکاپ آنلاین WAL-safe می‌گیرد
mkdir -p /opt/backups
DB="$(sed -n 's/^DB_FILE=//p' .env | tail -n1)"; DB="${DB:-newsbot.db}"
case "$DB" in /*) ;; *) DB="/opt/bot-official-telegram/$DB" ;; esac
python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' \
  "$DB" "/opt/backups/newsbot-$(date +%Y%m%d-%H%M%S).db"

# ۱) استقرار رسمی: بررسی working tree + sync ایمن + وابستگی‌ها + gate + restart + health
sudo ./deploy/deploy.sh deploy <GIT_COMMIT_SHA>
```

> `deploy.sh` پیش از هر تغییری working tree را با `git status --porcelain` بررسی می‌کند؛
> اگر تغییر محلی (tracked/staged/untracked) باشد **بدون هیچ تغییری متوقف می‌شود**.
> سپس با `git reset --keep` همگام می‌شود، `HEAD` را با `rev-parse` تأیید می‌کند،
> وابستگی‌ها را نصب و `run.py --check` را اجرا می‌کند، سرویس را restart و دروازهٔ
> سلامت را می‌سنجد. شکست پس از sync یعنی بازگشت خودکار کد به SHA قبلی و restart
> سرویس روی همان کد. اجرای `sudo -u botapp` معتبر نیست، چون restart و health به root
> نیاز دارند.

### بررسی نهایی

```bash
systemctl status newsbot --no-pager
journalctl -u newsbot -f      # خروج با Ctrl+C
```

در لاگ باید این خط را ببینید:
```
INFO | core v4 ready | backend=sqlite | CF KV: local | super: {…} | AI×… FETCH×… CYCLE×…
```

---

## 🛑 اگر بعد از آپدیت بالا نیامد (Rollback)

مسیر رسمی در **`docs/VPS_DEPLOYMENT.md` §۵** مستند شده است و باید با دسترسی **root**
اجرا شود:

```bash
cd /opt/bot-official-telegram
sudo ./deploy/deploy.sh rollback
```

`rollback` مقدار `.deployed_sha.prev` را می‌خواند، دوباره working tree را بررسی
می‌کند (تغییر محلی ⇒ توقف)، با `git reset --keep` جابه‌جا می‌شود و `HEAD` را با
`rev-parse` تأیید می‌کند، سپس سرویس را restart و همان دروازهٔ سلامت را اجرا می‌کند.
شکست restart/health یعنی خروج غیرصفر و «rollback موفق» اعلام نمی‌شود. تکرارش
idempotent است؛ اگر `.deployed_sha.prev` نبود، بدون تغییر متوقف می‌شود.

> اگر `deploy.sh` پس از sync شکست خورده باشد، کد را به SHA قبلی برگردانده و
> سرویس را روی همان کد restart کرده است؛ ابتدا خروجی اسکریپت و ژورنال را ببینید.

**بازگشت دستی محافظه‌کارانه (فقط اگر مسیر رسمی مناسب نیست):**

```bash
cd /opt/bot-official-telegram
git status --porcelain            # اگر خروجی دارد، تغییرات را commit یا stash کنید
git rev-parse HEAD                # SHA فعلی را برای بازگشت به جلو یادداشت کنید
git reset --keep <COMMIT_SHA>     # ایمن: تغییرات محلی حفظ می‌شوند
./venv/bin/pip install -r requirements.txt
./venv/bin/python run.py --check  # اگر fail شد، restart نکنید
systemctl restart newsbot
systemctl status newsbot --no-pager
journalctl -u newsbot --since "5 min ago" --no-pager
```

اگر دیتابیس آسیب دیده، بعد از rollback کد و **با سرویس متوقف** بکاپ اخیر را
برگردانید (همان مسیری که `.env` اعلام می‌کند)، سپس `systemctl start newsbot`:

```bash
systemctl stop newsbot
DB="$(sed -n 's/^DB_FILE=//p' .env | tail -n1)"; DB="${DB:-newsbot.db}"
case "$DB" in /*) ;; *) DB="/opt/bot-official-telegram/$DB" ;; esac
python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' \
  /opt/backups/newsbot-<TIMESTAMP>.db "$DB"
systemctl start newsbot
```

---

## 🧪 آپدیت در حالت «آزمایشی» (Dry-run) — بدون قطع سرویس

اگر می‌خواهید **اول در یک پوشهٔ جدا** صحت کد جدید را بسنجید، بدون دست‌زدن به سرویس فعال:

```bash
cd /tmp
rm -rf newsbot-test
git clone https://github.com/Aryasamadi/Bot-Official-Telegram-.git newsbot-test
cd newsbot-test
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# گیت‌های کیفیت (همان‌هایی که CI اجرا می‌کند)
./.venv/bin/python -m pyflakes newsbot tools run.py tests   # باید exit 0 باشد
./.venv/bin/python -m pytest -q                             # باید همهٔ تست‌ها پاس شوند
./.venv/bin/python tools/final_verification.py              # باید 17/17 بدهد

# گیت سلامت با env واقعی خودتان
cp /opt/bot-official-telegram/.env .
./.venv/bin/python run.py --check
```

اگر همه سبز بود، با خیال راحت روش ۱ یا ۲ را اجرا کنید.

---

## اختصاصی: آیا این آپدیت نیاز به کار اضافی دارد؟

| موضوع | آیا نیاز به اقدام دارد؟ | توضیح |
|---|---|---|
| **اسکیمای دیتابیس** | ❌ خیر | migration جدیدی لازم نیست؛ `init_core()` هنگام استارت خودش اعمال می‌کند |
| **`.env`** | ❌ خیر | فایل شما در `.gitignore` است و همگام‌سازی ایمن (`git reset --keep`) به آن دست نمی‌زند |
| **داده‌های موجود** | ❌ خیر | هیچ حذف/تغییر داده‌ای در این آپدیت نیست |
| **TTL ۷۲ ساعت** | ⚙️ اختیاری | پیش‌فرض جدید ۷۲ است؛ فقط اگر در `.env` مقدار ۴۸ گذاشته‌اید و می‌خواهید عوض شود |
| **بک‌اند D1** | ⚙️ توجه | حالا هر تراکنش **یک batch اتمیک** است — مصرف API کمی بالا می‌رود ولی سازگاری داده تضمین می‌شود |
| **محتوای منتشرشده** | ❌ خیر | دیگر هرگز پاک نمی‌شود (تصمیم C4) |

### اگر می‌خواهید TTL را هم‌تراز کنید

در `/opt/bot-official-telegram/.env`:

```bash
DATA_TTL_HOURS=72
DEDUP_TTL_HOURS=72
```

سپس `systemctl restart newsbot`.

---

## چک‌لیست سریع (کپی کنید)

```bash
# ── بکاپ ──
cd /opt/bot-official-telegram && mkdir -p /opt/backups
DB="$(sed -n 's/^DB_FILE=//p' .env | tail -n1)"; DB="${DB:-newsbot.db}"
case "$DB" in /*) ;; *) DB="/opt/bot-official-telegram/$DB" ;; esac
python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' \
  "$DB" "/opt/backups/newsbot-$(date +%Y%m%d-%H%M%S).db"

# ── آپدیت رسمی (root) ──
sudo ./deploy/deploy.sh deploy <GIT_COMMIT_SHA>
sudo systemctl is-active newsbot
```

خروجی خط آخر باید `active` باشد.

---

## 🔍 عیب‌یابی

| نشانه | علت احتمالی | کار |
|---|---|---|
| `⛔ راه‌اندازی متوقف شد — BOT_TOKEN تنظیم نشده` | `.env` نیست یا خوانده نمی‌شود | مطمئن شوید در `/opt/bot-official-telegram/.env` است و سرویس با `WorkingDirectory` درست اجرا می‌شود |
| `run.py --check` fail می‌شود | پیکربندی ناقص (توکن/ادمین/D1) | خطای دقیق چاپ می‌شود — همان را رفع کنید. **سرویس قدیمی هنوز سالم است** |
| سرویس `active` است ولی پستی منتشر نمی‌شود | قبلاً باگ P0 (H8/H9/H10) همین بود | این آپدیت رفعش کرده. با `journalctl` دنبال `core v4 ready` بگردید |
| خطای `database is locked` | دو نمونه هم‌زمان اجرا شده | `flock` باید جلوگیری کند؛ `ps aux \| grep run.py` را ببینید |
| تغییرات git را نمی‌بینم | روی شاخهٔ درست نیستید | `git branch --show-current` باید `main` باشد |

---

## 📌 نکتهٔ مهم دربارهٔ `.gitignore`

این آپدیت فایل `.gitignore` را اضافه می‌کند که از `.env` و `newsbot.db` محافظت می‌کند.
اگر روی VPS فایل `.env` تعقیب‌شده (tracked) داشتید، `deploy.sh` به‌دلیل بررسی
working tree و استفاده از `git reset --keep` تغییرات محلی را بازنویسی نمی‌کند؛ در
این حالت استقرار پیش از هر تغییری متوقف می‌شود. قبل از آپدیت مطمئن شوید بکاپ دارید:

```bash
cp /opt/bot-official-telegram/.env "/opt/backups/.env-$(date +%Y%m%d-%H%M%S)"
```
