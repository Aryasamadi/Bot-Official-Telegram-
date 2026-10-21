# -*- coding: utf-8 -*-
"""دیکشنری دوزبانه‌ی رابط کاربری + متن‌های قابل ویرایش مدیر کلان + DIAG/STAGES.\nNEW v4: کلیدهای ref_* (ارجاع)، mand_* (کانال اجباری) و کلیدهای DIAG\n«queue_full / soft_window / mand_missing / hint_mand» اضافه شدند\n(در نسخهٔ قبل soft_window ثبت می‌شد ولی در DIAG نبود ⇒ render با KeyError می‌ترکید).\n"""
from newsbot import config
from newsbot.db import gget, gset
from newsbot.timeutils import tz_key_of
from newsbot.ai_models import ai_err_text

# ══════════════════════════════════════════════════════════════════════════
# متن‌های سراسری دوزبانه (قابل ویرایش توسط مدیر کلان)
# ══════════════════════════════════════════════════════════════════════════
DEFAULT_TEXTS = {
    "welcome": {"fa": "سلام {name} 👋\\n\\nمنابع خبری‌تان را وصل کنید؛ بازنویسی و انتشار را به ربات بسپارید.",
                "en": "Hi {name} 👋\\n\\nConnect your sources. Leave the rewriting and publishing to me."},
    "help": {"fa": "<b>راهنمای سریع</b>\\n/create · مدیریت کانال‌ها\\n/ref · دعوت و پاداش\\n/man · پشتیبانی\\n/lang · زبان\\n/about · درباره\\n/cancel · لغو",
             "en": "<b>Quick guide</b>\\n/create · setup & manage\\n/ref · invites & rewards\\n/man · support\\n/lang · language\\n/about · about\\n/cancel · cancel"},
    "about": {"fa": "<b>از منبع تا انتشار.</b>\\nهوش مصنوعی، کارهای تکراری کانال شما را انجام می‌دهد.",
              "en": "<b>From source to post.</b>\\nAI takes care of your channel’s repetitive work."},
    "pay": {"fa": "<b>💳 پرداخت پلن «{plan}»</b>\\nمبلغ: <b>{price}</b>\\n\\nلطفاً دقیقاً همین مبلغ را به کارت زیر واریز کنید:\\n<code>[شماره کارت]</code>\\nبه نام: <b>[نام صاحب کارت]</b>",
            "en": "<b>💳 Payment for “{plan}”</b>\\nAmount: <b>{price}</b>\\n\\nPlease transfer exactly this amount to the card below:\\n<code>[CARD NUMBER]</code>\\nName: <b>[CARD HOLDER NAME]</b>"},
}

# کلیدهای متن‌های قابل‌ویرایش توسط سوپرادمین (منبع یکتا: لایهٔ i18n).
# قبلاً در ui_views_admin تعریف شده بود و ui_inputs از آن‌جا import می‌کرد که
# منجر به ImportError هنگام import شدن ui_inputs می‌شد (باگ ریشه‌ای H15).
# حالا هم‌جا با gtext/gtext_set که روی همین کلیدها کار می‌کنند زندگی می‌کند.
TEXT_KEYS = ["welcome", "help", "about", "pay"]


def gtext(key, lang="fa", **kw):
    """متنِ قابل‌ویرایشِ مدیر کلان.

    PH-05/UI-002: قالب همیشه اعمال می‌شود (حتی بدونِ kw) تا متن‌هایی که عمداً یک
    placeholder را **نشان می‌دهند** با ``{{…}}`` نوشته شوند و ناخواسته به «نامِ کلیدِ
    گم‌شده» تبدیل نشوند. اگر قالب‌بندی شکست بخورد (مثلاً آدمین یک «{» تایپ کرده باشد)
    ⇒ متنِ خام برگردانده می‌شود (مثلِ رفتارِ قبلی)."""
    v = gget(f"text_{key}_{lang}") or DEFAULT_TEXTS[key].get(lang) or DEFAULT_TEXTS[key]["fa"]
    try: return v.format(**kw)
    except Exception: return v

def gtext_set(key, lang, value): gset(f"text_{key}_{lang}", value)


def ltr(s):
    """PH-05/UI-018: جداسازیِ دوجهته برای متن‌های لاتین/عددی داخلِ پاراگرافِ فارسی.

    بدونِ این، یک بازهٔ لاتین (مثل «۵ USDT») در متنِ راست‌به‌چپ جابه‌جا دیده می‌شود."""
    t = str(s if s is not None else "")
    return "‎" + t + "‎" if t else t

