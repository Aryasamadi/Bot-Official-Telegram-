# راهنمای استقرار، رول‌بک و بکاپ (فاز ۲)

این سند **واقعیتِ کد** را توصیف می‌کند: همهٔ مسیرهایش در `deploy/deploy.sh` و
`.github/workflows/` پیاده شده و با تست تمرین شده‌اند (`tests/test_deploy_script.py`).

## ۱. نمای کلی جریان

```
push به main ──▶ CI (ci.yml) ──سبز──▶ workflow_run ──▶ Deploy to VPS (deploy-vps.yml)
                   │ قرمز = deploy اصلاً اجرا نمی‌شود (OPS-01)
                   └ concurrency: لغوِ اجرای قدیمیِ همان ref (OPS-10)

Deploy: preflight (وجود secrets) ──▶ checkout ──▶ scp deploy.sh ──▶ ssh:
  deploy.sh deploy <SHA>  =  بکاپ آنلاین ← sync به SHA ← نصب ← دروازه ← restart ← سلامت
```

- **SHA محرک:** `workflow_run.head_sha` (یا `github.sha` برای اجرای دستی) — deploy.sh
  پس از `reset --hard` دوباره `rev-parse HEAD` می‌کند و با SHAِ محرک مقایسه می‌کند؛
  یعنی هیچ‌وقت «کد مستقر = کامیت محرک» نیست؟ نه — اگر نباشد، deploy متوقف می‌شود (OPS-02).
- **اجرای دستی:** Actions ← Deploy to VPS ← Run workflow (workflow_dispatch / OPS-06).
- **صف:** `concurrency: production-vps-deploy` با `cancel-in-progress: false` — دو deploy
  هم‌زمان نمی‌شوند و وسطِ کار لغو هم نمی‌شوند (OPS-05).
- **مهلت:** `timeout-minutes: 25` در job و `command_timeout: 20m` در SSH (OPS-20).

## ۲. Secrets لازم (بدون آن‌ها deploy اجرا نمی‌شود)

| Secret | مقدار |
|---|---|
| `VPS_HOST` | آدرس IP/میزبان VPS |
| `VPS_SSH_PRIVATE_KEY` | کلید خصوصی SSH (با `username: root`) |
| `VPS_HOST_FINGERPRINT` | `sha256:...` اثر انگشت میزبان — با `ssh-keyscan -t ed25519 <host>` استخراج شود |

نکته: قبلاً `secrets.VPS_HOST` خالی بود و هر ۱۵ اجرای deploy با
`Error: missing server host` شکست می‌خورد. حالا پیش‌شرطِ روشن (preflight) قبل از هر
اتصالی بررسی می‌کند و خطای خوانا می‌دهد؛ fingerprint خالی هم یعنی deploy متوقف می‌شود
(OPS-07 — بدون پین fingerprint، اتصال قابل اعتماد نیست).

## ۳. دروازه‌ها و رفتار خطاها در `deploy.sh`

| مرحله | شکست چه می‌شود؟ |
|---|---|
| preflight (git/python3/systemctl/.env/.venv) | هیچ تغییری در کد/سرویس نمی‌شود |
| بکاپ آنلاین (`sqlite3.backup` — سازگار با WAL) | قبل از هر تغییری متوقف (OPS-04/D12) |
| sync به SHA | سرویس دست‌نخورده؛ کد یا reset شده یا نه (rollback لازم نیست) |
| نصب requirements / دروازه (`compileall` + `run.py --check`) | **کد به SHA قبلی برمی‌گردد**؛ سرویس هرگز restart نشده (OPS-03) |
| سلامت بعد از restart (۱۰× فعال‌شدن + NRestarts=0 + نبود traceback تازه در ۲ دقیقهٔ اخیر ژورنال) | **کد برمی‌گردد و سرویس دوباره روی کد قبلی restart می‌شود** — رول‌بک واقعی نه فقط ادعا (OPS-15/D7) |

نشانگرها در `APP_DIR`:
- `.deployed_sha` — نسخهٔ فعلیِ مستقر (فقط پس از سلامت کامل)
- `.deployed_sha.prev` — نسخهٔ قبلی (پیش از sync نوشته می‌شود؛ مبنای rollback)

## ۴. رول‌بک

**خودکار:** هر شکستِ بعد از sync (جدول بالا) خودش کد را به SHA قبلی برمی‌گرداند.

**دستی (شامل رول‌بک سرویس + کد):**

```bash
ssh root@<VPS>
bash /tmp/newsbot-deploy.sh rollback    # یا نسخهٔ deploy/deploy.sh
```

rollback: `HEAD ← .deployed_sha.prev` با تأیید `rev-parse`، سپس restart و همان
دروازهٔ سلامت. تکرارش idempotent است. اگر `.deployed_sha.prev` نبود، متوقف می‌شود.

**بازگشت کاملِ محیط (که ادعاشده بود ولی در کد نبود — D7):**

