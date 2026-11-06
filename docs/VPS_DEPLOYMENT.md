# استقرار روی VPS — راهنمای مرجع (فاز C / Session 3)

> **این سند، منبعِ حقیقتِ استقرار است.** هر چیزی که دربارهٔ *Production* (سرورِ
> واقعیِ ربات) می‌گوید اینجاست. سندِ قدیمی‌تر (`docs/DEPLOY_RUNBOOK.md`) تاریخچهٔ
> فازهای قبلی را نگه می‌دارد؛ اگر دو سند اختلاف داشتند، **این سند مقدم است**.

| | |
|---|---|
| **حالتِ Production** | **Docker + Compose** (ایمیجِ immutable با تگِ SHA) |
| **حالتِ systemd** | **LEGACY** — فقط برای نصب‌های قدیمی؛ `deploy/systemd/README.md` |
| **مدلِ استقرار** | pull از راهِ GitHub Actions ⇒ SSH با کاربرِ محدودِ `deploy` ⇒ `deploy.sh` |
| **نسخهٔ پایتون** | ۳.۱۳ (VPS، CI و ایمیج هم‌تراز) |
| **مخزنِ کد روی سرور** | `/opt/Bot-Official-Telegram` (فقط برای ساختِ ایمیج و نگه‌داریِ rollback) |
| **دادهٔ ماندگار** | `/opt/newsbot-data` (روی میزبان) ⇒ `/app/data` (داخلِ کانتینر) |
| **فایلِ env** | `/etc/newsbot/env` (داخلِ کانتینر به‌صورت `env_file` تزریق می‌شود) |
| **بکاپ** | `/opt/backups` (فقط‌خواندنی داخلِ کانتینر) |

---

## ۱. چیدمانِ واقعیِ سرور

```
/opt/Bot-Official-Telegram/          ← checkout گیت (root-owned) — فقط برای ساختِ ایمیج
/opt/newsbot-data/                   ← دادهٔ ماندگار: newsbot.db (+ -wal/-shm) و backups/
/opt/backups/                        ← بکاپ‌های sqlite3.backup + patchهای dirty-tree
/etc/newsbot/env                      ← BOT_TOKEN، SUPER_ADMIN_IDS، … (mode 600)
/etc/sudoers.d/newsbot-deploy        ← قاعدهٔ محدودِ sudo برای کاربرِ deploy
/home/deploy/.ssh/authorized_keys    ← کلیدِ CI + فرمانِ اجباری
```

کانتینر (نام: `bot-official-telegram`):

| | |
|---|---|
| **ایمیج** | `newsbot:<sha-40>` (و `newsbot:latest` به‌عنوان تگِ محلی) — کد و وابستگی‌ها داخلِ ایمیج |
| **mount کد** | **هیچ** — کدِ میزبان هرگز داخلِ کانتینر mount نمی‌شود |
| **mount داده** | `/opt/newsbot-data:/app/data` |
| **mount بکاپ** | `/opt/backups:/opt/backups:ro` |
| **دیتابیس** | `DB_FILE=/app/data/newsbot.db` |
| **کاربر** | `newsbot` (uid 10001، غیرroot) |
| **سخت‌سازی** | `read_only: true` · `tmpfs /tmp` · `cap_drop: ALL` · `no-new-privileges` · `mem_limit 512m` · `pids_limit 256` |
| **لاگ** | `json-file` با چرخش (۱۰ مگ × ۵) |
| **سلامت** | `python -m newsbot.healthcheck` هر ۶۰ ثانیه (خواندنی، بدونِ توکن، بدونِ نوشتن) |
| **توقف** | `stop_grace_period: 40s` (بیشتر از مهلتِ تخلیهٔ ۳۰ ثانیه) |

## ۲. دسترسیِ SSH — کاربرِ اختصاصی و محدود (DEVOPS-01)

Production دیگر با `root` وصل نمی‌شود. یک کاربرِ سیستمیِ `deploy` هست که:

* رمز ندارد (`passwd -l`) و پوستهٔ آزاد ندارد؛
* عضوِ گروهِ `docker` است (برای اجرای فرمان‌های docker)؛
* فقط از طریق `sudo -n` می‌تواند **اسکریپتِ استقرار** را اجرا کند؛
* هر نشستِ SSH با `command="…"` به `deploy/ssh-forced-command.sh` می‌رود و فقط
  چهار فرمان را می‌پذیرد:

| فرمان | کار |
|---|---|
| `deploy <۴۰-hex-sha>` | استقرارِ آن کامیت (اسکریپتِ هدف با `sudo`) |
| `rollback` | بازگشت به نسخهٔ ثبت‌شدهٔ قبلی |
| `preflight` | گزارشِ فقط‌خواندنی (HEAD، نسخهٔ مستقر، وضعیتِ کانتینر) |
| `health` | اجرای healthcheck داخلِ کانتینر |

هر چیزِ دیگری با **exit 126** رد می‌شود. کلید در `authorized_keys` با
`no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty` قید شده است.

نصب/به‌روزرسانی (روی VPS، با root):

```bash
bash deploy/setup-deploy-user.sh --key-file /root/newsbot_deploy_ed25519.pub
# پیش‌نمایش بدونِ تغییر:
bash deploy/setup-deploy-user.sh --key-file /root/newsbot_deploy_ed25519.pub --dry-run
```

اسکریپت، قبل از نصب، قاعدهٔ sudoers را با `visudo -c` اعتبارسنجی می‌کند و اگر
خراب بود اصلاً نصبش نمی‌کند.

> **نکتهٔ امنیتی (صادقانه):** عضویت در گروهِ `docker` عملاً دسترسیِ root می‌دهد
> (`docker run -v /:/host`). فرمانِ اجباری و قاعدهٔ sudoers برای جلوگیری از
> *سوءاستفادهٔ تصادفی* و برای *ردِ ممیزی* است، نه برای جدا کردنِ کاربر از root.

## ۳. جریانِ استقرار

```
push/PR ──▶ CI (ci.yml) ──سبز──▶ merge به main
                                     │
                                     ▼
                       workflow_run ──▶ Deploy to VPS (deploy-vps.yml)
                                     │   ① preflight: secrets + در دسترس بودنِ پورت
                                     │   ② CIِ سبز برای همان SHA (فقط در حالتِ دستی)
                                     │   ③ دروازهٔ ضدِ عقب‌گرد (کلاینت)
                                     │   ④ scp اسکریپت + ssh «deploy <sha>»
                                     ▼
                       deploy.sh (روی سرور، با sudo)
                         ① preflight · ② resolve DB · ③ ضدِ عقب‌گرد (سرور)
                         ④ بکاپ · ⑤ sync · ⑥ build ایمیج · ⑦ gate روی ایمیج
                         ⑧ تخلیهٔ ایمن · ⑨ stop · ⑩ مهاجرتِ مسیرِ DB
                         ⑪ recreate کانتینر · ⑫ سلامت · ⑬ ثبتِ SHA
```

### جدولِ دروازه‌ها

| مرحله | شکست ⇒ چه می‌شود؟ |
|---|---|
| preflight (secrets، git، docker، `.env`) | هیچ تغییری در کد/سرویس نمی‌شود |
| دروازهٔ ضدِ عقب‌گرد | **هیچ تغییری** — استقرارِ قدیمی‌تر رد می‌شود |
| بکاپِ آنلاین (`sqlite3.backup`) | توقف *قبل از هر تغییری* |
| build ایمیج | rollback به نسخهٔ قبلی؛ سرویس دست‌نخورده |
| gate روی ایمیج (`compileall` + `run.py --check`) | rollback؛ کانتینرِ زنده دست‌نخورده |
| تخلیهٔ ایمن (۳۰ ثانیه) | هشدار؛ سپس ادامه (انتشارِ در حالِ پرواز ممکن است قطع شود) |
| سلامت بعد از بازساخت | **rollback واقعی**: کانتینر از ایمیجِ قبلی دوباره بالا می‌آید |