# ══════════════════════════════════════════════════════════════════════════
# دیکشنری دوزبانه‌ی رابط کاربری (fa, en)
# ══════════════════════════════════════════════════════════════════════════
TXT = {
    "back": ("🔙 بازگشت", "🔙 Back"), "home": ("🏠 خانه", "🏠 Home"), "cancel": ("❌ انصراف", "❌ Cancel"), "cancelled": ("↩️ لغو شد", "↩️ Cancelled"),
    "saved": ("✅ ذخیره شد", "✅ Saved"), "deleted": ("🗑 حذف شد", "🗑 Deleted"), "on": ("روشن", "on"), "off": ("خاموش", "off"), "yes": ("✅ بله", "✅ Yes"), "no": ("❌ خیر", "❌ No"),
    "send_value": ("مقدار جدید را بفرستید", "Send the value"), "step": ("🧩 <b>{i}/{n}</b>", "🧩 <b>{i}/{n}</b>"), "empty": ("⚠️ موردی وجود ندارد", "⚠️ Empty"),
    "notfound": ("❌ یافت نشد", "❌ Not found"), "error": ("❌ {e}", "❌ {e}"),
    "choose_lang": ("🌐 <b>زبان / Language</b>", "🌐 <b>زبان / Language</b>"), "lang_set": ("✅ زبان: فارسی", "✅ English"), "banned": ("⛔ دسترسی شما به ربات مسدود شده است", "⛔ Access blocked"),
    "lang_set_n": (" · زبان {n} کانال هم تغییر کرد", " · {n} channel(s) relocalized"),
    "busy_click": ("⏳ لطفاً کمی صبر کنید", "⏳ Slow down a bit"),
    # ---- کاربر / پلن
    # PH-05/UI-014: دکمه‌های صفحهٔ خانه (پشتیبانی/دعوت/راهنما/زبان) — پیش از این صفحهٔ
    # خانه فقط یک دکمه داشت و مسیرِ دعوت (u:ref) از هیچ‌جا قابل‌دسترس نبود.
    "sup_btn": ("💬 پشتیبانی", "💬 Support"), "help_btn": ("📖 راهنما", "📖 Help"), "lang_btn": ("🌐 زبان", "🌐 Language"),
    # PH-05/UI-003: بازخوردِ دکمه‌هایی که کاری نمی‌کردند
    "u_pending_note": ("⏳ پرداخت شما در حال بررسی است؛ به‌زودی نتیجه اعلام می‌شود.", "⏳ Your payment is under review; the result will be announced soon."),
    "u_locked_note": ("🔒 این پلن در حال حاضر قابل خرید نیست؛ به‌زودی اعلام می‌شود.", "🔒 This plan is not available right now; we'll announce it soon."),
    "u_noop_note": ("ℹ️ این دکمه الان کاری نمی‌کند.", "ℹ️ This button does nothing right now."),
    # PH-05/UI-010: هشدارِ «فرمِ در حالِ تکمیل دور ریخته شد» (پیش از این بی‌صدا بود)
    "form_cancelled": ("⚠️ فرمِ در حالِ تکمیل لغو شد.", "⚠️ The form in progress was cancelled."),
    "plans_btn": ("🧾 پلن‌ها", "🧾 Plans"), "plans_title": ("🧾 <b>پلن‌ها و اشتراک</b>", "🧾 <b>Plans & subscription</b>"), "plans_ref": ("🎁 با دعوت دوستان اعتبار کیف پول بگیرید: /ref", "🎁 Invite friends to earn wallet credit: /ref"), "plans_current": ("✅ پلن فعلی: <b>{name}</b>{until}", "✅ Current plan: <b>{name}</b>{until}"), "until": (" · تا {d}", " · until {d}"),
    "plans_next": ("⏭ پلن بعدی: <b>{name}</b>", "⏭ Next plan: <b>{name}</b>"), "plans_pick": ("👇 برای دیدن جزئیات و قیمت، یک پلن را انتخاب کنید.", "👇 Choose a plan to see details and price."),
    "plan_details": ("⏳ مدت: {days} روز\n📢 {posts} پست در روز · 🧪 {tests} تست در روز\n🌐 {src} منبع · 📣 {ch} کانال\n💰 قیمت: <b>{price}</b>", "⏳ Duration: {days} days\n📢 {posts} posts/day · 🧪 {tests} tests/day\n🌐 {src} sources · 📣 {ch} channels\n💰 Price: <b>{price}</b>"),
    "plan_disc_line": ("🎟 کد <b>{code}</b> · {p}٪ تخفیف: <s>{old}</s> ← <b>{new}</b>", "🎟 Code <b>{code}</b> · {p}% off: <s>{old}</s> → <b>{new}</b>"),
    "plan_renew_note": ("♻️ <b>تمدید:</b> یک دورهٔ کامل به انتهای اشتراک فعلی‌تان اضافه می‌شود؛ روزهای باقی‌مانده حفظ می‌شوند.", "♻️ <b>Renewal:</b> a full period is added to the end of your current subscription; your remaining days are kept."),
    "plan_upgrade_note": ("⬆️ <b>ارتقا:</b> پلن جدید همین الان فعال می‌شود و تاریخ پایان اشتراک تغییر نمی‌کند. فقط مابه‌التفاوت روزهای باقی‌مانده را می‌پردازید؛ ارزش باقی‌ماندهٔ پلن فعلی و موجودی کیف پول از مبلغ کم می‌شود.", "⬆️ <b>Upgrade:</b> the new plan starts now and your end date stays the same. You only pay the difference for the remaining days; your current plan's remaining value and wallet balance are deducted."),
    "plan_nonum": ("ℹ️ قیمت این پلن هنوز برای ارتقا تعیین نشده است. برای تغییر پلن با پشتیبانی تماس بگیرید: /man", "ℹ️ This plan's upgrade price isn't set yet. Contact support to switch: /man"),
    "plan_downgrade": ("🔒 تغییر به پلن پایین‌تر در طول اشتراک ممکن نیست. بعد از پایان پلن فعلی می‌توانید این پلن را بخرید.", "🔒 You can't switch to a lower plan mid-subscription. You can buy it once your current plan ends."),
    "plan_locked_btn": ("🔒 فعلاً قابل خرید نیست", "🔒 Not available right now"),
     # PH-01/FIN-005: پلنِ پولیِ فعال با پلنِ رایگان جایگزین نمی‌شود
     "plan_active_free": ("ℹ️ پلنِ فعالِ شما تا پایانِ دوره برقرار است؛ پلنِ رایگان در این فاصله قابل فعال‌سازی نیست.",
                         "ℹ️ Your active plan stays until it ends; the free plan cannot be activated in the meantime."),
     # PH-01/Q-4c: پلنِ بدونِ تعرفهٔ عددی ⇒ قیمت توافقی و خریدِ آنلاین ممکن نیست
     "plan_negotiable": ("💬 قیمت این پلن توافقی است و آنلاین خریداری نمی‌شود؛ برای دریافت قیمت با پشتیبانی (/man) تماس بگیرید.",
                         "💬 This plan's price is negotiable and is not sold online; contact support (/man) for a quote."),
    "plan_num_line": ("💵 مبلغ پایهٔ پلن: <b>{v}</b>", "💵 Base price: <b>{v}</b>"),
    "plan_credit_line": ("➖ ارزش باقی‌ماندهٔ «{name}»: {v}", "➖ Remaining value of “{name}”: {v}"),
    "plan_wallet_line": ("➖ کسر از کیف پول: {v}", "➖ Paid from wallet: {v}"),
    "plan_upg_display": ("💰 مبلغ قابل پرداخت: <b>{v}</b>\n🧮 هزینهٔ «{plan}» برای {days} روز باقی‌مانده: {cost}", "💰 Amount due: <b>{v}</b>\n🧮 “{plan}” for {days} remaining day(s): {cost}"),
    "free_word": ("رایگان", "Free"), "plan_free_btn": ("🎁 فعال‌سازی رایگان", "🎁 Activate for free"), "plan_free_used": ("✔️ قبلاً استفاده کرده‌اید", "✔️ Already used"),
    "plan_req_btn": ("💳 خرید / ارتقا", "💳 Buy / Upgrade"), "plan_renew_btn": ("🔄 تمدید", "🔄 Renew"), "plan_pending_btn": ("⏳ پرداخت در حال بررسی", "⏳ Payment under review"), "disc_btn": ("🎟 وارد کردن کد تخفیف", "🎟 Enter discount code"), "disc_remove": ("✖️ حذف کد تخفیف", "✖️ Remove code"),
    "disc_prompt": ("🎟 کد تخفیف را بفرستید", "🎟 Send the discount code"), "disc_ok": ("✅ کد تخفیف اعمال شد: {p}٪ از مبلغ قابل پرداخت کم می‌شود.", "✅ Discount applied: {p}% off the amount due."), "disc_bad": ("❌ کد تخفیف معتبر نیست.", "❌ Invalid discount code."), "disc_expired": ("❌ مهلت این کد تخفیف تمام شده است.", "❌ This code has expired."), "disc_exhausted": ("❌ ظرفیت استفاده از این کد تمام شده است.", "❌ This code has reached its usage limit."),
    "plan_notfound": ("❌ پلن یافت نشد", "❌ Plan not found"), "free_once": ("⚠️ پلن رایگان فقط یک‌بار برای هر حساب فعال می‌شود.", "⚠️ The free plan can be activated only once per account."), "free_done": ("✅ پلن «{name}» فعال شد.\n➕ حالا کانال و منبع خود را اضافه کنید: /create", "✅ “{name}” is active.\n➕ Now add your channel and sources: /create"),
    "req_pending": ("⏳ یک پرداخت شما برای این پلن در حال بررسی است؛ لطفاً منتظر نتیجه بمانید.", "⏳ A payment for this plan is already under review; please wait for the result."), "receipt_hint": ("\n\n📎 بعد از واریز، <b>تصویر رسید</b> را همین‌جا بفرستید.\n❓ سؤال دارید؟ /man", "\n\n📎 After paying, send the <b>receipt image</b> here.\n❓ Questions? /man"),
    "receipt_ok": ("✅ <b>رسید شما دریافت شد.</b>\n🕐 حداکثر تا ۱ ساعت بررسی می‌شود و نتیجه همین‌جا اعلام می‌شود.", "✅ <b>Receipt received.</b>\n🕐 It will be reviewed within 1 hour; you'll be notified here."), "wait_btn": ("🚪 خروج", "🚪 Exit"), "receipt_empty": ("⚠️ لطفاً تصویر رسید یا توضیح پرداخت را بفرستید.", "⚠️ Please send the receipt image or a payment note."),
    "receipt_too_big": ("⚠️ حجم فایل بیشتر از {n} مگابایت است؛ لطفاً تصویر یا فایل کوچک‌تری بفرستید", "⚠️ Receipt exceeds {n} MB; send a smaller image/file"),
    "pay_approved": ("🎉 <b>پرداخت شما تأیید شد.</b>\n🧾 پلن <b>{name}</b> {when}\n➕ مدیریت کانال: /create", "🎉 <b>Your payment was approved.</b>\n🧾 Plan <b>{name}</b> {when}\n➕ Manage your channel: /create"), "pay_when_now": ("فعال است تا {d}", "is active until {d}"), "pay_when_queued": ("⏭ پس از پایان پلن فعلی ({d}) شروع می‌شود", "⏭ starts after the current plan ({d})"),
    "pay_rejected": ("❌ پرداخت شما تأیید نشد.\nاگر مبلغ را واریز کرده‌اید، از طریق پشتیبانی پیگیری کنید: /man", "❌ Your payment was not approved.\nIf you already paid, contact support: /man"),
    # ---- NEW v4: ارجاع (Referral)
    "ref_btn": ("🤝 دعوت دوستان", "🤝 Invite friends"),
    "ref_title": ("🤝 <b>دوستانتان را دعوت کنید و اعتبار بگیرید</b> 🎁", "🤝 <b>Invite friends. Earn credit!</b> 🎁"),
    "ref_how": ("\n\n💎 دوستانتان را با لینک اختصاصی خود دعوت کنید.\n💰 از هر خرید موفق آن‌ها، <b>{p}٪</b> مبلغ به کیف پول شما اضافه می‌شود.\n🛍️ از این اعتبار می‌توانید برای خریدهای بعدی خود استفاده کنید.\n🚀 همین حالا لینک را برای دوستانتان بفرستید.", "\n\n💎 Invite friends using your personal link.\n💰 Earn <b>{p}%</b> of the amount from every successful purchase they make.\n🛍️ Use your wallet credit for your own purchases and save money!\n🚀 Share your link and build up more credit."),
    # PH-05/UI-015: وقتی درصدِ تنظیمات در دسترس نیست (خرابیِ موقت) ⇒ متنِ بدونِ عددِ جعلی
    "ref_how_nopct": ("\n\n💎 دوستانتان را با لینک اختصاصی خود دعوت کنید.\n💰 میزانِ اعتبار به درصدِ تنظیم‌شده در پنل مدیر بستگی دارد.\n🛍️ از این اعتبار می‌توانید برای خریدهای بعدی خود استفاده کنید.\n🚀 همین حالا لینک را برای دوستانتان بفرستید.", "\n\n💎 Invite friends using your personal link.\n💰 The reward rate follows the administrator's current settings.\n🛍️ Use your wallet credit for your own purchases and save money!\n🚀 Share your link and build up more credit."),
    "ref_line": ("\n👥 دعوت‌شدگان: {n} · 💰 اعتبار دریافتی: {c}", "\n👥 Invited: {n} · 💰 Earned credit: {c}"),
    "ref_cap_line": ("\n🎯 سقف کل پاداش شما: {cap}", "\n🎯 Your total reward cap: {cap}"),
    "ref_link": ("\n\n🔗 <b>لینک اختصاصی دعوت شما:</b>\n<code>{link}</code>", "\n\n🔗 <b>Your personal invite link:</b>\n<code>{link}</code>"),
    "ref_off": ("🤝 سیستم دعوت در حال حاضر فعال نیست", "🤝 Referral system is disabled"),
    "ref_reward": ("🎉 پاداش دعوت: {amount} به کیف پول شما اضافه شد؛ در خرید یا ارتقای بعدی خودکار کسر می‌شود.", "🎉 Referral reward: {amount} added to your wallet; it's applied automatically to your next purchase or upgrade."),
    "ref_joined": ("👤 یک کاربر جدید با لینک شما عضو شد", "👤 Someone joined via your link"),
    # ---- REF-3/تصمیم ۸ (فاز ۱۲): تأیید دعوت + تنظیمات پاداش در پنل سوپرادمین
    "ref_join_ok": ("✅ عضویت شما با لینک دعوت ثبت شد؛ از هر خرید شما، پاداشی به دعوت‌کننده تعلق می‌گیرد", "✅ Your invite is confirmed; the inviter earns a reward for each of your purchases"),
    "s_ref": ("🤝 دعوت دوستان", "🤝 Referrals"),
    "s_ref_title": ("🤝 <b>تنظیمات دعوت دوستان</b>", "🤝 <b>Referral reward settings</b>"),
    "s_ref_help": ("\n\nℹ️ درصد روی مبلغ هر خرید موفق اعمال می‌شود و همان لحظه به کیف پول معرف اضافه می‌شود.\n🎯 سقف، محدودیتِ مجموع پاداش هر معرف است (۰ = نامحدود).", "\n\nℹ️ The percent applies to the amount of each successful purchase and is added to the inviter's wallet instantly.\n🎯 The cap limits the inviter's total reward (0 = unlimited)."),
    "s_ref_line_p": ("\n\n📊 درصد پاداش هر خرید: <b>{p}%</b>", "\n\n📊 Reward percent per purchase: <b>{p}%</b>"),
    "s_ref_line_c": ("\n🎯 سقف کل پاداش هر معرف: <b>{c}</b>", "\n🎯 Total cap per referrer: <b>{c}</b>"),
    "s_ref_unlimited": ("نامحدود", "unlimited"),
    "s_ref_setp": ("✏️ درصد پاداش", "✏️ Reward percent"),
    "s_ref_setc": ("✏️ سقف پاداش", "✏️ Reward cap"),
    "s_ref_prompt_p": ("درصد پاداش هر خرید (۰ تا ۵۰):", "Reward percent per purchase (0 to 50):"),
    "s_ref_prompt_c": ("سقف مبلغ کل پاداش هر معرف (۰ = نامحدود):", "Total reward cap per referrer (0 = unlimited):"),
    # ---- NEW v4: کانال اجباری (فقط ادمین)
    "mand_join": ("🔐 عضویت در کانال", "🔐 Join mandatory channel"),
    "mand_check": ("✅ عضو شدم", "🔄 Check membership"),
    "mand_missing": ("🔐 عضویت در کانال الزامی تأیید نشد؛ اجرا متوقف شد", "🔐 Mandatory channel membership not confirmed; cycle stopped"),
    "mand_ok": ("✅ عضویت تأیید شد", "✅ Membership confirmed"),
    "mand_still": ("⚠️ هنوز عضو کانال نشده‌اید", "⚠️ Membership not confirmed yet"),
    # ---- پنل مدیر
    "no_admin": ("⛔ برای استفاده از این بخش، ابتدا یک پلن فعال کنید", "⛔ Activate a plan first"), "panel": ("🛠 <b>مدیریت کانال‌ها</b>", "🛠 <b>Admin panel</b>"), "no_plan": ("بدون پلن", "no plan"),
    "today_line": ("📊 امروز: 📢 {p}/{pc} پست · 🧪 {t}/{tc} تست · 📣 {c}/{cc} کانال · 🌐 {s}/{sc} منبع", "📊 Today: 📢 {p}/{pc} posts · 🧪 {t}/{tc} tests · 📣 {c}/{cc} channels · 🌐 {s}/{sc} sources"),
    "plan_inactive": ("⛔ پلن شما فعال نیست؛ انتشار انجام نمی‌شود", "⛔ Plan inactive"), "pick_channel": ("📣 کانال‌های شما — برای مدیریت، یک کانال را انتخاب کنید", "📣 Your channels — pick one to manage"),
    "no_channels": ("📣 هنوز کانالی اضافه نکرده‌اید\n1️⃣ ربات را در کانال خود ادمین کنید\n2️⃣ روی «➕ افزودن کانال» بزنید", "📣 No channel\n🤖 Make the bot a channel admin\n➕ Tap “Add channel”"),
    "ch_add": ("➕ افزودن کانال", "➕ Add channel"), "my_plan": ("🧾 پلن من", "🧾 My plan"), "super_panel": ("👑 مدیریت کل", "👑 Super admin"),
    "ch_add_prompt": ("1️⃣ ربات را در کانال <b>ادمین</b> کنید (با مجوز ارسال پیام)\n2️⃣ یک پیام از کانال <b>فوروارد</b> کنید یا آیدی آن را بفرستید:\n<code>@mychannel</code> یا <code>-100…</code>\n\n🔐 فقط ادمین‌های همان کانال می‌توانند آن را اضافه کنند", "1) Make the bot a channel <b>admin</b>\n2) <b>Forward</b> a message or send the ID\n<code>@mychannel</code> / <code>-100…</code>\n\n🔐 Channel admin only"),
    "ch_need_fwd": ("⚠️ لطفاً یک پیام فورواردشده از کانال یا آیدی آن را بفرستید", "⚠️ Forward a channel message or send its ID"),
    "ch_bot_not_admin": ("❌ ربات در این کانال ادمین نیست یا مجوز ارسال پیام ندارد", "❌ Bot is not admin of this channel / can't post"), "ch_user_not_admin": ("🔐 شما ادمین این کانال نیستید", "🔐 You are not an admin of this channel"),
    "ch_exists_mine": ("⚠️ این کانال قبلاً در پنل شما ثبت شده است", "⚠️ Channel already registered"),
    "ch_owner_moved": ("⚠️ کانال «{title}» با <b>توکن معتبر</b> به {who} منتقل شد\n❓ اگر این کار را شما انجام نداده‌اید: /man", "⚠️ “{title}” moved to {who} with a <b>valid token</b>\n❓ Not you? /man"),
    "ch_taken": ("🔐 <b>این کانال در پنل کاربر دیگری ثبت شده است</b>\n👤 مالک فعلی: {who}\n🔑 انتقال فقط با توکن ۱۰ رقمی مالک امکان‌پذیر است", "🔐 <b>This channel is in another user's panel</b>\n👤 Current owner: {who}\n🔑 Transfer needs the owner's 10 digit token"),
    "ch_token_sent": ("🔑 <b>توکن انتقال برای {who} ارسال شد</b>\n📩 توکن را از مالک بگیرید و همین‌جا بفرستید\n⏳ اعتبار: <b>{min} دقیقه</b> · یک‌بارمصرف", "🔑 <b>Transfer token sent to {who}</b>\n📩 Ask the owner and send it here\n⏳ <b>{min} min</b> · single use"),
    "ch_token_ok": ("✅ توکن تأیید شد\n📣 «{title}» به پنل شما منتقل شد", "✅ Token accepted\n📣 “{title}” moved to your panel"),
    "ch_token_bad": ("❌ توکن نامعتبر، منقضی یا مصرف‌شده", "❌ Token invalid, expired or used"),
    "ch_token_dm": ("توکن انتقال مالکیت کانال «{title}»: <code>{token}</code>\nاعتبار: {min} دقیقه", "Ownership transfer token for “{title}”: <code>{token}</code>\nValid for {min} minutes"),
    "ch_recover_text": ("🔐 کانال «{title}» در حال حاضر در پنل {who} است\n\n1️⃣ دسترسی ادمین او را در تلگرام بردارید\n2️⃣ روی «🔄 بررسی» بزنید تا مالکیت به شما برگردد", "🔐 “{title}” is currently in {who}'s panel\n\n1️⃣ Remove their access in Telegram\n2️⃣ Tap “Recheck” to take ownership back"),
    "b_recheck": ("🔄 بررسی", "🔄 Recheck"),
    "ch_recheck_ok": ("✅ مالکیت «{title}» به شما برگشت", "✅ Ownership of “{title}” restored"),
    "ch_recheck_still": ("⚠️ {who} هنوز ادمین کانال است\n1️⃣ دسترسی ادمین او را در تلگرام بردارید\n2️⃣ سپس روی «🔄 بررسی» بزنید", "⚠️ {who} is still a channel admin\n1️⃣ Remove their access in Telegram\n2️⃣ then tap “Recheck”"),
    "ch_recheck_err": ("⚠️ وضعیت ادمین‌ها از تلگرام دریافت نشد\n🔁 لطفاً کمی بعد دوباره تلاش کنید", "⚠️ Could not read admin status from Telegram\n🔁 Try again shortly"),
    "ch_reverted": ("♻️ مالکیت «{title}» به ثبت‌کنندهٔ اول برگشت\nℹ️ {who} دیگر ادمین کانال نیست", "♻️ Ownership of “{title}” returned to the original registrant\nℹ️ {who} was no longer a channel admin"),
    "more_steps": ("… و {n} مرحلهٔ قبلی", "… {n} earlier step(s)"),
    "queue_reset": ("🧹 پاک‌سازی صف", "🧹 Reset queue"),
    "queue_reset_q": ("⚠️ همهٔ موارد <b>تأییدنشدهٔ</b> صف پاک می‌شوند\n🔴 فعالیت ربات متوقف می‌شود و چرخه از اول شروع می‌شود\n🌐 منابع و پست‌های منتشرشده حفظ می‌شوند\n\nادامه می‌دهید؟", "⚠️ The <b>unapproved</b> queue is cleared\n🔴 Automation off · cycle reset\n🌐 Sources and published posts stay\n\nContinue?"),
    "queue_reset_ok": ("✅ صف پاک شد · {n} مورد حذف شد\n🔴 فعالیت ربات متوقف شد", "✅ Queue reset · {n} item(s) cleared\n🔴 automation off"),
    "ch_added": ("✅ کانال «{title}» اضافه شد", "✅ “{title}” added"), "limit_channels": ("⚠️ به سقف تعداد کانال پلن خود رسیده‌اید ({n})", "⚠️ Plan channel limit: {n}"), "limit_sources": ("⚠️ به سقف تعداد منبع پلن خود رسیده‌اید ({n})", "⚠️ Plan source limit: {n}"),
    "limit_posts": ("⚠️ سهمیهٔ پست امروز تمام شد ({n})", "⚠️ Today's post quota used ({n})"), "limit_tests": ("⚠️ سهمیهٔ تست امروز تمام شد ({n})", "⚠️ Today's test quota used ({n})"),
    # ---- پنل کانال
    "ch_panel": ("📣 <b>{title}</b>", "📣 <b>{title}</b>"), "ch_status": ("{i} ربات {st} · {mode}", "{i} Automation {st} · {mode}"), "auto": ("⚡ خودکار", "⚡ auto"), "review": ("📝 {n} مورد برای تأیید شما در صف انتشار قرار گرفت", "📝 {n} awaiting approval in publish page"),
    "ch_stats": ("🌐 {s} منبع · 📝 صف {q} · ✅ ۲۴ ساعت اخیر {p} · ♻️ ردشده {r} · 🕐 {a}", "🌐 {s} sources · 📝 queue {q} · ✅ 24h {p} · ♻️ rejected {r} · 🕐 {a}"),
    "report": ("📊 گزارش", "📊 Report"), "test": ("🧪 تست فوری", "🧪 Quick test"), "sources": ("🌐 منابع ({n})", "🌐 Sources ({n})"), "content": ("✍️ محتوا", "✍️ Content"), "sched": ("⏰ زمان‌بندی", "⏰ Schedule"),
    "queue": ("📝 صف انتشار ({n})", "📝 Publish queue ({n})"), "rejected": ("♻️ ردشده‌ها", "♻️ Rejected"), "automation": ("{i} فعالیت ربات", "{i} Automation"), "ch_del": ("🗑 حذف کانال", "🗑 Remove channel"),
    "refresh": ("🔄 به‌روزرسانی", "🔄 Refresh"), "ch_del_q": ("❓ کانال «{title}» حذف شود؟\n🌐 منابع · ⚙️ تنظیمات · 📝 صف انتشار این کانال هم حذف می‌شوند", "❓ Remove “{title}”?\n🌐 Sources · ⚙️ Settings · 📝 Queue"), "ch_deleted": ("🗑 کانال حذف شد", "🗑 Channel removed"),
    "tog_enabled": ("🟢 ربات برای این کانال فعال شد", "🟢 Automation on"), "tog_disabled": ("🔴 ربات برای این کانال متوقف شد", "🔴 Automation off"),
    # ---- تأیید دوسطحی اقدامات مخرب (فاز ۹ — UX-05/UISEC-05)
    "confirm_q": ("⚠️ <b>مطمئن هستید؟</b>\n\n{what}", "⚠️ <b>Are you sure?</b>\n\n{what}"),
    "confirm_del_q": ("🗑 «{name}» برای همیشه حذف شود؟", "🗑 Delete «{name}» permanently?"),
    "confirm_api_del_q": ("🔑 کلید API منبع «{name}» پاک شود؟", "🔑 Remove API key of «{name}»?"),
    "confirm_txtr_q": ("↩️ متن این پیام به پیش‌فرض برگردد؟ (ویرایش‌های شما پاک می‌شود)", "↩️ Reset this text to default? (your edits will be lost)"),
    "confirm_ban_q": ("⛔ این کاربر مسدود شود؟", "⛔ Ban this user?"),
    "confirm_unban_q": ("✅ مسدودیت این کاربر برداشته شود؟", "✅ Unban this user?"),
    "confirm_revoke_q": ("🎫 پلن فعال این کاربر لغو شود؟", "🎫 Revoke this user's active plan?"),
    "confirm_reset_q": ("♻️ {what} بازنشانی شود؟", "♻️ Reset {what}?"),
    "confirm_pay_no_q": ("❌ این رسید رد شود؟", "❌ Reject this receipt?"),
     "confirm_pay_refund_q": ("مبلغِ پرداخت #{id} به کیف پولِ مشتری برگردانده شود؟ این کار یک «رسیدِ بازگشت» ثبت می‌کند.", "Return payment #{id} to the customer's wallet? This records a refund receipt."),
    "s_what_free": ("امکان پلن رایگان", "free quota"), "s_what_tests": ("شمارندهٔ تست", "test counter"), "s_what_posts": ("شمارندهٔ پست", "post counter"),
    # PH-05/UI-012: متنِ «چه چیزی تأیید می‌شود» برای کنش‌های پرخطرِ سوپرادمین
    "s_what_auto": ("فعالیت ربات در همهٔ کانال‌ها", "automation in all channels"),
    "s_what_plan_free": ("پلنِ رایگان", "the free plan"),
    "s_what_uassign": ("اعطای پلن به کاربر", "assigning a plan to the user"),
    # ---- حالت‌های خالی و مرتب‌سازی (فاز ۹ — UX-02/UX-06/UX-07)
    "src_none": ("\n⚠️ هنوز منبعی ندارید.\nاز «➕ افزودن منبع» یک فید RSS یا لینک صفحه اضافه کنید.", "\n⚠️ No sources yet.\nUse “➕ Add source” to add an RSS feed or page URL."),
    "queue_none": ("📭 صف خالی است.\nبرای ساخت پیش‌نمایش، «🧪 تست فوری» را بزنید.", "📭 The queue is empty — nothing to publish.\nRun “🧪 Quick test” to generate a preview."),
    "rej_none": ("📭 موردی رد نشده است.", "📭 Nothing has been rejected."),
    "s_model_none": ("⚠️ هنوز مدلی تعریف نشده است.\nمدل‌های عمومی ممکن است شلوغ شوند؛ دست‌کم یک مدل اضافه کنید.", "⚠️ No models defined yet.\nShared models can get busy; add at least one."),
    "s_pays_more": ("\n\n… و {n} رسید قدیمی‌تر دیگر", "\n\n… and {n} older receipt(s)"),
    "q_sort_score": ("⭐ مرتب‌سازی بر اساس امتیاز", "⭐ Sort by score"), "q_sort_time": ("🕐 مرتب‌سازی بر اساس زمان", "🕐 Sort by time"),
    "s_text_reset": ("↩️ پیش‌فرض", "↩️ Default"),
    # ---- اصطلاحات و نقش‌ها (فاز ۹ — UX-03/BIZ-04)
    "s_tm_use": ("🧪 تنظیم به‌عنوان مدل تست", "Use as Test Model"), "s_tm_unset": ("↩️ لغو انتخاب مدل تست", "Unset Test Model"),
    "s_tm_selected": ("✅ انتخاب‌شده", "Selected"), "s_tm_not_sel": ("انتخاب نشده", "Not selected"),
    "s_tm_line": ("مدل تست: {st}", "Test Model: {st}"),
    "s_tm_picked": ("✅ مدل تست انتخاب شد", "Test Model selected"), "s_tm_cleared": ("↩️ مدل تست لغو شد", "Test Model cleared"),
    "role_super": ("مدیر کل", "super admin"), "role_admin": ("مشترک", "admin"), "role_user": ("کاربر", "user"),
    "assign_role_note": ("ℹ️ با تعیین پلن، نقش کاربر در صورت نیاز به‌طور خودکار به‌روز می‌شود.", "ℹ️ Assigning a plan may update the user's role automatically."),
    "s_pay_rc_err": ("❌ نمایش رسید ممکن نشد؛ کمی بعد دوباره تلاش کنید.", "❌ The receipt could not be sent; please try again later."),
    # ---- منابع
    "src_title": ("🌐 <b>منابع {title}</b> · {n}/{cap}\n💡 فید <b>RSS</b> بهترین نتیجه را می‌دهد\n🟢 وضعیت برای کانال · 🤖 وضعیت برای ربات", "🌐 <b>Sources {title}</b> · {n}/{cap}\n💡 An <b>RSS</b> feed gives the best results\n🟢 Channel status · 🤖 Bot status"),
    "src_line": ("\n{i}{b} {host} · 🆕 {n}{err}", "\n{i}{b} {host} · 🆕 {n}{err}"), "src_err": (" · ⚠️×{n}", " · ⚠️×{n}"), "src_add": ("➕ افزودن منبع", "➕ Add source"),
    "src_add_prompt": ("🔗 آدرس منبع را بفرستید.\n\n<code>https://example.com/feed</code>\n\n💡 سایت با RSS بهتر است (<code>/feed</code> · <code>/rss</code> · <code>/atom.xml</code>). آدرس صفحهٔ اصلی هم کافی است.", "🔗 Send the source URL.\n\n<code>https://example.com/feed</code>\n\n💡 An RSS site works best (<code>/feed</code> · <code>/rss</code> · <code>/atom.xml</code>). A homepage URL is enough."),
    "src_dup": ("⚠️ این منبع قبلاً اضافه شده است", "⚠️ Duplicate source"), "src_checking": ("🔎 در حال بررسی منبع…", "🔎 Checking source…"),
    "src_added": ("✅ منبع اضافه شد · {n} مطلب پیدا شد [{m}]", "✅ Source added · {n} articles [{m}]"),
    "src_403": ("🚫 این سایت دسترسی ربات را مسدود کرده است (HTTP 403)\nامکان دریافت مطلب از آن وجود ندارد", "🚫 This site blocks bots (HTTP 403)\nNo content can be fetched from it or similar sites"),
    "src_dead": ("⚠️ دریافت محتوا از این آدرس ناموفق بود\n📡 اگر سایت فید RSS دارد، آدرس فید را مستقیم بفرستید", "⚠️ Load test failed\n📡 If it has RSS, give the feed URL directly"),
    "src_added_off": ("\n➕ منبع اضافه شد اما 🔴 غیرفعال است", "\n➕ The source was added but is 🔴 off"), "src_now_off": ("\n🔴 منبع غیرفعال شد", "\n🔴 The source was turned off"),
    "src_view": ("🌐 <b>{host}</b>\n<code>{url}</code>\n{feed}\n{st} · آخرین بررسی {last} · 🆕 {n} · ⚠️×{f}{err}", "🌐 <b>{host}</b>\n<code>{url}</code>\n{feed}\n{st} · last check {last} · 🆕 {n} · ⚠️×{f}{err}"),
    "src_feed": ("📡 <code>{u}</code>", "📡 <code>{u}</code>"), "active": ("🟢 فعال", "🟢 active"), "inactive": ("🔴 غیرفعال", "🔴 inactive"), "toggle": ("⏯ فعال/غیرفعال", "⏯ On/off"), "delete": ("🗑 حذف", "🗑 Delete"),
    "src_st": ("{c} کانال · {b} ربات", "{c} channel · {b} bot"), "tog_ch": ("⏯ منبع کانال", "⏯ Channel source"), "tog_bot": ("🤖 منبع ربات", "🤖 Bot source"),
    "src_ch_on": ("🟢 منبع برای کانال روشن شد", "🟢 Source on for channel"), "src_ch_off": ("🔴 منبع برای کانال خاموش شد", "🔴 Source off for channel"),
    "src_bot_on": ("🟢 منبع برای محتوای ربات روشن شد", "🟢 Source on for bot content"), "src_bot_off": ("🔴 منبع برای محتوای ربات خاموش شد", "🔴 Source off for bot content"),
    "src_api": ("🔌 API", "🔌 API"), "src_api_none": ("🔌 بدون API", "🔌 No API"), "src_api_on": ("🔌 API: <code>{u}</code>", "🔌 API: <code>{u}</code>"),
    "src_api_url": ("🔌 آدرس API منبع را بفرستید\n<code>https://site.com/wp-json/wp/v2/posts?per_page=20</code>\n\nℹ️ جای کلید در آدرس را <code>{{key}}</code> بگذارید\n🗑 برای حذف بنویسید <code>-</code>", "🔌 Send the source API URL\n<code>https://site.com/wp-json/wp/v2/posts?per_page=20</code>\n\nℹ️ Put <code>{{key}}</code> where the key goes\n🗑 Send <code>-</code> to remove"),
    "src_api_key": ("🔑 کلید API را بفرستید\n➖ اگر کلید لازم نیست، <code>-</code> بفرستید", "🔑 Send the API key\n🗑 If not needed, send <code>-</code>"),
    "src_api_ok": ("✅ API ثبت شد · {n} آیتم خوانده شد", "✅ API saved · {n} items read"), "src_api_bad": ("⚠️ پاسخ API قابل استفاده نبود\n🔴 آدرس ثبت شد اما منبع غیرفعال است", "⚠️ API response was not usable\n🔴 Saved but the source is off"),
    "src_api_del": ("🗑 API حذف شد", "🗑 API removed"),
    "src_deleted": ("🗑 منبع حذف شد", "🗑 Source deleted"), "src_recheck": ("🔎 بررسی دوباره", "🔎 Re-check"),
    # ---- محتوا
    "content_title": ("✍️ <b>محتوا — {title}</b>\n🎯 {topic} · 🗣 {lang}\n📝 <i>{prompt}</i>\n✒️ {sig}", "✍️ <b>Content — {title}</b>\n🎯 {topic} · 🗣 {lang}\n📝 <i>{prompt}</i>\n✒️ {sig}"),
    "general": ("عمومی", "general"), "b_topic": ("🎯 موضوع", "🎯 Topic"), "b_lang": ("🗣 زبان خروجی", "🗣 Output language"), "b_prompt": ("📝 پرامپت", "📝 Prompt"), "b_sig": ("✒️ امضا", "✒️ Signature"),
    "b_cats": ("🗂 دسته‌ها ({n})", "🗂 Categories ({n})"), "b_crits": ("📏 معیارها ({n})", "📏 Criteria ({n})"), "b_min": ("⭐ حداقل امتیاز {n}", "⭐ Min score {n}"), "b_words": ("🔢 حداکثر {n} کلمه", "🔢 Words {n}"), "b_limit": ("📏 حداکثر {n} کاراکتر", "📏 Char limit {n}"),
    "b_hashtags": ("#️⃣ هشتگ {i}", "#️⃣ Hashtags {i}"), "b_link": ("🔗 لینک منبع {i}", "🔗 Source in bot {i}"), "b_media": ("🖼 رسانه {i}", "🖼 Media {i}"), "b_strict": ("🛡 فیلتر سخت تبلیغ {i}", "🛡 Strict filter {i}"),
    "tog_hashtags": ("#️⃣ هشتگ", "#️⃣ Hashtags"), "tog_include_link": ("🔗 لینک منبع", "🔗 Source in bot"), "tog_include_media": ("🖼 رسانه", "🖼 Media"), "tog_strict_ads": ("🛡 فیلتر سخت تبلیغ", "🛡 Strict ad filter"), "tog_allow_undated": ("📅 خبرهای بدون تاریخ", "📅 Undated"),
    "togd_hashtags": ("هشتگ‌های مرتبط در انتهای پست اضافه می‌شوند", "Related hashtags are added at the end of the post"), "togd_include_link": ("لینک منبع فقط در نسخهٔ کامل («ادامه در ربات») نمایش داده می‌شود", "The news source is shown only in “continue in bot”"), "togd_include_media": ("تصویر یا ویدیوی خبر همراه پست ارسال می‌شود", "The news photo or video is sent with the post"), "togd_strict_ads": ("فیلتر سخت‌گیرانه‌تر، مطالب تبلیغاتی را دقیق‌تر حذف می‌کند", "The stricter filter rejects ad-like articles earlier"), "togd_allow_undated": ("مطالب بدون تاریخ هم منتشر می‌شوند", "Articles without a publish date are published too"),
    "cats_title": ("🗂 <b>دسته‌ها</b>\nهوش مصنوعی برای هر مطلب یک دسته انتخاب می‌کند و با سبک همان دسته می‌نویسد.", "🗂 <b>Categories</b>\nAI picks one per article and writes in its style."), "cat_add": ("➕ افزودن دسته", "➕ Category"),
    "cat_view": ("{e} <b>{name}</b>\n📐 {style}", "{e} <b>{name}</b>\n📐 {style}"), "cat_edit": ("✏️ ویرایش سبک", "✏️ Style"), "cat_style_prompt": ("سبک نگارش جدید این دسته را بفرستید:", "New writing style for this category:"),
    "cat_added": ("✅ دسته اضافه شد", "✅ Category added"), "cat_updated": ("✅ سبک به‌روزرسانی شد", "✅ Style updated"), "cat_min": ("⚠️ حداقل یک دسته لازم است", "⚠️ At least one category required"),
    "crits_title": ("📏 <b>معیارها</b>\nامتیاز هر مطلب بین ۰ تا ۱۰۰ است؛ مطالب زیر {m} رد می‌شوند.", "📏 <b>Criteria</b>\nScore 0–100 · below {m} is rejected."), "crit_add": ("➕ افزودن معیار", "➕ Criterion"),
    "crit_line": ("\n• {name} — {w} ({p}%)", "\n• {name} — {w} ({p}%)"), "crit_btn": ("{name} · {w}", "{name} · {w}"), "crit_view": ("📏 <b>{name}</b> · وزن {w}", "📏 <b>{name}</b> · weight {w}"), "crit_w": ("✏️ ویرایش وزن", "✏️ Weight"),
    "crit_w_prompt": ("وزن جدید را بفرستید (۱ تا ۱۰۰):", "New weight (1–100):"), "crit_added": ("✅ معیار اضافه شد", "✅ Criterion added"), "crit_updated": ("✅ وزن به‌روزرسانی شد", "✅ Weight updated"), "crit_min": ("⚠️ حداقل یک معیار لازم است", "⚠️ At least one criterion required"),
    # ---- هوش مصنوعی شخصیِ مدیر
    "my_ai": ("🤖 هوش مصنوعی من", "🤖 My AI"),
    "my_ai_title": ("🤖 <b>هوش مصنوعی من</b>\nابتدا از مدل‌های شخصی شما استفاده می‌شود؛ در صورت خطا، مدل‌های عمومی ربات جایگزین می‌شوند.", "🤖 <b>My AI</b>\nYour own models are used first; if they fail, the bot's shared models take over."),
    "my_ai_none": ("\n\n⚠️ <b>هنوز مدل شخصی اضافه نکرده‌اید.</b> مدل‌های عمومی ممکن است شلوغ یا موقتاً در دسترس نباشند؛ پیشنهاد می‌کنیم یک مدل شخصی اضافه کنید.", "\n\n⚠️ <b>You have no personal model yet.</b> The shared models can get busy or go down at any time; adding your own is strongly recommended."),
    "my_ai_line": ("\n{i} <b>{name}</b> · <code>{model}</code> · ✅{ok}", "\n{i} <b>{name}</b> · <code>{model}</code> · ✅{ok}"),
    "my_ai_add": ("➕ افزودن مدل", "➕ Add model"), "my_ai_added": ("✅ مدل «{name}» اضافه شد · {res}", "✅ Model “{name}” added · {res}"),
    "my_ai_view": ("🤖 <b>{name}</b>\n\n📦 نوع: {kind}\n🧬 مدل: <code>{model}</code>\n🔗 نشانی: <code>{base}</code>\n📊 وضعیت: {st}\n✅ موفق: {ok}\n⚠️ خطا: {fc}\n\n🕐 آخرین موفق: {lo}\n🕐 آخرین خطا: {lf}", "🤖 <b>{name}</b>\n\n📦 Kind: {kind}\n🧬 Model: <code>{model}</code>\n🔗 Base: <code>{base}</code>\n📊 Status: {st}\n✅ OK: {ok}\n⚠️ Fails: {fc}\n\n🕐 Last OK: {lo}\n🕐 Last fail: {lf}"),
    # ---- زمان‌بندی
    "sched_title": ("⏰ <b>زمان‌بندی کانال {title}</b>\n\n⚡ حالت: {mode}\n⏱ فاصلهٔ چرخه: {iv} دقیقه\n📦 پست در هر چرخه: {ppc}\n🗓 بازهٔ خبری: {lb} ساعت\n📅 خبر بدون تاریخ: {ud}\n🌙 ساعات سکوت: {quiet}\n🌍 منطقه: {city} {tz}\n⌚ ساعت محلی: {loc}\n", "⏰ <b>Schedule — {title}</b>\n\n⚡ Mode: {mode}\n⏱ Cycle interval: {iv} min\n📦 Posts per cycle: {ppc}\n🗓 News window: {lb}h\n📅 Undated news: {ud}\n🌙 Quiet hours: {quiet}\n🌍 Zone: {city} {tz}\n⌚ Local time: {loc}\n"),
    "mode_auto": ("⚡ خودکار — انتشار مستقیم", "⚡ auto — publish directly"), "mode_review": ("📝 بازبینی — تأیید دستی پیش از انتشار", "📝 review — manual approval in queue"), "none": ("—", "—"),
    "b_interval": ('⏱ هر {n} دقیقه', '⏱ every {n} min'), "b_ppc": ('📦 {n} پست در چرخه', '📦 {n} post(s)/cycle'), "b_lookback": ('🗓 پنجره {n} ساعت', '🗓 window {n}h'), "b_undated": ('📅 بدون تاریخ: {i}', '📅 undated: {i}'),
    "b_daily_cap": ('📊 سقف روزانه {n}', '📊 daily cap {n}'), "cap_over_plan": ("⚠️ این مقدار بیشتر از سقف پلن شما ({n} پست در روز) است.", "⚠️ Cannot exceed your plan limit ({n} posts/day)."), "cap_range": ("حداکثر تا {n} پست در روز (طبق پلن شما)", "Max {n} posts/day (your plan)"), "b_quiet": ("🌙 ساعات سکوت", "🌙 Quiet hours"), "b_tz": ("🌍 منطقهٔ زمانی", "🌍 Time zone"), "b_mode": ("🔁 {m}", "🔁 {m}"),
    "mode_set_auto": ("⚡ خودکار: انتشار مستقیم", "⚡ Auto: publish directly"), "mode_set_review": ("📝 بازبینی: مطالب پیش از انتشار منتظر تأیید شما می‌مانند", "📝 Review: waits for approval"),
    "quiet_pick_start": ("🌙 <b>ساعات سکوت</b> · ساعت <b>شروع</b> را انتخاب کنید ({tz} · اکنون {loc}):", "🌙 <b>Quiet hours</b> · <b>start</b> hour ({tz} · now {loc}):"), "quiet_pick_end": ("🌙 شروع: {h}:00 · حالا ساعت <b>پایان</b> را انتخاب کنید:", "🌙 Start {h}:00 · <b>end</b> hour:"),
    "quiet_off": ("🚫 بدون ساعات سکوت", "🚫 No quiet hours"), "quiet_set": ("🌙 ساعات سکوت: {a}:00 تا {b}:00", "🌙 Quiet {a}:00 → {b}:00"), "quiet_cleared": ("🌙 ساعات سکوت حذف شد", "🌙 Quiet hours cleared"),
    "tz_pick": ("🌍 <b>منطقهٔ زمانی</b> · ساعت UTC اکنون: <b>{utc}</b>\nنزدیک‌ترین شهر به خود را انتخاب کنید:", "🌍 <b>Time zone</b> · UTC now <b>{utc}</b>\nPick the nearest city:"), "tz_set": ("🌍 {city} {tz} · ⌚ {loc}", "🌍 {city} {tz} · ⌚ {loc}"),
    # ---- صف / مقاله
    "queue_title": ("📝 <b>صف انتشار {title}</b> · {n} آماده", "📝 <b>Publish queue {title}</b> · {n} ready"), "rej_title": ("♻️ <b>ردشده‌های ۲۴ ساعت اخیر — {title}</b>", "♻️ <b>Rejected 24h {title}</b>"),
    "pub_first": ("🚀 انتشار اولین مورد", "🚀 Publish first"), "art_title": ("📝 <b>{title}</b>\n⭐ {score} · 🗂 {cat}\n📌 {st}{reason} · 🌐 {host}\n━━━━━━━━━━━━\n{body}", "📝 <b>{title}</b>\n⭐ {score} · 🗂 {cat}\n📌 {st}{reason} · 🌐 {host}\n━━━━━━━━━━━━\n{body}"),
    "art_nobody": ("<i>(محتوایی تولید نشده)</i>", "<i>(no content generated)</i>"), "art_pub": ("🚀 انتشار", "🚀 Publish"), "art_edit": ("✏️ ویرایش", "✏️ Edit"), "art_full": ("📖 نسخهٔ کامل", "📖 Full version"), "art_view": ("👁 مشاهده", "👁 View"),
    "art_del_arch": ("🗑 حذف از آرشیو", "🗑 Remove from archive"), "art_to_ready": ("♻️ بازگشت به صف انتشار", "♻️ To publish queue"), "art_edit_prompt": ("متن جدید پست را بفرستید (قالب‌بندی تلگرام حفظ می‌شود):", "New post text (Telegram formatting kept):"),
    "art_updated": ("✅ متن به‌روزرسانی شد", "✅ Text updated"), "art_moved": ("♻️ به صف انتشار منتقل شد", "♻️ Moved to queue"), "publishing": ("⏳ در حال انتشار…", "⏳ Publishing…"), "published_ok": ("✅ منتشر شد", "✅ Published"), "pub_failed": ("❌ انتشار ناموفق بود: {e}", "❌ Publish: {e}"), "full_head": ("📖 <b>نسخهٔ کامل</b>\n\n", "📖 <b>Full version</b>\n\n"),
    # ---- پلن من
    "myplan": ("🧾 <b>پلن من: {name}</b>\n\n📌 وضعیت: {st}\n📅 انقضا: {exp}{nxt}\n\n📢 پست امروز: {p}/{pc}\n🧪 تست امروز: {t}/{tc}\n🌐 منابع: {s}/{sc}\n📣 کانال‌ها: {c}/{cc}", "🧾 <b>My plan: {name}</b>\n\n📌 Status: {st}\n📅 Expires: {exp}{nxt}\n\n📢 Posts today: {p}/{pc}\n🧪 Tests today: {t}/{tc}\n🌐 Sources: {s}/{sc}\n📣 Channels: {c}/{cc}"),
    "st_active": ("🟢 فعال", "🟢 active"), "st_expired": ("🔴 منقضی‌شده", "🔴 inactive"), "exp_at": (" · تا {d}", " · until {d}"), "upgrade_btn": ("⬆️ ارتقا / تمدید", "⬆️ Upgrade / renew"),
    # ---- دلیل کوتاه ردشده‌ها (بدون جزئیات فنی)
    "rj_extract": ("استخراج متن ممکن نبود", "no extractable text"), "rj_ad": ("فیلتر تبلیغ", "ad filter"),     "rj_ai_ad": ("تبلیغ (تشخیص هوش مصنوعی)", "ad (AI)"), "rj_score": ("امتیاز کمتر از حد مجاز", "below minimum score"),
    "rj_ai": ("سرویس هوش مصنوعی پاسخ نداد", "AI service did not respond"), "rj_send": ("ارسال به کانال ناموفق بود", "sending to channel failed"), "rj_dup": ("تکراری", "duplicate"),
    "rj_admin": ("ربات در کانال ادمین نیست", "bot is not channel admin"), "rj_old": ("خارج از بازهٔ زمانی", "older than window"), "rj_undated": ("بدون تاریخ", "undated"), "rj_other": ("نامشخص", "unspecified"),
    # ---- تست
    "await_expired": ("⌛ مهلت این مرحله تمام شد؛ لطفاً دوباره از منو شروع کنید.", "⌛ This form session expired; please start again from the menu."),
    "preparing": ("⏳ در حال آماده‌سازی…", "Preparing…"), "test_wait": ("⏳ تست بعدی تا {n} ثانیهٔ دیگر امکان‌پذیر است", "⏳ Next test in {n}s"), "test_busy": ("⏳ در حال حاضر پردازش دیگری روی این کانال در جریان است", "⏳ A cycle is already running on this channel"),
    "test_ok": ("✅ <b>منتشر شد</b>", "✅ <b>Published</b>"), "test_queued": ("📝 در <b>صف انتشار</b> و منتظر تأیید شما (حالت بازبینی)", "📝 Waiting in <b>publish queue</b> (review mode)"), "test_fail": ("⚠️ <b>چیزی منتشر نشد</b>", "⚠️ <b>Nothing published</b>"),
    "test_summary_ok": ("{n} مطلب با موفقیت منتشر شد", "{n} articles published successfully"), "test_summary_fail": ("تست ناموفق بود", "Test failed"),
    "test_log": ("📋 {d}", "📋 {d}"), "back_panel": ("🔙 پنل کانال", "🔙 Channel panel"),
    "test_retry_note": ("ℹ️ این تست از سهمیهٔ شما کم نشد؛ می‌توانید همین حالا دوباره تست کنید.", "ℹ️ No quota or cooldown was applied; you can retry right away."),
    "test_src": ("🌐 ساخته‌شده از منبع: <b>{name}</b>", "🌐 Generated from source: <b>{name}</b>"),
    # --- پنل مدل/قیمت، پشتیبانی، دیپ‌لینک و متن‌های صف
    "s_title": ("👑 <b>مدیریت کل</b>\n\n🤖 مدل‌ها: {m} ({ms})\n💼 مشترکان: {a} / کاربران: {u}\n🛎 پرداخت معلق: {p}\n🎟 کد تخفیف: {dc}\n⚖️ صف زمان‌بند: {due}\n💓 آخرین فعالیت سیستم: {hb}", "👑 <b>Super admin</b>\n\n🤖 Models: {m} ({ms})\n👥 Admins: {a} / users: {u}\n🛎 Pending payments: {p}\n🎟 Discount codes: {dc}\n⚖️ Scheduler queue: {due}\n💓 Last tick: {hb}"),
    "s_all_ok": ("🟢 همه سالم", "healthy"), "s_down": ("🔴 {n} مورد خراب", "{n} down 🔴"), "s_report": ("📊 گزارش", "📊 Report"), "s_plans": ("🧾 پلن‌ها", "🧾 Plans"), "s_users": ("👥 کاربران", "👥 Users"),
    "s_models": ("🤖 مدل‌ها", "🤖 Models"), "s_pays": ("🛎 پرداخت‌ها ({n})", "🛎 Payments ({n})"), "s_discs": ("🎟 کدهای تخفیف", "🎟 Discount codes"), "s_bc": ("📣 پیام همگانی", "📣 Broadcast"), "s_texts": ("📝 متن‌های ربات", "📝 Texts"),
    "s_admins": ("💼 مشترکان", "🧑‍💼 Admins"), "s_auto": ("{i} فعالیت کل ربات", "{i} Global automation"), "s_me": ("👤 پنل شخصی", "👤 My panel"), "s_auto_on": ("🟢 فعالیت ربات در همهٔ کانال‌ها روشن شد", "🟢 Global automation on"), "s_auto_off": ("🔴 فعالیت ربات در همهٔ کانال‌ها متوقف شد", "🔴 Global automation off"),
    "s_plans_title": ("🧾 <b>پلن‌ها</b>", "🧾 <b>Plans</b>"), "s_plan_new": ("➕ پلن جدید", "➕ Plan"), "s_plan_created": ("✅ پلن «{name}» ساخته شد", "✅ “{name}” created"), "s_plan_deleted": ("🗑 پلن حذف شد", "🗑 Plan deleted"),
    "s_plan_in_use": ("🔒 این پلن کاربر فعال یا پرداخت در انتظار دارد؛ به‌جای حذف، غیرفعال شد", "🔒 Plan has active users or pending payments — deactivated (not deleted)"),
    "s_plan_view": ("🧾 <b>{name}</b> {free}\n\n📌 وضعیت: {st}\n📝 توضیح: {desc}\n\n⏳ مدت: {days} روز\n📢 پست روزانه: {posts}\n🧪 تست روزانه: {tests}\n🌐 منابع: {src}\n📣 کانال‌ها: {ch}\n\n💰 قیمت: {price} / {price_en}\n🔢 تعرفهٔ عددی: {price_num}\n👥 کاربران: {n}", "🧾 <b>{name}</b> {free}\n\n📌 Status: {st}\n📝 Description: {desc}\n\n⏳ Duration: {days} days\n📢 Posts/day: {posts}\n🧪 Tests/day: {tests}\n🌐 Sources: {src}\n📣 Channels: {ch}\n\n💰 Price: {price} / {price_en}\n🔢 Numeric tariff: {price_num}\n👥 Users: {n}"),
    "s_plan_free_set": ("🎁 تنظیم به‌عنوان پلن رایگان", "🎁 Make free"), "s_plan_free_done": ("🎁 پلن رایگان تنظیم شد", "🎁 Free plan set"), "s_plan_del": ("🗑 حذف پلن", "🗑 Delete plan"), "s_field_prompt": ("✏️ مقدار جدید «{f}» را بفرستید:", "✏️ New value for “{f}”"),
    "s_discs_title": ("🎟 <b>کدهای تخفیف</b>", "🎟 <b>Discount codes</b>"), "s_disc_new": ("➕ کد جدید", "➕ Code"), "s_disc_line": ("\n{i} <code>{code}</code> · 🎟 {p}% · ⏳ {exp} · 👥 {used}/{max}", "\n{i} <code>{code}</code> · 🎟 {p}% · ⏳ {exp} · 👥 {used}/{max}"),
    "s_disc_view": ("🎟 <code>{code}</code> {st}\n💥 {p}% · ⏳ {exp} · 👥 {used}/{max}", "🎟 <code>{code}</code> {st}\n💥 {p}% · ⏳ {exp} · 👥 {used}/{max}"), "s_disc_created": ("✅ کد {code} ساخته شد", "✅ Code {code} created"), "s_disc_dup": ("⚠️ این کد تکراری یا نامعتبر است", "⚠️ Duplicate or invalid code"),
    "s_disc_exp_bad": ("⚠️ تاریخ انقضا نامعتبر است؛ تعداد روز یا تاریخ به‌شکل YYYY-MM-DD بفرستید", "⚠️ Expiry: days number or YYYY-MM-DD"), "s_disc_p": ("✏️ درصد تخفیف", "✏️ Percent"), "s_disc_e": ("✏️ تاریخ انقضا", "✏️ Expiry"), "s_disc_m": ("✏️ سقف استفاده", "✏️ Max uses"), "s_disc_del": ("🗑 حذف کد", "🗑 Delete code"), "unlimited": ("∞", "∞"),
    "s_users_title": ("👥 <b>{what}</b> · {n} نفر\nصفحهٔ {p} از {pp}", "👥 <b>{what}</b> · {n} rows\nPage {p}/{pp}"),
    # PH-05/UI-001: این شش کلید در دیکشنری نبودند ⇒ کلیدِ خام به سوپرادمین نشان داده می‌شد
    "s_users_w": ("👥 کاربران", "👥 Users"), "s_admins_w": ("💼 مشترکان", "💼 Subscribers"),
    "s_prev": ("◀️ قبلی", "◀️ Prev"), "s_next": ("بعدی ▶️", "Next ▶️"),
    "s_model_add": ("➕ مدل", "➕ Model"), "s_models_test": ("🧪 تست همه", "🧪 Test all"),
    # PH-05/UI-023: حالتِ خالیِ فهرست‌ها (قبلاً با «کدی نیست» یکی بود با «کوئری شکست خورد»)
    "s_disc_none": ("📭 هیچ کد تخفیفی ثبت نشده است.", "📭 No discount codes yet."),
    "s_admins_none": ("📭 هنوز مشترکی وجود ندارد.", "📭 No subscribers yet."),
    "s_users_none": ("📭 کاربری ثبت نشده است.", "📭 No users yet."),
    "s_user_view": ("{name} · {role} · {lang}\nپلن: {plan} {act}\nپست امروز: {p} · تست: {t}\nکانال: {c} · منبع: {s} · تولید: {d} (انتشار: {pub})\nتاریخ عضویت: {join} · آخرین بازدید: {seen}", "{name} · {role} · {lang}\nPlan: {plan} {act}\nToday: {p} posts · {t} tests\nChannels: {c} · sources: {s} · generated: {d} (published: {pub})\nJoined: {join} · seen: {seen}"),
    "s_ban_y": ("⛔ مسدود", "⛔ banned"), "s_ban_n": ("✅ فعال", "✅ active"), "s_uplan": ("🧾 تعیین پلن", "🧾 Assign plan"), "s_urevoke": ("❌ لغو پلن", "❌ Revoke plan"), "s_uban": ("⛔ مسدود / رفع مسدودی", "⛔ Ban/unban"), "s_umsg": ("✉️ ارسال پیام", "✉️ Message"),
    "s_ureport": ("📊 کانال‌های کاربر", "📊 Channels"), "s_ufree": ("🔁 بازنشانی پلن رایگان", "🔁 Reset free"), "s_utests": ("🧪 بازنشانی تست‌ها", "🧪 Reset tests"), "s_uposts": ("📢 بازنشانی پست‌ها", "📢 Reset posts"),
    "s_plan_pick": ("🧾 پلن این کاربر را انتخاب کنید:", "🧾 Plan for this user:"), "s_assigned": ("✅ پلن تا {d} فعال شد", "✅ Plan active until {d}"), "s_assigned_q": ("⏭ به‌عنوان پلن بعدی ثبت شد (شروع از {d})", "⏭ Queued as next (after {d})"),
    "s_revoked": ("❌ پلن لغو شد", "❌ Plan revoked"), "s_banned": ("⛔ مسدود شد", "⛔ Banned"), "s_unbanned": ("✅ مسدودیت برداشته شد", "✅ Unbanned"), "s_free_reset": ("🔁 امکان پلن رایگان بازنشانی شد", "🔁 Free quota reset"), "s_tests_reset": ("🧪 شمارندهٔ تست امروز بازنشانی شد", "🧪 Tests reset"), "s_posts_reset": ("📢 شمارندهٔ پست امروز بازنشانی شد", "📢 Posts reset"),
    "s_no_channels": ("این کاربر کانالی ندارد", "No channels"), "s_pick_channel": ("📢 کانال را انتخاب کنید:", "📢 Channel:"),
    "s_models_title": ("🤖 <b>مدل‌ها</b>", "<b>Models</b>"),
    "s_model_view": ("🤖 <b>{name}</b>\n\n📦 نوع: {kind}\n🧬 مدل: <code>{model}</code>\n🔗 نشانی: <code>{base}</code>\n📊 وضعیت: {st}\n🎯 اولویت: {pr}\n🌡 دما: {temp}\n📤 حداکثر توکن: {mx}\n\n✅ موفق: {ok}\n⚠️ خطا: {fc}\n🕐 آخرین موفق: {lo}\n🕐 آخرین خطا: {lf}", "🤖 <b>{name}</b>\n\n📦 Kind: {kind}\n🧬 Model: <code>{model}</code>\n🔗 Base: <code>{base}</code>\n📊 Status: {st}\n🎯 Priority: {pr}\n🌡 Temperature: {temp}\n📤 Max tokens: {mx}\n\n✅ OK: {ok}\n⚠️ Fails: {fc}\n🕐 Last OK: {lo}\n🕐 Last fail: {lf}"),
    "s_m_off": ("⏸ خاموش", "⏸ off"), "s_m_ok": ("🟢 سالم", "🟢 healthy"), "s_m_down": ("🔴 خراب", "🔴 down"), "s_model_test": ("🧪 تست", "🧪 Test"), "s_model_del": ("🗑 حذف مدل", "🗑 Delete model"),
    "s_model_testing": ("🧪 در حال تست…", "🧪 Testing…"), "s_model_progress": ("🧪 تست مدل {i}/{n}: <b>{name}</b>", "🧪 Testing model {i}/{n}: <b>{name}</b>"), "s_model_res": ("{i} {t}s: {out}", "{i} {t}s: {out}"), "s_model_added": ("✅ مدل «{name}» اضافه شد · {res}", "✅ Model “{name}” added · {res}"), "s_model_need": ("⚠️ Base URL و نام مدل الزامی است", "⚠️ Base URL and model name are required"), "s_model_on": ("🟢 مدل روشن شد", "🟢 Model on"), "s_model_off": ("⏸ مدل خاموش شد", "⏸ Model off"),
    "s_pays_title": ("🛎 <b>پرداخت‌های معلق</b>", "🛎 <b>Pending payments</b>"), "s_pay_none": ("📭 رسید در انتظاری وجود ندارد.", "📭 No pending receipts."), "s_pay_line": ("\n• #{id} {who} · 🕐 {t}\n🧾 <b>{plan}</b> · 💰 {price}", "\n• #{id} {who} · 🕐 {t}\n🧾 <b>{plan}</b> · 💰 {price}"), "s_pay_rc": ("📎 #{id}", "📎 #{id}"),
     "s_pays_done_title": ("<b>پرداخت‌های اخیرِ تأییدشده</b>", "<b>Recently approved payments</b>"),
     "s_pay_refund_done": ("✅ مبلغ به کیف پولِ مشتری برگشت و رسیدِ بازگشت ثبت شد.", "✅ The amount was returned to the customer's wallet and a refund receipt was recorded."),
     "s_pay_refund_btn": ("↩️ بازگشت #{id}", "↩️ Refund #{id}"),
     "s_pay_disc_invalid": ("⚠️ کد تخفیف دیگر معتبر نیست (غیرفعال، منقضی یا تکمیل)؛ تأیید متوقف شد.", "⚠️ The discount code is no longer valid (inactive, expired or exhausted); approval was stopped."),
     "s_pay_stale": ("⚠️ مبلغ یا موجودیِ کیف پول از زمانِ ثبتِ رسید تغییر کرده؛ تأیید متوقف شد. از مشتری بخواهید قیمت را دوباره بگیرد.", "⚠️ The amount or the wallet balance changed since the receipt was submitted; approval was stopped. Ask the customer to re-quote."),
    "s_pay_ok": ("✅ تأیید", "✅ Approve"), "s_pay_no": ("❌ رد", "❌ Reject"), "s_pay_done_ok": ("✅ تأیید شد · پلن فعال یا رزرو شد", "✅ Approved · plan activated or queued"), "s_pay_done_no": ("❌ رد شد", "❌ Rejected"), "s_pay_seen": ("⚠️ این رسید قبلاً بررسی شده است", "⚠️ Already handled"),
    "s_pay_new": ("🛎 <b>پرداخت #{id}</b>\n👤 {who} (<code>{uid}</code>)\n🧾 <b>{plan}</b>\n💵 مبلغ پلن: {plan_price}\n💰 پرداختی: {price}{disc}{note}", "🛎 <b>Payment #{id}</b>\n👤 {who} (<code>{uid}</code>)\n🧾 <b>{plan}</b>\n💵 Plan price: {plan_price}\n💰 Payable: {price}{disc}{note}"),
    "s_bc_prompt": ("پیام همگانی را بفرستید (متن، عکس یا ویدیو؛ قالب‌بندی حفظ می‌شود):", "Broadcast message (text/photo/video; formatting kept):"), "s_bc_confirm": ("📣 این پیام برای <b>{n}</b> کاربر ارسال شود؟", "📣 Send to <b>{n}</b> users?"), "s_bc_go": ("✅ ارسال", "✅ Send"),     "s_bc_sending": ("📣 در حال ارسال به {n} کاربر…", "📣 Sending to {n} users…"), "s_bc_progress": ("📣 ارسال همگانی: {i}/{n}", "📣 Broadcasting: {i}/{n}"), "s_bc_done": ("📣 ارسال تمام شد · موفق: {ok} · ناموفق: {fail}", "📣 ok {ok} · failed {fail}"),
    "s_texts_title": ("📝 <b>متن‌های ربات</b>\nمتغیرها: <code>{{name}}</code> <code>{{plan}}</code> <code>{{price}}</code>", "<b>Texts</b>\nPlaceholders: <code>{{name}}</code> <code>{{plan}}</code> <code>{{price}}</code>"),
    "s_text_prompt": ("متن جدید «{k}» ({lg}) را بفرستید:", "New “{k}” text ({lg}):"), "s_text_saved": ("✅ متن ذخیره شد", "✅ Text saved"), "s_text_reset_done": ("↩️ به متن پیش‌فرض برگشت", "↩️ Reset to default"),
    "s_health": ("سلامت", "Health"),
    "s_mand": ("🔐 کانال اجباری", "🔐 Mandatory channel"),
    "s_mand_title": ("🔐 <b>کانال اجباری</b>\nبرای هر زبان جدا تنظیم می‌شود.\n", "<b>Mandatory channel</b>\nConfigured separately per language.\n"),
    "s_mand_line": ("{i} <b>{lg}</b> — {tgt}\n", "{i} <b>{lg}</b> — {tgt}\n"),
    "s_mand_id": ("شناسه عددی کانال", "Numeric channel ID"),
    "s_mand_user": ("یوزرنیم کانال (بدون @)", "Channel username (no @)"),
    "s_mand_bad_id": ("⚠️ شناسه باید عدد باشد", "ID must be numeric"),
    "mand_user_notice": ("🔐 برای استفاده از ربات، ابتدا عضو کانال زیر شوید.", "Join the channel to use the bot."),
    "s_umsg_prompt": ("پیام خود را برای این کاربر بفرستید:", "Message to this user:"), "s_umsg_sent": ("✅ ارسال شد", "✅ Sent"), "s_umsg_head": ("📩 <b>پیام از طرف مدیریت:</b>\n\n", "📩 <b>Admin message:</b>\n\n"),
    "sup_open": ("💬 <b>پشتیبانی</b>\nپیام خود را بفرستید؛ مستقیم به پشتیبانی می‌رسد و پاسخ همین‌جا ارسال می‌شود.", "💬 <b>Support</b>\nAny message goes straight to the admin; replies arrive here."), "sup_close": ("❌ پایان گفتگو", "❌ End chat"), "sup_closed": ("✅ گفتگو پایان یافت", "✅ Chat ended"),
    "sup_reply_head": ("💬 <b>پاسخ پشتیبانی:</b>\n", "💬 <b>Support reply:</b>\n"), "sup_sent": ("✅ ارسال شد", "✅ Sent"), "sup_fail": ("❌ ارسال نشد؛ کمی بعد دوباره تلاش کنید.", "❌ Couldn't send; please try again shortly."), "sup_received": ("✅", "✅"),
    "dl_notfound": ("🔎 متأسفانه نسخهٔ کامل این مطلب پیدا نشد؛ ممکن است حذف شده باشد. لطفاً پست را از خود کانال باز کنید یا چند دقیقه بعد دوباره تلاش کنید.", "🔎 Sorry, this full version could not be found — it may have been removed. Please open the post in the channel or try again later."), "dl_source": ("🔗 منبع", "🔗 Source"),
    # ---- PH-03 (UI-006 · UI-009 · OBS-013): پایداریِ رسیدِ پرداخت
    "pay_notify_delayed": ("✅ رسید شما ثبت شد. اعلامِ آن به مدیر با کمی تأخیر انجام می‌شود؛ لطفاً تا اعلامِ نهایی صبر کنید.", "✅ Your receipt is saved. The administrator will be notified shortly — please wait for the final confirmation."),
    "receipt_still_pending": ("ℹ️ فرمِ پرداختِ شما هنوز باز است — تصویرِ رسید را همین‌جا بفرستید.", "ℹ️ Your payment form is still open — send the receipt image here."),
}

