# گزارشِ ممیزیِ مخزن — Session 3 (فاز C): CI/CD، Docker، استقرار و مستندات

> **تاریخ:** ۲۰۲۶-۱۱-۰۶ · **مخزن:** `Aryasamadi/Bot-Official-Telegram-` · **شاخه:** `main`
> **دامنه:** ۷ یافتهٔ ممیزی (TEST-01، DEVOPS-01/03/05/06/07/08) + ۴ ناسازگاریِ مستند/پیکربندی
> **روش:** خواندنِ کد و پیکربندی، اجرای *واقعی* ابزارها و اسکریپت‌ها، و سنجشِ خروجی.

---

## ۱. چکیدهٔ اجرایی

| | |
|---|---|
| **یافته‌های بررسی‌شده** | ۷ (TEST-01، DEVOPS-01، DEVOPS-03، DEVOPS-05، DEVOPS-06، DEVOPS-07، DEVOPS-08) |
| **ناسازگاری‌های مستند/پیکربندی** | ۴ (شمارهٔ تست، مسیرهای استقرار، چیدمانِ دیتابیس، ادعاهای README) |
| **رفع‌شده** | **۱۱ از ۱۱** (۱۰۰٪) — هرکدام با شاهدِ اجرایی |
| **تست‌ها** | ۸۴۴ تست، همه سبز (پیش از این فاز: ۷۷۰ ادعا شده بود — نادرست) |
| **پوششِ کد** | **۷۰٪** واقعی (`newsbot`، ۹۱۵۸ دستور، ۲۷۰۴ miss) — دروازهٔ CI روی ۶۸٪ قفل شد |
| **کیفیتِ ایستا** | `pyflakes` بی‌خروجی · `ruff check` «All checks passed!» · `mypy newsbot` «no issues found in 44 source files» |
| **امنیتِ وابستگی** | `pip-audit` ⇒ «No known vulnerabilities found» (بعد از اصلاحِ CVEِ certifi) |
| **حفاظتِ مخزن (زنده)** | شاخهٔ `main` حفاظت‌شده شد و محیطِ `production` تأییدِ دستی دارد — با API راستی‌آزمایی شد |
| **باقی‌ماندهٔ خارج از دسترس** | اجرای واقعیِ deploy روی VPS (دسترسیِ شبکه‌ای به سرور در این محیط وجود ندارد) |

**داوری:** یافته‌های این فاز *نقصِ محصول* نبودند؛ نقصِ **فرایندِ تحویل** بودند
(گیتِ کیفیتِ ناقص، استقرارِ غیرقابلِ بازگشت، دسترسیِ root، و مستنداتِ ناهماهنگ).
اصلاحات انجام شد و هرکدام با فرمانِ واقعی سنجیده شده‌اند.

---

## ۲. یافته‌ها

### TEST-01 — نبودِ دروازهٔ پوششِ تست

| | |
|---|---|
| **شدت** | MEDIUM · P1 |
| **وضعیت پیش از اصلاح** | CI فقط `pytest -q` اجرا می‌کرد؛ هیچ سنجهٔ پوششی وجود نداشت ⇒ افتِ پوشش *دیده* نمی‌شد و هیچ‌کس نمی‌دانست. |
| **شاهد** | `.github/workflows/ci.yml` (نسخهٔ قبل): بدونِ `--cov` و بدونِ `--cov-fail-under`. |
| **اصلاح** | ① `pyproject.toml` (منبعِ واحدِ تنظیماتِ ruff/mypy/pytest) ② افزودنِ `pytest-cov==7.0.0` به `requirements-dev.txt` ③ اجرای تست‌ها با `--cov=newsbot --cov-report=term-missing --cov-report=xml:coverage.xml --cov-report=json:coverage.json --junitxml=junit.xml --cov-fail-under=68` ④ انتشارِ `coverage.xml`/`coverage.json`/`junit.xml` به‌صورت artifact. |
| **خطِ پایهٔ واقعی** | **۷۰٪** (۹۱۵۸ دستور، ۲۷۰۶ Miss) روی Python 3.14؛ دروازه روی **۶۸٪** قفل شد تا نوسانِ نسخهٔ پایتون (۳.۱۳ در CI) CI را نشکند. |
| **راستی‌آزمایی** | `pytest -q --cov=newsbot --cov-report=term-missing` ⇒ `844 passed` و `TOTAL 9158 2704 70%`. |

