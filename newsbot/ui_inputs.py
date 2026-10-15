# -*- coding: utf-8 -*-
"""ورودی‌های متنی: اعتبارسنج‌ها، ویزاردها، فیلدها، منبع/API، افزودن کانال + توکن انتقال،
کد تخفیف، رسید پرداخت و ورودی‌های مدیر کلان.
FIX v4:
  • _input_url برای همه‌ی URLهای کاربری (منبع/API/مدل) میزبان خصوصی را رد می‌کند؛
    فقط base_url مدل و فقط با ALLOW_PRIVATE_MODEL_HOSTS=1 می‌تواند مقصد داخلی داشته باشد
    (قبلاً بررسی IP خصوصی فقط وقتی explicit_scheme=True اجرا می‌شد ⇒ منبع/API باز بود)
  • دروازه‌ی کانال اجباری (mand_gate) در جریان افزودن کانال و دریافت توکن انتقال نصب شد
    — فقط ادمین؛ تا تأیید عضویت، ثبت کانال متوقف می‌شود
"""
import os, re
from datetime import datetime, timedelta
import httpx
from telegram import InlineKeyboardMarkup
from telegram.constants import ChatType
from newsbot import config
from newsbot.db import q
from newsbot.logutils import log, log_event
from newsbot.timeutils import now_utc, parse_dt
from newsbot.ratelimit import rate_ok, rate_left
from newsbot.net_http import is_private_host, FetchError
from newsbot.core_users import admin_limits, get_user, is_super, user_lang, can_admin
from newsbot.core_channels import (channel_owned, channel_by_chat, add_channel, update_channel_meta,
                                   get_channel, publication_pending, check_transfer_token,
                                   consume_transfer_token, transfer_channel, first_registrant,
                                   TRANSFER_TTL_MIN, get_settings, save_settings, update_settings,
                                   mandatory_set)
from newsbot.core_sources import add_source, get_source, set_source_api, set_source_active, count_sources
from newsbot.core_articles import article_update, get_article
from newsbot.core_plans import create_plan, get_plan
from newsbot.core_discounts import disc_valid, disc_create
from newsbot.core_payments import pay_pending_for, pay_create
from newsbot.ai_models import add_model, get_model, update_model, test_model, ai_err_text, err_code, hostname
from newsbot.discover import probe_source, _api_discover
from newsbot.publish import bot_can_post
from newsbot.text_format import sanitize_html, strip_tags
from newsbot.ui_i18n import tr, WIZ, FIELD_LABEL, INT_FIELDS, gtext_set, TEXT_KEYS
from newsbot.ui_widgets import (B, esc, render, popup, wiz_prompt, operation_status, uname, L,
                                notify_supers, notify_user_fn)
from newsbot.ui_views_user import view_receipt_ok
from newsbot.ui_views_admin import mand_gate, PLAN_FIELDS, MODEL_FIELDS

_ALLOW_PRIVATE_MODELS = os.getenv("ALLOW_PRIVATE_MODEL_HOSTS", "").strip() == "1"   # پیش‌فرض: مقصد داخلی مسدود


def _input_url(text, explicit_scheme=False, allow_private=False):
    """FIX v4: اعتبارسنجی نحو + بررسی میزبان خصوصی برای همه‌ی URLها.
    allow_private=True فقط برای base_url مدل شخصی وقتی ALLOW_PRIVATE_MODEL_HOSTS=1 است
    (پروایدرهای محلی مثل Ollama/LM Studio عمداً پشتیبانی می‌شوند)."""
    if not text or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in text): raise ValueError
    if any(c in text for c in "\\<>\"#") or re.search(r"%(?![0-9a-fA-F]{2})", text): raise ValueError
    if not re.match(r"(?i)^https?://", text):
        if explicit_scheme or "://" in text or text.startswith("//"): raise ValueError
        text = "https://" + text
    parsed = urlparse_safe(text)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc or parsed.username is not None or parsed.password is not None: raise ValueError
    host = parsed.hostname
    if not host or any(c in parsed.netloc for c in "{}%") or parsed.netloc.endswith(":"): raise ValueError
    if not allow_private and is_private_host(host):   # FIX v4: منبع/API/مدل — همه با یک قاعده‌ی مشترک
        raise ValueError("private host")
    if parsed.netloc.startswith("["):
        if not re.fullmatch(r"\[[0-9a-fA-F:.]+\](?::[0-9]+)?", parsed.netloc): raise ValueError
    elif parsed.netloc.count(":") > 1: raise ValueError
    if parsed.port is not None and not 1 <= parsed.port <= 65535: raise ValueError
    try: ipaddress_check(host)
    except ValueError:
        host = host.rstrip(".").encode("idna").decode("ascii")
        if host.lower() != "localhost" and "." not in host: raise ValueError
        if len(host) > 253 or re.fullmatch(r"[0-9.]+", host): raise ValueError
        if any(not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label) for label in host.split(".")): raise ValueError
    return parsed._replace(scheme=parsed.scheme.lower()).geturl()

