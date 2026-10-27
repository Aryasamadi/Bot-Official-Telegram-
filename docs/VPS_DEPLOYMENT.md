# راهنمای جامع استقرار و نگهداری سرور VPS (NewsBot)

این مستند مراحل استقرار از صفر، به‌روزرسانی زنده، تنظیمات systemd watchdog، و دستورالعمل بازگشت به نسخه قبل (Rollback) را شرح می‌دهد.

---

## ۱. پیش‌نیازها
- **سیستم‌عامل پیشنهادی:** Ubuntu 22.04 LTS یا Debian 12
- **نسخه پایتون:** Python 3.13 همراه با ماژول `venv`
- **ابزارهای سیستمی:** `git`، `sqlite3`، `systemd`، `curl`

---

## ۲. نصب اولیه از صفر روی سرور

### ۲.۱. ایجاد کاربر و آماده‌سازی مسیر کاری
```bash
sudo useradd -m -s /bin/bash botapp
sudo mkdir -p /opt/bot-official-telegram /opt/backups
sudo chown -R botapp:botapp /opt/bot-official-telegram /opt/backups
```

### ۲.۲. کلون مخزن و ساخت محیط مجازی
```bash
sudo -u botapp git clone https://github.com/Aryasamadi/Bot-Official-Telegram-.git /opt/bot-official-telegram
cd /opt/bot-official-telegram
sudo -u botapp python3 -m venv venv
sudo -u botapp venv/bin/pip install --upgrade pip
sudo -u botapp venv/bin/pip install -r requirements.txt
```
> نامِ محیط مجازی باید با `ExecStart` در واحد سرویس یکی باشد. `deploy/deploy.sh` هر دو
> حالت `venv/` و `.venv/` را خودکار تشخیص می‌دهد؛ اگر هر دو موجود باشند خطا می‌دهد و با
> `VENV_DIR` انتخاب صریح لازم است.

### ۲.۳. تنظیم متغیرهای محیطی (`.env`)
فایل `.env` را ایجاد و مجوز دسترسی آن را فقط برای مالک تنظیم کنید:
```bash
sudo -u botapp cp .env.example .env
sudo -u botapp nano .env
chmod 600 .env
```
*حداقل موارد الزامی:*
- `BOT_TOKEN`: توکن دریافتی از BotFather
- `SUPER_ADMIN_IDS`: شناسه عددی ادمین‌های اصلی (با ویرگول جدا شود)
- `SECRETS_KEY`: کلید تصادفی برای رمزنگاری کلیدهای AI
- `DB_BACKEND=sqlite` و `DB_FILE=/opt/bot-official-telegram/data/newsbot.db`: مسیر
  دیتابیس — **منبعِ واحد** برای ربات، `deploy.sh` و بکاپ؛ حدس زده نمی‌شود

### ۲.۴. بررسی صحت راه‌اندازی (Self-Check)
قبل از اجرای سرویس، وضعیت بررسی فقط‌خواندنی را اجرا کنید:
```bash
sudo -u botapp venv/bin/python run.py --check
```
انتظار می‌رود خروجی شامل پیام تایید سلامت پیکربندی باشد:
`✅ خودآزمایی موفق: پیکربندی سالم است...`

---

## ۳. پیکربندی Systemd با مکانیزم Watchdog و Restart خودکار