نشانگرها در `APP_DIR`:
`.deployed_sha` (نسخهٔ فعلی) و `.deployed_sha.prev` (نسخهٔ قبلی — مبنای rollback).

## ۴. ایمیجِ immutable (DEVOPS-03)

`deploy/Dockerfile` کد و وابستگی‌های قفل‌شده را داخلِ ایمیج می‌پزد:

```bash
docker build --build-arg NEWSBOT_VERSION=<sha> -t newsbot:<sha> -t newsbot:latest \
  -f deploy/Dockerfile /opt/Bot-Official-Telegram
```

* `NEWSBOT_VERSION` هنگام build تزریق می‌شود ⇒ پنلِ «نسخهٔ در حال اجرا» درست است
  (`newsbot/ui_views_super.py`).
* `python:3.13-slim` با **digest** پین شده ⇒ rebuild ناگهانی تغییر نمی‌کند.
* هیچ `pip install` داخلِ کانتینرِ زنده انجام نمی‌شود (این کار در فاز قبلی، تناقضِ
  «ایمیجِ اجرایی» با «ایمیجِ روی دیسک» می‌ساخت).
* `docker compose up -d --force-recreate --no-build` کانتینر را از همان تگِ SHA
  می‌سازد. اگر پلاگینِ compose نباشد، `docker run` با همان محدودیت‌های امنیتی
  به‌عنوان fallback اجرا می‌شود.

فایلِ مرجعِ Compose: `docker-compose.yml` (ریشهٔ مخزن — تنها نسخهٔ canonical؛
نسخهٔ دومی در `deploy/` وجود ندارد).

## ۵. تخلیهٔ ایمن (DEVOPS-08)

پیش از `docker stop`، ربات به حالتِ «تخلیه» می‌رود:

1. اسکریپتِ استقرار فایلِ `DRAIN_FLAG_FILE` (پیش‌فرض `/tmp/newsbot_draining`) را
   *داخلِ کانتینر* می‌سازد.
2. ربات (`newsbot/runtime.py`) از آن لحظه **انتشارِ تازه را شروع نمی‌کند**
   (`publish.py` مقدارِ `"draining"` برمی‌گرداند) ولی انتشارهای در حالِ پرواز را
   تمام می‌کند.
3. وقتی شمارندهٔ «در حالِ پرواز» صفر شد، ربات فایلِ `DRAINED_FLAG_FILE` را
   می‌نویسد.
4. اسکریپت منتظرِ آن فایل می‌ماند (سقف: `DRAIN_TIMEOUT=30s`) و بعد کانتینر را با
   `docker stop --time 30` متوقف می‌کند.

نتیجه: هیچ مقاله‌ای در وضعیت `delivery_unknown` رها نمی‌شود.
مسیرهای نشانگر با `DRAIN_FLAG_FILE`/`DRAINED_FLAG_FILE` قابل تنظیم‌اند (در Compose
روی tmpfsِ `/tmp` هستند ⇒ با بازساختِ کانتینر پاک می‌شوند).

## ۶. بکاپ و بازیابی

* بکاپ با `sqlite3.backup` (سازگار با WAL) و روی همان فایلی که سرویس واقعاً
  استفاده می‌کند گرفته می‌شود ⇒ سرویس حین بکاپ قطع نمی‌شود.
* نام: `/opt/backups/newsbot-YYYYMMDD-HHMMSS.<pid>.db` با `chmod 600`؛ فقط
  `KEEP_BACKUPS` (پیش‌فرض ۷) نسخهٔ اخیر نگه داشته می‌شود.
* آرشیوِ مقالات (`<کنارِ DB>/backups`) آینه می‌شود به `/opt/backups/archive`.

بازیابی (نمونه):

```bash
docker stop bot-official-telegram
python3 - <<'PY'
import sqlite3
src = sqlite3.connect('/opt/backups/newsbot-<stamp>.db')
dst = sqlite3.connect('/opt/newsbot-data/newsbot.db')
src.backup(dst); dst.close(); src.close()
PY
docker start bot-official-telegram
```

