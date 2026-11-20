# Phase 6 — Typing lifecycle (design)

**تاریخ:** 2026-11-21 · **وضعیت:** تأییدشده توسط مالک · **فاز:** ۶ از ۱۰

---

## 1. هدف و چرا

فاز ۵ تمام کرد، ولی هنوز هیچ جای مخزن **هیچ** `send_chat_action`/typing ندارد. کاربری که
دستورِ «ساخت پست» میدهد یا در گروهِ بحث سؤال میپرسد، در خلالِ انتظارِ مدل (چند ثانیه تا
چند دقیقه) هیچ سیگنالی از ربات نمیمیرد؛ تنها «انیمیشنِ تفکر» در `ui_widgets.py` متنِ
**پیامِ وضعیت** را هر ثانیه edit میکند — آن یک پیامِ قابلِ مشاهده است، نه نشانگرِ typing
بومیِ تلگرام، و دو مکانیزم متفاوتاند.

فاز ۶ نشانگرِ typing را در **تمامِ مدتِ انتظارِ کاربر** روشن نگه میدارد و تضمین میکند
که در **هر** مسیر خروج (موفقیت، خطا، لغو، خاموشیِ ربات) متوقف و جمع شود. شرطِ مالک:
`no orphan tasks` — یعنی هیچ heartbeatای بعد از پایانِ کار یا بعد از خاموشی در حلقهٔ
رویداد باقی نماند.

## 2. تصمیم‌های قطعیِ مالک (binding)

| # | تصمیم | اثرِ طراحی |
|---|---|---|
| 1 | **رویکرد B**: typing در مرزِ عملیات (`run_user_operation`) + همان context manager در `on_group_reply` | یک `typing_while` قابلِ استفادهٔ مجدد؛ تمامِ انتظارهای کاربر پوشش میگیرد |
| 2 | **بعد از ۱ ثانیه شروع، هر ۴ ثانیه تمدید** | cmd/callback سریع هیچ تماسِ شبکه نمیزند؛ تمدید قبل از انقضای ~۵ ثانیهای تلگرام |
| 3 | **فقط env سراسری** (`TYPING_ENABLED`, `TYPING_START_DELAY_S`, `TYPING_REFRESH_S`) | بدونِ UI و بدونِ overrideِ per-channel |
| 4 | کیفیتِ بالاتر از فاز ۵ | تستِ قطعیِ لغو/خطا/خاموشی/dedupe، بازبینِ جداگانهٔ نهایی، و ثبتِ شواهدِ واقعی |
| 5 | بعد از ۳ شکستِ متوالی: خاموشی ۳۰ ثانیه در همان چت، سپس تلاشِ تازه | جلوگیری از اسپمِ 429 بدونِ مرگِ دائمیِ typing |

## 3. هدف‌ها / غیرهدف‌ها / ضدهدف‌ها

**هدف‌ها**
- G1: هر عملیاتی که بیش از ۱ ثانیه طول بکشد، در چتِ مربوطه typing نشان دهد.
- G2: typing دقیقاً همان موقع متوقف شود که کار تمام/لغو/شکست خورده است — حتی در استثنا.
- G3: هیچ تسکِ heartbeat بعد از خروج از context یا بعد از `post_shutdown` زنده نماند.
- G4: خطای ارسالِ chat action هرگز پاسخِ اصلی را نشکند.
- G5: در یک چتِ مشترک (گروهِ بحث با چند پرسشگر همزمان) فقط **یک** heartbeat وجود داشته باشد.

**غیرهدف‌ها**
- حذف/تغییرِ «انیمیشنِ تفکر» و پیامِ وضعیتِ فازهای قبل — مستقل باقی میماند.
- typing برای چرخههای thời/پسزمینهای بدونِ چتِ کاربر.
- نمایشِ متنِ جدید، i18n، یا تنظیماتِ per-channel.
- ارسالِ actionِ «خاموش» — چنین چیزی در Bot API وجود ندارد (روی انقضای ۵ ثانیهای تکیه میکنیم).