### DEVOPS-01 — استقرار با `root` روی VPS

| | |
|---|---|
| **شدت** | HIGH · P1 |
| **وضعیت پیش از اصلاح** | `deploy-vps.yml` با `username: root` وصل می‌شد؛ هر نشستِ SSH یک پوستهٔ کاملِ root بود و کلیدِ CI عملاً *همه‌چیز* بود. |
| **اصلاح** | ① کاربرِ سیستمیِ `deploy` با `deploy/setup-deploy-user.sh` (عضوِ گروهِ docker، رمزِ قفل‌شده، بدونِ پوستهٔ آزاد) ② قاعدهٔ محدودِ `/etc/sudoers.d/newsbot-deploy` که فقط اجرای اسکریپتِ استقرار را اجازه می‌دهد و **قبل از نصب با `visudo -c` اعتبارسنجی می‌شود** ③ فرمانِ اجباری در `deploy/ssh-forced-command.sh`: فقط `deploy <۴۰-hex>`، `rollback`، `preflight`، `health` — بقیه exit 126 ④ کلید با `no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty` ⑤ workflow با `username: ${{ secrets.VPS_USER || 'deploy' }}`. |
| **راستی‌آزمایی** | `bash deploy/setup-deploy-user.sh --dry-run --key-file …` ⇒ تولیدِ کاربر/گروه/قاعدهٔ sudoers/کلید؛ `bash -n` روی هر سه اسکریپت ⇒ بدونِ خطا. |
| **نکتهٔ صادقانه** | عضویت در گروهِ `docker` عملاً root می‌دهد؛ فرمانِ اجباری جلوی *سوءاستفادهٔ تصادفی* را می‌گیرد و ردِ ممیزی می‌سازد، نه این‌که کاربر را از root جدا کند. |

### DEVOPS-03 — استقرارِ کانتینری با ایمیجِ immutable

| | |
|---|---|
| **شدت** | HIGH · P1 |
| **وضعیت پیش از اصلاح** | حالتِ docker در `deploy.sh` کدِ میزبان را به `/app` bind-mount می‌کرد، `pip install` داخلِ کانتینرِ زنده اجرا می‌کرد و با `docker restart` بالا می‌آورد ⇒ «ایمیجِ اجراشده» با «کدِ مستقرشده» یکی نبود و rollback یعنی «نصبِ دوباره روی کانتینرِ زنده». |
| **اصلاح** | ① `docker-compose.yml` به‌عنوان **تنها** منبعِ حقیقتِ Compose (در ریشهٔ مخزن) ② `deploy/Dockerfile`: کد + وابستگی‌های قفل‌شده داخلِ ایمیج، `ARG/ENV NEWSBOT_VERSION` (SHA تزریق‌شده)، base image با digest پین‌شده، کاربرِ غیرroot (uid 10001) ③ `deploy.sh`: `build_image` با تگِ `newsbot:<sha>` ⇒ دروازهٔ پیش از restart **روی همان ایمیجِ تازه** اجرا می‌شود ⇒ بازساخت با `docker compose up -d --force-recreate --no-build` (fallback: `docker run` با همان سخت‌سازی) ⇒ rollback = برگشت به تگِ ایمیجِ قبلی ④ دیتابیس به مسیرِ استانداردِ `/opt/newsbot-data/newsbot.db` منتقل می‌شود (همراه `-wal`/`-shm` و پوشهٔ آرشیو) ⑤ سخت‌سازی: `read_only`، `tmpfs /tmp`، `cap_drop: ALL`، `no-new-privileges`، `mem_limit 512m`، `pids_limit 256`، لاگِ چرخشی. |
| **راستی‌آزمایی** | `tests/test_deploy_script.py` (۳۳ تست) — از جمله: «ایمیج با تگِ SHA ساخته می‌شود»، «دیگر `pip install` در کانتینرِ زنده نیست»، «دروازه روی ایمیجِ تازه اجرا می‌شود»، «کانتینر یک‌بار از روی ایمیج بازساخته می‌شود»، «شکستِ سلامت ⇒ بازساختِ دوم با نسخهٔ قبلی». |

