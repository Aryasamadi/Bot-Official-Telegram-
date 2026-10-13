# -*- coding: utf-8 -*-
"""دیکشنری دوزبانه‌ی رابط کاربری + متن‌های قابل ویرایش مدیر کلان + DIAG/STAGES.\nNEW v4: کلیدهای ref_* (ارجاع)، mand_* (کانال اجباری) و کلیدهای DIAG\n«queue_full / soft_window / mand_missing / hint_mand» اضافه شدند\n(در نسخهٔ قبل soft_window ثبت می‌شد ولی در DIAG نبود ⇒ render با KeyError می‌ترکید).\n"""
from newsbot import config
from newsbot.db import gget, gset
from newsbot.timeutils import TZ_ZONES
from newsbot.ai_models import ai_err_text

# ══════════════════════════════════════════════════════════════════════════
# متن‌های سراسری دوزبانه (قابل ویرایش توسط مدیر کلان)
# ══════════════════════════════════════════════════════════════════════════
DEFAULT_TEXTS = {
    "welcome": {"fa": "سلام {name}\n\nمنابع را می‌خوانم، با هوش مصنوعی بازنویسی می‌کنم و در کانال منتشر می‌کنم.",
                "en": "Hi {name}\n\nI read your sources, rewrite them with AI and publish to your channel."},
    "help": {"fa": "<b>راهنما</b>\n\n/create · پنل مدیریت\n/ref · دعوت دوستان\n/man · پشتیبانی\n/lang · تغییر زبان\n/about · درباره\n/cancel · لغو عملیات",
             "en": "<b>Help</b>\n\n/create · admin panel\n/ref · invite friends\n/man · support\n/lang · language\n/about · about\n/cancel · cancel"},
    "about": {"fa": "<b>درباره</b>\n\nمدیریت خودکار محتوای کانال تلگرام", "en": "<b>About</b>\n\nAutomated content manager for Telegram channels"},
    "pay": {"fa": "<b>پرداخت پلن «{plan}»</b>\n\nمبلغ: <b>{price}</b>\n\nبه شماره کارت زیر واریز کنید:\n<code>0000-0000-0000-0000</code>\nبه نام: ...\n\nسپس <b>تصویر رسید</b> را همین‌جا بفرستید.",
            "en": "<b>Payment for “{plan}”</b>\n\nAmount: <b>{price}</b>\n\nTransfer to:\n<code>0000-0000-0000-0000</code>\nName: ...\n\nThen send the <b>receipt image</b> right here."},
}

# کلیدهای متن‌های قابل‌ویرایش توسط سوپرادمین (منبع یکتا: لایهٔ i18n).
# قبلاً در ui_views_admin تعریف شده بود و ui_inputs از آن‌جا import می‌کرد که
# منجر به ImportError هنگام import شدن ui_inputs می‌شد (باگ ریشه‌ای H15).
# حالا هم‌جا با gtext/gtext_set که روی همین کلیدها کار می‌کنند زندگی می‌کند.
TEXT_KEYS = ["welcome", "help", "about", "pay"]


def gtext(key, lang="fa", **kw):
    v = gget(f"text_{key}_{lang}") or DEFAULT_TEXTS[key].get(lang) or DEFAULT_TEXTS[key]["fa"]
    try: return v.format(**kw) if kw else v
    except Exception: return v

def gtext_set(key, lang, value): gset(f"text_{key}_{lang}", value)

