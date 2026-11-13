# ⚠️ حالتِ systemd — LEGACY (قدیمی)

> **این مسیر دیگر مسیرِ Production نیست.** Production از فاز C (Session 3) روی
> **Docker + Compose** اجرا می‌شود: ایمیجِ immutable با تگِ SHA، بدونِ bind-mountِ
> کد، داده در `/opt/newsbot-data`. مرجعِ کامل: `docs/VPS_DEPLOYMENT.md`.

## چرا LEGACY است؟

| | systemd (قدیمی) | Docker + Compose (فعلی) |
|---|---|---|
| کد | روی میزبان، bind-mount داخلِ کانتینر | داخلِ ایمیج (immutable) |
| وابستگی‌ها | `pip install` روی محیطِ مجازیِ میزبان | پخش‌شده داخلِ ایمیج |
| rollback | برگشتِ کد + نصبِ دوبارهٔ وابستگی‌ها | فقط عوض‌کردنِ تگِ ایمیج |
| تخلیهٔ ایمن | ندارد | دارد (DEVOPS-08) |
| سخت‌سازی | واحدِ systemd | `read_only` · `cap_drop` · `no-new-privileges` · `mem_limit` |
| healthcheck | ندارد | `newsbot/healthcheck.py` |

## چه چیزی در این پوشه می‌ماند؟

* `10-restart-always.conf` — drop-inِ «همیشه بالا بیا» (`Restart=always` +
  `StartLimitIntervalSec=0`). برای نصب‌های systemdِ موجود هنوز مفید است.
* `20-watchdog.conf` — drop-inِ watchdog (`Type=notify` + `WatchdogSec`) که فقط
  **بعد از** استقرارِ کدِ فاز ۱۵ معنا دارد.

## اگر مجبورید از systemd استفاده کنید

۱. واحد را با `User=botapp` (یا کاربرِ غیرمدیرِ خودتان) بسازید و `WorkingDirectory`
   و `EnvironmentFile` را با `APP_DIR` هماهنگ کنید.
۲. در `deploy.sh` صریحاً بگویید که حالتِ systemd است:
   `RUNTIME=systemd APP_DIR=/opt/Bot-Official-Telegram SERVICE=newsbot.service`.
۳. بدانید که در این حالت **تخلیهٔ ایمن و ایمیجِ immutable فعال نیست** و بکاپِ
   دیتابیس از همان مسیری گرفته می‌شود که `DB_FILE`/`WorkingDirectory` نشان می‌دهد.
۴. پس از هر تغییر: `systemctl daemon-reload && systemctl restart newsbot.service`
   و `systemctl show newsbot.service -p ActiveState -p NRestarts` را چک کنید.

> تاریخچهٔ کاملِ این مسیر (شامل نصبِ واحد و drop-inها) در `docs/DEPLOY_RUNBOOK.md`
> بخش ۹ آمده است و دست‌نخورده باقی مانده.