### DEVOPS-05 — گیت‌های کیفیت و امنیت در CI

| | |
|---|---|
| **شدت** | HIGH · P1 |
| **وضعیت پیش از اصلاح** | CI فقط pyflakes + pytest + `final_verification.py` داشت؛ نه lint، نه بررسیِ نوع، نه اسکنِ آسیب‌پذیری، نه اسکنِ راز، نه بررسیِ Dockerfile، نه اسکنِ ایمیج. |
| **اصلاح** | یک job (`test` — همان contextِ لازمِ حفاظتِ شاخه) با این گیت‌ها: `pyflakes` · `ruff check newsbot tests tools run.py` · `mypy newsbot` · `py_compile` · `pip-audit -r requirements.txt --no-deps` · `gitleaks` (پین‌شده به SHA) · `hadolint` (ایمیج با digest پین‌شده) · `trivy` (اسکنِ فایل‌سیستم + اسکنِ ایمیجِ production) · `docker compose config` · buildِ ایمیجِ production با `NEWSBOT_VERSION=${{ github.sha }}`. همهٔ اکشن‌ها به **SHA کاملِ کامیت** پین شده‌اند. |
| **راستی‌آزمایی** | `ruff check newsbot tests tools run.py` ⇒ «All checks passed!» · `mypy newsbot` ⇒ «Success: no issues found in 44 source files» · `pip-audit -r requirements.txt --no-deps` ⇒ «No known vulnerabilities found». |

### DEVOPS-06 — دروازهٔ ضدِ عقب‌گرد و حفظِ درختِ کاریِ سرور

| | |
|---|---|
| **شدت** | HIGH · P1 |
| **وضعیت پیش از اصلاح** | صفِ `workflow_run` ترتیبِ زمانیِ کامیت‌ها را تضمین نمی‌کند ⇒ یک CIِ قدیمی‌تر که دیرتر تمام می‌شد می‌توانست **نسخهٔ جدیدتر را روی سرور بنویسد**؛ ضمناً `git reset --hard` هر hotfixِ دستیِ اپراتور را پاک می‌کرد. |
| **اصلاح** | ① دروازهٔ *کلاینت* در `deploy-vps.yml`: اگر SHA هدف نوکِ `origin/main` نباشد ولی در تاریخچهٔ آن باشد ⇒ رد ② دروازهٔ *سرور* (`anti_downgrade_gate` در `deploy.sh`): هدفِ ancestorِ `origin/main` یا HEADِ فعلی، یا divergent ⇒ رد (escape: `ALLOW_OLDER_SHA=1`) ③ `preserve_dirty_tree`: پیش از reset، `git status` + هر دو diff + فهرستِ untracked در `/opt/backups/dirty-tree-<ts>.patch` ④ `tools/verify_github_protection.py` برای راستی‌آزماییِ زندهٔ حفاظتِ مخزن. |
| **راستی‌آزمایی** | ① **زنده** (API): پیش از این فاز، `main` اصلاً حفاظت‌شده نبود (`{"message":"Branch not protected"}`)؛ اکنون حفاظت فعال است: ۱ بازبینیِ لازم، checkِ `test` (strict)، `enforce_admins`، و محیطِ `production` با reviewerِ اجباری + branch policy برای `main` ⇒ `verify_github_protection.py` ⇒ «✅ the repository protection is complete». ② محلی: `tests/test_session3_cicd_docker_deploy_docs.py` (بازآزماییِ دروازه روی یک گیتِ واقعیِ sandbox). |

### DEVOPS-07 — ناسازگاریِ مستندات با پیکربندی