# ══════════════════════════════════════════════════════════════════════════
# دیکشنری دوزبانه‌ی رابط کاربری (fa, en)
# ══════════════════════════════════════════════════════════════════════════
TXT = {
    "back": ("بازگشت", "Back"), "home": ("خانه", "Home"), "cancel": ("انصراف", "Cancel"), "cancelled": ("لغو شد", "Cancelled"),
    "saved": ("ذخیره شد", "Saved"), "deleted": ("حذف شد", "Deleted"), "on": ("روشن", "on"), "off": ("خاموش", "off"), "yes": ("بله", "Yes"), "no": ("خیر", "No"),
    "send_value": ("مقدار را بفرستید", "Send the value"), "step": ("🧩 <b>{i}/{n}</b>", "🧩 <b>{i}/{n}</b>"), "empty": ("خالی است", "Empty"),
    "notfound": ("یافت نشد", "Not found"), "error": ("❌ {e}", "❌ {e}"),
    "choose_lang": ("🌐 <b>زبان / Language</b>", "🌐 <b>زبان / Language</b>"), "lang_set": ("✅ فارسی", "✅ English"), "banned": ("دسترسی مسدود است", "Access blocked"),
    "lang_set_n": (" · {n} کانال هم‌زبان شد", " · {n} channel(s) relocalized"),
    "busy_click": ("کمی آهسته‌تر", "Slow down"),
    # ---- کاربر / پلن
    "plans_btn": ("پلن‌ها", "Plans"), "plans_title": ("<b>پلن‌ها</b>", "<b>Plans</b>"), "plans_current": ("🧾 فعلی: <b>{name}</b>{until}", "🧾 Current: <b>{name}</b>{until}"), "until": (" · تا {d}", " · until {d}"),
    "plans_next": ("⏭ بعدی: <b>{name}</b>", "⏭ Next: <b>{name}</b>"), "plans_pick": ("👆 پلن را انتخاب کنید · جزئیات و قیمت", "👆 Pick a plan · details and price"),
    "plan_details": ("⏳ {days} روز · 📢 {posts} پست/روز · 🧪 {tests} تست/روز\n🌐 {src} منبع · 📣 {ch} کانال\n💰 <b>{price}</b>", "⏳ {days} days · 📢 {posts} posts/day · 🧪 {tests} tests/day\n🌐 {src} sources · 📣 {ch} channels\n💰 <b>{price}</b>"),
    "plan_disc_line": ("🎟 <s>{old}</s> ➜ <b>{new}</b> · {code} −{p}%", "🎟 <s>{old}</s> ➜ <b>{new}</b> · {code} −{p}%"),
    "plan_chain_note": ("ℹ️ پلن متفاوت: شروع بعد از پلن فعلی\n♻️ تمدید همین پلن: افزودن روز", "ℹ️ Different plan: starts after the current one\n♻️ Same plan renewed: days are added"),
    "free_word": ("رایگان", "Free"), "plan_free_btn": ("فعال‌سازی رایگان", "Activate free"), "plan_free_used": ("استفاده شده", "Used"),
    "plan_req_btn": ("خرید", "Buy"), "plan_renew_btn": ("تمدید", "Renew"), "plan_pending_btn": ("در بررسی", "Pending"), "disc_btn": ("کد تخفیف", "Discount code"), "disc_remove": ("حذف کد", "Remove code"),
    "disc_prompt": ("🎟 کد تخفیف را بفرستید", "🎟 Send the discount code"), "disc_ok": ("🎟 کد اعمال شد · −{p}%", "🎟 Code applied · −{p}%"), "disc_bad": ("❌ کد نامعتبر", "❌ Invalid code"), "disc_expired": ("❌ کد منقضی شده", "❌ Code expired"), "disc_exhausted": ("❌ ظرفیت کد تمام شده", "❌ Code fully used"),
    "plan_notfound": ("❌ پلن یافت نشد", "❌ Plan not found"), "free_once": ("⚠️ پلن رایگان فقط یک بار", "⚠️ Free plan only once"), "free_done": ("✅ «{name}» فعال شد\n➕ کانال و منبع اضافه کنید", "✅ “{name}” activated\n➕ Add a channel and sources"),
    "req_pending": ("⏳ درخواست قبلی در بررسی است", "⏳ Previous request under review"), "receipt_hint": ("\n\n📎 <i>تصویر رسید را همین‌جا بفرستید</i>", "\n\n📎 <i>Send the receipt image here</i>"),
    "receipt_ok": ("✅ <b>رسید دریافت شد</b>\n🕐 بررسی تا ۱ ساعت · نتیجه همین‌جا اعلام می‌شود", "✅ <b>Receipt received</b>\n🕐 Reviewed within 1 hour · you'll be notified here"), "wait_btn": ("خروج", "Exit"), "receipt_empty": ("⚠️ رسید یا توضیح پرداخت را بفرستید", "⚠️ Send the receipt or a payment note"),
    "pay_approved": ("🎉 پرداخت تأیید شد\n🧾 پلن <b>{name}</b> {when}\n/create", "🎉 Payment approved\n🧾 Plan <b>{name}</b> {when}\n/create"), "pay_when_now": ("🟢 فعال تا {d}", "🟢 active until {d}"), "pay_when_queued": ("⏭ شروع پس از پلن فعلی ({d})", "⏭ starts after the current plan ({d})"),
    "pay_rejected": ("❌ پرداخت تأیید نشد\n📩 پیگیری: /man", "❌ Payment not approved\n📩 Follow up: /man"),
    # ---- NEW v4: ارجاع (Referral)
    "ref_btn": ("دعوت دوستان", "Invite friends"),
    "ref_title": ("🤝 <b>دعوت دوستان</b>\nبا هر خرید موفقِ دعوت‌شده، {p}٪ از مبلغ به کیف پول شما برمی‌گردد.", "🤝 <b>Invite friends</b>\nFor each successful purchase by an invitee, {p}% returns to your wallet."),
    "ref_line": ("\n👥 دعوت‌شده: {n} · 💰 اعتبار کسب‌شده: {c}", "\n👥 Invited: {n} · 💰 Earned credit: {c}"),
    "ref_link": ("\n🔗 لینک دعوت شما:\n<code>{link}</code>", "\n🔗 Your invite link:\n<code>{link}</code>"),
    "ref_off": ("🤝 سیستم دعوت فعال نیست", "🤝 Referral system is disabled"),
    "ref_reward": ("🎉 پاداش دعوت: {amount} به کیف پول شما اضافه شد", "🎉 Referral reward: {amount} added to your wallet"),
    "ref_joined": ("👤 یک نفر با لینک شما عضو شد", "👤 Someone joined via your link"),
    # ---- NEW v4: کانال اجباری (فقط ادمین)
    "mand_join": ("عضویت در کانال", "Join channel"),
    "mand_check": ("بررسی عضویت", "Check membership"),
    "mand_missing": ("عضویت کانال اجباری تأیید نشد", "Mandatory channel membership not confirmed"),
    "mand_ok": ("عضویت تأیید شد", "Membership confirmed"),
    "mand_still": ("عضویت تأیید نشد", "Membership not confirmed"),
    # ---- پنل مدیر
    "no_admin": ("ابتدا پلن فعال کنید", "Activate a plan first"), "panel": ("<b>پنل مدیریت</b>", "<b>Admin panel</b>"), "no_plan": ("بدون پلن", "no plan"),
    "today_line": ("📢 {p}/{pc} پست · 🧪 {t}/{tc} تست · 📣 {c}/{cc} کانال · 🌐 {s}/{sc} منبع", "📢 {p}/{pc} posts · 🧪 {t}/{tc} tests · 📣 {c}/{cc} channels · 🌐 {s}/{sc} sources"),
    "plan_inactive": ("پلن فعال نیست", "Plan inactive"), "pick_channel": ("📣 کانال را انتخاب کنید\n⚙️ هر کانال تنظیمات مستقل دارد", "📣 Pick a channel\n⚙️ Each channel has its own settings"),
    "no_channels": ("📣 بدون کانال\n🤖 ربات را در کانال ادمین کنید\n➕ «افزودن کانال» را بزنید", "📣 No channel\n🤖 Make the bot a channel admin\n➕ Tap “Add channel”"),
    "ch_add": ("افزودن کانال", "Add channel"), "my_plan": ("پلن من", "My plan"), "super_panel": ("مدیر کلان", "Super admin"),
    "ch_add_prompt": ("کانال را پیدا کردم: <b>{title}</b>\nربات باید ادمین باشد.", "Found channel: <b>{title}</b>\nThe bot must be an admin."),
    "ch_need_fwd": ("⚠️ پیام فورواردشده یا آیدی کانال بفرستید", "⚠️ Forward a channel message or send its ID"),
    "ch_bot_not_admin": ("❌ ربات ادمین این کانال نیست / مجوز ارسال ندارد", "❌ Bot is not admin of this channel / can't post"), "ch_user_not_admin": ("🔐 شما ادمین این کانال نیستید", "🔐 You are not an admin of this channel"),
    "ch_exists_mine": ("⚠️ این کانال قبلاً ثبت شده", "⚠️ Channel already registered"),
    "ch_owner_moved": ("⚠️ «{title}» با <b>توکن معتبر</b> به {who} منتقل شد\n❓ اگر شما نبودید: /man", "⚠️ “{title}” moved to {who} with a <b>valid token</b>\n❓ Not you? /man"),
    "ch_taken": ("🔐 <b>این کانال در پنل کاربر دیگری است</b>\n👤 مالک فعلی: {who}\n🔑 انتقال فقط با توکن ۱۰ رقمی مالک", "🔐 <b>This channel is in another user's panel</b>\n👤 Current owner: {who}\n🔑 Transfer needs the owner's 10 digit token"),
    "ch_token_sent": ("🔑 <b>توکن انتقال برای {who} ارسال شد</b>\n📩 آن را از مالک بگیر و همین‌جا بفرست\n⏳ <b>{min} دقیقه</b> · یک‌بارمصرف", "🔑 <b>Transfer token sent to {who}</b>\n📩 Ask the owner and send it here\n⏳ <b>{min} min</b> · single use"),
    "ch_token_ok": ("✅ توکن تأیید شد\n📣 «{title}» به پنل شما منتقل شد", "✅ Token accepted\n📣 “{title}” moved to your panel"),
    "ch_token_bad": ("❌ توکن نامعتبر، منقضی یا مصرف‌شده", "❌ Token invalid, expired or used"),
    "ch_token_dm": ("توکن انتقال مالکیت کانال «{title}»: <code>{token}</code>\nاعتبار: {min} دقیقه", "Ownership transfer token for “{title}”: <code>{token}</code>\nValid for {min} minutes"),
    "ch_recover_text": ("«{title}» در پنل {who} است\n۱ دسترسی او را در تلگرام سلب کن\n۲ «بررسی» را بزن تا مالکیت برگردد", "“{title}” is in {who}'s panel\n1 Remove their access in Telegram\n2 Tap “Check” to take ownership back"),
    "b_recheck": ("بررسی", "Check"),
    "ch_recheck_ok": ("✅ مالکیت «{title}» برگشت", "✅ Ownership of “{title}” restored"),
    "ch_recheck_still": ("⚠️ {who} هنوز ادمین کانال است\n1️⃣ دسترسی‌اش را در تلگرام سلب کن\n2️⃣ بعد «بررسی» را بزن", "⚠️ {who} is still a channel admin\n1️⃣ Remove their access in Telegram\n2️⃣ then tap “Recheck”"),
    "ch_recheck_err": ("⚠️ وضعیت ادمین از تلگرام خوانده نشد\n🔁 کمی بعد دوباره تلاش کن", "⚠️ Could not read admin status from Telegram\n🔁 Try again shortly"),
    "ch_reverted": ("♻️ مالکیت «{title}» به ثبت‌کنندهٔ نخست برگشت\nℹ️ {who} دیگر ادمین کانال نبود", "♻️ Ownership of “{title}” returned to the original registrant\nℹ️ {who} was no longer a channel admin"),
    "more_steps": ("… {n} مرحلهٔ پیشین", "… {n} earlier step(s)"),
    "queue_reset": ("ریست صف", "Reset queue"),
    "queue_reset_q": ("⚠️ صف <b>تأییدنشده</b> پاک می‌شود\n🔴 اتوماسیون خاموش · چرخه صفر می‌شود\n🌐 منابع و پست‌های منتشرشده می‌مانند\n\nادامه می‌دهید؟", "⚠️ The <b>unapproved</b> queue is cleared\n🔴 Automation off · cycle reset\n🌐 Sources and published posts stay\n\nContinue?"),
    "queue_reset_ok": ("✅ صف ریست شد · {n} مورد پاک شد\n🔴 اتوماسیون خاموش شد", "✅ Queue reset · {n} item(s) cleared\n🔴 automation off"),
    "ch_added": ("✅ «{title}» اضافه شد", "✅ “{title}” added"), "limit_channels": ("⚠️ سقف کانال پلن: {n}", "⚠️ Plan channel limit: {n}"), "limit_sources": ("⚠️ سقف منبع پلن: {n}", "⚠️ Plan source limit: {n}"),
    "limit_posts": ("⚠️ سهمیه‌ی پست امروز تمام شد ({n})", "⚠️ Today's post quota used ({n})"), "limit_tests": ("⚠️ سهمیه‌ی تست امروز تمام شد ({n})", "⚠️ Today's test quota used ({n})"),
    # ---- پنل کانال
    "ch_panel": ("<b>{title}</b>", "<b>{title}</b>"), "ch_status": ("{i} اتوماسیون {st} · {mode}", "{i} Automation {st} · {mode}"), "auto": ("⚡ خودکار", "⚡ auto"), "review": ("{n} مورد منتظر تأیید", "{n} awaiting approval"),
    "ch_stats": ("🌐 {s} منبع · 📝 صف {q} · ✅ ۲۴h {p} · ♻️ رد {r} · 🕐 {a}", "🌐 {s} sources · 📝 queue {q} · ✅ 24h {p} · ♻️ rejected {r} · 🕐 {a}"),
    "report": ("گزارش", "Report"), "test": ("تست فوری", "Quick test"), "sources": ("منابع ({n})", "Sources ({n})"), "content": ("محتوا", "Content"), "sched": ("زمان‌بندی", "Schedule"),
    "queue": ("صف انتشار ({n})", "Queue ({n})"), "rejected": ("ردشده‌ها", "Rejected"), "automation": ("{i} اتوماسیون", "{i} Automation"), "ch_del": ("حذف کانال", "Delete channel"),
    "refresh": ("بروزرسانی", "Refresh"), "ch_del_q": ("❓ «{title}» حذف شود؟\n🌐 منابع · ️ تنظیمات ·  صف", "❓ Remove “{title}”?\n🌐 Sources · ⚙️ Settings · 📝 Queue"), "ch_deleted": ("🗑 کانال حذف شد", "🗑 Channel removed"),
    "tog_enabled": ("🟢 اتوماسیون روشن شد", "🟢 Automation on"), "tog_disabled": ("🔴 اتوماسیون خاموش شد", "🔴 Automation off"),
    # ---- منابع
    "src_title": ("🌐 <b>منابع {title}</b> · {n}/{cap}\n💡 فید <b>RSS</b> بهترین نتیجه را می‌دهد\n🟢 وضعیت کانال · 🤖 وضعیت ربات", "🌐 <b>Sources {title}</b> · {n}/{cap}\n💡 An <b>RSS</b> feed gives the best results\n🟢 Channel status · 🤖 Bot status"),
    "src_line": ("\n{i}{b} {host} · 🆕 {n}{err}", "\n{i}{b} {host} · 🆕 {n}{err}"), "src_err": (" · ⚠️×{n}", " · ⚠️×{n}"), "src_add": ("افزودن منبع", "Add source"),
    "src_add_prompt": ("آدرس منبع (فید، سایت یا API):", "Source URL (feed, site or API):"),
    "src_dup": ("⚠️ منبع تکراری", "⚠️ Duplicate source"), "src_checking": ("🔎 بررسی منبع…", "🔎 Checking source…"),
    "src_added": ("✅ منبع اضافه شد · {n} مقاله [{m}]", "✅ Source added · {n} articles [{m}]"),
    "src_403": ("🚫 این سایت ربات‌ها را مسدود می‌کند (HTTP 403)\nاز این سایت و سایت‌های مشابه محتوایی درنمی‌آید", "🚫 This site blocks bots (HTTP 403)\nNo content can be fetched from it or similar sites"),
    "src_dead": ("⚠️ تست بارگذاری موفق نبود\n📡 اگر RSS دارد آدرس فید را مستقیم بدهید", "⚠️ Load test failed\n📡 If it has RSS, give the feed URL directly"),
    "src_added_off": ("\n➕ منبع اضافه شد اما 🔴 خاموش است", "\n➕ The source was added but is 🔴 off"), "src_now_off": ("\n🔴 منبع خاموش شد", "\n🔴 The source was turned off"),
    "src_view": ("🌐 <b>{host}</b>\n<code>{url}</code>\n{feed}\n{st} · آخرین بررسی {last} · 🆕 {n} · ⚠️×{f}{err}", "🌐 <b>{host}</b>\n<code>{url}</code>\n{feed}\n{st} · last check {last} · 🆕 {n} · ⚠️×{f}{err}"),
    "src_feed": ("📡 <code>{u}</code>", "📡 <code>{u}</code>"), "active": ("فعال", "Active"), "inactive": ("غیرفعال", "Inactive"), "toggle": ("روشن/خاموش", "Toggle"), "delete": ("حذف", "Delete"),
    "src_st": ("{c} کانال · {b} ربات", "{c} channel · {b} bot"), "tog_ch": ("منبع کانال", "Channel source"), "tog_bot": ("منبع ربات", "Bot source"),
    "src_ch_on": ("🟢 منبع برای کانال روشن شد", "🟢 Source on for channel"), "src_ch_off": ("🔴 منبع برای کانال خاموش شد", "🔴 Source off for channel"),
    "src_bot_on": ("🟢 منبع برای محتوای ربات روشن شد", "🟢 Source on for bot content"), "src_bot_off": ("🔴 منبع برای محتوای ربات خاموش شد", "🔴 Source off for bot content"),
    "src_api": ("API", "API"), "src_api_none": ("بدون API", "No API"), "src_api_on": ("🔌 API: <code>{u}</code>", "🔌 API: <code>{u}</code>"),
    "src_api_url": ("API: <code>{u}</code>", "API: <code>{u}</code>"),
    "src_api_key": ("🔑 کلید API را بفرستید\n🗑 اگر لازم نیست بنویسید <code>-</code>", "🔑 Send the API key\n🗑 If not needed, send <code>-</code>"),
    "src_api_ok": ("✅ API ثبت شد · {n} آیتم خوانده شد", "✅ API saved · {n} items read"), "src_api_bad": ("⚠️ پاسخ API قابل استفاده نبود\n🔴 ثبت شد اما منبع خاموش است", "⚠️ API response was not usable\n🔴 Saved but the source is off"),
    "src_api_del": ("🗑 API حذف شد", "🗑 API removed"),
    "src_deleted": ("🗑 منبع حذف شد", "🗑 Source deleted"), "src_recheck": ("بررسی دوباره", "Re-check"),
    # ---- محتوا
    "content_title": ("✍️ <b>محتوا — {title}</b>\n🎯 {topic} · 🗣 {lang}\n📝 <i>{prompt}</i>\n✒️ {sig}", "✍️ <b>Content — {title}</b>\n🎯 {topic} · 🗣 {lang}\n📝 <i>{prompt}</i>\n✒️ {sig}"),
    "general": ("عمومی", "general"), "b_topic": ("موضوع", "Topic"), "b_lang": ("زبان خروجی", "Output language"), "b_prompt": ("پرامپت", "Prompt"), "b_sig": ("امضا", "Signature"),
    "b_cats": ("دسته‌ها ({n})", "Categories ({n})"), "b_crits": ("معیارها ({n})", "Criteria ({n})"), "b_min": ("حداقل امتیاز {n}", "Min score {n}"), "b_words": ("🔢 کلمات {n}", "🔢 Words {n}"), "b_limit": ("📏 سقف کاراکتر {n}", "📏 Char limit {n}"),
    "b_hashtags": ("#️⃣ هشتگ {i}", "#️⃣ Hashtags {i}"), "b_link": ("🔗 منبع در ربات {i}", "🔗 Source in bot {i}"), "b_media": ("رسانه {i}", "Media {i}"), "b_strict": ("فیلتر سخت {i}", "Strict filter {i}"),
    "tog_hashtags": ("هشتگ", "Hashtags"), "tog_include_link": ("منبع در ربات", "Source link"), "tog_include_media": ("رسانه", "Media"), "tog_strict_ads": ("فیلتر سخت تبلیغ", "Strict ad filter"), "tog_allow_undated": ("بدون تاریخ", "Undated"),
    "togd_hashtags": ("هشتگ‌های مرتبط در انتهای پست اضافه می‌شوند", "Related hashtags are added at the end of the post"), "togd_include_link": ("منبع خبر فقط در «ادامه در ربات» نمایش داده می‌شود", "The news source is shown only in “continue in bot”"), "togd_include_media": ("تصویر یا ویدیوی خبر همراه پست ارسال می‌شود", "The news photo or video is sent with the post"), "togd_strict_ads": ("فیلتر سخت‌گیرانه‌تر، تبلیغ‌ها را زودتر رد می‌کند", "The stricter filter rejects ad-like articles earlier"), "togd_allow_undated": ("مقالاتِ بدون تاریخ هم منتشر می‌شوند", "Articles without a publish date are published too"),
    "cats_title": ("🗂 <b>دسته‌ها</b>\nAI برای هر مقاله یکی را انتخاب و با سبک آن می‌نویسد.", "🗂 <b>Categories</b>\nAI picks one per article and writes in its style."), "cat_add": ("دسته", "Category"),
    "cat_view": ("{e} <b>{name}</b>\n📐 {style}", "{e} <b>{name}</b>\n📐 {style}"), "cat_edit": ("سبک", "Style"), "cat_style_prompt": ("سبک نگارش این دسته:", "Writing style for this category:"),
    "cat_added": ("✅ دسته اضافه شد", "✅ Category added"), "cat_updated": ("✅ سبک بروزرسانی شد", "✅ Style updated"), "cat_min": ("⚠️ حداقل یک دسته لازم است", "⚠️ At least one category required"),
    "crits_title": ("📏 <b>معیارها</b>\nامتیاز ۰–۱۰۰ · زیر {m} رد می‌شود.", "📏 <b>Criteria</b>\nScore 0–100 · below {m} is rejected."), "crit_add": ("معیار", "Criterion"),
    "crit_line": ("\n• {name} — {w} ({p}%)", "\n• {name} — {w} ({p}%)"), "crit_btn": ("{name} · {w}", "{name} · {w}"), "crit_view": ("📏 <b>{name}</b> · وزن {w}", "📏 <b>{name}</b> · weight {w}"), "crit_w": ("وزن", "Weight"),
    "crit_w_prompt": ("وزن جدید (۱–۱۰۰):", "New weight (1-100):"), "crit_added": ("✅ معیار اضافه شد", "✅ Criterion added"), "crit_updated": ("✅ وزن بروزرسانی شد", "✅ Weight updated"), "crit_min": ("⚠️ حداقل یک معیار لازم است", "⚠️ At least one criterion required"),
    # ---- هوش مصنوعی شخصیِ مدیر
    "my_ai": ("هوش مصنوعی", "AI models"),
    "my_ai_title": ("🤖 <b>هوش مصنوعی من</b>\nمدل‌های شخصی شما اول استفاده می‌شوند؛ اگر خطا دادند، مدل‌های عمومی ربات جایگزین می‌شوند.", "🤖 <b>My AI</b>\nYour own models are used first; if they fail, the bot's shared models take over."),
    "my_ai_none": ("\n\n⚠️ <b>هنوز مدل شخصی ندارید.</b> مدل‌های عمومی ممکن است هر لحظه شلوغ یا خاموش شوند؛ حتماً یک مدل شخصی اضافه کنید.", "\n\n⚠️ <b>You have no personal model yet.</b> The shared models can get busy or go down at any time; adding your own is strongly recommended."),
    "my_ai_line": ("\n{i} <b>{name}</b> · <code>{model}</code> · ✅{ok}", "\n{i} <b>{name}</b> · <code>{model}</code> · ✅{ok}"),
    "my_ai_add": ("افزودن مدل", "Add model"), "my_ai_added": ("✅ مدل «{name}» اضافه شد · {res}", "✅ Model “{name}” added · {res}"),
    "my_ai_view": ("<b>{name}</b> · {st}\n{model}\nموفق {ok} · خطا {fc}", "<b>{name}</b> · {st}\n{model}\nOK {ok} · fail {fc}"),
    # ---- زمان‌بندی
    "sched_title": ("<b>زمان‌بندی</b>\nهر {iv} دقیقه · {ppc} پست · پنجره {lb} ساعت\nحالت: {mode} · منطقه: {tz}", "<b>Schedule</b>\nEvery {iv} min · {ppc} posts · {lb}h window\nMode: {mode} · zone: {tz}"),
    "mode_auto": ("خودکار — انتشار مستقیم", "Auto — publish directly"), "mode_review": ("بازبینی — تأیید دستی", "Review — manual approval"), "none": ("—", "—"),
    "b_interval": ('⏱ هر {n} دقیقه', '⏱ every {n} min'), "b_ppc": ('📦 {n} پست در چرخه', '📦 {n} post(s)/cycle'), "b_lookback": ('🗓 پنجره {n} ساعت', '🗓 window {n}h'), "b_undated": ('📅 بدون تاریخ: {i}', '📅 undated: {i}'),
    "b_daily_cap": ('📊 سقف روزانه {n}', '📊 daily cap {n}'), "cap_over_plan": ("⚠️ بیشتر از سقف پلن شما ({n} پست در روز) قابل تنظیم نیست.", "⚠️ Cannot exceed your plan limit ({n} posts/day)."), "cap_range": ("حداکثر تا {n} پست در روز (طبق پلن شما)", "Max {n} posts/day (your plan)"), "b_quiet": ("خاموشی", "Quiet hours"), "b_tz": ("منطقه زمانی", "Time zone"), "b_mode": ("{m}", "{m}"),
    "mode_set_auto": ("⚡ خودکار: انتشار مستقیم", "⚡ Auto: publish directly"), "mode_set_review": ("📝 بازبینی: منتظر تأیید در صف", "📝 Review: waits for approval"),
    "quiet_pick_start": ("🌙 <b>خاموشی</b> · ساعت <b>شروع</b> ({tz} · الان {loc}):", "🌙 <b>Quiet hours</b> · <b>start</b> hour ({tz} · now {loc}):"), "quiet_pick_end": ("🌙 شروع {h}:00 · ساعت <b>پایان</b>:", "🌙 Start {h}:00 · <b>end</b> hour:"),
    "quiet_off": ("🚫 بدون خاموشی", "🚫 No quiet hours"), "quiet_set": ("🌙 خاموشی {a}:00 → {b}:00", "🌙 Quiet {a}:00 → {b}:00"), "quiet_cleared": ("🌙 خاموشی حذف شد", "🌙 Quiet hours cleared"),
    "tz_pick": ("🌍 <b>منطقه زمانی</b> · UTC الان <b>{utc}</b>\nنزدیک‌ترین شهر را انتخاب کنید:", "🌍 <b>Time zone</b> · UTC now <b>{utc}</b>\nPick the nearest city:"), "tz_set": ("🌍 {city} {tz} · ⌚ {loc}", "🌍 {city} {tz} · ⌚ {loc}"),
    # ---- صف / مقاله
    "queue_title": ("📝 <b>صف انتشار {title}</b> · {n} آماده", "📝 <b>Publish queue {title}</b> · {n} ready"), "rej_title": ("♻️ <b>ردشده‌های ۲۴h {title}</b>", "♻️ <b>Rejected 24h {title}</b>"),
    "pub_first": ("انتشار اولین", "Publish first"), "art_title": ("📝 <b>{title}</b>\n⭐ {score} · 🗂 {cat}\n📌 {st}{reason} · 🌐 {host}\n━━━━━━━━━━━━\n{body}", "📝 <b>{title}</b>\n⭐ {score} · 🗂 {cat}\n📌 {st}{reason} · 🌐 {host}\n━━━━━━━━━━━━\n{body}"),
    "art_nobody": ("<i>(محتوایی تولید نشده)</i>", "<i>(no content generated)</i>"), "art_pub": ("انتشار", "Publish"), "art_edit": ("ویرایش", "Edit"), "art_full": ("نسخه کامل", "Full text"), "art_view": ("مشاهده", "View"),
    "art_del_arch": ("حذف از آرشیو", "Delete from archive"), "art_to_ready": ("به صف انتشار", "Move to queue"), "art_edit_prompt": ("متن جدید پست (فرمت تلگرام حفظ می‌شود):", "New post text (Telegram formatting kept):"),
    "art_updated": ("✅ متن بروزرسانی شد", "✅ Text updated"), "art_moved": ("♻️ به صف منتقل شد", "♻️ Moved to queue"), "publishing": ("⏳ انتشار…", "⏳ Publishing…"), "published_ok": ("✅ منتشر شد", "✅ Published"), "pub_failed": ("❌ انتشار: {e}", "❌ Publish: {e}"), "full_head": ("📖 <b>نسخه‌ی کامل</b>\n\n", "📖 <b>Full version</b>\n\n"),
    # ---- پلن من
    "myplan": ("🧾 <b>پلن من: {name}</b>\n{st}{exp}{nxt}\n📢 {p}/{pc} پست · 🧪 {t}/{tc} تست (امروز)\n🌐 {s}/{sc} منبع · 📣 {c}/{cc} کانال", "🧾 <b>My plan: {name}</b>\n{st}{exp}{nxt}\n📢 {p}/{pc} posts · 🧪 {t}/{tc} tests (today)\n🌐 {s}/{sc} sources · 📣 {c}/{cc} channels"),
    "st_active": ("🟢 فعال", "🟢 active"), "st_expired": ("🔴 غیرفعال", "🔴 inactive"), "exp_at": (" · تا {d}", " · until {d}"), "upgrade_btn": ("ارتقا / تمدید", "Upgrade / renew"),
    # ---- دلیل کوتاه ردشده‌ها (بدون جزئیات فنی)
    "rj_extract": ("متن قابل استخراج نبود", "no extractable text"), "rj_ad": ("فیلتر تبلیغ", "ad filter"), "rj_ai_ad": ("تبلیغ (تشخیص AI)", "ad (AI)"), "rj_score": ("امتیاز کمتر از حداقل", "below minimum score"),
    "rj_ai": ("سرویس هوش مصنوعی پاسخ نداد", "AI service did not respond"), "rj_send": ("ارسال به کانال ناموفق", "sending to channel failed"), "rj_dup": ("تکراری", "duplicate"),
    "rj_admin": ("ربات ادمین کانال نیست", "bot is not channel admin"), "rj_old": ("قدیمی‌تر از بازه", "older than window"), "rj_undated": ("بدون تاریخ", "undated"), "rj_other": ("نامشخص", "unspecified"),
    # ---- تست
    "preparing": ("آماده‌سازی…", "Preparing…"), "test_wait": ("⏳ تست بعدی تا {n} ثانیه دیگر", "⏳ Next test in {n}s"), "test_busy": ("⏳ چرخه‌ای روی این کانال در حال اجراست", "⏳ A cycle is already running on this channel"),
    "test_ok": ("✅ <b>منتشر شد</b>", "✅ <b>Published</b>"), "test_queued": ("📝 در <b>صف انتشار</b> منتظر تأیید (حالت بازبینی)", "📝 Waiting in <b>publish queue</b> (review mode)"), "test_fail": ("⚠️ <b>چیزی منتشر نشد</b>", "⚠️ <b>Nothing published</b>"),
    "test_summary_ok": ("{n} مقاله منتشر شد", "{n} articles published"), "test_summary_fail": ("تست ناموفق بود", "Test failed"),
    "test_log": ("{d}", "{d}"), "back_panel": ("پنل کانال", "Channel panel"),
    "test_retry_note": ("ℹ️ سهمیه و محدودیت زمانی اعمال نشد؛ می‌توانید همین حالا دوباره تست کنید.", "ℹ️ No quota or cooldown was applied; you can retry right away."),
    "test_src": ("🌐 تولید از منبع: <b>{name}</b>", "🌐 Generated from source: <b>{name}</b>"),
    # --- پنل مدل/قیمت، پشتیبانی، دیپ‌لینک و متن‌های صف
    "s_title": ("👑 <b>مدیر کلان</b>\n🤖 مدل‌ها {m} ({ms}) · 👥 مدیران {a} / کاربران {u}\n🛎 پرداخت معلق {p} · 🎟 کد تخفیف {dc} · ⚖️ صف {due}\n💓 {hb}", "👑 <b>Super admin</b>\n🤖 models {m} ({ms}) · 👥 admins {a} / users {u}\n🛎 pending payments {p} · 🎟 discount codes {dc} · ⚖️ queue {due}\n💓 {hb}"),
    "s_all_ok": ("سالم", "healthy"), "s_down": ("{n} خراب 🔴", "{n} down 🔴"), "s_report": ("گزارش", "Report"), "s_plans": ("پلن‌ها", "Plans"), "s_users": ("کاربران", "Users"),
    "s_models": ("مدل‌ها", "Models"), "s_pays": ("پرداخت‌ها ({n})", "Payments ({n})"), "s_discs": ("کدهای تخفیف", "Discount codes"), "s_bc": ("همگانی", "Broadcast"), "s_texts": ("متن‌ها", "Texts"),
    "s_admins": ("مدیران", "Admins"), "s_auto": ("{i} اتوماسیون کل", "{i} Global automation"), "s_me": ("پنل من", "My panel"), "s_auto_on": ("🟢 اتوماسیون کل روشن شد", "🟢 Global automation on"), "s_auto_off": ("🔴 اتوماسیون کل خاموش شد", "🔴 Global automation off"),
    "s_plans_title": ("<b>پلن‌ها</b>", "<b>Plans</b>"), "s_plan_new": ("پلن", "Plan"), "s_plan_created": ("✅ «{name}» ساخته شد", "✅ “{name}” created"), "s_plan_deleted": ("🗑 پلن حذف شد", "🗑 Plan deleted"),
    "s_plan_view": ("🧾 <b>{name}</b> / {name_en} {free} {st}\n{desc}\n{desc_en}\n⏳ {days}d · 📢 {posts}/d · 🧪 {tests}/d · 🌐 {src} · 📣 {ch}\n💰 {price} / {price_en} · 🔢 {price_num}\n👥 {n} کاربر", "🧾 <b>{name}</b> / {name_en} {free} {st}\n{desc}\n{desc_en}\n⏳ {days}d · 📢 {posts}/d · 🧪 {tests}/d · 🌐 {src} · 📣 {ch}\n💰 {price} / {price_en} · 🔢 {price_num}\n👥 {n} users"),
    "s_plan_free_set": ("رایگان شود", "Make free"), "s_plan_free_done": ("🎁 پلن رایگان تنظیم شد", "🎁 Free plan set"), "s_plan_del": ("حذف پلن", "Delete plan"), "s_field_prompt": ("✏️ مقدار جدید «{f}»", "✏️ New value for “{f}”"),
    "s_discs_title": ("<b>کدهای تخفیف</b>", "<b>Discount codes</b>"), "s_disc_new": ("کد", "Code"), "s_disc_line": ("\n{i} <code>{code}</code> · 🎟 {p}% · ⏳ {exp} · 👥 {used}/{max}", "\n{i} <code>{code}</code> · 🎟 {p}% · ⏳ {exp} · 👥 {used}/{max}"),
    "s_disc_view": ("🎟 <code>{code}</code> {st}\n💥 {p}% · ⏳ {exp} · 👥 {used}/{max}", "🎟 <code>{code}</code> {st}\n💥 {p}% · ⏳ {exp} · 👥 {used}/{max}"), "s_disc_created": ("✅ کد {code} ساخته شد", "✅ Code {code} created"), "s_disc_dup": ("⚠️ کد تکراری یا نامعتبر", "⚠️ Duplicate or invalid code"),
    "s_disc_exp_bad": ("⚠️ انقضا: عدد روز یا تاریخ YYYY-MM-DD", "⚠️ Expiry: days number or YYYY-MM-DD"), "s_disc_p": ("درصد", "Percent"), "s_disc_e": ("انقضا", "Expiry"), "s_disc_m": ("سقف استفاده", "Max uses"), "s_disc_del": ("حذف کد", "Delete code"), "unlimited": ("∞", "∞"),
    "s_users_title": ("<b>{what}</b> ({n}) · {p}/{pp}", "<b>{what}</b> ({n}) · {p}/{pp}"),
    "s_user_view": ("{name} · {role} · {lang}\nپلن: {plan} {act}\nپست امروز: {p} · تست: {t}\nکانال: {c} · منبع: {s} · تولید: {d} (انتشار: {pub})\nعضو: {join} · آخرین بازدید: {seen}", "{name} · {role} · {lang}\nPlan: {plan} {act}\nToday: {p} posts · {t} tests\nChannels: {c} · sources: {s} · generated: {d} (published: {pub})\nJoined: {join} · seen: {seen}"),
    "s_ban_y": ("مسدود", "Banned"), "s_ban_n": ("آزاد", "Active"), "s_uplan": ("تعیین پلن", "Set plan"), "s_urevoke": ("لغو پلن", "Revoke plan"), "s_uban": ("مسدود/آزاد", "Ban/unban"), "s_umsg": ("پیام", "Message"),
    "s_ureport": ("کانال‌ها", "Channels"), "s_ufree": ("ریست رایگان", "Reset free"), "s_utests": ("ریست تست", "Reset tests"), "s_uposts": ("ریست پست", "Reset posts"),
    "s_plan_pick": ("پلن این کاربر:", "Plan for this user:"), "s_assigned": ("پلن تا {d} فعال شد", "Plan active until {d}"), "s_assigned_q": ("پلن بعدی (پس از {d})", "Queued as next (after {d})"),
    "s_revoked": ("پلن لغو شد", "Plan revoked"), "s_banned": ("مسدود شد", "Banned"), "s_unbanned": ("آزاد شد", "Unbanned"), "s_free_reset": ("سهمیهٔ رایگان ریست شد", "Free quota reset"), "s_tests_reset": ("تست امروز ریست شد", "Tests reset"), "s_posts_reset": ("پست امروز ریست شد", "Posts reset"),
    "s_no_channels": ("کانالی ندارد", "No channels"), "s_pick_channel": ("کانال:", "Channel:"),
    "s_models_title": ("<b>مدل‌ها</b>", "<b>Models</b>"),
    "s_model_view": ("<b>{name}</b> · {st}\n<code>{model}</code>\nموفق {ok} · خطا {fc}", "<b>{name}</b> · {st}\n<code>{model}</code>\nOK {ok} · fail {fc}"),
    "s_m_off": ("خاموش", "Off"), "s_m_ok": ("سالم", "Healthy"), "s_m_down": ("خراب", "Down"), "s_model_test": ("تست", "Test"), "s_model_del": ("حذف مدل", "Delete model"),
    "s_model_testing": ("🧪 در حال تست…", "🧪 Testing…"), "s_model_res": ("{i} {t}s: {out}", "{i} {t}s: {out}"), "s_model_added": ("✅ مدل «{name}» اضافه شد · {res}", "✅ Model “{name}” added · {res}"), "s_model_need": ("⚠️ Base URL و نام مدل الزامی است", "⚠️ Base URL and model name are required"), "s_model_on": ("🟢 مدل روشن شد", "🟢 Model on"), "s_model_off": ("⏸ مدل خاموش شد", "⏸ Model off"),
    "s_pays_title": ("🛎 <b>پرداخت‌های معلق</b>", "🛎 <b>Pending payments</b>"), "s_pay_none": ("📭 خالی", "📭 none"), "s_pay_line": ("\n• #{id} {who} · 🕐 {t}\n🧾 <b>{plan}</b> · 💰 {price}", "\n• #{id} {who} · 🕐 {t}\n🧾 <b>{plan}</b> · 💰 {price}"), "s_pay_rc": ("📎 #{id}", "📎 #{id}"),
    "s_pay_ok": ("✅ تأیید", "✅ Approve"), "s_pay_no": ("❌ رد", "❌ Reject"), "s_pay_done_ok": ("✅ تأیید شد · پلن فعال یا رزرو", "✅ Approved · plan activated or queued"), "s_pay_done_no": ("❌ رد شد", "❌ Rejected"), "s_pay_seen": ("⚠️ قبلاً بررسی شده", "⚠️ Already handled"),
    "s_pay_new": ("🛎 <b>پرداخت #{id}</b>\n👤 {who} (<code>{uid}</code>)\n🧾 <b>{plan}</b> · 💰 {price}{disc}{note}", "🛎 <b>Payment #{id}</b>\n👤 {who} (<code>{uid}</code>)\n🧾 <b>{plan}</b> · 💰 {price}{disc}{note}"),
    "s_bc_prompt": ("پیام همگانی (متن/عکس/ویدیو؛ فرمت حفظ می‌شود):", "Broadcast message (text/photo/video; formatting kept):"), "s_bc_confirm": ("📣 ارسال به <b>{n}</b> کاربر؟", "📣 Send to <b>{n}</b> users?"), "s_bc_go": ("✅ ارسال", "✅ Send"), "s_bc_sending": ("📣 ارسال به {n} کاربر…", "📣 Sending to {n} users…"), "s_bc_done": ("📣 موفق {ok} · ناموفق {fail}", "📣 ok {ok} · failed {fail}"),
    "s_texts_title": ("<b>متن‌ها</b>\nجای‌گذار: <code>{name}</code> <code>{plan}</code> <code>{price}</code>", "<b>Texts</b>\nPlaceholders: <code>{name}</code> <code>{plan}</code> <code>{price}</code>"),
    "s_text_prompt": ("متن جدید «{k}» ({lg}):", "New “{k}” text ({lg}):"), "s_text_saved": ("ذخیره شد", "Saved"), "s_text_reset_done": ("پیش‌فرض شد", "Reset"),
    "s_health": ("سلامت", "Health"),
    "s_mand": ("🔐 کانال اجباری", "🔐 Mandatory channel"),
    "s_mand_title": ("<b>کانال اجباری</b>\nبرای هر زبان جدا تنظیم می‌شود.\n", "<b>Mandatory channel</b>\nConfigured separately per language.\n"),
    "s_mand_line": ("{i} <b>{lg}</b> — {tgt}\n", "{i} <b>{lg}</b> — {tgt}\n"),
    "s_mand_id": ("شناسه عددی کانال", "Numeric channel ID"),
    "s_mand_user": ("یوزرنیم کانال (بدون @)", "Channel username (no @)"),
    "s_mand_bad_id": ("شناسه باید عددی باشد", "ID must be numeric"),
    "mand_user_notice": ("برای استفاده از ربات، عضو کانال شوید.", "Join the channel to use the bot."),
    "s_umsg_prompt": ("پیام برای این کاربر:", "Message to this user:"), "s_umsg_sent": ("✅ ارسال شد", "✅ Sent"), "s_umsg_head": ("📩 <b>پیام مدیریت:</b>\n\n", "📩 <b>Admin message:</b>\n\n"),
    "sup_open": ("💬 <b>پشتیبانی</b>\nهر پیامی بفرستید مستقیم به مدیر می‌رسد و پاسخ همین‌جا می‌آید.", "💬 <b>Support</b>\nAny message goes straight to the admin; replies arrive here."), "sup_close": ("❌ پایان گفتگو", "❌ End chat"), "sup_closed": ("✅ گفتگو پایان یافت", "✅ Chat ended"),
    "sup_reply_head": ("💬 <b>پاسخ پشتیبانی:</b>\n", "💬 <b>Support reply:</b>\n"), "sup_sent": ("✅ ارسال شد", "✅ Sent"), "sup_fail": ("❌ ارسال نشد: {e}", "❌ Not sent: {e}"), "sup_received": ("✅", "✅"),
    "dl_notfound": ("🔎 متأسفم، نسخهٔ کامل این مطلب پیدا نشد — ممکن است پاک شده باشد. لطفاً از خودِ کانال پست را باز کن یا چند دقیقه بعد دوباره امتحان کن.", "🔎 Sorry, this full version could not be found — it may have been removed. Please open the post in the channel or try again later."), "dl_source": ("🔗 Source", "🔗 Source"),
}