def urlparse_safe(text):
    from urllib.parse import urlparse
    return urlparse(text)

def ipaddress_check(host):
    import ipaddress
    ipaddress.ip_address(host)


def _input_value(field, raw, lang, name_limit=100):
    """Validate before advancing pending input; errors are ready for a popup."""
    def invalid(fa, en): raise ValueError(en if lang == "en" else fa)
    text = raw.strip()
    if field == "emoji":
        # ایموجی یک ایموجی است، نه HTML: تگ نامتوازن پنل دسته‌ها را برای همیشه می‌شکست
        if not text or len(text) > 4 or any(ch in text for ch in "<>&"):
            invalid("فقط یک ایموجی بفرستید (بدون تگ و نویسهٔ اضافه).", "Send a single emoji (no markup).")
        return text
    if field == "base_url" or field in ("source_url", "api_url"):
        try: return _input_url(raw, explicit_scheme=(field == "base_url"), allow_private=(field == "base_url" and _ALLOW_PRIVATE_MODELS))
        except (ValueError, TypeError, UnicodeError):
            invalid("نشانی معتبر HTTP(S) بدون فاصله، نام کاربری، رمز یا # وارد کنید." + (" برای مدل http:// یا https:// الزامی است." if field == "base_url" else ""),
                    "Enter a valid HTTP(S) URL without spaces, credentials or #." + (" Model URLs require http:// or https://." if field == "base_url" else ""))
    if field in ("api_key", "model", "code"):
        limit = 256 if field == "model" else 24 if field == "code" else None
        if not raw or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in raw) or (limit and len(raw) > limit):
            if field == "api_key": invalid("کلید نباید خالی یا دارای فاصله باشد؛ برای سرویس بدون کلید، - بفرستید.", "Enter a nonempty key without whitespace, or - for an unauthenticated service.")
            if field == "model": invalid("شناسه مدل باید ۱ تا ۲۵۶ نویسه و بدون فاصله باشد.", "Model ID must contain 1–256 characters without whitespace.")
            invalid("کد تخفیف باید ۱ تا ۲۴ نویسه و بدون فاصله باشد.", "Discount code must contain 1–24 characters without whitespace.")
        # callback_data تلگرام سقف ۶۴ بایت دارد؛ بلندترین قالب s:disc_e:<code>:max_uses است
        if field == "code":
            if ":" in raw: invalid("کد تخفیف نمی‌تواند شامل «:» باشد.", "Discount code cannot contain ':'.")
            if len(raw.upper().encode("utf-8")) > 46: invalid("کد برای دکمه‌های مدیریت بیش از حد بلند است.", "Code is too long for the admin buttons; use a shorter one.")
        return "" if field == "api_key" and raw == "-" else raw
    if field in ("name", "name_en"):
        if not text or len(text) > name_limit or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in text): invalid(f"نام باید ۱ تا {name_limit} نویسه و بدون نویسه کنترلی باشد.", f"Name must contain 1–{name_limit} characters without control characters.")
        return text
    if field == "expires":
        value = text.translate(config._FA_DIGITS); now = now_utc()
        try:
            if re.fullmatch(r"[+]?[0-9]+", value):
                days = int(value)
                if days <= 0: raise ValueError
                exp = now + timedelta(days=days)
            else:
                match = re.fullmatch(r"([0-9]{4})[-/]([0-9]{1,2})[-/]([0-9]{1,2})", value)
                if not match: raise ValueError
                exp = datetime(*(int(x) for x in match.groups()), 23, 59, tzinfo=config.UTC)
            if exp <= now: raise ValueError
        except (ValueError, TypeError, OverflowError): invalid("انقضا باید تعداد روز مثبت یا تاریخ معتبر آینده به صورت YYYY-MM-DD باشد.", "Expiry must be positive days or a valid future date in YYYY-MM-DD format.")
        return exp.isoformat()
    if field == "channel":
        value = text.translate(config._FA_DIGITS)
        if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{4,31}", value): return value
        if re.fullmatch(r"-[0-9]+", value):
            number = int(value)
            if -(2 ** 63) <= number < 0: return number
        invalid("یوزرنیم معتبر کانال با @ یا شناسه عددی منفی کانال را بفرستید.", "Send a valid @channel_username or a negative integer channel ID.")
    if field == "temperature":
        try: value = float(text.translate(config._FA_DIGITS))
        except (ValueError, TypeError): invalid("دما باید عددی بین ۰ و ۲ باشد.", "Temperature must be a finite number between 0 and 2.")
        if not 0 <= value <= 2: invalid("دما باید عددی بین ۰ و ۲ باشد.", "Temperature must be a finite number between 0 and 2.")
        return value
    if field == "daily_posts_cap":
        t = text.strip()
        if t == "": return None
        try: value = to_int_safe(t)
        except (ValueError, TypeError): invalid("عدد صحیح بین ۱ تا سقف پلن", "Integer between 1 and plan cap")
        if not 1 <= value <= config.MAX_DAILY_POSTS_CAP: invalid(f"بین ۱ تا {config.MAX_DAILY_POSTS_CAP} انتخاب کنید.", f"Between 1 and {config.MAX_DAILY_POSTS_CAP}")
        # سقف واقعیِ «پلن» در handle_input بررسی می‌شود؛ آن‌جا context و uid در دسترس است
        return value
    if field in INT_FIELDS or field in ("days", "daily_posts", "max_sources", "max_channels", "daily_tests", "max_tokens", "max_uses", "priority", "weight", "percent", "price_num"):
        if field == "price_num":
            if text == "": return 0.0
            try: value = float(text.translate(config._FA_DIGITS).replace(",", ""))
            except (ValueError, TypeError): invalid("قیمت عددی باید یک عدد باشد (مثلاً 5 یا 0.5).", "Numeric price must be a number (e.g. 5 or 0.5).")
            if value < 0: invalid("قیمت عددی نمی‌تواند منفی باشد.", "Numeric price cannot be negative.")
            return value
        try: value = to_int_safe(text)
        except (ValueError, TypeError): invalid("عدد صحیح وارد کنید؛ اعشار مجاز نیست.", "Enter an integer; decimals are not allowed.")
        lo, hi = INT_FIELDS.get(field, (1 if field in ("days", "max_tokens", "weight", "percent") else -(2 ** 63) if field == "priority" else 0, 100 if field in ("weight", "percent") else 2 ** 63 - 1))
        if field == "days": hi = (datetime.max.replace(tzinfo=config.UTC) - now_utc()).days
        if not lo <= value <= hi: invalid(f"عدد صحیح بین {lo} و {hi} وارد کنید.", f"Enter an integer between {lo} and {hi}.")
        return value
    if not text: invalid("مقدار نمی‌تواند خالی باشد.", "The value cannot be empty.")
    return text