| | |
|---|---|
| **شدت** | MEDIUM · P2 |
| **وضعیت پیش از اصلاح** | مستندات مسیرهای قدیمی (`/opt/bot-official-telegram`، bind-mountِ `/app`)، دیتابیسِ `/app/newsbot.db`، «۷۷۰ تست»، «سلامت با `run.py --check`» و «نصبِ وابستگی داخلِ کانتینر» را توصیف می‌کردند — همه خلافِ کدِ واقعی. |
| **اصلاح** | ① `docs/VPS_DEPLOYMENT.md` (مرجعِ جدیدِ استقرار) ② `deploy/systemd/README.md` با برچسبِ صریحِ **LEGACY** ③ بخشِ «به‌روزرسانی فاز C» در ابتدای `docs/DEPLOY_RUNBOOK.md` (تاریخچهٔ قدیمی دست‌نخورده ماند) ④ اصلاحِ badgeهای `README.md` (تعدادِ تست، نسخهٔ پایتون، نسخهٔ PTB) ⑤ به‌روزرسانیِ وضعیتِ جاری در `docs/audit/MASTER_STATUS.md` و `docs/audit/status.json` (ویرایشِ جراحیِ فیلدهای جاری؛ تاریخچه حفظ شد) ⑥ گزارش‌های ممیزی: `docs/REPOSITORY_AUDIT_REPORT.md`، `docs/audit/MASTER_AUDIT_AND_REMEDIATION_REPORT.md` و چهار بخشِ `docs/audit/parts/`. |
| **راستی‌آزمایی** | `tests/test_session3_cicd_docker_deploy_docs.py` — هم‌خوانیِ مستندات با `deploy.sh`، `deploy/Dockerfile` و `docker-compose.yml` (نامِ کانتینر، مسیرِ دیتابیس، فرمانِ healthcheck، تگِ ایمیج، نبودِ bind-mountِ کد). |

### DEVOPS-08 — تخلیهٔ ایمن پیش از ری‌استارت

| | |
|---|---|
| **شدت** | HIGH · P1 |
| **وضعیت پیش از اصلاح** | `docker stop` وسطِ یک ارسال ⇒ مقاله در ژورنال `delivery_unknown` می‌ماند و نیاز به دخالتِ دستی پیدا می‌کند. |
| **اصلاح** | ① ماشینِ حالت در `newsbot/runtime.py`: `request_drain`/`cancel_drain`/`clear_stale_drain`/`begin_publish`/`end_publish`/`wait_for_in_flight_publishes`/`drain_watch_loop` ② دروازه در `publish.py` (انتشارِ تازه شروع نمی‌شود ⇒ `"draining"`)، در `scheduler.py` (تیکِ تازه) و در `cycle.py` (چرخهٔ تازه؛ صفِ باقی‌مانده دست‌نخورده می‌ماند) ③ `post_shutdown` پیش از لغوِ تسک‌ها منتظرِ پایانِ انتشارهای جاری می‌ماند ④ اسکریپتِ استقرار پرچم را *داخلِ کانتینر* می‌سازد، منتظرِ پرچمِ «تخلیه شد» می‌ماند (سقف ۳۰ ثانیه) و بعد `docker stop --time 30` می‌کند ⑤ نشانگرها در `newsbot/config.py` و `.env.example` (هم‌تراز با تستِ هم‌راستاییِ config). |
| **راستی‌آزمایی** | `tests/test_session3_cicd_docker_deploy_docs.py` (ترتیبِ drain پیش از stop، و رفتارِ دروازه‌ها) + `tests/test_deploy_script.py` (پروتکلِ drain داخلِ استابِ docker). |

---

## ۳. ناسازگاری‌های مستند/پیکربندی (۴ مورد)

| # | ادعای قدیمی | واقعیتِ کد | اصلاح |
|---|---|---|---|
| D-1 | «۷۷۰ تست سبز» (badgeِ README) | **۸۴۴** تست | badge به `tests-844 passing` تغییر کرد |
| D-2 | مسیرِ کد `/opt/bot-official-telegram` و bind-mountِ `/app` | مسیرِ واقعی `/opt/Bot-Official-Telegram` و **بدونِ** bind-mountِ کد | `docs/VPS_DEPLOYMENT.md` + بخشِ به‌روزرسانی در runbook |
| D-3 | دیتابیس در `/app/newsbot.db` (داخلِ کانتینرِ bind-mounted) | دیتابیس در `/opt/newsbot-data/newsbot.db` (حجمِ ماندگار) | compose + runbook + اسکریپتِ مهاجرتِ مسیر |
| D-4 | سلامت = `run.py --check` هر ۵ دقیقه؛ وابستگی‌ها داخلِ کانتینر نصب می‌شوند | healthcheckِ سبکِ `newsbot/healthcheck.py` هر ۶۰ ثانیه؛ وابستگی‌ها داخلِ ایمیج | `newsbot/healthcheck.py` + `deploy/Dockerfile` + `docker-compose.yml` |