1. رول‌بک کد: `rollback` بالا یا `git reset --hard <SHA قدیمی> && systemctl restart <service>`
2. رول‌بک اسکیما/داده (فقط اگر مهاجرت اجرا شده): بکاپ اخیر `/opt/backups/newsbot-*.db`
   را با `sqlite3.backup` معکوس برگردانید (بخش ۵) و بعد `run.py --check`.

## ۵. بکاپ و بازیابی (آزموده‌شده)

- **روش:** `sqlite3.backup` (پروتکل رسمی — زیر WAL هم یکپارچه). `cp` خامِ دیتابیسِ
  در حال نوشتن ممنوع (OPS-04).
- **مکان:** `/opt/backups/newsbot-YYYYMMDD-HHMMSS.<pid>.db` با `chmod 600`؛ فقط ۷ نسخهٔ
  اخیر نگه داشته می‌شود.
- **بازیابی:**

```bash
systemctl stop bot-ai-automatic
python3 - <<'PY'
import sqlite3
src = sqlite3.connect('/opt/backups/newsbot-<stamp>.db')
dst = sqlite3.connect('/opt/bot-ai-automatic/newsbot.db')
src.backup(dst); dst.close(); src.close()
PY
systemctl start bot-ai-automatic
```

- **اثباتِ بازیابی:** دو مسیر جداگانه تمرین شده‌اند:
  1. `tests/test_deploy_script.py` — بکاپِ واقعیِ `deploy.sh` روی دیتابیس sandbox ساخته و
     سالم برمی‌گردد.
  2. `tests/test_deployment.py::test_sqlite_backup_and_restore_roundtrip` — چرخهٔ کامل
     بکاپ ← بازگردانی ← اجرای `--check` ← خواندن داده (دریل بازیابی).

## ۵.۱ مهاجرت SQLite → D1 (پروتکل هماهنگ با ربات زنده — فاز ۱۰ / DB-21)

ابزار: `tools/migrate_sqlite_to_d1.py` · بازگردانی: `tools/restore_d1_to_sqlite.py`

1. **توقف ربات توصیه‌شده:** اگر ربات در ۵ دقیقهٔ گذشته heartbeat زده باشد، ابزار بدون
   `--force` اجرا نمی‌شود (گارد `LIVE_HEARTBEAT_SEC=300`). اول سرویس را ببندید:
   `systemctl stop bot-ai-automatic`.
2. ابزار خودش از مبدأ یک **snapshot سازگار** (`sqlite3.backup`، WAL-safe) می‌سازد؛ حتی در
   اجرای هم‌زمان هم دادهٔ نیمه‌نوشته خوانده نمی‌شود؛ snapshot موقت پایان کار حذف می‌گردد.
3. **گارد مقصد (DB-03):** اگر D1 داده داشته باشد فقط با `--force`؛ اگر `meta.schema_version`
   مقصد از بیلد فعلی جدیدتر باشد **حتی با `--force`** متوقف می‌شود.
4. اسکیمای D1 (SCHEMA + ایندکس‌ها + `meta` + trigger ارجاع) در **یک batch اتمیک** ساخته
   می‌شود (DB-24)؛ سپس داده‌ها با `INSERT OR REPLACE` و در پایان **verify هر ردیف**
   (fingerprint) — عدم تطابق ⇒ `exit 1` و هیچ تغییری در انتخاب بک‌اند ندهید.
5. پس از موفقیت `INSERT OR REPLACE INTO meta(key,value) VALUES('last_migrate_at', …)` ثبت
   می‌شود؛ همین کلید تاریخ آخرین مهاجرت را نشان می‌دهد.
6. `DB_BACKEND=d1` را تنها پس از «verify موفق» ست کنید. بازگشت:
   `python tools/restore_d1_to_sqlite.py restored.db` که در پایان همان verify را اجرا
   می‌کند و در صورت نقص `exit 1` می‌دهد.

> نسخهٔ اسکیما (DB-25): `SCHEMA_VERSION=4` منبع واحد در `newsbot/__init__.py` است؛
> ابزارها و `db.py` همان را import می‌کنند. بیلد قدیمی روی دیتابیس جدیدتر در استارت
> refuse می‌شود (downgrade ممنوع).

**آرشیو و بهداشت DB (DB-23 / تصمیم ۱۴ — ۳۶۵ روز):** در `cleanup()` مقالات قدیمی‌تر از
`ARCHIVE_DAYS` (پیش‌فرض ۳۶۵) *پس از* گرفتن بکاپ اتمیک در `<کنار DB>/backups/` حذف می‌شوند
(۲ بکاپ آخر نگه داشته می‌شود، `ARCHIVE_BACKUP_KEEP`). VACUUM حداکثر ماهی یک‌بار و فقط وقتی
freelist از آستانه بگذرد اجرا می‌شود؛ `PRAGMA optimize` هر cleanup. این پوشه هم باید در
بکاپ‌های VPS لحاظ شود.

## ۶. پایش و هشدار پایه

- **شکست deploy:** خودِ GitHub Actions ایمیلِ شکستِ workflow را برای اجراکننده می‌فرستد
  (به‌روزرسانیِ هر run در Actions ← Deploy to VPS).