def tr(lang, key, /, **kw):
    """متنِ دوزبانه.

    PH-05/UI-002: قالب همیشه اعمال می‌شود (حتی وقتی kw نیست) تا متن‌هایی که عمداً یک
    placeholder را **نشان می‌دهند** (مثل «متغیرها: {name}») با ``{{name}}`` نوشته شوند و
    به «نامِ کلیدِ گم‌شده» تبدیل نشوند. اگر قالب‌بندی شکست بخورد ⇒ متنِ خام (مثلِ قبل)."""
    if key not in TXT: return str(key)   # کلید ناشناخته (callback_data دست‌ساز) نباید KeyError بدهد
    s = TXT[key][1 if lang == "en" else 0]
    try: return s.format(**kw)
    except Exception: return s

TOGGLABLE = {"enabled", "hashtags", "include_link", "include_media", "strict_ads", "allow_undated"}
STATUS = {"discovered": ("🔎 کشف‌شده", "🔎 discovered"), "skipped": ("⏭ خارج از بازه", "⏭ out of window"), "rejected": ("♻️ ردشده", "♻️ rejected"), "failed": ("❌ ناموفق", "❌ failed"), "ready": ("📝 آماده", "📝 ready"), "published": ("✅ {n} مطلب در کانال منتشر شد", "✅ {n} published")}