**ضدهدف‌ها**
- تماسِ شبکه بهازای هر callbackِ سریع ⇒ با delayِ ۱ ثانیه جلوگیری میشود.
- تسکِ پشتیبانِ رهاشده هنگامِ `CancelledError` ⇒ لغو + awaitِ محافظتشده در `finally`.
- بالا رفتنِ نرخ 429 ⇒ تمدید ۴ ثانیهای و توقفِ self-loop بعد از ۳ شکستِ متوالی.

## 4. قیدها

- متنِ گزارش/UI فارسی؛ شناسههای فنی انگلیسی. هیچ secret، متنِ سؤال/پست یا شناسهٔ چت در لاگِ typing.
- هر تغییرِ رفتار ⇒ تست، اول قرمز (`tests/test_phase6_typing_lifecycle.py`).
- `python-telegram-bot==22.8`؛ `ChatAction.TYPING` تنها actionِ مجاز.
- SQLite/D1 و اسکیما دستنخورده میمانند؛ `SCHEMA_VERSION` همان ۱۲ است.
- خاموشیِ فعلی فقط تسکهای `publication|user|thinking` را لغو میکند ⇒ نامِ heartbeat باید
  صریحاً `typing:` باشد و به لیستِ خاموشی اضافه شود، وگرنه orphan میماند.
- `run_user_operation` در برخی تستها با `context`ِ بدونِ `bot` صدا زده میشود ⇒ نبودِ bot
  باید بیِ اثر رد شود (هیچ استثنا، هیچ typing).

## 5. رویکردهای بررسی‌شده

| # | رویکرد | مزیت | عیب | نتیجه |
|---|---|---|---|---|
| A | typing دقیقاً دورِ `ai_chat` | کمترینِ تماسِ شبکه | کابلکشیِ `chat_id` به `generate.py`/`qa.py`/تستِ مدل و تغییرِ امضاءها | رد شد |
| B | typing در مرزِ عملیات + Q&A با context managerِ مشترک | پوششِ کامل، لغو و خاموشیِ موجود قابلِ استفاده، بدونِ کابلکشی | cmdهای خیلی سریع واردِ مرز میشوند ⇒ delay حل میکند | **انتخاب‌شده** |
| C | فقط Q&A | کوچکترین diff | طولانیترین انتظار (تولیدِ مقاله) بدونِ typing ⇒ نیمهکاره | رد شد |

## 6. طراحی

### 6.1 ماژولِ جدید: `newsbot/chat_action.py`

```
typing_while(bot, chat_id, kind="op") -> AsyncContextManager[None]
```

- `async with` وارد میشود ⇒ heartbeat ثبت/آغاز میشود؛ خارج میشود ⇒ متوقف و جمع میشود.
- **قابلِ لغو در هر لحظه**؛ `finally` حتی در `CancelledError` اجرا میشود.
- داخلِ heartbeat:
  1. `await asyncio.sleep(TYPING_START_DELAY_S)` — لوپِDelay قابلِ لغو است.
  2. حلقه: `await bot.send_chat_action(chat_id, ChatAction.TYPING)` سپس
     `await asyncio.sleep(TYPING_REFRESH_S)`.
  3. هر استثنا ⇒ debug log و شمارشِ شکست؛ بعد از ۳ شکستِ متوالی heartbeat آرام خاموش
     میشود (دیگر 429 نزنیم)، رکوردش از `_active` حذف و `_muted_until[chat_id]` =
     `time.monotonic()+30` ثبت میشود. ورودِ بعدی در همان چت تا ۳۰ ثانیه heartbeat جدید
     نمیسازد و بعد از آن دوباره تلاش میکند (تصمیم ۵). استثنا **هرگز** به بیرون نشت نمیکند.
- **`_active` registry**: `{chat_id: _Heartbeat}` با شمارندهٔ ورود. ورودِ دوم در همان چت
  فقط شمارنده را بالا میبرد؛ خروجِ آخرین نفر تسک را لغو و جمع میکند ⇒ G5.