---

## ۴. فرمان‌های راستی‌آزمایی و خروجیِ واقعی

| فرمان | خروجی |
|---|---|
| `python -m pyflakes newsbot tools run.py tests` | *(بدونِ خروجی — پاک)* |
| `ruff check newsbot tests tools run.py` | `All checks passed!` |
| `mypy newsbot` | `Success: no issues found in 44 source files` |
| `python -m py_compile run.py $(find newsbot tools tests -name '*.py')` | *(بدونِ خروجی — پاک)* |
| `pytest -q --cov=newsbot --cov-report=term-missing` | `844 passed` · `TOTAL 9158 2704 70%` |
| `python tools/final_verification.py` | همهٔ بررسی‌ها سبز |
| `bash -n deploy/*.sh` | *(بدونِ خروجی — پاک)* |
| `pip-audit -r requirements.txt --no-deps` | `No known vulnerabilities found` |
| `python tools/verify_github_protection.py --repo …` | `✅ branch …` + `✅ environment …` |

## ۵. آنچه *انجام نشد* (صادقانه)

* **استقرارِ واقعی روی VPS اجرا نشد.** از این محیط دسترسیِ شبکه‌ای به سرور وجود
  ندارد (مبدأ IPv6-only و بدونِ مسیر؛ IPv4 هم timeout می‌شود). تمامِ سنجش‌های
  استقرار *روی محیطِ شبیه‌سازی‌شدهٔ واقعی* (sandbox با git و استاب‌های systemd/docker)
  انجام شده‌اند. راهِ عملی: اجرای دستیِ workflow از Actions (متغیر `VPS_USER` و
  کلیدِ کاربرِ `deploy` باید روی سرور آماده باشد).
* **مهاجرتِ مسیرِ دیتابیس روی دادهٔ واقعی آزموده نشده** (فایلِ واقعیِ دیتابیس در
  دسترس نبود)؛ منطقِ آن با مسیرهای sandbox آزموده شده است.
* **قاعدهٔ sudoers روی سرورِ واقعی اعمال نشده**؛ اعتبارسنجیِ `visudo -c` روی
  فایلِ تولیدی انجام شد (خروجیِ واقعی در لاگِ همین فاز موجود است).

## ۶. نقشهٔ تغییرات

| ناحیه | فایل‌ها |
|---|---|
| **جدید** | `docker-compose.yml` · `deploy/setup-deploy-user.sh` · `deploy/ssh-forced-command.sh` · `deploy/systemd/README.md` · `newsbot/healthcheck.py` · `tools/verify_github_protection.py` · `pyproject.toml` · `docs/VPS_DEPLOYMENT.md` · `docs/REPOSITORY_AUDIT_REPORT.md` · `docs/audit/MASTER_AUDIT_AND_REMEDIATION_REPORT.md` · `docs/audit/parts/01..04-*.md` · `tests/test_session3_cicd_docker_deploy_docs.py` |
| **تغییرکرده (هسته)** | `deploy/deploy.sh` · `deploy/Dockerfile` · `.github/workflows/ci.yml` · `.github/workflows/deploy-vps.yml` · `newsbot/runtime.py` · `newsbot/publish.py` · `newsbot/scheduler.py` · `newsbot/cycle.py` · `newsbot/app.py` · `newsbot/config.py` · `newsbot/healthcheck.py` · `newsbot/ui_views_super.py` |
| **تغییرکرده (کیفیت)** | `requirements.txt` (certifi) · `requirements-dev.txt` · ۲۷ یافتهٔ Ruff در `newsbot/` و `tests/`/`tools/` · annotationهای mypy در ۹ ماژول |
| **مستندات** | `README.md` · `docs/DEPLOY_RUNBOOK.md` · `docs/audit/MASTER_STATUS.md` · `docs/audit/status.json` |
