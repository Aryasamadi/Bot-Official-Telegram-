# بخش ۰۴ — VPS، DevOps، کارایی و مقیاس‌پذیری

> بخشی از گزارشِ ممیزیِ Session 3 (فاز C). مرجعِ کلی:
> `docs/audit/MASTER_AUDIT_AND_REMEDIATION_REPORT.md` · گزارشِ جزئی:
> `docs/REPOSITORY_AUDIT_REPORT.md`.

## دامنه

دسترسی به سرور، فرایندِ استقرار، کیفیتِ ایمیج و کانتینر، دروازه‌های CI، حفاظتِ
مخزن در GitHub، رول‌بک و اثرِ این تغییرات بر کارایی.

---

## ۱. DEVOPS-01 — کاربرِ اختصاصیِ استقرار

**شدت:** HIGH · P1 · **وضعیت:** ✅ رفع‌شده

| قبل | اکنون |
|---|---|
| `username: root` در هر دو اکشنِ SSH/SCP | `username: ${{ secrets.VPS_USER || 'deploy' }}` |
| هر نشست = پوستهٔ کاملِ root | فرمانِ اجباری: فقط `deploy <sha>` · `rollback` · `preflight` · `health` |
| بدونِ محدودیتِ forwarding | `no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty` |
| — | قاعدهٔ sudoersِ محدود + اعتبارسنجی با `visudo -c` قبل از نصب |

ابزارِ نصب: `deploy/setup-deploy-user.sh` (idempotent، با `--dry-run`).

## ۲. DEVOPS-03 — ایمیجِ immutable و Compose

**شدت:** HIGH · P1 · **وضعیت:** ✅ رفع‌شده

`docker-compose.yml` (ریشهٔ مخزن) تنها منبعِ حقیقتِ Compose است:

| تنظیم | مقدار | چرا |
|---|---|---|
| `image` | `${NEWSBOT_IMAGE:-newsbot:latest}` | تگِ SHA را `deploy.sh` می‌گذارد |
| `read_only` | `true` | کد و FS ریشه تغییرپذیر نیستند |
| `tmpfs` | `/tmp:rw,noexec,nosuid,nodev,size=64m` | فضایِ نوشتنیِ کوچک برای نشانگرها و فایل‌های موقت |
| `cap_drop` | `ALL` | بدونِ هیچ قابلیتِ اضافه |
| `security_opt` | `no-new-privileges:true` | جلوگیری از افزایشِ امتیاز |
| `mem_limit` / `cpus` / `pids_limit` | `512m` / `1.0` / `256` | سقفِ منابع (اسکنِ OOM و fork-bomb) |
| `logging` | `json-file` با `max-size=10m` · `max-file=5` | لاگِ بی‌نهایت نشود |
| `healthcheck` | `python -m newsbot.healthcheck` | بررسیِ سبک و بدونِ توکن |
| `stop_grace_period` | `40s` | بیشتر از مهلتِ تخلیه (۳۰ ثانیه) |
| volumes | `/opt/newsbot-data:/app/data` و `/opt/backups:/opt/backups:ro` | داده ماندگار، بکاپ فقط‌خواندنی |
| **بدونِ** bind-mountِ کد | — | کد داخلِ ایمیج است |

اسکریپتِ استقرار: `build_image` ⇒ `gate` روی همان ایمیج ⇒
`docker compose up -d --force-recreate --no-build` (fallback با `docker run` و
همان محدودیت‌ها اگر پلاگینِ compose نباشد) ⇒ rollback به تگِ قبلی.

## ۳. DEVOPS-05 — گیت‌های CI

**شدت:** HIGH · P1 · **وضعیت:** ✅ رفع‌شده

همهٔ گیت‌ها داخلِ **یک** job (`test`) هستند تا همان checkِ لازمِ شاخهٔ `main`
(که در حفاظتِ شاخه ثبت شده) واقعاً همهٔ گیت‌ها را پوشش دهد؛ شکستِ یک jobِ جدا
merge را متوقف نمی‌کند.

| گیت | ابزار | پین |
|---|---|---|
| نامِ تعریف‌نشده | `pyflakes` | — |
| lint | `ruff` (قوانین در `pyproject.toml`) | `ruff==0.14.4` |
| نوع | `mypy newsbot` | `mypy==1.18.2` |
| آسیب‌پذیری | `pip-audit -r requirements.txt --no-deps` | `pip-audit==2.9.0` |
| راز | `gitleaks/gitleaks-action` | SHA: `ff98106e4c7b2bc287b24eaf42907196329070c7` |
| Dockerfile | `hadolint/hadolint` | digest: `sha256:30a8fd2e…` |
| اسکنِ فایل‌سیستم و ایمیج | `aquasecurity/trivy-action` | SHA: `b6643a29fecd7f34b3597bc6acb0a98b03d33ff8` |
| پوشش | `pytest --cov … --cov-fail-under=68` | `pytest-cov==7.0.0` |

همهٔ اکشن‌های GitHub به **SHA کاملِ کامیت** پین شده‌اند (نه تگِ متحرک).

## ۴. DEVOPS-06 — ضدِ عقب‌گرد و حفاظتِ مخزن

**شدت:** HIGH · P1 · **وضعیت:** ✅ رفع‌شده

### دو دروازهٔ مستقل