### ۳.۱. فایل واحد سرویس (`/etc/systemd/system/newsbot.service`)
```ini
[Unit]
Description=NewsBot Telegram Bot
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=notify
User=botapp
WorkingDirectory=/opt/bot-official-telegram
EnvironmentFile=-/opt/bot-official-telegram/.env
ExecStart=/opt/bot-official-telegram/venv/bin/python run.py
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/bot-official-telegram /opt/backups
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

### ۳.۲. فعال‌سازی و راه‌اندازی سرویس
```bash
sudo systemctl daemon-reload
sudo systemctl enable newsbot
sudo systemctl start newsbot
sudo systemctl status newsbot --no-pager
```

---

## ۴. روش به‌روزرسانی امن (Deploy) با اسکریپت خودکار

> **ورود به سرور:** دستورهای این بخش روی VPS اجرا می‌شوند، نه روی ماشین محلی. از
> ماشین خودتان با کاربر root وارد شوید (`ssh root@<VPS_HOST>`) یا با کاربری که
> دسترسی `sudo` دارد (`ssh <ADMIN_USER>@<VPS_HOST>`) و دستورهای زیر را با `sudo`
> اجرا کنید.

برای ارتقای بات به یک commit خاص، اسکریپت `deploy/deploy.sh` کلیه مراحل بکاپ‌گیری آنلاین دیتابیس (بدون قطعی و ایمن در WAL)، بررسی تمیز بودن درخت کاری، همگام‌سازی، نصب پیش‌نیازها، گیت بررسی صحت و سلامت نهایی سرویس را انجام می‌دهد:

> **دسترسی لازم — root:** مرحلهٔ پایانی این اسکریپت `systemctl restart newsbot` است و
> کنترل systemd فقط با دسترسی root انجام می‌شود. اسکریپت را با `sudo` (یا در شل root)
> اجرا کنید. اجرای اسکریپت با `sudo -u botapp` **معتبر نیست**: کاربر `botapp` فقط فرایند
> ربات را اجرا می‌کند و اجازهٔ restart سرویس را ندارد؛ در این حالت deploy در مرحلهٔ
> restart شکست می‌خورد و rollback هم نمی‌تواند سرویس را بالا بیاورد. خود سرویس همچنان با
> `User=botapp` اجرا می‌شود و این مورد تغییر نمی‌کند.

```bash
cd /opt/bot-official-telegram
sudo ./deploy/deploy.sh deploy <GIT_COMMIT_SHA>
```

اگر از قبل با کاربر root وارد شده‌اید، `sudo` لازم نیست:

```bash
cd /opt/bot-official-telegram
./deploy/deploy.sh deploy <GIT_COMMIT_SHA>
```

> اگر مخزن را با کاربر `botapp` ساخته‌اید و اسکریپت را با root اجرا می‌کنید، یک‌بار
> `sudo git config --global --add safe.directory /opt/bot-official-telegram` را اجرا کنید
> تا Git خطای «dubious ownership» ندهد.

اگر هر مرحله با خطا مواجه شود، اسکریپت کد را به نسخه پیشین بازمی‌گرداند و سرویس را روی
نسخه پایدار ری‌استارت می‌کند. اگر خودِ restart یا بررسی سلامت شکست بخورد، اسکریپت با کد
خروج غیرصفر تمام می‌شود و موفقیت را اعلام نمی‌کند؛ در این حالت خروجی خطا و ژورنال را
بررسی کنید (بخش ۶).

---

## ۵. بازگشت به نسخه قبل (Rollback)

در صورت بروز مشکل در محیط عملیاتی، برای بازگشت سریع به نسخه ثبت‌شده پیشین (ابتدا طبق
بخش ۴ به سرور متصل شوید):

> **دسترسی لازم — root:** rollback نیز در پایان `systemctl restart newsbot` را اجرا
> می‌کند و باید با `sudo` (یا در شل root) اجرا شود؛ `sudo -u botapp` برای آن کافی نیست.

```bash
cd /opt/bot-official-telegram
sudo ./deploy/deploy.sh rollback
```

ترتیب کار اسکریپت: بازگرداندن `HEAD` به SHA ثبت‌شده در `.deployed_sha.prev` (با تأیید
`rev-parse`)، سپس `systemctl restart newsbot`، سپس همان دروازهٔ سلامت، و در پایان ثبت
`.deployed_sha`. اگر restart یا سلامت شکست بخورد، اسکریپت با کد خروج غیرصفر متوقف
می‌شود و «rollback موفق» اعلام نمی‌کند؛ کد ممکن است روی نسخهٔ قبلی باشد ولی سرویس هنوز
بالا نیامده باشد. در این حالت وضعیت را دستی بررسی کنید:

```bash
systemctl status newsbot --no-pager
systemctl show newsbot -p ActiveState -p SubState -p NRestarts
sudo journalctl -u newsbot -n 80 --no-pager
git -C /opt/bot-official-telegram rev-parse --short HEAD
```

اگر سرویس بالا نیامد، ابتدا علت را از ژورنال پیدا کنید و سپس در صورت نیاز
`sudo systemctl restart newsbot` را دوباره اجرا کنید.

---

## ۶. پایش، لاگ‌ها و بازیابی اضطراری

### پایش زنده لاگ‌ها:
```bash
journalctl -u newsbot -f
```

### بررسی لاگ خطاهای اخیر:
```bash
journalctl -u newsbot --since "1 hour ago" -p err..emerg --no-pager
```

### پشتیبان‌گیری دستی از دیتابیس (WAL-Safe):
مسیر دیتابیس از `DB_FILE` در `.env` خوانده می‌شود (منبعِ واحد)؛ مسیر نسبی نسبت به
`/opt/bot-official-telegram` تفسیر می‌شود.
```bash
cd /opt/bot-official-telegram
DB="$(sed -n 's/^DB_FILE=//p' .env | tail -n1)"; DB="${DB:-newsbot.db}"
case "$DB" in /*) ;; *) DB="/opt/bot-official-telegram/$DB" ;; esac
sudo -u botapp python3 - "$DB" /opt/backups/manual-$(date +%Y%m%d-%H%M%S).db << 'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1], timeout=30)
dst = sqlite3.connect(sys.argv[2])
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
print("Backup completed successfully.")
PY
```