def reason_text(reason, lang):
    """دلیل کوتاه و امن برای مدیر: هیچ نام مدل، Base URL یا متن خطای فنی نشان داده نمی‌شود."""
    r = (reason or "").strip().lower()
    if not r: return ""
    if r.startswith("ai:"): return ai_err_text(r.split(":", 1)[1].strip(), lang)
    for pre, key in (("extract", "rj_extract"), ("ad_filter", "rj_ad"), ("ai_ad", "rj_ai_ad"), ("score", "rj_score"), ("send", "rj_send"), ("duplicate", "rj_dup"), ("bot_not_admin", "rj_admin"), ("old", "rj_old"), ("undated", "rj_undated")):
        if r.startswith(pre): return tr(lang, key)
    if r.startswith("ai_"): return ai_err_text(r, lang)
    return tr(lang, "rj_other")

INT_FIELDS = {"max_words": (30, 600), "min_score": (0, 100), "post_limit": (300, 900), "interval_minutes": (config.MIN_INTERVAL, 1440), "posts_per_cycle": (1, config.MAX_PPC), "lookback_hours": (1, config.MAX_LOOKBACK)}
FIELD_LABEL = {"prompt": ("پرامپت نگارش", "Writing prompt"), "topic": ("موضوع کانال", "Channel topic"), "language": ("زبان خروجی (نام زبان را بنویسید: فارسی، English، Italian، …)", "Output language (type the language name: Italian, Chinese, English, …)"), "signature": ("امضای پایان پست (@channel = یوزرنیم کانال)", "Post signature (@channel = channel username)"),
               "daily_posts_cap": ("📊 سقف روزانهٔ پست — عددی تا سقف پلن خود بفرستید (خالی = بدون محدودیت)", "📊 Daily post cap — a number up to your plan max (blank = unlimited)"), "max_words": ("حداکثر تعداد کلمات پست", "Max post words"), "min_score": ("حداقل امتیاز قبولی (۰ تا ۱۰۰)", "Min score (0–100)"), "post_limit": ("سقف کاراکتر پست در کانال (۳۰۰ تا ۹۰۰، پیش‌فرض ۹۰۰)؛ ادامهٔ متن در «ادامه در ربات» نمایش داده می‌شود", "Channel post char limit (default 900); longer content → “continue in bot” (300–900)"),
               "interval_minutes": (f"فاصله‌ی چرخه (دقیقه، ≥{config.MIN_INTERVAL})", f"Cycle interval (min, ≥{config.MIN_INTERVAL})"), "posts_per_cycle": (f"پست در هر چرخه (≤{config.MAX_PPC})", f"Posts per cycle (≤{config.MAX_PPC})"), "lookback_hours": (f"مقالات چند ساعت اخیر (≤{config.MAX_LOOKBACK})", f"Articles from last N hours (≤{config.MAX_LOOKBACK})")}
