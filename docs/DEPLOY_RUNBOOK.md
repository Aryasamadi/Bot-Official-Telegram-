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

## ۹. حدود این راهنما

- اجرای واقعی deploy نیازمند تنظیم سه secret بالاست (تصمیم ۵) — تا آن زمان هیچ تماسی
  با VPS برقرار نمی‌شود.
- تمرین‌های محلی (`tests/test_deploy_script.py`) جای راستی‌آزماییِ محیط واقعی را نمی‌گیرند؛
  اولین اجرای production باید با `workflow_dispatch` و مشاهدهٔ لاگ انجام شود.