1. **کلاینت** (`deploy-vps.yml`): اگر SHA هدف نوکِ `origin/main` نباشد ولی در
   تاریخچهٔ آن باشد ⇒ «SHA قدیمی‌تر از نوکِ main» ⇒ خطا و توقف.
2. **سرور** (`deploy.sh` → `anti_downgrade_gate`): هدفِ ancestorِ `origin/main`،
   ancestorِ HEADِ فعلی، یا divergent ⇒ رد. `ALLOW_OLDER_SHA=1` فقط به‌صورتِ صریح
   (برای rollbackِ اضطراری).

### حفظِ درختِ کاریِ سرور

`preserve_dirty_tree` پیش از `git reset --hard`، وضعیت و patch را در
`/opt/backups/dirty-tree-<timestamp>.patch` می‌نویسد (`git status --porcelain` +
`git diff` + `git diff --cached` + فهرستِ untracked) ⇒ hotfixِ دستیِ اپراتور گم
نمی‌شود.

### حفاظتِ مخزن (وضعیتِ زنده)

پیش از این فاز، API می‌گفت: `{"message":"Branch not protected"}`. اکنون:

* `main`: یک بازبینیِ PR لازم، checkِ `test` به‌صورت strict، `enforce_admins` روشن؛
* محیطِ `production`: reviewerِ اجباری + wait timer + branch policy برای `main`.

ابزارِ راستی‌آزمایی: `tools/verify_github_protection.py` (fail-closed: اگر API در
دسترس نباشد، «نامعلوم» سالم حساب نمی‌شود).

## ۵. DEVOPS-08 — تخلیهٔ ایمن

**شدت:** HIGH · P1 · **وضعیت:** ✅ رفع‌شده

پیش از `docker stop --time 30`، ربات به حالتِ تخلیه می‌رود و تا پایانِ
انتشارهای در حالِ پرواز صبر می‌کند (جزئیات در بخش ۰۳). اثرِ عملیاتی: هیچ
`delivery_unknown` در زمانِ استقرار ساخته نمی‌شود.

## ۶. DEVOPS-07 / D-2 / D-3 / D-4 — ناسازگاریِ مستندات

**وضعیت:** ✅ رفع‌شده

| ادعای قدیمی | واقعیتِ اکنون |
|---|---|
| `/opt/bot-official-telegram` | `/opt/Bot-Official-Telegram` (حروفِ بزرگ) |
| bind-mountِ `/app` از کدِ میزبان | بدونِ bind-mountِ کد |
| دیتابیس در `/app/newsbot.db` | `/opt/newsbot-data/newsbot.db` (حجمِ ماندگار) |
| سلامت با `run.py --check` هر ۵ دقیقه | `newsbot/healthcheck.py` هر ۶۰ ثانیه |
| `pip install` داخلِ کانتینر | وابستگی‌ها داخلِ ایمیج |
| نصبِ وابستگی در rollback | rollback = برگشت به تگِ ایمیجِ قبلی |

مرجعِ جدید: `docs/VPS_DEPLOYMENT.md`. حالتِ systemd صریحاً **LEGACY** شد
(`deploy/systemd/README.md`). تاریخچهٔ `docs/DEPLOY_RUNBOOK.md` حفظ شد و یک بخشِ
«به‌روزرسانی فاز C» به آن اضافه شد.

## ۷. اثر بر کارایی

| موضوع | وضعیت |
|---|---|
| مصرفِ حافظهٔ کانتینر | سقفِ ۵۱۲ مگابایت (قبلاً بدونِ سقف ⇒ OOMِ خاموشِ کلِ VPS ممکن بود) |
| لاگِ دیسک | چرخش (۱۰ مگ × ۵) ⇒ پر شدنِ دیسک از سمتِ لاگ ممکن نیست |
| زمانِ استقرار | build (~۳۰–۶۰ ثانیه) + بازساخت + healthcheck؛ در عوض rollback به یک تعویضِ تگ محدود می‌شود |
| تخلیه | حداکثر ۳۰ ثانیه تأخیرِ اضافه در استقرار (قابل تنظیم با `DRAIN_TIMEOUT`) |
| کشِ CI | کشِ pip با کلیدِ مبتنی بر `requirements*.txt` ⇒ نصبِ وابستگی‌ها تکراری نباشد |

## ۸. ریسک‌های باقی‌مانده

۱. **دسترسیِ شبکه به VPS** — از این محیط ممکن نیست؛ نخستین استقرارِ واقعی باید
   از Actions و با آماده‌بودنِ کاربرِ `deploy` روی سرور انجام شود.
۲. **بوت‌استرپِ اسکریپت** — تا وقتی نسخهٔ جدیدِ `deploy.sh` یک‌بار مستقر نشده، خودِ
   اسکریپتِ روی سرور نسخهٔ قبلی است (به همین دلیل `deploy.sh` اسکریپتِ کامیتِ هدف
   را استخراج و اجرا می‌کند و ورک‌فلو هم نسخهٔ هدف را scp می‌کند).
3. **نگه‌داریِ ایمیج‌ها** — ایمیج‌های قدیمی روی میزبان می‌مانند؛ یک cron برای
   حذفِ ایمیج‌های قدیمی‌تر از N روز پیشنهاد می‌شود (خارج از دامنهٔ این فاز).