- **سرویس روی سرور:** دروازهٔ سلامتِ deploy (NRestarts/ژورنال) + `systemctl status`.
- **پنل داخل ربات:** نمای سلامتِ ادمین (commit در حال اجرا، وضعیت سرویس) —
  `newsbot/ui_views_super.py`.
- **Docker:** `HEALTHCHECK` هر ۵ دقیقه `run.py --check` را اجرا می‌کند (پیکربندی + دیتابیس).

## ۷. نسخهٔ پایتون — واحد و هم‌تراز

| محیط | نسخه |
|---|---|
| VPS | `3.13.x` |
| CI | `3.13` |
| Docker | `python:3.13-slim@sha256:70729b46…` (پین digest) |

هر تغییر باید هر سه را با هم جلو ببرد (OPS-16/D8/C2). وابستگی‌ها در
`requirements.txt` با قفل دقیق (`==`) و فقطِ production؛ ابزارهای تست در
`requirements-dev.txt` (OPS-09).

## ۸. هدرهای امنیتی مرورگر (SEC-05)

در مخزن **هیچ سرور HTTP وجود ندارد** (ربات با polling کار می‌کند)، پس هدری برای
تنظیم در کد نیست. اگر روی VPS nginx/caddy چیزی سرو می‌شود (که از اینجا قابل رؤیت نیست)،
این هدرها را روی آن بگذارید:

```nginx
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "no-referrer" always;
add_header Content-Security-Policy "default-src 'self'" always;
```

هر سرور HTTP اضافه‌شده در آینده این هدرها را از همان روز اول باید داشته باشد.

## ۹. سخت‌کردن واحد systemd (کمترین دسترسی)

روی VPS، فایل `/etc/systemd/system/bot-ai-automatic.service` باید حداقل این‌ها را
داشته باشد (پس از تغییر: `daemon-reload` و restart در پنجرهٔ استقرار):

```ini
[Service]
User=botapp            # نه root — کاربر اختصاصیِ غیرمدیر
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/bot-ai-automatic
# لاگ‌ها به ژورنال systemd بروند:
StandardOutput=journal
StandardError=journal
Restart=on-failure
RestartSec=5
```

اعمال این‌ها بدون دسترسی VPS ممکن نیست؛ تا قبل از آن سرویس با دسترسیِ فعلی اجرا
می‌شود (ثبت‌شده به‌عنوان انحراف در گزارش فاز ۲).

## ۱۰. بنچمارک بار و اسکیمای v7 (فاز ۱۳ و ۱۴)

**اسکیمای v6 (فاز ۱۳):** جدول `credit_log` (رسید حرکت‌های کیف پول: دلتا، موجودی پس از حرکت، دلیل،
مرجع، زمان) به اسکیمای پایه اضافه شد و trigger ارجاع علاوه بر شارژ کیف پول، رسید اتمیک هم
ثبت می‌کند. ارتقا خودکار است (بیلد جدید در نخستین اجرا trigger را جایگزین و
`schema_version` را به ۶ می‌برد)؛ برای D1 هم `python tools/migrate_sqlite_to_d1.py`
جدول را منتقل می‌کند.

**اسکیمای v7 (فاز ۱۴):** ستون `plans.price_num_fa` (تعرفهٔ عددی تومانی برای کاربران fa؛
`price_num` = تعرفهٔ دلاری برای کاربران en) به جدول پلن‌ها اضافه شد. ارتقا خودکار است
(بیلد جدید در نخستین اجرا ستون را اضافه و `schema_version` را به ۷ می‌برد)؛ برای D1 هم
`python tools/migrate_sqlite_to_d1.py` ستون را منتقل می‌کند. بررسی سریع پس از استقرار:

```bash
sqlite3 /opt/bot-ai-automatic/newsbot.db "SELECT value FROM meta WHERE key='schema_version';"   # باید 7 باشد
sqlite3 /opt/bot-ai-automatic/newsbot.db "SELECT COUNT(*) FROM credit_log;"
```

**بنچمارک پایهٔ بار (QA-08):** پیش از هر تغییر مهم در لایهٔ دیتابیس و پس از هر استقرار،
روی نسخهٔ محلی/سرور اجرا و با جدول `docs/PERFORMANCE.md` مقایسه کنید:

```bash
python tools/benchmark.py --ops 2000 --json /tmp/bench-$(date +%F).json
```

افت بیش از ~۲ برابر در هر بلوک نسبت به پایه یعنی regression؛ تا رفع آن استقرار را جلو نبرید.
(اسکریپت روی دیتابیس موقت SQLite اجرا می‌شود و به نصب واقعی دست نمی‌زند.)

## ۱۱. حدود این راهنما

- اجرای واقعی deploy نیازمند تنظیم سه secret بالاست (تصمیم ۵) — تا آن زمان هیچ تماسی
  با VPS برقرار نمی‌شود.
- تمرین‌های محلی (`tests/test_deploy_script.py`) جای راستی‌آزماییِ محیط واقعی را نمی‌گیرند؛
  اولین اجرای production باید با `workflow_dispatch` و مشاهدهٔ لاگ انجام شود.
