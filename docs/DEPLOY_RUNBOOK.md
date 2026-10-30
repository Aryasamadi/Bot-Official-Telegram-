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
| preflight (git/python3/systemctl/.env/venv) | هیچ تغییری در کد/سرویس نمی‌شود |
| تعیین مسیر دیتابیس (خواندن `.env` با پیکربندیِ خودِ برنامه) | قبل از هر تغییری متوقف؛ مسیر حدس زده نمی‌شود (بخش ۳.۱) |
| بکاپ آنلاین (`sqlite3.backup` — سازگار با WAL) | قبل از هر تغییری متوقف (OPS-04/D12) |
| کپیِ آرشیوِ مقالات (پوشهٔ `backups` کنارِ دیتابیس) | نبودِ پوشه مجاز است؛ **کپی ناموفق ⇒ توقف** پیش از هر تغییری (OPS-10) |
| sync به SHA | سرویس دست‌نخورده؛ کد یا reset شده یا نه (rollback لازم نیست) |
| نصب requirements / دروازه (`compileall` + `run.py --check`) | **کد به SHA قبلی برمی‌گردد**؛ سرویس هرگز restart نشده (OPS-03) |
| سلامت بعد از restart (۱۰× فعال‌شدن + NRestarts=0 + نبود traceback تازه در ۲ دقیقهٔ اخیر ژورنال) | **کد برمی‌گردد و سرویس دوباره روی کد قبلی restart می‌شود** — رول‌بک واقعی نه فقط ادعا (OPS-15/D7) |

نشانگرها در `APP_DIR`:
- `.deployed_sha` — نسخهٔ فعلیِ مستقر (فقط پس از سلامت کامل)
- `.deployed_sha.prev` — نسخهٔ قبلی (پیش از sync نوشته می‌شود؛ مبنای rollback)

### ۳.۱ تعیین مسیر دیتابیس و بکاپ (پیش‌فرض‌های همین کد)

`deploy.sh` مسیر دیتابیس را **حدس نمی‌زند**: همان ماژولی را که خودِ ربات import می‌کند
(`newsbot/config.py`) در همان مفسرِ سرویس اجرا می‌کند و `DB_FILE`/`DB_BACKEND` را از
`.env` با همان parser (`python-dotenv`) می‌خواند. قواعد قابل‌اثبات از روی کد:

| وضعیت | رفتار `deploy.sh` |
|---|---|
| `DB_FILE` تعریف‌نشده | پیش‌فرض برنامه: `newsbot.db` نسبت به `WorkingDirectory` سرویس |
| `DB_FILE` نسبی | نسبت به `WorkingDirectory` سرویس حل می‌شود (همان قرارداد `sqlite3.connect`) |
| `DB_FILE` مطلق | عیناً استفاده می‌شود |
| `DB_FILE=` خالی | خطا: مسیر حدس زده نمی‌شود |
| `WorkingDirectory` نامعلوم (DB نسبی) | خطا؛ `DEPLOY_WORKING_DIR` یا `DEPLOY_DB_FILE` لازم است |
| فایل دیتابیس پیکربندی‌شده موجود نیست | خطا: «database not found … refusing to deploy without a backup»؛ برای استقرار اولیه `ALLOW_MISSING_DB=1` |
| `DB_BACKEND=d1` | دیتابیس محلی مرجع نیست: بکاپ SQLite محلی گرفته نمی‌شود و پیام صریح داده می‌شود |

متغیرهای اختیاری (همگی از محیطِ اجرای `deploy.sh`):

| متغیر | کار |
|---|---|
| `DEPLOY_DB_FILE` | مسیر صریح دیتابیس؛ بر `DB_FILE` از `.env` مقدم است (نسبی ⇒ نسبت به `WorkingDirectory`) |
| `DEPLOY_DB_BACKEND` | بک‌اند صریح (`sqlite`/`d1`)؛ بر `DB_BACKEND` مقدم است |
| `DEPLOY_WORKING_DIR` | مسیر کاری سرویس، وقتی از یونیت قابل‌خواندن نیست |
| `ALLOW_MISSING_DB=1` | اجازهٔ استقرار اولیه بدون دیتابیس (بکاپ گرفته نمی‌شود و هشدار چاپ می‌شود) |

اگر یونیت `DB_FILE`/`DB_BACKEND` را در `Environment=` تعریف کند یا `EnvironmentFile` دیگری
داشته باشد، `deploy.sh` هشدار می‌دهد و از حدس‌زدن پرهیز می‌کند؛ در آن حالت
`DEPLOY_DB_FILE`/`DEPLOY_DB_BACKEND` را بدهید تا سرویس و اسکریپت دقیقاً یک مقدار ببینند.