SCHED_FIELDS = ("interval_minutes", "posts_per_cycle", "lookback_hours", "daily_posts_cap")
TZ_NAMES = {"tehran": ("🇮🇷 تهران", "🇮🇷 Tehran"), "istanbul": ("🇹🇷 استانبول", "🇹🇷 Istanbul"), "dubai": ("🇦🇪 دبی", "🇦🇪 Dubai"), "kabul": ("🇦🇫 کابل", "🇦🇫 Kabul"), "karachi": ("🇵🇰 کراچی", "🇵🇰 Karachi"), "delhi": ("🇮🇳 دهلی", "🇮🇳 Delhi"),
            "moscow": ("🇷🇺 مسکو", "🇷🇺 Moscow"), "berlin": ("🇩🇪 برلین", "🇩🇪 Berlin"), "london": ("🇬🇧 لندن", "🇬🇧 London"), "beijing": ("🇨🇳 پکن", "🇨🇳 Beijing"), "tokyo": ("🇯🇵 توکیو", "🇯🇵 Tokyo"), "sydney": ("🇦🇺 سیدنی", "🇦🇺 Sydney"),
            "newyork": ("🇺🇸 نیویورک", "🇺🇸 New York"), "losangeles": ("🇺🇸 لس‌آنجلس", "🇺🇸 Los Angeles"), "saopaulo": ("🇧🇷 سائوپائولو", "🇧🇷 São Paulo"), "utc": ("🌐 UTC (گرینویچ)", "🌐 UTC")}

