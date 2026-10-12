# راهنمای آپدیت روی VPS — NewsBot v4

این سند مسیر **رسمی و بازگشت‌پذیر** آپدیت روی VPS را می‌دهد. کوتاه‌ترین حالت: **روش ۱ (خودکار)**.
اگر می‌خواهید کنترل دستی داشته باشید: **روش ۲**.

> **مسیر نصب پیش‌فرض:** `/opt/bot-ai-automatic` · **سرویس systemd:** `bot-ai-automatic`
> اگر مسیر شما فرق دارد، در دستورات جایگزین کنید.

---

## ⚠️ پیش‌نیاز طلایی: بکاپ قبل از هر چیز

کد تغییرناپذیر است ولی **دادهٔ شما نیست**. اگر بک‌اند SQLite دارید، اول بکاپ بگیرید:

```bash
cd /opt/bot-ai-automatic
mkdir -p /opt/backups
cp newsbot.db "/opt/backups/newsbot-manual-$(date +%Y%m%d-%H%M%S).db"
ls -lh /opt/backups/ | tail -3
```

> اگر بک‌اند **D1** است، `newsbot.db` محلی وجود ندارد (داده روی Cloudflare است)؛ این مرحله را رد کنید.
> D1 خودش نسخه‌بندی/point-in-time recovery دارد.

---

## روش ۱ — آپدیت خودکار (توصیه‌شده) ✅

با یک `push` به `main`، ورک‌فلوی `deploy-vps.yml` این کارها را **به‌ترتیب** انجام می‌دهد:

1. بکاپ خودکار دیتابیس (۷ نسخهٔ آخر نگه داشته می‌شود)
2. `git fetch origin main` + `git reset --hard origin/main`
3. `pip install -r requirements.txt`
4. **گیت سلامت:** `python run.py --check` — اگر پیکربندی/اسکیما خراب باشد، **سرویس قدیمی دست‌نخورده می‌ماند**
5. `systemctl restart bot-ai-automatic` + تأیید `is-active`

پس فقط این را اجرا کنید (از روی ماشین خودتان):

```bash
git push origin main
```

و بعد وضعیت را ببینید:

```bash
# روی VPS
systemctl status bot-ai-automatic --no-pager
journalctl -u bot-ai-automatic -n 50 --no-pager
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

اگر CI/CD را نمی‌خواهید، همین ۵ گام را خودتان اجرا کنید:

```bash
cd /opt/bot-ai-automatic

# ۰) بکاپ
mkdir -p /opt/backups
[ -f newsbot.db ] && cp newsbot.db "/opt/backups/newsbot-$(date +%Y%m%d-%H%M%S).db"
ls -1t /opt/backups/newsbot-*.db 2>/dev/null | tail -n +8 | xargs -r rm --

# ۱) گرفتن کد جدید
git fetch origin main
git reset --hard origin/main

# ۲) وابستگی‌ها
./.venv/bin/pip install -r requirements.txt

# ۳) گیت سلامت (اگر این fail شود، متوقف شوید و restart نکنید)
./.venv/bin/python run.py --check

# ۴) راه‌اندازی مجدد
systemctl restart bot-ai-automatic
sleep 3
systemctl is-active --quiet bot-ai-automatic && echo "✅ ربات فعال است" || echo "❌ بالا نیامد — لاگ را ببینید"
```

### بررسی نهایی

```bash
systemctl status bot-ai-automatic --no-pager
journalctl -u bot-ai-automatic -f      # خروج با Ctrl+C
```

در لاگ باید این خط را ببینید:
```
INFO | core v4 ready | backend=sqlite | CF KV: local | super: {…} | AI×… FETCH×… CYCLE×…
```

---

## 🛑 اگر بعد از آپدیت بالا نیامد (Rollback)

چون `git reset --hard` و بکاپ داریم، بازگشت ساده است:

```bash
cd /opt/bot-ai-automatic

# ۱) برگشت کد به کامیت قبلی
git log --oneline -5              # کامیت سالم قبلی را پیدا کنید
git reset --hard <COMMIT_SHA>

# ۲) وابستگی‌ها را برگردانید (در صورت تغییر)
./.venv/bin/pip install -r requirements.txt

