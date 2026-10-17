# مرزهای دسترسی — ماتریس مجوز و نقاط اجرای سرور (فاز ۶)

تاریخ: ۲۰۲۶-۱۰-۱۷ · شاخه: `phase-6-authz` · مرجع: ROADMAP.md بخش ۶.۶ (فاز ۶ — مرز دسترسی)

خروجی الزامیِ فاز ۶: (۱) ماتریس مجوز نقش×سطح، (۲) فهرست نقاط اجرای سرور با ارجاع کد، (۳) سقف‌ها/محدودیت‌های فرم حساس، (۴) نگاشت تست‌های تقابل، (۵) اندازه‌گیری واقعی پوشش (QA-14).

---

## ۱. ماتریس نقش‌ها (تصمیم سمت سرور، در `dispatch` و `handle_input`)

| سطح | کاربر عادی (user) | ادمین (admin) | ابرادمین (super) |
|---|---|---|---|
| `u:` (خرید/پلن/ارجاع) | ✅ (فقط دادهٔ خودش) | ✅ | ✅ |
| `c:` (لغو/بازگشت) | ✅ | ✅ | ✅ |
| `a:home`, `a:plan` | ⛔ (`no_admin`) | ✅ | ✅ |
| `a:` عملیات محتوا: `test/pub/srca/chadd` | ⛔ | ✅ فقط با پلن فعال (`plan_inactive`) | ✅ |
| `a:ch:*` کانال‌ها | ⛔ / `notfound` | ✅ فقط کانال‌های خودش (`channel_owned`) | ✅ + استثنای مالکیت |
| `a:src*` منابع | ⛔ / `notfound` | ✅ فقط منابع `admin_id == uid` | ✅ |
| `a:art*`/`pub` مقالات | ⛔ / `notfound` | ✅ فقط `admin_id == uid` | ✅ |
| `a:myai*` مدل‌های شخصی | ⛔ / `notfound` | ✅ فقط `owner_id == uid` | ✅ |
| `a:chrec:{cid}` (بازیابی) | ⛔ `notfound` (مگر ثبت‌نام‌کنندهٔ نخست) | ✅ فقط ثبت‌نام‌کنندهٔ نخستِ کانال* | ✅ |
| `s:*` (پنل ابرادمین) | ⛔ (برمی‌گردد) | ⛔ (برمی‌گردد) | ✅ |
| فرم‌های ورودی (`handle_input`) | ⛔ (بدون state معتبر) | ✅ در دامنهٔ مالکیت | ✅ |
| کاربر مسدود (`banned`) | ⛔ (`banned`) | ⛔ | ✅ مستثنا |

\* استثنای C8 عامدانه است: بازیابی مالکیت باید برای «ثبت‌نام‌کنندهٔ نخست» کار کند حتی اگر اکنون مالکِ ربات‌ی نباشد؛ اعتبارسنجی نهایی هنگام تأیید وضعیت در تلگرام انجام می‌شود.

## ۲. نقاط اجرای سرور (کد)

| گیت | مکان | رفتار در نقض |
|---|---|---|
| نیاز به زبان انتخاب‌شده | `ui_dispatch.py:55` | `view_lang` |
| گارد کل `a:` | `ui_dispatch.py:83` (`can_admin`) | popup `no_admin` + پلن‌ها |
| پلن فعال برای عملیات محتوا | `ui_dispatch.py:86` | popup `plan_inactive` |
| مالکیت مدل شخصی | `ui_dispatch.py:96` | popup `notfound` |
| مالکیت منبع | `ui_dispatch.py:108` | popup `notfound` |
| مالکیت مقاله | `ui_dispatch.py:124` | popup `notfound` |
| استثنا‌ی C8 پیش از گارد مالکیت کانال | `ui_dispatch.py:152` → `ui_views_admin.py:120` (`first_registrant`/`is_super`) | popup `notfound` |
| مالکیت کانال | `ui_dispatch.py:155` + `core_channels.py:62` (`channel_owned`)، `ui_views_admin.py:79` | popup `notfound` |
| گارد کل `s:` | `ui_dispatch.py:228` (`is_super`) | بازگشت بی‌صدا |
| مسدود (banned) | `ui_dispatch.py:386` | alert `banned` |
| دروازهٔ کانال اجباری (نشر دستی) | `ui_dispatch.py:144` → `ui_views_admin.py:45` (`mand_gate`) | صفحهٔ عضویت |
| دسترسی فرم بر اساس state+مالکیت | `ui_inputs.py:165` (`_input_access`) | popup denial |
| اعتبارسنجی مقدار ورودی (سرور) | `ui_inputs.py:82` (`_input_value`)، `ui_inputs.py:46` (`_input_url`) | popup خطا |
| پاک‌سازی نمایشی (XSS) | `esc()` در ویوها (مثل `ui_views_super.py:185`) | خروجی escape‌شده |