def tz_city(val, lang):
    """نام شهرِ منطقه — ARCH-10: نام IANA («Asia/Tehran») یا آفست عددیِ قدیمی هر دو پذیرفته می‌شوند."""
    z = tz_key_of(val); return TZ_NAMES[z][1 if lang == "en" else 0] if z else ""

WIZ = {
    "cat_add": [("name", ("نام دسته (مثلاً خبری)", "Category name (e.g. News)"), "str"), ("emoji", ("یک ایموجی", "One emoji"), "str"), ("style", ("سبک نگارش (مثلاً: تیتر بولد، سه جمله، دو بولت)", "Writing style (e.g. bold headline, three sentences, two bullets)"), "str")],
    "crit_add": [("name", ("نام معیار", "Criterion name"), "str"), ("weight", ("وزن (۱–۱۰۰)", "Weight (1–100)"), "int")],
    "plan_new": [("name", ("نام (فارسی)", "Name (Persian)"), "str"), ("name_en", ("نام (انگلیسی)", "Name (English)"), "str"), ("days", ("مدت (روز)", "Days"), "int"), ("daily_posts", ("پست روزانه", "Posts/day"), "int"), ("max_sources", ("حداکثر منبع", "Max sources"), "int"), ("max_channels", ("حداکثر کانال", "Max channels"), "int"), ("daily_tests", ("تست روزانه", "Tests/day"), "int"),
                 ("price", ("قیمت (فارسی، مثلاً ۲۰۰٬۰۰۰ تومان)", "Price (Persian)"), "str"), ("price_en", ("قیمت (انگلیسی، مثلاً 5 USDT)", "Price (English)"), "str"),
                 ("price_num", ("تعرفهٔ عددی دلاری — برای کاربر en (مثلاً 5 = $5؛ خالی = بدون محاسبهٔ عددی)", "USD numeric tariff — for en users (e.g. 5 = $5; blank = no numeric billing)"), "float"),
                 ("price_num_fa", ("تعرفهٔ عددی تومانی — برای کاربر fa (مثلاً 3000000؛ خالی = بدون محاسبهٔ عددی)", "Toman numeric tariff — for fa users (e.g. 3000000; blank = no numeric billing)"), "float"),
                 ("description", ("توضیح کوتاه (فارسی)", "Short description (Persian)"), "str"), ("description_en", ("توضیح کوتاه (انگلیسی)", "Short description (English)"), "str")],
    "model_add": [("base_url", ("Base URL سرویس را بفرستید (همان آدرسی که سرویس‌دهندهٔ شما اعلام کرده است):", "Send the service Base URL (exactly as your provider documents it):"), "str"),
                  ("api_key", ("کلید API (توکن)", "API key (token)"), "str"), ("model", ("نام مدل", "Model name"), "str")],
    "my_ai_add": [("base_url", ("Base URL سرویس هوش مصنوعی خودتان را بفرستید (همان آدرسی که سرویس‌دهنده اعلام کرده):", "Send the Base URL of your own AI service (exactly as your provider documents it):"), "str"),
                  ("api_key", ("کلید API (توکن)", "API key (token)"), "str"), ("model", ("نام مدل", "Model name"), "str")],
    "disc_add": [("code", ("کد تخفیف (مثلاً SPRING30)", "Discount code (e.g. SPRING30)"), "str"), ("percent", ("درصد تخفیف (۱–۱۰۰)", "Discount percent (1–100)"), "int"), ("expires", ("انقضا: تعداد روز (مثلاً 30) یا تاریخ (2025-12-31)", "Expiry: days (e.g. 30) or date (2025-12-31)"), "str"), ("max_uses", ("حداکثر دفعات استفاده (0 = نامحدود)", "Max uses (0 = unlimited)"), "int")],
}