- نامِ تسک: `typing:<chat_id>:<kind>` (برایِ گیر خوردن در لیستِ خاموشی).
- **پروتکلِ توقف**: `task.cancel()`؛ سپس با همان الگوی `publish.py` (حلقهٔ
  `await asyncio.shield(task)` و جذبِ `CancelledError`) تا `task.done()` صبر میکند — حتی
  اگر تسکِ جاری خودش در حالِ لغو باشد. سپس رکورد از `_active` حذف میشود ⇒ G3.
- **idempotency**: خروج زمانی که رکورد قبلاً توسط `stop_all_typing()` حذف شده، هیچ
  `KeyError` نمیدهد (بررسیِ عضویت قبل از حذف).
- `stop_all_typing()` ⇒ تمامِ heartbeatها را لغو و جمع میکند، `_active` را خالی
  میکند و توسطِ `post_shutdown` قبل از لغوِ تسکها صدا زده میشود.
- `mark_shutdown()` ⇒ پرچمی که دلیلِ توقفِ بعدی را `shutdown` ثبت میکند (بهجایِ `cancel`).

### 6.2 پیکربندی (`newsbot/config.py`, `.env.example`)

```python
TYPING_ENABLED = os.getenv("TYPING_ENABLED", "1").strip() == "1"
TYPING_START_DELAY_S = _env_float("TYPING_START_DELAY_S", 1.0, 0.0, 30.0)
TYPING_REFRESH_S = _env_float("TYPING_REFRESH_S", 4.0, 1.0, 5.0)
```

سقفِ ۵ ثانیه عمداً است: بالاتر از آن انقضای طبیعیِ تلگرام باعثِ چشمک زدن میشود. فقط دو مقدارِ
عددی (`TYPING_START_DELAY_S`, `TYPING_REFRESH_S`) واردِ لیستِ بازههای `app.py` میشوند؛
`TYPING_ENABLED` بولی است و بازهٔ عددی ندارد (رulingِ بازبین).

### 6.3 نصب در `run_user_operation`

درونِ `run()` و بعد از `op.started = True`، فقط دورِ `work()`:

```python
async with typing_while(getattr(context, "bot", None), op.chat_id, kind=kind):
    result = await work()
```

دلیلِ دورِ `work()` نه کلِ `run()`: پیامِ وضعیتِ نهایی (terminal) نباید typing بماند.
نبودِ `bot` ⇒ context manager بلافاصله return میکند (سازگاری با تستها).

### 6.4 نصب در `on_group_reply`

از لحظهٔ بعد از claimِ موفق تا ارسالِ نتیجه، دورِ فراخوانیِ AI و `_send`:

```python
async with typing_while(context.bot, chat_id, kind="qa"):
    ... ai_models.ai_chat(...) / _send(...) / qa_finish(...)
```

پیامِ «سهمیه تمام شد» / cooldown / `no_text` که **قبل** از این بلوک رد میشود، هیچ
chat action نمیکند ⇒ G4 و قاعدهٔ فاز ۵ (درخواستِ ردشده هزینه ندارد) دستنخورده.

### 6.5 خاموشی (`app.post_shutdown`)

اضافه کردنِ `"typing"` بهtuple نامها:

```python
... in ("publication", "user", "thinking", "typing")
```

همان دو دورِ لغو و همان مهلتها اعمال میشود. پیش از لغوِ تسکها:
`chat_action.mark_shutdown()` و `await chat_action.stop_all_typing()` صدا زده میشود.
تستِ تقاطعی: `typing:` لغو، `_active` خالی، `other:keep` دستنخورده.

### 6.6 لاگ و امنیت

```
typing: start chat=<id> kind=<kind>      // debug
typing: stop  chat=<id> reason=<normal|exception|cancel|shutdown>
```

بدونِ متنِ پیام، بدونِ token، بدونِ کلید. chat id یک شناسهٔ فنی است و لاگِ سایر
ماژولها هم آن را نام میبرد؛ در صورتِ حساسیت، فقط طول/هشِ آن ثبت میشود (پیشفرض:
شناسه، همانندِ لاگِ Q&A).

## 7. فایل‌های درگیر