# ۳) اگر دیتابیس آسیب دیده، از بکاپ برگردانید
systemctl stop bot-ai-automatic
cp /opt/backups/newsbot-<TIMESTAMP>.db newsbot.db
systemctl start bot-ai-automatic
```

> 💡 قبل از `git reset --hard`، کامیت فعلی را یادداشت کنید (`git rev-parse HEAD`) تا اگر لازم شد
> بتوانید جلو هم بروید.

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
./.venv/bin/python -m pytest -q                             # باید 161 تست پاس شود
./.venv/bin/python tools/final_verification.py              # باید 17/17 بدهد

# گیت سلامت با env واقعی خودتان
cp /opt/bot-ai-automatic/.env .
./.venv/bin/python run.py --check
```

اگر همه سبز بود، با خیال راحت روش ۱ یا ۲ را اجرا کنید.

---

## اختصاصی: آیا این آپدیت نیاز به کار اضافی دارد؟

| موضوع | آیا نیاز به اقدام دارد؟ | توضیح |
|---|---|---|
| **اسکیمای دیتابیس** | ❌ خیر | migration جدیدی لازم نیست؛ `init_core()` هنگام استارت خودش اعمال می‌کند |
| **`.env`** | ❌ خیر | فایل شما در `.gitignore` است و `reset --hard` به آن دست نمی‌زند |
| **داده‌های موجود** | ❌ خیر | هیچ حذف/تغییر داده‌ای در این آپدیت نیست |
| **TTL ۷۲ ساعت** | ⚙️ اختیاری | پیش‌فرض جدید ۷۲ است؛ فقط اگر در `.env` مقدار ۴۸ گذاشته‌اید و می‌خواهید عوض شود |
| **بک‌اند D1** | ⚙️ توجه | حالا هر تراکنش **یک batch اتمیک** است — مصرف API کمی بالا می‌رود ولی سازگاری داده تضمین می‌شود |
| **محتوای منتشرشده** | ❌ خیر | دیگر هرگز پاک نمی‌شود (تصمیم C4) |

### اگر می‌خواهید TTL را هم‌تراز کنید

در `/opt/bot-ai-automatic/.env`:

```bash
DATA_TTL_HOURS=72
DEDUP_TTL_HOURS=72
```

سپس `systemctl restart bot-ai-automatic`.

---

## چک‌لیست سریع (کپی کنید)

```bash
# ── بکاپ ──
cd /opt/bot-ai-automatic && mkdir -p /opt/backups \
  && [ -f newsbot.db ] && cp newsbot.db "/opt/backups/newsbot-$(date +%Y%m%d-%H%M%S).db"

# ── آپدیت ──
git fetch origin main && git reset --hard origin/main \
  && ./.venv/bin/pip install -r requirements.txt \
  && ./.venv/bin/python run.py --check \
  && systemctl restart bot-ai-automatic && sleep 3 \
  && systemctl is-active bot-ai-automatic
```

خروجی خط آخر باید `active` باشد.

---

## 🔍 عیب‌یابی

| نشانه | علت احتمالی | کار |
|---|---|---|
| `⛔ راه‌اندازی متوقف شد — BOT_TOKEN تنظیم نشده` | `.env` نیست یا خوانده نمی‌شود | مطمئن شوید در `/opt/bot-ai-automatic/.env` است و سرویس با `WorkingDirectory` درست اجرا می‌شود |
| `run.py --check` fail می‌شود | پیکربندی ناقص (توکن/ادمین/D1) | خطای دقیق چاپ می‌شود — همان را رفع کنید. **سرویس قدیمی هنوز سالم است** |
| سرویس `active` است ولی پستی منتشر نمی‌شود | قبلاً باگ P0 (H8/H9/H10) همین بود | این آپدیت رفعش کرده. با `journalctl` دنبال `core v4 ready` بگردید |
| خطای `database is locked` | دو نمونه هم‌زمان اجرا شده | `flock` باید جلوگیری کند؛ `ps aux \| grep run.py` را ببینید |
| تغییرات git را نمی‌بینم | روی شاخهٔ درست نیستید | `git branch --show-current` باید `main` باشد |

---

## 📌 نکتهٔ مهم دربارهٔ `.gitignore`

این آپدیت فایل `.gitignore` را اضافه می‌کند که از `.env` و `newsbot.db` محافظت می‌کند.
اگر روی VPS فایل `.env` تعقیب‌شده (tracked) داشتید، `git reset --hard` **ممکن است** آن را تغییر دهد.
قبل از آپدیت مطمئن شوید بکاپ دارید:

```bash
cp /opt/bot-ai-automatic/.env "/opt/backups/.env-$(date +%Y%m%d-%H%M%S)"
```
