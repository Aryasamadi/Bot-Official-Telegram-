# بخش ۰۱ — کدبیس، معماری و تست

> بخشی از گزارشِ ممیزیِ Session 3 (فاز C). مرجعِ کلی:
> `docs/audit/MASTER_AUDIT_AND_REMEDIATION_REPORT.md` · گزارشِ جزئی:
> `docs/REPOSITORY_AUDIT_REPORT.md`.

## دامنه

ساختارِ مخزن، سبکِ کد، پیکربندیِ ابزارهای کیفیت، قراردادِ تست‌ها و صحتِ ادعاهای
«کیفیت» در README و اسنادِ ممیزی.

---

## ۱. TEST-01 — نبودِ دروازهٔ پوششِ تست

**شدت:** MEDIUM · P1 · **وضعیت:** ✅ رفع‌شده

### شاهدِ پیش از اصلاح

`.github/workflows/ci.yml` فقط این را اجرا می‌کرد:

```yaml
      - name: Unit + integration tests
        run: pytest -q
```

هیچ `--cov`ای نبود ⇒ کاهشِ پوشش نه دیده می‌شد و نه جلویش گرفته می‌شد. ضمناً
`requirements-dev.txt` ابزارِ پوشش نداشت.

### اصلاح

| گام | جزئیات |
|---|---|
| پیکربندیِ واحد | `pyproject.toml` تازه: `[tool.ruff]` (قوانینِ `E9,F,B,C4,PIE,W`)، `[tool.mypy]` (تایپینگِ تدریجی، `files=["newsbot"]`)، `[tool.pytest.ini_options]` |
| وابستگی | `pytest-cov==7.0.0` به `requirements-dev.txt` |
| گیتِ CI | `pytest -q --cov=newsbot --cov-report=term-missing --cov-report=xml:coverage.xml --cov-report=json:coverage.json --junitxml=junit.xml --cov-fail-under=68` |
| شواهد | artifactهای `coverage.xml` · `coverage.json` · `junit.xml` |

### خطِ پایهٔ واقعی (اندازه‌گیری‌شده)

```
TOTAL                        9158   2704    70%
```

دروازه روی **۶۸٪** قفل شد (۲ نمره زیرِ خطِ پایه) تا تفاوتِ جزئیِ پوشش بین
Python 3.13 (CI) و 3.14 (محلِ سنجش) CI را نشکند.

### ریسکِ باقی‌مانده

پوششِ `newsbot/discover.py` (۳۱٪) و `newsbot/extract.py` (۴۳٪) پایین است — این‌ها
مسیرِ شبکه‌ای‌اند و در تست با HTTP قلابی سخت پوشش داده می‌شوند. در فهرستِ
کارهای بعدی آمده‌اند.

---

## ۲. کیفیتِ ایستا: ۲۷ یافتهٔ Ruff و ۳۹ خطای mypy

**وضعیت:** ✅ رفع‌شده

پیکربندیِ Ruff **آگاهانه** انتخاب شد: خانهٔ سبکِ این مخزن «چند دستور روی یک خط»
است (`if x: return y`) که `E701/E702` آن را می‌گیرد ⇒ بازآراییِ ~۲۵۰۰ خط، ریسکِ
رگرسیون دارد و موضوعِ این فاز نیست. گیتِ lint روی قوانینی است که *واقعاً* باگ
می‌گیرند (نامِ تعریف‌نشده، حلقهٔ بسته‌شده روی متغیرِ حلقه، `raise` بدونِ `from`،
`zip` بدونِ `strict`، …).

| گروه | نمونه | تعداد |
|---|---|---|
| `B023` | بستنِ حلقه روی `tok` در `newsbot/core_channels.py` | ۱ |
| `B904` | `raise … from err` در `app.py`، `core_referrals.py`، `ui_inputs.py`، `tools/migrate_sqlite_to_d1.py` | ۵ |
| `B905` | `zip(..., strict=True)` در `newsbot/cycle.py` | ۱ |
| `C4` | `dict()` به‌جای literal در `core_plans.py`، `core_users.py` | ۵ |
| `C416/C401` | comprehension‌های غیرضروری | ۳ |
| `PIE810` | `startswith` با tuple | ۳ |
| `B007/B011` | متغیرِ بلااستفاده / `assert False` | ۶ |
| mypy | annotationهای صریح برای dictهای سراسری و اصلاحِ `None`-guardها | ۳۹ خطا |

خروجیِ واقعی:

```
ruff check newsbot tests tools run.py   →  All checks passed!
mypy newsbot                            →  Success: no issues found in 44 source files
```

---

## ۳. D-1 — ادعای نادرستِ «۷۷۰ تست» در README

**شدت:** LOW · **وضعیت:** ✅ رفع‌شده

`README.md` badge می‌گفت `tests-770 passing` ولی مجموعهٔ واقعی **۸۴۴** تست است
(شمارش با `pytest --collect-only` پس از این فاز). badge به `tests-844 passing`
تغییر کرد و دو badgeِ دیگر هم با پیکربندیِ واقعی هم‌راستا شدند:

| badge | پیش | اکنون |
|---|---|---|
| Python | `3.12+` | `3.13+` (پایتونِ هدفِ VPS/CI/Docker: ۳.۱۳) |
| python-telegram-bot | `21.6+` | `22.8+` (قفلِ واقعی در `requirements.txt`: `python-telegram-bot==22.8`) |

---

## ۴. هم‌راستاییِ `.env.example` با `newsbot/config.py`

**وضعیت:** ✅ حفظ شد (تستِ موجود)

`tests/test_phase12_config_docs.py` تضمین می‌کند هر کلیدی که در `.env.example`
هست واقعاً در `config.py` خوانده می‌شود و هر کلیدی که `config.py` می‌خواند مستند
شده است. در این فاز سه کلیدِ تازه اضافه شد و هر سه در `.env.example` هم آمدند:

```
#DRAIN_FLAG_FILE=/tmp/newsbot_draining
#DRAINED_FLAG_FILE=/tmp/newsbot_drained
#DRAIN_TIMEOUT_SEC=30
```

## ۵. یکپارچگیِ ارجاع‌های مخزن

**وضعیت:** ✅ حفظ شد

`tests/test_repo_integrity.py` هر مسیرِ backtick‌شده در اسناد و کد را بررسی
می‌کند که واقعاً وجود داشته باشد. همهٔ فایل‌های تازهٔ این فاز (مثل
`docs/VPS_DEPLOYMENT.md` و `tools/verify_github_protection.py`) در همین تست
اعتبارسنجی می‌شوند؛ یعنی یک ارجاعِ شکسته نمی‌تواند بی‌سروصدا وارد شود.