| فایل | تغییر |
|---|---|
| `newsbot/chat_action.py` | **جدید**: context manager، heartbeat، registry، پروتکلِ توقف |
| `newsbot/qa.py` | نصبِ `typing_while` دورِ AI+تحویل |
| `newsbot/ui_widgets.py` | نصبِ `typing_while` دورِ `work()` در `run_user_operation` |
| `newsbot/config.py` | سه متغیرِ `TYPING_*` |
| `newsbot/app.py` | بازههای preflight + `"typing"` در لیستِ خاموشی |
| `.env.example` | سه متغیر با توضیح |
| `tests/test_phase6_typing_lifecycle.py` | **جدید**: تستهای قطعیِ RED-first |
| `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md` | وضعیت/شواهد/تحویلِ فاز ۶ |

## 8. تست‌ها (TDD — اول قرمز)

**حالتِ خالص**
1. `TYPING_ENABLED=0` ⇒ نه action و نه تسک.
2. عملیاتِ کوتاهتر از delay ⇒ صفر action.
3. عملیاتِ طولانی ⇒ دقیقاً یک action بعد از delay و سپس هر refresh (با delay/refreshِ تزریقیِ ۰.۰۱s).
4. action برابرِ `ChatAction.TYPING` و chat_id درست است.
5. خروجِ عادی ⇒ تسک لغو، `task.done()`، `_active` خالی، `asyncio.all_tasks()` بدونِ typing.
6. استثنا داخلی ⇒ همان پاکسازی + استثنا عیناً به بیرون میرود.
7. `CancelledError` داخلی ⇒ همان پاکسازی، بدونِ orphan.
8. شکستِ `send_chat_action` ⇒ کارِ اصلی موفق میماند و heartbeat بعد از ۳ شکست متوقف میشود.
8b. همان چت در بازهٔ ۳۰ ثانیهٔ cooldown ⇒ heartbeatِ تازه ساخته نمیشود؛ بعد از ۳۰s دوباره تلاش میکند.
9. دو ورودِ همزمان در یک چت ⇒ **یک** heartbeat و خروج تنها بعد از دومی.
10. دو چتِ متفاوت ⇒ دو heartbeat مستقل.
11. تسکِ `typing:` توسط `post_shutdown` لغو میشود و تسکِ `other:keep` دستنخورده میماند.
11b. بعدِ `post_shutdown`، `_active` خالی است و خروجِ بعدیِ context بدونِ `KeyError` تمام میشود.
12. `run_user_operation` با `context.bot` ⇒ action میزند؛ بدونِ bot ⇒ بدونِ خطا و بدونِ action.
13. `on_group_reply` با AIِ کند ⇒ action؛ ردِ سهمیه ⇒ صفر action.
14. سه متغیرِ config در `.env.example` و بازههای `app.py` موجودند.

**پایداری**
15. همان تستها ۵ بار پشتِ سرهم (`-x`) سبز ⇒ بدونِ race/flake.
16. کل مجموعه بدونِ رگرسیون؛ `ruff`/`mypy`/`final_verification.py`/`run.py --check` تمیز.

## 9. پذیرش

- تمام تستهای بند ۸ اول قرمز، سپس سبز؛ ۱۶ تستِ تازه.
- CI سبز؛ gate 15/15؛ PRها با squashmerge بسته شوند.
- چون فاز ۶ رفتارِ گروه و عملیاتها را تغییر میدهد، سناریوی زندهٔ آن در همان اجرای واحدِ
  فاز ۱۰ (D-7) دیده میشود؛ در این فاز **ادعای live-verified نمیکنیم**.
- ولی برخلافِ فاز ۵، «پاک بودنِ تسکها» با تستّ قطعیِ `all_tasks()` ثابت میشود، نه فقط با نگاه.

## 10. کارِ باقیمانده برای سازنده

- نوشتنِ تستهای قرمز (بند ۸) ⇒ سپس پیادهسازی.
- دقتِ ویژه به الگوی لغوِ `publish.py` هنگامِ awaitِ heartbeat درونِ `finally`.
- اجرای ۵ بارِ متوالیِ تستها برای اطمینان از نبودِ flake.
- ثبتِ شواهدِ واقعی (تعداد تست، CI، محافظت) در فایلِ وضعیت.