def to_int_safe(s):
    from newsbot.ui_widgets import to_int
    return to_int(s)


def _input_access(st, uid, lang):
    kind = st.get("kind"); wiz = st.get("wiz") if kind == "wiz" else None
    user = get_user(uid)
    if user and user["banned"] and not is_super(uid): return tr(lang, "banned")
    if kind in ("plan_field", "disc_field", "gtext", "umsg", "bc", "mand_field") or wiz in ("plan_new", "model_add", "disc_add") or (kind == "model_field" and not st.get("own")):
        if not is_super(uid): return tr(lang, "no_admin")
    if kind in ("field", "cat_style", "crit_weight", "src_add", "src_api_url", "src_api_key", "ch_add", "chtoken", "art_edit") or wiz in ("cat_add", "crit_add", "my_ai_add") or (kind == "model_field" and st.get("own")):
        if not can_admin(uid): return tr(lang, "no_admin")
    cid = st.get("data", {}).get("cid") if wiz in ("cat_add", "crit_add") else st.get("cid") if kind in ("field", "cat_style", "crit_weight", "src_add") else None
    if cid is not None and not channel_owned(cid, uid): return tr(lang, "notfound")
    if kind in ("src_api_url", "src_api_key", "art_edit"):
        item = get_article(st["aid"]) if kind == "art_edit" else get_source(st["sid"])
        if not item or (item["admin_id"] != uid and not is_super(uid)) or not channel_owned(item["channel_id"], uid): return tr(lang, "notfound")
    if kind == "model_field":
        model = get_model(st["mid"])
        if not model or (st.get("own") and model["owner_id"] != uid): return tr(lang, "notfound")
    if kind in ("src_add", "ch_add", "chtoken"):
        lim = admin_limits(uid)
        if not lim["active"]: return tr(lang, "plan_inactive")
        if kind == "src_add" and lim["max_sources"] is not None and count_sources(uid) >= lim["max_sources"]: return tr(lang, "limit_sources", n=lim["max_sources"])
    return None