## ۷. رول‌بک

* **خودکار:** هر شکستِ بعد از sync، کد را به SHA قبلی برمی‌گرداند *و* کانتینر را
  از روی ایمیجِ `newsbot:<sha-قبلی>` دوباره می‌سازد (اگر ایمیج روی میزبان نبود،
  از روی همان SHA بازساخته می‌شود).
* **دستی:**

```bash
ssh deploy@<host> 'rollback'
```

قبل از جایگزینیِ نسخه، از دیتابیسِ فعلی بکاپِ معتبر گرفته می‌شود؛ اگر آن بکاپ
شکست بخورد، rollback متوقف می‌شود و **نه دیتابیس و نه نسخهٔ فعال تغییر نمی‌کند**.

## ۸. ضدِ عقب‌گرد (DEVOPS-06)

دو دروازه، یکی در کلاینت و یکی روی سرور:

* **کلاینت** (در `deploy-vps.yml`): اگر SHA هدف نوکِ `origin/main` نباشد ولی در
  تاریخچهٔ آن باشد ⇒ عقب‌گرد است و اجرا با خطا متوقف می‌شود.
* **سرور** (در `deploy.sh`): `anti_downgrade_gate` هدف را با `origin/main` و با
  HEADِ فعلی مقایسه می‌کند؛ اگر هدف ancestor یا divergent باشد ⇒ رد. برای rollback
  دستیِ اضطراری، `ALLOW_OLDER_SHA=1` به‌صورت صریح لازم است.

پیش از `git reset --hard`، تغییراتِ محلیِ سرور در
`/opt/backups/dirty-tree-<timestamp>.patch` حفظ می‌شود (`git status` + هر دو diff +
فهرستِ فایل‌های untracked) ⇒ hotfixِ دستیِ اپراتور پاک نمی‌شود.

## ۹. راستی‌آزماییِ حفاظتِ مخزن (DEVOPS-06/07)

```bash
python tools/verify_github_protection.py --repo Aryasamadi/Bot-Official-Telegram-
```

این ابزار *می‌خواند*: حفاظتِ شاخهٔ `main` (بازبینیِ PR، checkِ وضعیت، اعمال برای
ادمین‌ها) و محیطِ `production` (Required reviewers) را بررسی می‌کند. اگر API در
دسترس نباشد، **نامعلوم = سالم حساب نمی‌شود** و ابزار با خطا خارج می‌شود.

## ۱۰. عیب‌یابی

| نشانه | بررسی |
|---|---|
| کانتینر بالا نمی‌آید | `docker logs --tail 100 bot-official-telegram` · `docker inspect … .State` |
| سلامت ناموفق | `docker exec bot-official-telegram python -m newsbot.healthcheck` |
| انتشارها متوقف شده‌اند | `docker exec … ls -l /tmp/newsbot_draining` (نباید باقی مانده باشد) |
| نسخهٔ اجراشده نامعلوم | `docker inspect … --format '{{.Config.Image}}'` (باید تگِ SHA باشد) |
| SSH رد شد | `ssh -v deploy@<host> 'preflight'` ⇒ فقط چهار فرمان مجازند |
| استقرار عقب‌گرد رد شد | پیامِ `REFUSED:` در لاگ ⇒ SHA قدیمی‌تر است؛ آخرین SHAِ سبز را deploy کنید |

## ۱۱. فرمان‌های روزمره

```bash
ssh deploy@<host> 'preflight'     # HEAD، نسخهٔ مستقر، وضعیتِ کانتینر
ssh deploy@<host> 'health'        # healthcheck داخلِ کانتینر
ssh deploy@<host> 'deploy <sha>'  # استقرارِ دستیِ یک SHA
ssh deploy@<host> 'rollback'      # بازگشت به نسخهٔ قبلی
```

اجرای دستی از Actions (بدونِ نیاز به دسترسیِ مستقیم):
Actions ← *Deploy to VPS* ← *Run workflow* ← SHA را بدهید (باید CIِ سبز داشته باشد).
