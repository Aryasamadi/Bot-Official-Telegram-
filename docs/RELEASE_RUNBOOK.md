# RELEASE RUNBOOK — دروازهٔ انتشار

سندِ عملیاتیِ فاز ۱۰. هدف: **یک انتشارِ قابلِ بازگشت** که فقط وقتی انجام می‌شود که همهٔ
دروازه‌های خودکار **و** دروازه‌های مالک بسته باشند. این سند جای `docs/DEPLOY_RUNBOOK.md`
(نحوهٔ استقرار) را نمی‌گیرد؛ به آن ارجاع می‌دهد.

> قاعدهٔ کلّی: **هیچ گیتی بدونِ اجرای واقعی «سبز» حساب نمی‌شود.** اگر مرحله‌ای اجرا
> نشده، «نامشخص» است — نه «سبز».

---

## ۱. پیش‌نیازها و شواهدِ این دور

| مورد | مقدار |
|---|---|
| شاخهٔ هدف | `main` |
| SHA هدف | `9601c1a209fb59b12154d2aedb3e664545eece49` (PR #113 — پایدارسازیِ ارجاعِ اجرای استقرار) |
| **قاعدهٔ «SHA هدف»** | **نقطه‌در‌زمان.** هر merge به `main` نوک را جلو می‌برد؛ پس پیش از تأییدِ استقرار، `git log origin/main` را ببینید و **آخرین اجرای سبزِ CI** روی `main` را ملاک بگیرید — نه شناسهٔ نوشته‌شده در این سطر |
| CI روی `main` | **run 46112682756 — SUCCESS** · https://github.com/Aryasamadi/Bot-Official-Telegram-/actions/runs/46112682756 |
| CI روی PR #113 | run 46112222368 — SUCCESS · https://github.com/Aryasamadi/Bot-Official-Telegram-/actions/runs/46112222368 |
| اجرای زندهٔ D-7 | run 46013627630 — **failure (`NO_LINKED_GROUP`)** · https://github.com/Aryasamadi/Bot-Official-Telegram-/actions/runs/46013627630 |
| artifactِ D-7 | `qa-live-46013627630` (فقط `report.json` + `bot.log`؛ دیتابیس هرگز آپلود نمی‌شود) |
| استقرار به VPS | **تازه‌ترین** اجرای `Deploy to VPS` روی `main` — **pending** (در انتظارِ تأییدِ دستیِ محیطِ `production`). 46112934075 برای `main` @ `9601c1a` در لحظهٔ نگارش: https://github.com/Aryasamadi/Bot-Official-Telegram-/actions/runs/46112934075 — **قاعده:** هر merge به `main` یک اجرای تازه می‌سازد و اجرای معلقِ قبلی را cancelled می‌کند، پس همیشه **تازه‌ترین** اجرا را تأیید کنید (46108808054، 46105550745 و 46083654457 به این شکل cancelled شدند و هرگز به VPS نرسیدند)؛ هیچ استقراری انجام نشده |
| محافظتِ شاخه | ۱۵/۱۵ فیلد **IDENTICAL** پس از هر merge |
| تعدادِ تست | **۱۲۰۸ collected** · suite کامل: **۱۲۰۸ passed / ۰ failed** |
| verifier | `tools/final_verification.py` ⇒ **۱۷/۱۷** |
| `run.py --check` | exit 0 |

## ۲. دروازه‌های CI (خودکار — همه در یک job با نامِ `test`)

همهٔ این گیت‌ها داخلِ **همان** jobِ `test` هستند (چون فقط همین نام در حفاظتِ شاخه به‌عنوان
checkِ لازم ثبت شده است) و روی `main` در run **46112682756** اجرا شده‌اند:

| گیت | گام در CI (نامِ عینِ گام) | نتیجه در 46112682756 |
|---|---|---|
| `pyflakes` | `Static guard (pyflakes — zero undefined names)` | SUCCESS |
| `ruff` | `Lint (ruff — the rule set lives in pyproject.toml)` | SUCCESS |
| `mypy` | `Types (mypy — gradual, newsbot only)` | SUCCESS |
| `py_compile` | `Syntax check (all modules)` | SUCCESS |
| `pip-audit` | `Dependency audit (pip-audit — pinned requirements)` | SUCCESS |
| `gitleaks` | `Secret scan (gitleaks)` | SUCCESS |
| `hadolint` | `Dockerfile lint (hadolint)` | SUCCESS |
| `trivy` | `Filesystem scan (trivy)` | SUCCESS |
| `pytest` | `Unit + integration tests (with the coverage gate)` — `--cov-fail-under=68` | SUCCESS |
| `final_verification.py` | `Independent verification (Phase 10)` | SUCCESS |
| `run.py --check` | `Config self-check (dummy env)` | SUCCESS |
| `docker build` | `Build the production image` | SUCCESS |
| `trivy image` | `Image scan (trivy)` | SUCCESS |
| `docker compose config` | `Compose config is valid` | SUCCESS |
| حفاظتِ شاخه/محیط | `Verify the branch and environment protection` — `tools/verify_github_protection.py`؛ اگر `GH_ADMIN_TOKEN` تنظیم نباشد، گام با پیامِ «skipping» تمام می‌شود | SUCCESS (با پیامِ skip) |
| شواهد | `Upload the coverage and the test report` (`coverage.xml` · `coverage.json` · `junit.xml`) | SUCCESS |

> **`trivy` و `hadolint` فقط در CI اجرا می‌شوند**: روی این ماشینِ محلی `docker` نصب نیست،
> پس اجرای محلیِ آن‌ها ممکن نیست. ادعای «اسکن شد» فقط با URLِ run بالا معتبر است.
> `CVE-2026-124851` (بستهٔ `libzstd1` در ایمیجِ پایه) با PR #108 بسته شد و اسکنِ ایمیج
> از آن پس سبز است.

## ۳. `pip-audit` محلی (اختیاری ولی سریع)

```powershell
& ".\.venv\Scripts\python.exe" -m pip_audit -r requirements.txt --no-deps
```

- انتظار: `No known vulnerabilities found`.
- اگر چیزی گزارش شد: **انتشار را متوقف کنید** و ابتدا آن را برطرف کنید (یا با تصمیمِ
  صریحِ مالی به تعویق بیندازید و در گزارش ثبت کنید).

## ۴. suite کامل (محلی)

```powershell
& ".\.venv\Scripts\python.exe" -m pytest -q
```

- انتظار: **۰ failed** و شمارِ تست با `README` یکی (نگهبان:
  `tests/test_phase1_10_state_alignment.py::test_readme_test_count_matches_collection`).
- روی ویندوز، پیش از PR #109، ۱۹ شکستِ ویندوزی وجود داشت که **همه رفع شده‌اند**
  (شاهد: §5.1 فایلِ وضعیت و PR #109).

## ۵. verifier مستقل

```powershell
& ".\.venv\Scripts\python.exe" tools\final_verification.py
```

- انتظار: **«نتیجه: 17/17 یافته اثبات‌شده رفع شد»**. این ابزار مستقل از تست‌هاست و ۱۷
  ادعای تاریخیِ ممیزی را دوباره اثبات می‌کند.

## ۶. بررسیِ پیکربندی

```powershell
$env:BOT_TOKEN="123456789:AAtest_only_dummy_token_0000000000000"; $env:SUPER_ADMIN_IDS="1"; $env:DB_FILE="C:\Users\Mustafa\AppData\Local\Temp\opencode\check.db"
& ".\.venv\Scripts\python.exe" run.py --check
```

- انتظار: exit 0 و پیامِ «خودآزمایی موفق».
- این **توکنِ ساختگی** است و هرگز توکنِ واقعی نیست.

## ۷. حفاظتِ شاخه

- بعد از هر merge: `IDENTICAL on all 15 fields` (ابزار: `gate.py <PR> lift` →
  `gh pr merge --squash --delete-branch` → `gate.py <PR> restore` →
  `verify_protection_pr.py <PR>`).
- پیش از انتشار: تأیید کنید که `main` همچنان ۱ review + `test` (strict) + linear history
  دارد و force-push/branch-deletion خاموش است (see `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md` §7.1).

## ۸. دروازه‌های مالک (بدونِ این‌ها انتشار «آماده» نیست)

| # | دروازه | وضعیتِ فعلی | اثباتِ لازم |
|---|---|---|---|
| A | **D-7** — اجرای زندهٔ کامل | ❌ **باز** — run 46013627630 با `exit_code=2` و `problems=["NO_LINKED_GROUP"]` تمام شد | یک runِ `qa-live` موفق با artifact و `report.json` در §5 فایلِ وضعیت؛ کانال باید **linked discussion group** داشته باشد |
| B | **چرخشِ راز** (`BOT_TOKEN` + `SECRETS_KEY`) | ❌ **باز** — ابزار و runbook تحویل شده، اجرا نشده | گزارشِ مالک از `--apply` موفق + به‌روزرسانیِ env + ری‌استارت + تستِ Provider + بازصدورِ توکن در BotFather (فقط شمارش/تاریخ) |
| C | **D-13** — کلیدهای Provider بازصدور نشده | ⚠️ **پذیرفته‌شده توسط مالک** (ریسکِ باقی) | ثبت در §8 فایلِ وضعیت |
| D | **D-12** — `sources.api_key` متنی | ⚠️ **خارج از دامنه** | ثبت در §8 فایلِ وضعیت |
| E | **D-4** — ۱۱ PRِ Dependabot (#68–#78) | ⏸️ **به تعویق بعد از انتشار** (تصمیمِ مالک) | — |

## ۹. گام‌های انتشار

1. **تأییدِ دروازه‌های §8** (A و B باید بسته باشند).
2. **تأییدِ دستیِ استقرار**: در runِ «Deploy to VPS» روی `main` دکمهٔ **Approve** محیطِ
   `production` را بزنید (این تنها گامی است که به دسترسیِ VPS نیاز دارد).
3. **نحوهٔ استقرار**: `docs/DEPLOY_RUNBOOK.md` (گام‌های واقعیِ استقرار، پرچم‌های تخلیه،
   بررسیِ healthcheck، و rollback). این سند فقط می‌گوید **کِی** و **با کدام شواهد**.
4. **ثبتِ SHA**: SHAی که مستقر شد + زمانِ تأیید + شمارهٔ run را در §7 فایلِ وضعیت بنویسید.

## ۱۰. بررسی‌های پس از انتشار

- `docker compose ps` ⇒ سرویس `healthy` (healthcheck: `python -m newsbot.healthcheck`).
- لاگِ سرویس: بدونِ `ERROR` و بدونِ پیامِ «نشت»؛ هشدارهای `probe timed out` یا `504`
  را **با شمارش** ثبت کنید (اگر تکرار شد ⇒ **F-1**، §8 فایلِ وضعیت).
- یک پیامِ واقعی از حسابِ آزمایشی ⇒ ربات پاسخ می‌دهد و پیام بومی می‌فرستد.
- یک پاسخِ Q&A در گروهِ بحثِ لینک‌شده ⇒ `qa_answers` یک ردیف دارد و **دقیقاً یک‌بار** پاسخ
  داده شده (بررسیِ `answered_exactly_once`).
- `sqlite3` روی دیتابیس: `PRAGMA integrity_check` ⇒ `ok`.

## ۱۱. Rollback

- **نرم:** در همان workflow، `Re-run` روی SHA قبلی (یا `workflow_dispatch` با SHA قبلی).
- **سخت:** طبق `docs/DEPLOY_RUNBOOK.md` ⇒ بازگرداندنِ ایمیجِ قبلی + دیتابیسِ پشت‌بان +
  ری‌استارت. اگر چرخشِ راز انجام شده بود، بازگشتِ `SECRETS_KEY` **همراهِ** بازگرداندنِ
  دیتابیس لازم است (وگرنه کلیدهای رمزشده باز نمی‌شوند) — `docs/OPS_SECRET_ROTATION.md` §9.
- پس از rollback: علت را در §5 فایلِ وضعیت ثبت کنید.

## ۱۲. معیارهای توقف (Stop criteria) — در هر کدام، انتشار متوقف می‌شود

1. هر گیتِ §2 قرمز باشد (به‌ویژه `trivy`/`gitleaks`/`pip-audit`).
2. suite محلی یا CI شکست بخورد، یا شمارِ تست با `README` نخواند.
3. `final_verification.py` کمتر از **17/17** بدهد.
4. `run.py --check` exit ندهد.
5. محافظتِ شاخه پس از یک merge به ۱۵/۱۵ نرسد.
6. دروازهٔ مالکِ A (D-7) یا B (چرخشِ راز) هنوز باز باشد.
7. رازی در log، artifact، commit یا PR دیده شود ⇒ **فوراً** متوقف، و راز بازصدور شود.
8. `git push --force` یا حذفِ `main` لازم شود ⇒ متوقف (خارج از دامنه).

---

سندهای مرتبط: `docs/DEPLOY_RUNBOOK.md` · `docs/OPS_SECRET_ROTATION.md` ·
`docs/QA_LIVE_ACCEPTANCE.md` · `docs/PHASE10_FINAL_REPORT.md` ·
`docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md` (مرجعِ وضعیت).
