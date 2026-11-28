# PHASE 10 FINAL REPORT — سخت‌سازی، پاک‌سازی، دروازهٔ انتشار

تاریخ: 2026-11-27 (UTC) · وضعیتِ فاز ۱۰: **`IN_PROGRESS`** (دو دروازهٔ مالک هنوز باز است)

این گزارش فقط **اجرای واقعی** را ثبت می‌کند. هر چیزی که اجرا نشده، «نامشخص» است، نه
«سبز». شماره‌ها و زمان‌ها از اجراهای واقعی گرفته شده‌اند.

---

## ۱. جدولِ یافته‌ها

| یافته | وضعیت | شواهد |
|---|---|---|
| **AUD-001** (چرخشِ راز) | **نیمه‌بسته** — ابزارِ `tools/rotate_secrets_key.py` + runbookِ `docs/OPS_SECRET_ROTATION.md` تحویل شد؛ **خودِ چرخش دروازهٔ مالک است** و هنوز اجرا نشده؛ کلیدهای Provider بازصدور **نشدند** ⇒ ریسکِ باقی = **D-13** | ۱۸ تستِ چرخش (PR #109) · runbook · `newsbot-deploy-pkg\newsbot.env` هنوز موجود است (دست‌نخورده، طبق C8) |
| **AUD-012** (۱۹ شکستِ ویندوز) | **بسته** — ریشه‌یابی شد: ۱۵ شکست از نبودِ Git Bash (WSL) و ۴ شکست از تبدیلِ مسیرِ MSYS؛ روی ویندوز **۰ شکست** | `tests/_shell.py` (helper) · فقط `MSYS2_ARG_CONV_EXCL=/app` · خروجی LF و نرمال‌سازیِ POSIX · `pytest tests/test_deploy_script.py tests/test_session3_cicd_docker_deploy_docs.py -q` ⇒ **۹۷ passed / ۰ failed** · suite کامل ۱۲۰۸/۰ · PR #109 (`b660bc3`) |
| **AUD-014** (نظافتِ شاخه/کد) | **بسته در حدِ اثبات** — **۳۳ شاخه** حذف شد (۳۶ نامزد؛ ۳ مورد به‌دلیلِ جابه‌جاییِ شاخه پس از merge نگه داشته شدند)؛ **۱۶ شاخه** باقی و فهرست‌شده؛ refهای ردیابیِ کهنه با `git fetch --prune` پاک شدند (۵۱ ref پس از گامِ اول، ۱۹ ref پس از گامِ دوم باقی ماند)؛ **۱۱ تابعِ مرده** با اثباتِ سه‌گانه حذف شد (اسکن: ۱۱ ← ۰) | شاهد: `phase10_branch_cleanup.txt` · PR #110 (`ef185ca`) · اسکنِ کدِ مرده: `phase10_deadcode_before.txt` / `phase10_deadcode_after.txt` |
| **D-4** (۱۱ PRِ Dependabot) | **به تعویق بعد از انتشار** (تصمیمِ مالک: Q3=الف) | #68–#78: `python 3.13-slim → 3.15-slim` · `actions/checkout 4 → 7` · `setup-python 5 → 7` · `upload-artifact 4 → 7` · `gitleaks-action 2 → 3` · `appleboy/ssh-action 1.2.2 → 1.2.5` · `python-dateutil 2.9 → 4.0` · `tzlocal → 5.4.5` · `soupsieve → 4.1` · `regex → 2026.11.9` · `ruff 0.14 → 0.17` |
| **D-12** (`sources.api_key` متنی) | **باز** — خارج از دامنهٔ فاز ۱۰ | `newsbot/core_sources.py` (کلیدِ منبع به‌صورت متنی ذخیره می‌شود) |
| **D-13** (بازصدورنشدنِ کلیدهای Provider) | **باز** — تصمیمِ مالک: فقط `BOT_TOKEN` + `SECRETS_KEY` چرخش می‌کنند؛ نسخه‌های قدیمیِ دیتابیس با کلیدِ لو رفته همچنان رمزگشایی‌پذیرند | تصمیمِ Q1 در §8 فایلِ وضعیت |
| **F-1** (504 / تستِ اتصالِ کند) | **مشاهدهٔ باز** — بدونِ اصلاح در فاز ۱۰ (تغییرِ رفتار خارج از دامنه) | اجرای زندهٔ 46013627630: **۳×** `HTTP 504` · **۲×** `ai_timeout` · **۲۸×** `probe timed out after 15s` (۲۸ هشدارِ probe-timeout از مجموعِ ۳۶ خطِ `WARNING`) · کد: `newsbot/ai_models.py:310-311`، `newsbot/net_http.py:334`، `ai_models.py:607` |
| **F-2** (`CVE-2026-124851` در `libzstd1`) | **بسته** | PR #108 (`361a10c`) · `--only-upgrade zlib1g libzstd1` در `deploy/Dockerfile` · گام «Image scan (trivy)» سبز در run 46047560145 |
| **F-3** (نشتِ کاذبِ سنجشِ لاگ) | **بسته** | `secret_values_present` حالا مرزِ مستقل می‌خواهد ⇒ **۲۶۱ خط** دیگر «نشت» گزارش نمی‌شود (خودِ شناسه هرگز چاپ نشده بود) · ۲ تستِ تازه · PR #109 |
| **D-6** (کشِ `qa-live`) | **بسته در سطحِ کد** — پیشوند `qa-live-db-v1-` ⇒ `qa-live-db-v2-`؛ کش‌های v1 باید در پنجرهٔ مالک حذف شوند و اجرای بعدی `fresh` باشد | `.github/workflows/qa-live.yml` · PR #109 · `docs/OPS_SECRET_ROTATION.md` §7 |

## ۲. PRهای فاز ۱۰

| PR | موضوع | Merge | CI | محافظت |
|---|---|---|---|---|
| #108 | `fix(ci): upgrade libzstd1 for CVE-2026-124851` | `361a10c` | 46047560145 ✔ | ۱۵/۱۵ |
| #107 | `docs(phase10): approved design and implementation plan` | `9886972` | 46061459633 ✔ | ۱۵/۱۵ |
| #109 | `feat(phase10): harden windows tests and secrets rotation` | `b660bc3` | 46075556814 ✔ | ۱۵/۱۵ |
| #110 | `chore(phase10): remove 11 provably dead helpers` | `ef185ca` | 46082906798 ✔ | ۱۵/۱۵ |
| PR3 (#111) | اسنادِ فاز ۱۰ | `e0698ef` | 46104240680 · main: 46105260191 ✔ | ۱۵/۱۵ |
| PR3 follow-up (#112) | هم‌گام‌سازیِ سندِ وضعیت با اجرای استقرارِ تازه | `1d7916a` | 46108126154 · main: 46108539227 ✔ | ۱۵/۱۵ |

CI روی `main` بعد از هر merge: 46052671344 (#108) · 46062660874 (#107) · 46076401654 (#109) · 46083316285 (#110) · 46105260191 (#111) · **46108539227** (#112، آخرین، SUCCESS).

## ۳. نتیجهٔ کارهای W1 / W2 / W3 / W4

- **W1 — ویندوز:** `tests/_shell.py` یابندهٔ مشترکِ Git Bash است (`system32` و WSL رد
  می‌شوند). در sandbox فقط `MSYS2_ARG_CONV_EXCL=/app` ست می‌شود؛ **`MSYS_NO_PATHCONV=1`
  عمداً گذاشته نشد** (شِبِرنگِ استاب را می‌شکند). استاب خروجیِ LF-only می‌دهد و مسیرهای
  داخلِ کانتینر با `posixpath` نرمال می‌شوند. `deploy/deploy.sh` **دست‌نخورده** است.
- **W2 — چرخشِ راز:** `fernet_for(key)` خالص در `newsbot/secret_store.py`؛
  `migrate_ai_keys()` به جدولِ `ai_providers` هم گسترش یافت؛ ابزارِ اتمیک با
  `BEGIN IMMEDIATE`، خروجیِ فقط-شمارش، و کدهای خروج ۰/۲/۳. **۱۸ تست** (شاملِ
  idempotency، ردیفِ ناشناخته ⇒ exit 3 بدونِ نوشتن، `enc:v1:` بیرون از دو جدول ⇒ exit 2،
  و «هیچ مقدارِ رازی در خروجی»).
- **W3 — نظافت:** ۳۳ شاخه با نگهبانِ SHA حذف شد (فقط شاخه‌ای که PR آن MERGED است **و**
  SHAِ نوکِ ریموت با SHAِ headِ همان PR یکی است)؛ refهای ردیابیِ کهنه با `git fetch --prune`
  پاک شدند (۵۱ ref پس از گامِ اول، ۱۹ ref پس از گامِ دوم باقی ماند)؛ **۱۱ تابعِ
  مرده** حذف شد (اسکنِ کراندار: ۸۵۷ نمادِ سطح‌ماژول ⇒ ۸۴۶؛ نامزدِ بدونِ ارجاع: ۱۱ ⇒ ۰).
- **W4 — دروازهٔ انتشار:** `docs/RELEASE_RUNBOOK.md` (گیت‌ها، دروازه‌های مالک، گام‌های
  انتشار، بررسیِ پس از انتشار، rollback، معیارهای توقف) و `docs/OPS_SECRET_ROTATION.md`
  (۱۰ گامِ چرخش با rollback) نوشته شد.

## ۴. شواهدِ اجرا (واقعی)

| دروازه | فرمان | نتیجه |
|---|---|---|
| suite کامل (ویندوز) | `pytest -q` | **۱۲۰۸ passed / ۰ failed** (۷۰۳٫۵۳s در PR2، ۸۰۴٫۷۸s در HEAD نهاییِ PR2، ۸۰۶٫۸۴s در شاخهٔ PR #110) · ۶ هشدارِ pre-existing از `PTBDeprecationWarning` |
| collection | `pytest --collect-only -q` | **۱۲۰۸ tests collected** (نشانِ README و خطِ فارسی هم‌گام) |
| verifier مستقل | `python tools/final_verification.py` | **۱۷/۱۷** (exit 0) |
| بررسیِ پیکربندی | `run.py --check` (با envِ ساختگی) | exit 0 |
| lint | `ruff check newsbot tests tools run.py` | All checks passed |
| types | `mypy newsbot` | Success: no issues found in 52 source files |
| undefined names | `pyflakes newsbot tests tools run.py` | بدون خروجی (exit 0) |
| بایت‌کد | `py_compile` روی `run.py` + `newsbot/` + `tools/` + `tests/` | exit 0 |
| اسکنِ وابستگی | `pip-audit -r requirements.txt --no-deps` | SUCCESS (CI: run 46108539227) |
| اسکنِ راز | `gitleaks` | SUCCESS (همان run) |
| اسکنِ فایل‌سیستم | `trivy fs --severity CRITICAL,HIGH` | SUCCESS (همان run) |
| اسکنِ ایمیج | `trivy image --severity CRITICAL,HIGH` | SUCCESS (همان run) |
| lintِ Dockerfile | `hadolint` | SUCCESS (همان run) |
| محافظتِ شاخه | مقایسهٔ ۱۵ فیلد با بکاپ | **IDENTICAL on all 15 fields** پس از #107، #108، #109، #110 |

> `trivy` و `hadolint` **فقط در CI** اجرا می‌شوند (روی این ماشین `docker` نصب نیست).

## ۵. اجرای زندهٔ D-7 — نتیجهٔ صادقانه

| مورد | مقدار |
|---|---|
| run | **46013627630** · https://github.com/Aryasamadi/Bot-Official-Telegram-/actions/runs/46013627630 · head SHA `967afde` (کدِ فاز ۹ — **پیش از** PR #109 و #110) |
| زمان | 2026-11-27 14:11:14Z → 19:51:47Z |
| نتیجه | **failure** · `exit_code=2` · `problems=["NO_LINKED_GROUP"]` |
| سناریو | `duration` (`scenario_mode=hold_to_budget`) · `db_mode=fresh` (`db_reused=false`) |
| زمانِ فعال | `active_duration_s=20408` (بودجهٔ `configured_duration_s=20400`) · `startup_duration_s=2` · `cleanup_duration_s=0` |
| artifact | `qa-live-46013627630` ⇒ فقط `report.json` + `bot.log` (۶۴۷ خط) · دیتابیس آپلود نشده |
| چک‌های OBSERVED | `preconditions` · `public_model_active` (count=1) |
| چک‌های NOT_OBSERVED | `channel_registered` · `answer_delivered` · `channel_id_is_telegram_chat_id` · `asker_charged_only` · `answered_exactly_once` · `ignored_with_a_reason` · `unrelated_group_silent` · `toggle_off_silent` · `quota_exhausted_message` · `no_secret_in_log` |
| چکِ NOT_APPLICABLE | `no_text_no_ai` (مرحلهٔ اختیاری انجام نشد) — مجموعاً **۱۳ چک** |
| سطح‌ها | `telegram_bot`: real · `temporary_db`: real · `provider_ai`: **simulated** (`provider_credentials_present=false`) · `real_channel_publication`: not_performed · `runner_hard_cap`: real |

**تفسیر:** این شکست **نقصِ کد نیست**. کانالِ ثبت‌شده **linked discussion group** نداشت، پس
هیچ چکِ زنده‌ای قابلِ مشاهده نبود و ابزار fail-closed خروج داد. توجه: این اجرا روی head SHA
`967afde` (کدِ فاز ۹) انجام شد، یعنی **پیش از** PR #109 و #110 — پس نتیجهٔ آن دربارهٔ کدِ
فاز ۱۰ چیزی نمی‌گوید. برای اجرای بعدی لازم است:

1. کانال `Channel TechNowVpn (-1004341590425)` در تلگرام **گروهِ بحثِ لینک‌شده** داشته باشد
   (و ربات در آن admin باشد).
2. اجرا با `duration_minutes=340`، `scenario=duration`، **`db_mode=fresh`** (کشِ v1 پس از
   چرخش دیگر رمزگشایی نمی‌شود).
3. اگر چرخشِ راز انجام شده، `SECRETS_KEY` پایدار بماند و کلیدهای Provider به‌صورتِ رمزشده
   در دیتابیسِ تازه وارد شوند (وگرنه سطحِ `provider_ai` همچنان simulated می‌ماند).

## ۶. بهداشتِ شاخه (W3)

- **۳۳ شاخه حذف شد** (۳۲ در گامِ اول + ۱ در گامِ دوم، چون یک خطای گذرای `gh` باعث شده بود
  PR #28 در گامِ اول جا بماند؛ در گامِ دوم SHAها برابر بود و شاخه حذف شد).
- **۳ شاخه با نگهبانِ SHA نگه داشته شد** (PR آن‌ها MERGED است ولی شاخه بعد از merge جابه‌جا
  شده): `feat/i18n-plan-payment-texts` (#27) · `fix/referral-invite-message` (#18) ·
  `phase-15-watchdog` (#16).
- **۱۶ شاخه باقی ماند**: ۱۱ شاخهٔ Dependabot · `fix/deploy-and-tests-oct-2026` ·
  `fix/phase-a-safety-net` · ۳ شاخهٔ بالا.
- refهای ردیابیِ کهنه با `git fetch --prune` پاک شدند (۵۱ ref پس از گامِ اول، ۱۹ ref پس از گامِ دوم باقی ماند).
- **۱۱ تابعِ مرده** حذف شد؛ اسکنِ بعد: **۰ نامزد**.

## ۷. ارزیابیِ انتشار

> **انتشار «آماده» است فقط اگر همهٔ دروازه‌ها بسته باشند.**

| دروازه | وضعیت |
|---|---|
| گیت‌های CI (§2 گزارش) | ✅ بسته |
| suite کامل + verifier + `run.py --check` | ✅ بسته |
| محافظتِ شاخه ۱۵/۱۵ | ✅ بسته |
| کدِ مرده و شاخه‌های کهنه | ✅ بسته |
| **D-7** (اجرای زندهٔ کامل) | ❌ **باز** (`NO_LINKED_GROUP`) |
| **چرخشِ راز** (`BOT_TOKEN` + `SECRETS_KEY`) | ❌ **باز** (دروازهٔ مالک) |

**نتیجه:** فاز ۱۰ **`IN_PROGRESS`** است، نه `COMPLETED`. به‌محضِ بسته‌شدنِ این دو دروازه
(شواهدشان در §5 فایلِ وضعیت ثبت می‌شود) یک PRِ close-out فقط **سند** را به‌روز می‌کند.

سازوکارِ انتشار: `docs/RELEASE_RUNBOOK.md` · چرخشِ راز: `docs/OPS_SECRET_ROTATION.md`.

## ۸. آنچه در این فاز **انجام نشد**

- چرخشِ واقعیِ راز (ابزار و runbook هست، اجرا در دستِ مالک است).
- اجرای دوبارهٔ زنده (D-7) پس از لینک‌کردنِ گروهِ بحث.
- رمزنگاریِ `sources.api_key` (D-12) و بازصدورِ کلیدهای Provider (D-13).
- ۱۱ PRِ Dependabot (D-4 — به تعویق بعد از انتشار).
- اصلاحِ `F-1` (تغییرِ رفتارِ زمان‌بندی/timeout — خارج از دامنه).
- استقرار به VPS: **هیچ استقراری انجام نشده** و ادعایی هم نمی‌شود. «Deploy to VPS» پس از هر
  merge به `main` یک اجرای تازه می‌سازد و اجرای معلقِ قبلی را **cancelled** می‌کند؛ بنابراین
  مالک باید **تازه‌ترین** اجرا را در تبِ Actions روی `main` تأیید کند (46108808054 برای
  `main` @ `1d7916a` در لحظهٔ نگارش؛ 46105550745 و 46083654457 به این شکل cancelled شدند و
  هرگز به VPS نرسیدند).