def tr(lang, key, /, **kw):
    if key not in TXT: return str(key)   # کلید ناشناخته (callback_data دست‌ساز) نباید KeyError بدهد
    s = TXT[key][1 if lang == "en" else 0]
    try: return s.format(**kw) if kw else s
    except Exception: return s

TOGGLABLE = {"enabled", "hashtags", "include_link", "include_media", "strict_ads", "allow_undated"}
STATUS = {"discovered": ("🔎 کشف‌شده", "🔎 discovered"), "skipped": ("⏭ خارج از بازه", "⏭ out of window"), "rejected": ("♻️ ردشده", "♻️ rejected"), "failed": ("❌ ناموفق", "❌ failed"), "ready": ("📝 آماده", "📝 ready"), "published": ("{n} مطلب منتشر شد", "{n} published")}

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
FIELD_LABEL = {"prompt": ("پرامپت نگارش", "Writing prompt"), "topic": ("موضوع کانال", "Channel topic"), "language": ("زبان خروجی (نام زبان را بنویس: Italian، Chinese، فارسی، …)", "Output language (type the language name: Italian, Chinese, English, …)"), "signature": ("امضای پایان پست (@channel = یوزرنیم کانال)", "Post signature (@channel = channel username)"),
               "daily_posts_cap": ("📊 سقف روزانهٔ پست — عددی تا سقف پلنت بنویس (خالی = نامحدود)", "📊 Daily post cap — a number up to your plan max (blank = unlimited)"), "max_words": ("حداکثر کلمات پست", "Max post words"), "min_score": ("حداقل امتیاز (۰–۱۰۰)", "Min score (0–100)"), "post_limit": ("سقف کاراکتر پست کانال (پیش‌فرض ۹۰۰)؛ محتوای کامل‌تر → «ادامه در ربات» (۳۰۰–۹۰۰)", "Channel post char limit (default 900); longer content → “continue in bot” (300–900)"),
               "interval_minutes": (f"فاصله‌ی چرخه (دقیقه، ≥{config.MIN_INTERVAL})", f"Cycle interval (min, ≥{config.MIN_INTERVAL})"), "posts_per_cycle": (f"پست در هر چرخه (≤{config.MAX_PPC})", f"Posts per cycle (≤{config.MAX_PPC})"), "lookback_hours": (f"مقالات چند ساعت اخیر (≤{config.MAX_LOOKBACK})", f"Articles from last N hours (≤{config.MAX_LOOKBACK})")}