مسیر آرشیوِ مقالات هم از همان دیتابیسِ حل‌شده مشتق می‌شود: `<پوشهٔ DB>/backups`
(همان جایی که `newsbot/core_articles.py` snapshot می‌نویسد).

## ۴. رول‌بک

**خودکار:** هر شکستِ بعد از sync (جدول بالا) خودش کد را به SHA قبلی برمی‌گرداند.

**دستی (شامل رول‌بک سرویس + کد):**

```bash
ssh root@<VPS>
bash /tmp/newsbot-deploy.sh rollback    # یا نسخهٔ deploy/deploy.sh
```

rollback: `HEAD ← .deployed_sha.prev` با تأیید `rev-parse`، سپس restart و همان
دروازهٔ سلامت. تکرارش idempotent است. اگر `.deployed_sha.prev` نبود، متوقف می‌شود.
**پیش از جایگزینی نسخه، از دیتابیسِ فعلی بکاپِ معتبر گرفته می‌شود** (همان مسیرِ حل‌شدهٔ
بخش ۳.۱)؛ اگر آن بکاپ شکست بخورد، rollback متوقف می‌شود و **نه دیتابیس و نه نسخهٔ فعال
تغییر نمی‌کند**. برای بک‌اند `d1` بکاپ محلی گرفته نمی‌شود.

**بازگشت کاملِ محیط (که ادعاشده بود ولی در کد نبود — D7):**

1. رول‌بک کد: `rollback` بالا یا `git reset --hard <SHA قدیمی> && systemctl restart <service>`
2. رول‌بک اسکیما/داده (فقط اگر مهاجرت اجرا شده): بکاپ اخیر `/opt/backups/newsbot-*.db`
   را با `sqlite3.backup` معکوس برگردانید (بخش ۵) و بعد `run.py --check`.

## ۵. بکاپ و بازیابی (آزموده‌شده)

- **روش:** `sqlite3.backup` (پروتکل رسمی — زیر WAL هم یکپارچه). `cp` خامِ دیتابیسِ
  در حال نوشتن ممنوع (OPS-04).
- **مکان:** `BACKUP_DIR` (پیش‌فرض کد: `/opt/backups`) با نام
  `newsbot-YYYYMMDD-HHMMSS.<pid>.db` و `chmod 600`؛ فقط `KEEP_BACKUPS` نسخهٔ اخیر
  (پیش‌فرض کد: ۷) نگه داشته می‌شود.
- **آرشیوِ مقالات:** پوشهٔ `backups` کنارِ دیتابیس به `BACKUP_DIR/archive` آینه می‌شود؛
  در آن پوشه هم فقط فایل‌های `newsbot-archive-*.db` تا `KEEP_BACKUPS` تازه نگه داشته
  می‌شوند و فایل‌های دیگر دست‌نخورده می‌مانند.
- **بازیابی** (مسیرِ دیتابیس را با `DB_FILE`/`WorkingDirectory` خودتان تعیین کنید):