# ══════════════════════════════════════════════════════════════════════════
# تشخیص مرحله‌ای (Diagnostics) — کوتاه، نمادین، دوزبانه
# ══════════════════════════════════════════════════════════════════════════
DIAG = {
    "busy": ("⏳ پردازش دیگری روی همین کانال در جریان است؛ ابتدا همان تمام می‌شود", "⏳ Another cycle is running on this channel"),
    "checks": ("🔎 در حال بررسی تنظیمات، پلن و منابع…", "🔎 Checking settings, plan and sources…"),
    "dup": ("🔁 {n} خبر تکراری بود و دوباره ساخته نشد", "🔁 {n} duplicate item(s) skipped"),
    "no_channel": ("❌ کانالی برای بررسی پیدا نشد", "❌ Channel not found"), "plan_inactive": ("⛔ پلن شما فعال نیست؛ انتشار انجام نمی‌شود", "⛔ Plan inactive"), "no_sources": ("⚠️ هیچ منبعی ثبت نشده است", "⚠️ No source added"),
    "quota_posts": ("⛔ سهمیهٔ پست امروز پر شده است ({used}/{cap})", "⛔ Today's posts: {used}/{cap}"), "quota_tests": ("⛔ سهمیهٔ تست امروز پر شده است ({used}/{cap})", "⛔ Today's tests: {used}/{cap}"),
    "bot_not_admin": ("❌ ربات در کانال ادمین نیست یا مجوز ارسال پیام ندارد", "❌ Bot is not admin / can't post"),
    "src_ok": ("🌐 {name} [{m}]: {n} مورد بررسی شد · 🆕 {new} مورد جدید", "🌐 {name} [{m}]: {n} items · 🆕 {new}"), "src_notmod": ("🌐 {name}: از آخرین بررسی تغییری نکرده است", "🌐 {name}: unchanged"),
    "src_empty": ("🟡 {name}: فید یا مطلبی پیدا نشد", "🟡 {name}: no feed/articles found"), "src_fail": ("🔴 {name}: خواندن منبع ناموفق بود — {err}", "🔴 {name}: {err}"), "src_cooldown": ("⏸ {name}: به‌تازگی بررسی شده؛ این بار از آن صرف‌نظر شد", "⏸ {name}: checked recently"),
    "old": ("🕰 {n} خبر قدیمی‌تر از {h} ساعت بود و کنار گذاشته شد", "🕰 {n} older than {h}h"), "leftover": ("♻️ {n} مورد از دفعات قبل دوباره بررسی می‌شود", "♻️ {n} left over from earlier cycles"), "retry": ("🧪 {n} مورد قبلی دوباره بررسی می‌شود", "🧪 Re-checking {n} earlier items"),
    "found": ("🔎 {n} مطلب برای بررسی انتخاب شد", "🔎 {n} articles to process"), "none_found": ("⚠️ مطلب تازه‌ای پیدا نشد", "⚠️ No new article"),
    "extract_fail": ("📄 متن {n} مورد قابل استخراج نبود و رد شد", "📄 {n} without extractable text"), "undated": ("📅 {n} خبر بدون تاریخ بود و رد شد", "📅 {n} undated (skipped)"), "ad": ("🛡 {n} مورد تبلیغ تشخیص داده شد و حذف شد", "🛡 {n} ads (filter)"),
    "ai_fail": ("🤖 خطای هوش مصنوعی: {err}", "🤖 AI error: {err}"), "ai_ad": ("🤖 هوش مصنوعی {n} مورد را تبلیغ تشخیص داد", "🤖 {n} ads (AI)"), "low": ("⭐ {n} مورد امتیازی کمتر از حداقل ({min}) داشت؛ بالاترین امتیاز: {best}", "⭐ {n} below {min} · best {best}"),
    "from_src": ("🌐 محتوا از منبع {name} ساخته شد", "🌐 Generated from source: {name}"),
    "quiet": ("🌙 در ساعات سکوت هستیم؛ مطلب در صف انتشار قرار گرفت", "🌙 Quiet hours; queued"), "review": ("📝 {n} مورد برای تأیید شما در صف انتشار قرار گرفت", "📝 {n} awaiting approval in publish page"),
    "published": ("✅ {n} مطلب در کانال منتشر شد", "✅ {n} published"), "pub_fail": ("❌ انتشار ناموفق بود — {err}", "❌ Publish: {err}"), "detail": ("   ↳ {title} — {why}", "   ↳ {title} — {why}"),
    "queue_full": ("📥 صف انتشار پر است؛ {n} مورد جدید اضافه نشد (در اجرای بعدی برداشته می‌شوند)", "📥 Publish queue full; {n} new item(s) not inserted (next cycle picks them)"),
    "soft_window": ("🪟 در بازهٔ زمانی تعیین‌شده خبری نبود؛ {n} مورد از جدیدترین مطالب همین منابع انتخاب شد", "🪟 Nothing in window; taking {n} freshest items from the same sources"),
    "mand_missing": ("🔐 عضویت در کانال الزامی تأیید نشد؛ اجرا متوقف شد", "🔐 Mandatory channel membership not confirmed; cycle stopped"),
    "hint_sources": ("→ منبع دیگری اضافه کنید یا بازهٔ زمانی را بیشتر کنید (حداکثر ۴۸ ساعت)", "→ Add another source or widen the window (≤48h)"), "hint_score": ("→ حداقل امتیاز را کمتر کنید", "→ Lower the minimum score"),
    "hint_ads": ("→ فیلتر سخت تبلیغ را خاموش کنید", "→ Turn off strict ad filter"), "hint_ai": ("→ سرویس هوش مصنوعی پاسخ نمی‌دهد؛ چند دقیقه بعد دوباره تلاش کنید یا /man", "→ AI service failing; retry later or /man"),
    "hint_admin": ("→ ربات را با مجوز «ارسال پیام» در کانال ادمین کنید", "→ Make the bot admin with “post messages”"), "hint_undated": ("→ گزینهٔ «📅 خبرهای بدون تاریخ» را روشن کنید", "→ Turn on “undated articles”"),
    "hint_extract": ("→ متن این منبع قابل استخراج نیست؛ منبع دیگری اضافه کنید", "→ Source lacks readable text; try another"), "hint_review": ("→ حالت انتشار را روی «خودکار» بگذارید", "→ Switch mode to “auto”"), "hint_quota": ("→ پلن خود را ارتقا دهید", "→ Upgrade plan"),
    "hint_mand": ("→ ابتدا عضو کانال شوید و «✅ عضو شدم» را بزنید", "→ Join the mandatory channel and tap “Check membership”"),
    "stage": ("📍 مرحله: {stage}", "📍 Stage: {stage}"),
}
STAGES = {"start": ("شروع", "start"), "checks": ("بررسی پلن، کانال و منابع", "checks"), "discover": ("یافتن مطالب", "discovery"), "extract": ("استخراج متن", "extraction"), "filter": ("فیلتر", "filtering"), "ai": ("تولید با هوش مصنوعی", "AI generation"), "score": ("امتیازدهی", "scoring"), "publish": ("انتشار", "publishing"), "done": ("پایان", "done")}