SCHED_FIELDS = ("interval_minutes", "posts_per_cycle", "lookback_hours", "daily_posts_cap")
TZ_NAMES = {"tehran": ("🇮🇷 تهران", "🇮 Tehran"), "istanbul": ("🇹 استانبول", "🇷 Istanbul"), "dubai": ("🇦🇪 دبی", "🇦🇪 Dubai"), "kabul": ("🇦 کابل", "🇫 Kabul"), "karachi": ("🇵🇰 کراچی", "🇵 Karachi"), "delhi": ("🇮🇳 دهلی", "🇮🇳 Delhi"),
            "moscow": ("🇷🇺 مسکو", "🇷🇺 Moscow"), "berlin": ("🇩🇪 برلین", "🇩 Berlin"), "london": ("🇬🇧 لندن", "🇬🇧 London"), "beijing": ("🇨🇳 پکن", "🇨🇳 Beijing"), "tokyo": ("🇯🇵 توکیو", "🇯 Tokyo"), "sydney": ("🇦 سیدنی", "🇦🇺 Sydney"),
            "newyork": ("🇺🇸 نیویورک", "🇺🇸 New York"), "losangeles": ("🇺🇸 لس‌آنجلس", "🇺🇸 Los Angeles"), "saopaulo": ("🇧🇷 سائوپائولو", "🇧 São Paulo"), "utc": ("🌐 گرینویچ", "🌐 UTC")}