async def handle_input(update, context, st):
    from newsbot.ui_dispatch import dispatch   # import درون‌تابعی: شکستن چرخه‌ی inputs ↔ dispatch
    ud = context.user_data
    if not isinstance(st, dict) or ud.get("await") is not st: return
    msg = update.message; uid = update.effective_user.id; lang = L(update); kind = st["kind"]; back = st["back"]
    raw_text = msg.text or msg.caption or ""; text = raw_text.strip(); text_html = (msg.text_html or msg.caption_html or "").strip()
    if kind not in ("bc", "receipt"):
        try: await msg.delete()
        except Exception: pass
        if ud.get("await") is not st: return
    def done(notice=None):
        current = ud.get("await")
        if current is st: ud.pop("await", None)
        if notice and (current is st or current is None): ud["notice"] = notice
    denied = _input_access(st, uid, lang)
    if denied: return await popup(update, context, denied)
    # ---- ویزاردها
    if kind == "wiz":
        steps = WIZ.get(st.get("wiz"), ()); step = st.get("step")
        if type(step) is not int or not 0 <= step < len(steps): done(); return
        key = steps[step][0]
        try: val = _input_value(key, raw_text, lang, name_limit=40 if st["wiz"] in ("cat_add", "crit_add") else 100)
        except (ValueError, TypeError) as e: return await popup(update, context, str(e))
        st["data"][key] = val; st["step"] += 1
        if st["step"] < len(steps): return await wiz_prompt(update, context)
        # Consume the terminal state before the first finalization await.
        done(); dta = st["data"]
        if st["wiz"] == "cat_add": s = get_settings(dta["cid"]); s["categories"].append({"name": dta["name"], "emoji": dta["emoji"][:4], "style": dta["style"][:400]}); save_settings(dta["cid"], s); done(tr(lang, "cat_added"))
        elif st["wiz"] == "crit_add": s = get_settings(dta["cid"]); s["criteria"].append({"name": dta["name"], "weight": dta["weight"]}); save_settings(dta["cid"], s); done(tr(lang, "crit_added"))
        elif st["wiz"] == "plan_new": create_plan(**dta); done(tr(lang, "s_plan_created", name=esc(dta["name"])))
        elif st["wiz"] in ("model_add", "my_ai_add"):
            own = st["wiz"] == "my_ai_add"
            mid = add_model(dta["base_url"], dta["api_key"], dta["model"], owner_id=uid if own else None)
            model = get_model(mid); name = model["name"] if model else dta["model"]
            await operation_status(context, tr(lang, "s_model_testing"))
            if not get_model(mid):
                await popup(update, context, tr(lang, "notfound"))
                return await dispatch(update, context, back)
            ok, out, t = await test_model(mid)
            model = get_model(mid)
            if not model:
                await popup(update, context, tr(lang, "notfound"))
                return await dispatch(update, context, back)
            result = tr(lang, "my_ai_added" if own else "s_model_added", name=esc(name), res=(tr(lang, "saved") if ok else ai_err_text(err_code(out), lang)) + f" ({t}s)")
            await operation_status(context, result, failed=not ok)
            await popup(update, context, result)
            return await dispatch(update, context, f"a:myai_v:{mid}" if own else back)
        elif st["wiz"] == "disc_add":
            exp = parse_dt(dta["expires"])
            if not exp or exp <= now_utc():
                st["step"] = next(i for i, item in enumerate(steps) if item[0] == "expires")
                ud["await"] = st
                return await popup(update, context, "انقضا گذشته است؛ تاریخ آینده وارد کنید." if lang != "en" else "Expiry has passed; enter a future date.")
            code = disc_create(dta["code"], dta["percent"], dta["expires"], dta["max_uses"]); done(tr(lang, "s_disc_created", code=code) if code else tr(lang, "s_disc_dup"))
        return await dispatch(update, context, back)
    # ---- فیلد تنظیمات کانال
    if kind == "field":
        f = st["field"]; cid = st["cid"]
        if f not in FIELD_LABEL: return await popup(update, context, tr(lang, "notfound"))
        label = FIELD_LABEL[f][1 if lang == "en" else 0]
        try: v = _input_value(f, raw_text, lang)
        except (ValueError, TypeError) as e: return await popup(update, context, str(e))
        if f == "daily_posts_cap" and v is not None:          # سقف روزانهٔ کانال هرگز از پلن کاربر بالاتر نمی‌رود
            cap = admin_limits(uid).get("daily_posts")
            if cap is not None and v > cap:
                return await popup(update, context, tr(lang, "cap_over_plan", n=cap))
        if f == "signature": v = sanitize_html(text_html)[:200]
        elif isinstance(v, str): v = v[:3000]
        update_settings(cid, **{f: v}); done(f"{tr(lang, 'saved')} · {label}: {esc(strip_tags(str(v))[:40])}"); return await dispatch(update, context, back)
    if kind == "cat_style":
        s = get_settings(st["cid"]); i = st["idx"]
        if not text: return await popup(update, context, tr(lang, "empty"))
        if not 0 <= i < len(s["categories"]): return await popup(update, context, tr(lang, "notfound"))
        s["categories"][i]["style"] = text[:400]; save_settings(st["cid"], s); done(tr(lang, "cat_updated"))
        return await dispatch(update, context, back)
    if kind == "crit_weight":
        s = get_settings(st["cid"]); i = st["idx"]
        if not 0 <= i < len(s["criteria"]): return await popup(update, context, tr(lang, "notfound"))
        try: weight = _input_value("weight", raw_text, lang)
        except (ValueError, TypeError) as e: return await popup(update, context, str(e))
        s["criteria"][i]["weight"] = weight; save_settings(st["cid"], s); done(tr(lang, "crit_updated"))
        return await dispatch(update, context, back)
    # ---- منبع
    if kind == "src_add":
        if not is_super(uid) and not rate_ok(f"srcadd:{uid}", config.ABUSE_RATE_SEC * 2):
            return await popup(update, context, tr(lang, "test_wait", n=rate_left(f"srcadd:{uid}", config.ABUSE_RATE_SEC * 2)))
        cid = st["cid"]
        try: url = _input_value("source_url", raw_text, lang).rstrip("/")
        except (ValueError, TypeError) as e: return await popup(update, context, str(e))
        sid = add_source(uid, cid, url)
        if not sid: return await popup(update, context, tr(lang, "src_dup"))
        done()
        await operation_status(context, tr(lang, "src_checking"))
        source = get_source(sid)
        if not source or not channel_owned(cid, uid): return await popup(update, context, tr(lang, "notfound"))
        ok, note = await probe_source(source, lang, added=True)
        source = get_source(sid)
        if not source or not channel_owned(cid, uid): return await popup(update, context, tr(lang, "notfound"))
        if ok: q("UPDATE sources SET title=? WHERE id=?", (hostname(url), sid), commit=True)
        else: log_event("WARN", f"منبع جدید {hostname(url)}: تست بارگذاری ناموفق", uid)
        await operation_status(context, note, failed=not ok)
        await popup(update, context, note)
        return await dispatch(update, context, f"a:srcv:{sid}")
    if kind == "src_api_url":
        sid = st["sid"]
        if text in ("-", "—", "‑"): set_source_api(sid, "", "", ""); done(tr(lang, "src_api_del")); return await dispatch(update, context, back)
        try: api = _input_value("api_url", raw_text, lang)
        except (ValueError, TypeError) as e: return await popup(update, context, str(e))
        ud["await"] = {"kind": "src_api_key", "sid": sid, "api": api, "back": back}
        return await render(update, context, f"✏️ {tr(lang, 'src_api_key')}\n\n<i>{tr(lang, 'send_value')}</i>", [[B(tr(lang, "cancel"), "c:cancel")]])
    if kind == "src_api_key":
        sid = st["sid"]
        try:
            key = _input_value("api_key", raw_text, lang)
            api = _input_value("api_url", st["api"], lang)
        except (ValueError, TypeError) as e: return await popup(update, context, str(e))
        set_source_api(sid, api, key, ""); done()
        await operation_status(context, tr(lang, "src_checking"))
        source = get_source(sid)
        if not source or (source["admin_id"] != uid and not is_super(uid)) or not channel_owned(source["channel_id"], uid): return await popup(update, context, tr(lang, "notfound"))
        try: items, _, _ = await _api_discover(source, 10); n = len(items)
        except (httpx.HTTPError, FetchError, RuntimeError, ValueError, TypeError, KeyError) as e: n = 0; log_event("WARN", f"API منبع {hostname(api)}: {err_code(e)}", uid)
        source = get_source(sid)
        if not source or (source["admin_id"] != uid and not is_super(uid)) or not channel_owned(source["channel_id"], uid): return await popup(update, context, tr(lang, "notfound"))
        if not n: set_source_active(sid, False, "active")
        note = tr(lang, "src_api_ok", n=n) if n else tr(lang, "src_api_bad")
        await operation_status(context, note, failed=not n)
        await popup(update, context, note)
        return await dispatch(update, context, f"a:srcv:{sid}")
    # ---- افزودن کانال + لایه‌ی امنیتی + دروازه‌ی کانال اجباری (NEW v4)
    if kind == "ch_add":
        chat = None; fo = getattr(msg, "forward_origin", None)
        if fo and getattr(fo, "chat", None) and fo.chat.type == ChatType.CHANNEL: chat = fo.chat
        else:
            try: identifier = _input_value("channel", raw_text, lang)
            except (ValueError, TypeError) as e: return await popup(update, context, str(e))
            try: chat = await context.bot.get_chat(identifier)
            except Exception:
                log.warning("channel lookup failed for %s", uid, exc_info=True)
                return await popup(update, context, "به کانال دسترسی ندارم؛ شناسه و دسترسی ربات را بررسی کنید." if lang != "en" else "Cannot access this channel; check its ID and the bot's access.")
        if ud.get("await") is not st: return
        if not chat or chat.type != ChatType.CHANNEL: return await popup(update, context, tr(lang, "ch_need_fwd"))
        if not await bot_can_post(context.bot, chat.id): return await popup(update, context, tr(lang, "ch_bot_not_admin"))
        if ud.get("await") is not st: return
        user_is_admin = False
        try: user_is_admin = any(m.user.id == uid for m in await context.bot.get_chat_administrators(chat.id))
        except Exception:
            try: mem = await context.bot.get_chat_member(chat.id, uid); user_is_admin = mem.status in ("administrator", "creator")
            except Exception: user_is_admin = False
        if ud.get("await") is not st: return
        if not user_is_admin:
            log_event("WARN", f"ثبت کانال «{chat.title}» ({chat.id}) بدون ادمین‌بودن", uid)
            await notify_supers(f"<b>هشدار امنیتی</b>\n<code>{uid}</code> تلاش کرد «{esc(chat.title)}» را بدون ادمین‌بودن ثبت کند.")
            return await popup(update, context, tr(lang, "ch_user_not_admin"))
        denied = _input_access(st, uid, lang)
        if denied: return await popup(update, context, denied)
        done()
        if await mand_gate(update, context, uid, lang): return   # NEW v4: تا تأیید عضویت، ثبت کانال متوقف است
        existing = channel_by_chat(chat.id)
        if existing:
            if existing["admin_id"] == uid: update_channel_meta(existing["id"], chat.title, chat.username or ""); done(tr(lang, "ch_exists_mine")); return await dispatch(update, context, f"a:ch:{existing['id']}")
            return await channel_claim_flow(update, context, existing, uid, lang, back, ud)
        lim = admin_limits(uid)
        if lim["max_channels"] is not None and len(list_channels_wrap(uid)) >= lim["max_channels"]: return await popup(update, context, tr(lang, "limit_channels", n=lim["max_channels"]))
        cid = add_channel(uid, chat.id, chat.title or str(chat.id), chat.username or "", uid, lang); log_event("INFO", f"کانال ثبت شد: {chat.title} ({chat.id})", uid)
        done(tr(lang, "ch_added", title=esc(chat.title))); return await dispatch(update, context, f"a:ch:{cid}")
    if kind == "chtoken":
        # ضد brute-force روی توکن ۱۰ رقمی: برخلاف سقف ۳ تلاش در هر نشست، این‌جا نرخ هم محدود می‌شود
        if not is_super(uid) and not rate_ok(f"tok:{uid}", config.ABUSE_RATE_SEC * 2):
            return await popup(update, context, tr(lang, "test_wait", n=rate_left(f"tok:{uid}", config.ABUSE_RATE_SEC * 2)))
        cid = st["cid"]; ch = get_channel(cid)
        if not ch: return await popup(update, context, tr(lang, "notfound"))
        # ۱) اعتبارسنجی بدون مصرف: توکن باطل نباید سهمیه/حالت را هدر بدهد
        if not check_transfer_token(cid, text):
            st["tries"] = int(st.get("tries") or 0) + 1
            log_event("WARN", f"توکن انتقال نادرست/منقضی برای «{ch['title']}» توسط {uid}", uid)
            if st["tries"] >= 3:
                done(); await popup(update, context, tr(lang, "ch_token_bad"))
                return await dispatch(update, context, back)
            await popup(update, context, tr(lang, "ch_token_bad"))
            if ud.get("await") is st:
                return await render(update, context, tr(lang, "ch_token_sent", who=esc(uname(get_user(first_registrant(cid)))), min=TRANSFER_TTL_MIN), [[B(tr(lang, "cancel"), "c:cancel")]])
            return
        # ۲) همهٔ بررسی‌ها پیش از مصرف توکن تا یک تلاشِ ناقص، توکن را نسوزاند
        if not await bot_can_post(context.bot, ch["chat_id"]): return await popup(update, context, tr(lang, "ch_bot_not_admin"))
        try: mem = await context.bot.get_chat_member(ch["chat_id"], uid); user_is_admin = mem.status in ("administrator", "creator")
        except Exception: user_is_admin = False
        if ud.get("await") is not st: return
        if not user_is_admin: return await popup(update, context, tr(lang, "ch_user_not_admin"))
        denied = _input_access(st, uid, lang)
        if denied: return await popup(update, context, denied)
        done()
        if await mand_gate(update, context, uid, lang): return   # NEW v4: دریافت کانال منتقل‌شده هم از دروازه می‌گذرد
        ch = get_channel(cid)
        if not ch: return await popup(update, context, tr(lang, "notfound"))
        lim = admin_limits(uid)
        if lim["max_channels"] is not None and len(list_channels_wrap(uid)) >= lim["max_channels"]: return await popup(update, context, tr(lang, "limit_channels", n=lim["max_channels"]))
        if publication_pending(cid): return await popup(update, context, tr(lang, "test_busy"))
        # ۳) مصرف اتمی یک‌بارمصرف؛ تنها برندهٔ UPDATE منتقل می‌کند
        if not consume_transfer_token(cid, text): return await popup(update, context, tr(lang, "ch_token_bad"))
        me = get_user(uid); who = f"{esc(uname(me))} (<code>{uid}</code>)"
        old_uid = ch["admin_id"]; ol = user_lang(old_uid) or "fa"
        try: transfer_channel(cid, uid, lang)
        except RuntimeError: return await popup(update, context, tr(lang, "test_busy"))
        done(); log_event("WARN", f"کانال {ch['title']} از {old_uid} به {uid} منتقل شد", uid)
        await notify_user_fn(old_uid, tr(ol, "ch_owner_moved", title=esc(ch["title"]), who=who)); await notify_supers(f"«{esc(ch['title'])}» transferred {old_uid} → {uid}")
        done(tr(lang, "ch_token_ok", title=esc(ch["title"]))); return await dispatch(update, context, f"a:ch:{cid}")
    # ---- مقاله
    if kind == "art_edit":
        art = get_article(st["aid"])
        if not art or (art["admin_id"] != uid and not is_super(uid)) or art["status"] == "published":
            return await popup(update, context, tr(lang, "notfound"))
        if publication_pending(art["channel_id"]): return await popup(update, context, tr(lang, "test_busy"))
        if not text_html: return await popup(update, context, tr(lang, "empty"))
        article_update(st["aid"], post_html=sanitize_html(text_html)); done(tr(lang, "art_updated"))
        return await dispatch(update, context, back)
    # ---- کد تخفیف
    if kind == "disc":
        d, why = disc_valid(text); pid = str(st["pid"])
        if not d: return await popup(update, context, tr(lang, {"expired": "disc_expired", "exhausted": "disc_exhausted"}.get(why, "disc_bad")))
        ud.setdefault("disc", {})[pid] = d["code"]; done(tr(lang, "disc_ok", p=d["percent"]))
        return await dispatch(update, context, back)
    # ---- رسید پرداخت
    if kind == "receipt":
        if not is_super(uid) and not rate_ok(f"rcpt:{uid}", config.ABUSE_RATE_SEC * 5):
            return await popup(update, context, tr(lang, "test_wait", n=rate_left(f"rcpt:{uid}", config.ABUSE_RATE_SEC * 5)))
        from newsbot.concurrency import user_op_lock
        async with user_op_lock(uid):   # BIZ-05: بررسی+ثبت رسید بدون پنجرهٔ هم‌زمان (دو رسید موازی = یکی)
            pid = st["pid"]; p = get_plan(pid); u = get_user(uid)
            if not p or not p["active"]: return await popup(update, context, tr(lang, "notfound"))
            if not (msg.photo or msg.document or text): await popup(update, context, tr(lang, "receipt_empty")); return
            if pay_pending_for(uid, pid): done(tr(lang, "req_pending")); return await dispatch(update, context, back)
            # st["final"] is the single source of truth (prorated amount for upgrades, full price text otherwise)
            rid = pay_create(uid, pid, msg.chat_id, msg.message_id, text, st.get("disc"), st.get("final"), st.get("pc"))
        done(); ud.get("disc", {}).pop(str(pid), None)
        disc = f"\n🎟 {esc(st['disc'])}" if st.get("disc") else ""; note = f"\n📝 {esc(text[:400])}" if text else ""
        header = tr("fa", "s_pay_new", id=rid, who=esc(uname(u)), uid=uid, plan=esc(p["name"]), price=esc(st.get("display") or st.get("final") or p["price"]), disc=disc, note=note); kb = InlineKeyboardMarkup([[B("✅ تأیید / Approve", f"s:pay_ok:{rid}"), B("❌ رد / Reject", f"s:pay_no:{rid}")]])
        for sid in config.SUPER_ADMIN_IDS:
            try:
                if msg.photo or msg.document: await context.bot.copy_message(sid, msg.chat_id, msg.message_id, caption=header[:1000], parse_mode="HTML", reply_markup=kb)
                else: await context.bot.send_message(sid, header, parse_mode="HTML", reply_markup=kb)
            except Exception as e: log.warning(f"pay notify {sid}: {e}")
        log_event("INFO", f"درخواست پرداخت #{rid} برای پلن {p['name']}", uid); return await view_receipt_ok(update, context)
    # ---- مدیر کلان
    if kind == "mand_field":
        lg, f = st.get("lg"), st.get("field")
        if lg not in config.LANGS or f not in ("id", "user"): return await popup(update, context, tr(lang, "notfound"))
        val = re.sub(r"\s+", "", strip_tags(text_html or "")).lstrip("@") if f == "user" else re.sub(r"\s+", "", strip_tags(text_html or ""))
        if f == "id" and val and not re.fullmatch(r"-?\d+", val): return await popup(update, context, tr(lang, "s_mand_bad_id"))
        mandatory_set(lg, **{f: val}); done(tr(lang, "saved")); return await dispatch(update, context, back)
    if kind in ("plan_field", "disc_field", "model_field"):
        from newsbot.core_discounts import disc_get, disc_update
        f = st["field"]; rec = st[{"plan_field": "pid", "disc_field": "code", "model_field": "mid"}[kind]]
        fields = {"plan_field": {i[0] for i in PLAN_FIELDS}, "disc_field": ("percent", "max_uses", "expires"), "model_field": {i[0] for i in MODEL_FIELDS}}[kind]
        if f not in fields or (kind == "plan_field" and not get_plan(rec)) or (kind == "disc_field" and not disc_get(rec)):
            return await popup(update, context, tr(lang, "notfound"))
        try: v = _input_value(f, raw_text, lang)
        except (ValueError, TypeError) as e: return await popup(update, context, str(e))
        if kind == "plan_field": update_plan_wrap(rec, **{f: v})
        elif kind == "disc_field": disc_update(rec, **{f: v})
        else: update_model(rec, **{f: v})
        done(tr(lang, "saved")); return await dispatch(update, context, back)
    if kind == "gtext":
        if st["key"] not in TEXT_KEYS or st["lg"] not in config.LANGS: return await popup(update, context, tr(lang, "notfound"))
        gtext_set(st["key"], st["lg"], sanitize_html(text_html)); done(tr(lang, "s_text_saved")); return await dispatch(update, context, back)
    if kind == "umsg":
        if not text_html: return await popup(update, context, tr(lang, "empty"))
        tl = user_lang(st["target"]) or "fa"
        try: await context.bot.send_message(st["target"], tr(tl, "s_umsg_head") + sanitize_html(text_html), parse_mode="HTML")
        except Exception:
            log.warning("admin message send failed for %s", uid, exc_info=True)
            return await popup(update, context, "پیام ارسال نشد؛ دوباره تلاش کنید." if lang != "en" else "Message could not be sent; please try again.")
        done(tr(lang, "s_umsg_sent")); return await dispatch(update, context, back)
    if kind == "bc": done(); ud["bc_src"] = (msg.chat_id, msg.message_id); return await render(update, context, tr(lang, "s_bc_confirm", n=len(list_users_wrap())), [[B(tr(lang, "s_bc_go"), "s:bc_go"), B(tr(lang, "cancel"), "s:home")]], force_new=True)
    done(); return await dispatch(update, context, back)


# ── کمک‌های کوچک (import دیرهنگام برای شکستن چرخه‌ها) ──
async def channel_claim_flow(update, context, existing, uid, lang, back, ud):
    from newsbot.ui_views_admin import channel_claim
    return await channel_claim(update, context, existing, uid, lang, back, ud)

def list_channels_wrap(uid):
    from newsbot.core_channels import list_channels
    return list_channels(uid)

def list_users_wrap():
    from newsbot.core_users import list_users
    return list_users()

def update_plan_wrap(pid, **f):
    from newsbot.core_plans import update_plan
    update_plan(pid, **f)