## ۳. سقف‌ها و مهلت‌های فرم حساس

| موضوع | محل | مقدار/رفتار | ماندگاری |
|---|---|---|---|
| مهلت state فرم‌ها (UISEC-06) | `config.py` (`AWAIT_TTL_SEC`، پیش‌فرض ۱۸۰۰s؛ preflight: `app.py` بازهٔ ۶۰..۸۶۴۰۰) | state قدیمی/بدون `ts` منقضی؛ `await_expired` در `ui_widgets.py` | — |
| اعمال مهلت در پیام | `ui_support.py:58-64` (`on_message`): pop + اعلام + fall-through | پیام تازه مثل پیام جدید پردازش می‌شود | — |
| اعمال مهلت در ورودی callback | `ui_inputs.py:204-208` (`handle_input`): `done()` + اعلام + بازگشت | ورودی به فرم فراموش‌شده نمی‌خورد | — |
| ثبت `ts` در فرم‌ها | `ui_widgets.py` (`ask`/`wiz_start`)، `ui_views_user.py:151` (رسید)، `ui_views_admin.py:116` (توکن انتقال)، `ui_inputs.py:301` (حفظ `ts` مرحلهٔ بعد) | — | — |
| نرخ کد تخفیف (UISEC-04) | `ui_inputs.py:412` (`rate_ok("disc:{uid}")`) | super مستثنا؛ پیام `test_wait` | ماندگار (`RATE_PERSIST_PREFIXES`: `disc:`) |
| نرخ تست مدل شخصی (UISEC-04) | `ui_widgets.py:464` (`rate_ok("mtest:{uid}")`) | super مستثنا | ماندگار (`mtest:`) |
| سقف حجم رسید (UISEC-09) | `ui_inputs.py:427-431` (`MAX_MEDIA_BYTES`) | `receipt_too_big`؛ state حفظ می‌شود تا فایل کوچک‌تر بیاید | — |
| حذف نمایش دادهٔ حساس (UISEC-03) | قالب `s_model_view` (`ui_i18n.py`) هیچ خط کلیدی رندر نمی‌کند (view مقدار ماسک‌شدهٔ `key[:3]+…+key[-2:]` را می‌فرستد ولی قالب جای `{key}` ندارد) | کلید کامل و پیشوند آن در هیچ‌یک از ویوهای کاربری/ابرادمین ظاهر نمی‌شود | — |

## ۴. تست‌های تقابل نقش (GAP-02/C8 و بقیه) — `tests/test_authz_matrix.py` (۱۶ تست)