def tz_city(off, lang):
    z = next((k for k, o in TZ_ZONES if abs(o - float(off)) < 0.01), None); return TZ_NAMES[z][1 if lang == "en" else 0] if z else ""

WIZ = {
    "cat_add": [("name", ("نام دسته (مثلاً خبری)", "Category name (e.g. News)"), "str"), ("emoji", ("یک ایموجی", "One emoji"), "str"), ("style", ("سبک نگارش (مثلاً: تیتر بولد، سه جمله، دو بولت)", "Writing style (e.g. bold headline, three sentences, two bullets)"), "str")],
    "crit_add": [("name", ("نام معیار", "Criterion name"), "str"), ("weight", ("وزن (۱–۱۰۰)", "Weight (1–100)"), "int")],
    "plan_new": [("name", ("نام (فارسی)", "Name (Persian)"), "str"), ("name_en", ("نام (انگلیسی)", "Name (English)"), "str"), ("days", ("مدت (روز)", "Days"), "int"), ("daily_posts", ("پست روزانه", "Posts/day"), "int"), ("max_sources", ("حداکثر منبع", "Max sources"), "int"), ("max_channels", ("حداکثر کانال", "Max channels"), "int"), ("daily_tests", ("تست روزانه", "Tests/day"), "int"),
                 ("price", ("قیمت (فارسی، مثلاً ۲۰۰,۰۰ تومان)", "Price (Persian)"), "str"), ("price_en", ("قیمت (انگلیسی، مثلاً 5 USDT)", "Price (English)"), "str"),
                 ("price_num", ("قیمت عددی برای proration (مثلاً ۵ = $5؛ خالی = بدون proration)", "Numeric price for proration (e.g. 5 = $5; blank = no proration)"), "float"),
                 ("description", ("توضیح کوتاه (فارسی)", "Short description (Persian)"), "str"), ("description_en", ("توضیح کوتاه (انگلیسی)", "Short description (English)"), "str")],
    "model_add": [("base_url", ("Base URL سرویس را بفرستید (همان آدرسی که سرویس‌دهنده‌ی شما اعلام کرده است):", "Send the service Base URL (exactly as your provider documents it):"), "str"),
                  ("api_key", ("کلید API (توکن)", "API key (token)"), "str"), ("model", ("نام مدل", "Model name"), "str")],
    "my_ai_add": [("base_url", ("Base URL سرویس هوش مصنوعی خودتان را بفرستید (همان آدرسی که سرویس‌دهنده اعلام کرده):", "Send the Base URL of your own AI service (exactly as your provider documents it):"), "str"),
                  ("api_key", ("کلید API (توکن)", "API key (token)"), "str"), ("model", ("نام مدل", "Model name"), "str")],
    "disc_add": [("code", ("کد تخفیف (مثلاً SPRING30)", "Discount code (e.g. SPRING30)"), "str"), ("percent", ("درصد تخفیف (۱–۱۰۰)", "Discount percent (1–100)"), "int"), ("expires", ("انقضا: تعداد روز (مثلاً 30) یا تاریخ (2025-12-31)", "Expiry: days (e.g. 30) or date (2025-12-31)"), "str"), ("max_uses", ("حداکثر دفعات استفاده (0 = نامحدود)", "Max uses (0 = unlimited)"), "int")],
}