```bash
systemctl stop newsbot.service
python3 - <<'PY'
import sqlite3
src = sqlite3.connect('/opt/backups/newsbot-<stamp>.db')
dst = sqlite3.connect('<مسیر دیتابیسِ پیکربندی‌شده>')
src.backup(dst); dst.close(); src.close()
PY
systemctl start newsbot.service
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
   `systemctl stop newsbot.service`.
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

## ۸.۱ قطعِ دسترسی SSH (۱۹ اکتبر ۲۰۲۶) — علت و راه‌حل

**علتِ اثبات‌شده:** از ۱۹ اکتبر ۲۰۲۶، همهٔ ترافیکِ ورودیِ TCP به VPS دور ریخته می‌شود
و `scp/ssh` با `dial tcp <host>:22: i/o timeout` می‌میرد. این خطا **از پیکربندیِ
workflow نیست**:

| بررسی | نتیجه |
|---|---|
| `VPS_HOST` / `VPS_SSH_PRIVATE_KEY` / `VPS_HOST_FINGERPRINT` | هر سه موجود ✓ |
| پورتِ SSH از رانرِ Actions | timeout (بسته/فیلتر) |
| اسکنِ کاملِ ۶۵۵۳۵ پورت از رانرِ Actions | **هیچ پورتِ بازی نیست** |
| چهار نقطهٔ بیرونیِ مستقل (CH/HU/SE/UK) روی پورتِ ۲۲ | همه «Connection timed out» |
| ICMP به همان IP | پاسخ دارد ⇒ ماشین زنده است، فقط ورودیِ TCP بسته است |

یعنی فایروالِ سرویس‌دهنده (یا allowlistِ آن) باید از راهِ کنسولِ پنلِ سرویس‌دهنده باز
شود؛ پیش از آن هیچ استقرارِ SSH-محوری ممکن نیست.

**چه چیزی در این مخزن بهتر شد (تا وقتی فایروال باز نشود هم کار را روشن می‌کند):**

- مرحلهٔ پیش‌شرطِ «VPS must accept TCP on the SSH port» پیش از `scp` اجرا می‌شود ⇒
  خطای صریح در ۱۰ ثانیه به‌جای timeoutِ مبهمِ ۶۰ ثانیه‌ای (و آدرسِ VPS چاپ نمی‌شود).
- پورتِ SSH از secretِ `VPS_SSH_PORT` خوانده می‌شود (پیش‌فرض ۲۲) ⇒ اگر `sshd` روی
  پورتِ دیگری گوش می‌دهد، فقط همان secret عوض می‌شود، نه کد.
- قفلِ هم‌زمانی از سطحِ workflow به سطحِ job منتقل شد ⇒ اجرایی که در انتظارِ تأییدِ
  دستیِ محیطِ `production` است، دیگر صفِ استقرارهای بعدی را قفل نمی‌کند.

**گزینه‌های عملی برای باز کردن مسیر (به ترتیبِ کم‌هزینه‌ترین):**

1. در پنلِ سرویس‌دهنده، پورتِ SSH را باز کنید یا allowlistِ رنجِ IPِ GitHub Actions را
   اضافه کنید (رنج‌های `githubusercontent.com`/`actions` را از مستنداتِ GitHub بگیرید؛
   رنج‌ها تغییر می‌کنند ⇒ allowlistِ پویا ترجیح دارد).
2. اگر سیاستِ امنیتی اجازهٔ بازکردنِ پورت از اینترنت را نمی‌دهد: یک **runnerِ
   self-hosted روی همان VPS** (یا یک میزبانِ کوچک در همان شبکه) بسازید و
   `runs-on` را به آن تغییر دهید — آن‌وقت ترافیکِ SSH اصلاً از اینترنت نمی‌آید.
3. راهِ بلندمدتِ پیشنهادی: **استقرارِ pull-based** (عاملِ کوچکی روی VPS که خودش
   نسخهٔ جدید را از GitHub می‌گیرد و سرویس را restart می‌کند). این راه inbound-TCP
   نمی‌خواهد و با این حادثه سازگار است؛ ولی برای نصبِ اولیهٔ عامل، یک‌بار دسترسیِ
   SSH یا کنسول لازم است.

## ۹. سخت‌کردن واحد systemd (کمترین دسترسی + تضمین روشن‌ماندن)

> **OPS-22/OPS-23 (فاز ۱۵):** دو لایهٔ مستقل اضافه شد. لایهٔ یک (`Restart=always` و حذف
> سقف start-limit) با کد فعلی هم سازگار است و باید **همین حالا** اعمال شود؛ لایهٔ دو
> (`Type=notify` + watchdog) فقط **بعد از استقرار کد فاز ۱۵** معنا دارد. ترتیب را جابه‌جا نکنید.

### ۹.۱ نصب تازه — واحد کامل

روی VPS، فایل `/etc/systemd/system/newsbot.service` (نام یونیت = پیش‌فرض `SERVICE` در
`deploy/deploy.sh`؛ مسیرها = پیش‌فرض `APP_DIR` همان اسکریپت):

```ini
[Unit]
Description=NewsBot Telegram bot
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=notify
User=botapp
WorkingDirectory=/opt/bot-official-telegram
EnvironmentFile=/opt/bot-official-telegram/.env
ExecStart=/opt/bot-official-telegram/venv/bin/python run.py
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/bot-official-telegram
StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=5
TimeoutStartSec=180
TimeoutStopSec=60
WatchdogSec=300
NotifyAccess=main
ExecStopPost=/bin/sh -c 'logger -t newsbot "stop result=$SERVICE_RESULT exit=$EXIT_CODE status=$EXIT_STATUS"'