| تست | چه چیزی را اثبات می‌کند |
|---|---|
| `test_role_matrix_user_cannot_enter_admin_area` | `a:home`/`a:plan` برای user ⇒ `no_admin` (UISEC-02) |
| `test_role_matrix_admin_cannot_enter_super_area` | `s:*` برای admin ⇒ بازگشت بی‌اثر (UISEC-02) |
| `test_admin_cannot_touch_foreign_channel_source_article` | مالکیت کانال/منبع/مقاله سمت سرور (GAP-02) |
| `test_plan_gate_blocks_content_actions` | `a:test` بدون پلن فعال ⇒ `plan_inactive` |
| `test_input_access_matrix_rejects_by_role_and_ownership` | `_input_access`: نقش+مالکیت روی همهٔ kindها |
| `test_input_value_validates_server_side` | `_input_value`: عدد/URL/کلید — مرزهای سرور (UISEC-01) |
| `test_chrec_only_registrant_can_restore` | C8: فقط ثبت‌نام‌کنندهٔ نخست + مسیر revert (`member_status="left"`) |
| `test_await_ttl_and_config_value` | `AWAIT_TTL_SEC == 1800` و رفتار `await_expired` |
| `test_handle_input_expired_state_is_discarded` | ورودی روی state منقضی ⇒ پاک‌سازی + اعلام (UISEC-06) |
| `test_on_message_expired_state_falls_through` | پیام روی state منقضی ⇒ pop + ادامهٔ جریان تازه |
| `test_on_message_fresh_state_is_consumed` | state تازه مصرف می‌شود (رگرسیون‌گیر) |
| `test_disc_rate_limit_and_persistence` | سقف نرخ کد تخفیف + ماندگاری `disc:` (UISEC-04) |
| `test_mtest_rate_limit_personal_and_super_exempt` | سقف نرخ تست مدل شخصی + استثنای super (UISEC-04) |
| `test_api_key_never_leaks_into_super_and_user_views` | عدم نشت کلید در `s:model` و ویوهای کاربری (UISEC-03) |
| `test_views_escape_untrusted_titles_and_names` | escape عنوان/نام در ویوها (UISEC-08 پایه) |
| `test_receipt_size_cap_blocks_oversize` | سقف حجم رسید: مرز+۱ رد، دقیقاً مرز عبور (UISEC-09) |

## ۵. یافته‌های جانبی فاز (باگ‌های واقعیِ کشف‌شده توسط تست‌ها)

1. **`UnboundLocalError: elapsed` در تست مدل شخصی** — مسیر موفقیت `run_model_test(personal=True)` هرگز `elapsed` را ست نمی‌کرد و با استثنا می‌شکست (روی `main` هم وجود داشت؛ پشت `run_user_operation` پنهان می‌شد). رفع: یک خط در `ui_widgets.py` (محاسبهٔ `elapsed` در شاخهٔ personal).
2. **کلید داخل قالب `s_model_view` هرگز رندر نمی‌شد** — ماسکِ سخت‌گیرانه‌تر بی‌اثر بود؛ معیار «حذف نمایش» (UISEC-03) با همین رفتار برقرار است و تست روی «عدم نشت» بسته شد (نه «نمایش ماسک‌شده»).
3. **fixture تکراری در `tests/test_ui_routing.py`** — `article_insert` روی درج تکراری `None` برمی‌گرداند و `_mk_fixtures` بدون جایگزین، `a:art:None` می‌ساخت؛ idempotent شد (توسط تعامل تست‌های جدید آشکار شد).

## ۶. اندازه‌گیری واقعی پوشش (QA-14)

- ابزار: `pytest-cov 7.1.0` — **فقط نصب محلی در venv**؛ `requirements-dev.txt` و CI دست‌نخورده (مطابق تصمیم محدودهٔ تست).
- دستور: `.venv\Scripts\python.exe -m pytest tests -q --cov=newsbot --cov-report=term`
- نتیجه (۲۹۵ تست سبز): **TOTAL 53%** — ۵۴۹۷ statement، ۲۵۶۷ miss.
- ماژول‌های شاخص فاز ۶: `ratelimit.py` 83٪ · `config.py` 100٪ · `ui_views_admin.py` 64٪ · `ui_widgets.py` 51٪ · `ui_dispatch.py` 46٪ · `ui_inputs.py` 39٪ · `ui_views_super.py` 53٪ · `ui_support.py` 53٪.
- توجه: این عدد «پوشش واقعی» است (بدون claim قبلی)؛ مسیرهای دور از دست نوبت (دور‌فعال‌ها مانند `discover/extract/cycle`) عمداً در فاز خودشان پوشش می‌گیرند.

## ۷. دروازهٔ کیفیت محلی (پیش از PR)

- تست‌ها: `295 سبز` (۲۷۹ + ۱۶ تست این فاز) · pyflakes: `صفر` · `tools/final_verification.py`: `17/17`.
- CI/merge و وضعیت پس از ادغام در ROADMAP (بخش گزارش فاز ۶) ثبت می‌شود.