# ══════════════════════════════════════════════════════════════════════════
# تشخیص مرحله‌ای (Diagnostics) — کوتاه، نمادین، دوزبانه
# ══════════════════════════════════════════════════════════════════════════
DIAG = {
    "busy": ("چرخهٔ دیگر روی کانال اجراست", "Another cycle is running"),
    "checks": ("بررسی تنظیمات…", "Checking settings…"),
    "dup": ("{n} مورد تکراری", "{n} duplicates"),
    "no_channel": ("کانال یافت نشد", "Channel not found"), "plan_inactive": ("⛔ نمی‌توانم منتشر کنم؛ پلن فعال نیست", "⛔ Plan inactive"), "no_sources": ("منبعی ثبت نشده", "No source added"),
    "quota_posts": ("سهمیهٔ پست امروز پر است", "Today's post quota is full"), "quota_tests": ("سهمیهٔ تست امروز پر است", "Today's test quota is full"),
    "bot_not_admin": ("ربات ادمین کانال نیست", "Bot is not channel admin"),
    "src_ok": ("{name}: {n} مورد · {new} جدید", "{name}: {n} items · {new} new"), "src_notmod": ("{name} تغییر نکرده", "{name} unchanged"),
    "src_empty": ("{name} خالی بود", "{name} empty"), "src_fail": ("{name} خوانده نشد — {err}", "{name} failed — {err}"), "src_cooldown": ("{name} اخیراً بررسی شد", "{name} checked recently"),
    "old": ("{n} مورد قدیمی‌تر از {h} ساعت", "{n} older than {h}h"), "leftover": ("{n} مورد از چرخهٔ پیش", "{n} left from earlier cycle"), "retry": ("🧪 {n} مورد قبلی را دوباره بررسی می‌کنم", "🧪 Re-checking {n} earlier items"),
    "found": ("{n} مقاله برای بررسی", "{n} articles to process"), "none_found": ("مقالهٔ جدیدی نبود", "No new article"),
    "extract_fail": ("{n} مورد متن نداشت", "{n} without readable text"), "undated": ("{n} بدون تاریخ", "{n} undated"), "ad": ("{n} تبلیغ فیلتر شد", "{n} ads filtered"),
    "ai_fail": ("مدل: {err}", "Model: {err}"), "ai_ad": ("{n} تبلیغ (AI)", "{n} ads (AI)"), "low": ("{n} مورد زیر حداقل امتیاز", "{n} below min score"),
    "from_src": ("🌐 محتوا را از منبع {name} ساختم", "🌐 Generated from source: {name}"),
    "quiet": ("ساعت خاموشی؛ در صف", "Quiet hours; queued"), "review": ("📝 {n} مورد را برای تأیید تو در صفحه‌ی انتشار گذاشتم", "📝 {n} awaiting approval in publish page"),
    "published": ("✅ {n} مطلب را در کانال منتشر کردم", "✅ {n} published"), "pub_fail": ("انتشار نشد — {err}", "Publish failed — {err}"), "detail": ("   ↳ {title} — {why}", "   ↳ {title} — {why}"),
    "queue_full": ("صف پر بود؛ {n} مورد درج نشد", "Queue full; {n} not inserted"),
    "soft_window": ("{n} مورد از همان منابع", "{n} from the same sources"),
    "mand_missing": ("🔐 عضویت کانال اجباری تأیید نشده؛ چرخه متوقف شد", "🔐 Mandatory channel membership not confirmed; cycle stopped"),
    "hint_sources": ("منبع اضافه کنید یا پنجره را بیشتر کنید", "Add a source or widen the window"), "hint_score": ("→ حداقل امتیاز را کمتر کنید", "→ Lower the minimum score"),
    "hint_ads": ("→ فیلتر سخت تبلیغ را خاموش کنید", "→ Turn off strict ad filter"), "hint_ai": ("سرویس AI پاسخ نداد", "AI service did not respond"),
    "hint_admin": ("ربات را ادمین کنید", "Make the bot an admin"), "hint_undated": ("«بدون تاریخ» را روشن کنید", "Enable “undated”"),
    "hint_extract": ("منبع متن خوانا ندارد", "Source lacks readable text"), "hint_review": ("حالت را خودکار کنید", "Switch to auto mode"), "hint_quota": ("پلن را ارتقا دهید", "Upgrade your plan"),
    "hint_mand": ("در کانال اجباری عضو شوید", "Join the mandatory channel"),
    "stage": ("مرحله: {stage}", "Stage: {stage}"),
}
STAGES = {"start": ("شروع", "start"), "checks": ("بررسی پلن/کانال/منبع", "checks"), "discover": ("کشف مقالات", "discovery"), "extract": ("استخراج متن", "extraction"), "filter": ("فیلتر", "filtering"), "ai": ("تولید با AI", "AI generation"), "score": ("امتیازدهی", "scoring"), "publish": ("انتشار", "publishing"), "done": ("پایان", "done")}