[Install]
WantedBy=multi-user.target
```

- `User=botapp` کاربر اختصاصیِ غیرمدیر است؛ اگر روی نصب موجود کاربر دیگری دارید،
  unit اصلی را عوض نکنید و از drop-inهای بخش ۹.۲ استفاده کنید.
- نامِ محیطِ مجازی: `deploy.sh` اول `APP_DIR/venv` را امتحان می‌کند و اگر نبود به
  `APP_DIR/.venv` برمی‌گردد (`VENV_DIR`)؛ نام پوشه را با یونیت هماهنگ کنید.
- `WorkingDirectory` لنگرِ حلِ مسیرهای نسبی است: `DB_FILE` نسبی نسبت به همین پوشه
  حل می‌شود (بخش ۳.۱). اگر `WorkingDirectory` نباشد یا ناشناخته بماند، `deploy.sh`
  برای `DB_FILE` نسبی خطا می‌دهد و حدس نمی‌زند.
- `EnvironmentFile` نباید با `-` اختیاری باشد؛ `deploy.sh` در حالت `-` هشدار می‌دهد
  (حلقهٔ بی‌پایانِ ری‌استارت).
- `READY=1` را کد فاز ۱۵ در پایان `post_init` می‌فرستد؛ کد قدیمی آن را نمی‌فرستد.

### ۹.۲ نصب موجود — مرحلهٔ ۱ (همین حالا، با کد فعلی)

```bash
cd /opt/bot-official-telegram
install -D -m 0644 deploy/systemd/10-restart-always.conf \
  /etc/systemd/system/newsbot.service.d/10-restart-always.conf
systemctl daemon-reload
systemctl enable newsbot.service
systemctl reset-failed newsbot.service 2>/dev/null || true
systemctl restart newsbot.service
systemctl show newsbot.service -p Restart -p StartLimitIntervalUSec -p ActiveState -p NRestarts
```

انتظار: `Restart=always` · `StartLimitIntervalUSec=0` · `ActiveState=active`.
`ExecStopPost` دلیل هر توقف را با تگ `newsbot` در ژورنال ثبت می‌کند.
دلیل حادثهٔ واقعی: `Restart=on-failure` + رسیدن به سقف start-limit باعث شد سرویس پس از
۳۱٬۱۲۰ restart با `Failed with result 'resources'` رها شود.

### ۹.۳ نصب موجود — مرحلهٔ ۲ (فقط بعد از استقرار کد فاز ۱۵)

```bash
cd /opt/bot-official-telegram
./deploy/deploy.sh deploy <MAIN_SHA>
install -D -m 0644 deploy/systemd/20-watchdog.conf \
  /etc/systemd/system/newsbot.service.d/20-watchdog.conf
systemctl daemon-reload
systemctl restart newsbot.service
systemctl show newsbot.service -p Type -p NotifyAccess -p WatchdogUSec -p ActiveState
journalctl -u newsbot.service -n 80 --no-pager
```

انتظار: `Type=notify` · `NotifyAccess=main` · `WatchdogUSec=5min` · `ActiveState=active` و
در ژورنال پیام فعال‌شدن watchdog. اگر مرحلهٔ ۲ را قبل از کد فاز ۱۵ نصب کنید، سرویس در
استارت منتظر `READY=1` می‌ماند و بالا نمی‌آید.

**رول‌بک:** اگر به کد قبل از فاز ۱۵ برگشتید، `20-watchdog.conf` را حذف و
`daemon-reload`/`restart` کنید؛ `10-restart-always.conf` می‌تواند بماند.

### ۹.۴ محدودهٔ تضمین

`Restart=always` خروج تمیز و خطا را پوشش می‌دهد؛ `StartLimitIntervalSec=0` از تسلیم‌شدن
systemd جلوگیری می‌کند؛ watchdog پروسهٔ قفل‌شده/گیرکرده را با kill و restart نجات می‌دهد.
خاموشی کل کانتینر/هاست یا OOM و disk-full پیوسته باید در پنل provider و پایش منابع جدا
پوشش داده شود. این تغییرات در کانتینر فعلی از طریق **کنسول** اعمال می‌شود؛ Actions به
sshd کانتینر (فقط IPv6) دسترسی ندارد.

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
# مسیر دیتابیس = همان چیزی که سرویس استفاده می‌کند (DB_FILE + WorkingDirectory)
sqlite3 "<مسیر دیتابیسِ پیکربندی‌شده>" "SELECT value FROM meta WHERE key='schema_version';"   # باید 9 باشد
sqlite3 "<مسیر دیتابیسِ پیکربندی‌شده>" "SELECT COUNT(*) FROM credit_log;"
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
