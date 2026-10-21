# -*- coding: utf-8 -*-
"""کانال‌ها، توکن انتقال مالکیت، تنظیمات مستقل هر کانال و کانال اجباری.
NEW v4: بررسی عضویت کانال اجباری فقط برای ادمین/مدیر کلان اعمال می‌شود؛
کاربر عادی هرگز به جریان Join/Check وارد نمی‌شود.
"""
import json, random, sqlite3
from datetime import datetime, timedelta
from newsbot import config
from newsbot.db import q, db, _lock, _trx, gget, gset
from newsbot.logutils import log
from newsbot.timeutils import now_utc, now_iso, parse_dt, zone_of, offset_now, tz_name_for_offset, DEFAULT_TZ
from newsbot.core_users import user_lang, is_super
from newsbot.concurrency import ch_lock

# ══════════════════════════════════════════════════════════════════════════
# لایه‌ی امنیتی انتقال مالکیت (توکن ۱۰ رقمی یک‌بارمصرف)
# ══════════════════════════════════════════════════════════════════════════
TRANSFER_TTL_MIN = 5

def _new_transfer_token(): return f"{random.SystemRandom().randint(0, 10**10 - 1):010d}"

def issue_transfer_token(cid, issued_to):
    """توکن تازه صادر می‌کند و توکن‌های مصرف‌نشده‌ی قبلیِ همان کانال را باطل می‌کند (همیشه یکی فعال است).
    PHASE 4 (DB-15): باطل‌سازی و درج در یک تراکنش — کرش دیگر کانال را بدون توکن فعال رها نمی‌کند.

    PH-05/SEC-008: اگر یک توکنِ **مصرف‌نشده و منقضی‌نشده** برای همان کانال و همان
    گیرنده وجود داشته باشد، **همان** دوباره استفاده می‌شود ⇒ تلاش‌های مکررِ یک
    هم‌ادمین، توکنِ در انتظارِ مالکِ واقعی را باطل نمی‌کند و برایش پیامِ تکراری نمی‌فرستد."""
    now = now_utc()
    row = q("SELECT * FROM ch_tokens WHERE channel_id=? AND used_at IS NULL AND issued_to=? "
            "ORDER BY issued_at DESC LIMIT 1", (cid, issued_to), one=True)
    if row:
        exp = parse_dt(row["expires_at"])
        if exp and now_utc() <= exp:
            return row["token"]          # توکنِ معتبرِ در جریان ⇒ همان را برگردان
    for _ in range(6):
        tok = _new_transfer_token()
        def _go(conn):
            conn.execute("DELETE FROM ch_tokens WHERE channel_id=? AND used_at IS NULL", (cid,))
            conn.execute("INSERT INTO ch_tokens(token,channel_id,issued_to,issued_at,expires_at) VALUES(?,?,?,?,?)",
                         (tok, cid, issued_to, now.isoformat(), (now + timedelta(minutes=TRANSFER_TTL_MIN)).isoformat()))
        try:
            _trx(_go)
            return tok
        except sqlite3.Error:
            continue    # برخورد PK توکن تصادفی ⇒ تلاش مجدد با توکن تازه (رول‌بک کامل هر تلاش)
    return None

def check_transfer_token(cid, token):
    """اعتبارسنجی بدون مصرف (پیش‌بررسی): ۱۰ رقم، همان کانال، مصرف‌نشده، منقضی‌نشده."""
    tok = str(token or "").strip()
    if len(tok) != 10 or not tok.isdigit(): return None
    row = q("SELECT * FROM ch_tokens WHERE token=? AND channel_id=?", (tok, cid), one=True)
    if not row or row["used_at"]: return None
    exp = parse_dt(row["expires_at"])
    return row if (exp and now_utc() <= exp) else None

def consume_transfer_token(cid, token):
    """مصرف اتمی یک‌بارمصرف: فقط برندهٔ UPDATE اجازهٔ انتقال می‌گیرد."""
    row = check_transfer_token(cid, token)
    if not row: return None
    with _lock:
        cur = db().execute("UPDATE ch_tokens SET used_at=? WHERE token=? AND used_at IS NULL", (now_iso(), row["token"]))
        db().commit()
    return row if cur.rowcount else None

# ══════════════════════════════════════════════════════════════════════════
# CRUD کانال‌ها
# ══════════════════════════════════════════════════════════════════════════
def list_channels(uid): return q("SELECT * FROM channels WHERE admin_id=? ORDER BY id", (uid,))
def get_channel(cid): return q("SELECT * FROM channels WHERE id=?", (cid,), one=True)
def channel_owned(cid, uid):
    ch = get_channel(cid); return ch if ch and (ch["admin_id"] == uid or is_super(uid)) else None
def channel_by_chat(chat_id): return q("SELECT * FROM channels WHERE chat_id=?", (chat_id,), one=True)

def add_channel(uid, chat_id, title, username, verified_by, lang="fa"):
    if channel_by_chat(chat_id): return None
    try:
        return q("INSERT INTO channels(admin_id,chat_id,title,username,verified_by,created_at,settings,original_admin_id) VALUES(?,?,?,?,?,?,?,?)",
                 (uid, chat_id, title, username or "", verified_by, now_iso(), json.dumps(default_settings(lang, username), ensure_ascii=False), uid), commit=True)
    except sqlite3.Error:   # ایندکس یکتای chat_id: ثبت تکراری هم‌زمان → مثل حالت موجود، None
        log.warning("add_channel duplicate chat_id ignored: %s", chat_id); return None

def first_registrant(cid):
    """ثبت‌کنندهٔ نخست کانال: مرجع نهایی مالکیت. هرگز با انتقال عوض نمی‌شود."""
    ch = get_channel(cid)
    return (ch["original_admin_id"] or ch["admin_id"]) if ch else None

def publication_pending(cid):
    if ch_lock(cid).locked(): return True
    rows = q("SELECT journal FROM pub_journal WHERE article_id IN (SELECT id FROM articles WHERE channel_id=?)", (cid,))
    return any((json.loads(r["journal"]) or {}).get("phase") in ("in_flight", "partial", "ack") for r in rows)

def transfer_channel(cid, new_uid, lang="fa"):
    """انتقال مالکیت. منابع، صفِ تأییدنشده و ردپای انتشار/تکراریِ مالک قبلی پاک می‌شوند تا نه
    داده‌ی مالک قبلی همراهش برود و نه سابقه‌ی انتشارش جلوی انتشار مالک تازه را بگیرد.
    توکن‌های انتقالِ باقی‌مانده باطل می‌شوند (هر انتقال تنها یک توکن مصرف می‌کند)."""
    if publication_pending(cid): raise RuntimeError("channel_busy")
    ch = get_channel(cid)
    if not ch: return None
    old_uid = ch["admin_id"]
    def _go(conn):
        conn.execute("DELETE FROM sources WHERE channel_id=?", (cid,))
        conn.execute("DELETE FROM articles WHERE channel_id=? AND status!='published'", (cid,))
        conn.execute("DELETE FROM ch_tokens WHERE channel_id=?", (cid,))
        conn.execute("DELETE FROM dedup WHERE channel_id=?", (cid,))
        conn.execute("DELETE FROM posted WHERE channel_id=?", (ch["chat_id"],))
        conn.execute("UPDATE channels SET admin_id=?, verified_by=?, settings=? WHERE id=?",
                     (new_uid, new_uid, json.dumps(default_settings(lang, ch["username"]), ensure_ascii=False), cid))
        return old_uid
    return _trx(_go)   # اتمیک: یا کل انتقال انجام می‌شود یا هیچ‌کدام (قبلاً چهار commit جدا بود)

def revert_channel_owner(cid, uid):
    """بازگرداندن مالکیت به ثبت‌کنندهٔ نخست، بعد از اینکه مالک فعلی در تلگرام ادمینِ کانال نبود.
    PHASE 4 (DB-15): باطل‌سازی توکن‌ها و تحویل مالکیت در یک تراکنش (مثل transfer_channel)."""
    if publication_pending(cid): raise RuntimeError("channel_busy")
    ch = get_channel(cid)
    if not ch: return None
    def _go(conn):
        conn.execute("DELETE FROM ch_tokens WHERE channel_id=?", (cid,))
        conn.execute("UPDATE channels SET admin_id=?, verified_by=? WHERE id=?", (uid, uid, cid))
        return ch["admin_id"]
    return _trx(_go)

def reset_channel_queue(cid):
    """صف انتشار (تأییدنشده‌ها) پاک، اتوماسیون خاموش و وضعیت چرخه صفر می‌شود. منابع و آرشیو منتشرشده دست‌نخورده می‌مانند."""
    if publication_pending(cid): raise RuntimeError("channel_busy")
    n = q("SELECT COUNT(*) c FROM articles WHERE channel_id=? AND status!='published'", (cid,), one=True)["c"]
    def _go(conn):
        conn.execute("DELETE FROM articles WHERE channel_id=? AND status!='published'", (cid,))
        s = get_settings(cid)   # در همان تراکنش: یا صف و وضعیت با هم پاک می‌شوند یا هیچ‌کدام
        s.update(enabled=False, last_run=None, last_end=None, last_result="", last_diag=None, last_notified_diag="")
        conn.execute("UPDATE channels SET settings=? WHERE id=?", (json.dumps(s, ensure_ascii=False), cid))
    _trx(_go)
    return n

def del_channel(cid, uid):
    """حذف کانال به‌همراه همهٔ وابستگی‌هایش (منابع، مقاله‌ها، توکن‌ها، ردپای posted/dedup) در یک تراکنش؛
    وگرنه ثبت دوبارهٔ همان کانال با سابقهٔ انتشارِ کهنه سرکوب می‌شد."""
    if publication_pending(cid): raise RuntimeError("channel_busy")
    ch = get_channel(cid)
    if not ch or ch["admin_id"] != uid: return None
    def _go(conn):
        conn.execute("DELETE FROM sources WHERE channel_id=?", (cid,))
        conn.execute("DELETE FROM articles WHERE channel_id=?", (cid,))
        conn.execute("DELETE FROM ch_tokens WHERE channel_id=?", (cid,))
        conn.execute("DELETE FROM dedup WHERE channel_id=?", (cid,))
        conn.execute("DELETE FROM posted WHERE channel_id=?", (ch["chat_id"],))
        conn.execute("DELETE FROM channels WHERE id=? AND admin_id=?", (cid, uid))
        return ch["chat_id"]
    return _trx(_go)

def update_channel_meta(cid, title=None, username=None):
    if title is not None: q("UPDATE channels SET title=? WHERE id=?", (title, cid), commit=True)
    if username is not None: q("UPDATE channels SET username=? WHERE id=?", (username, cid), commit=True)

def msg_link(ch, msg): return f"https://t.me/{ch['username']}/{msg.message_id}" if ch["username"] else f"https://t.me/c/{str(ch['chat_id'])[4:]}/{msg.message_id}"

# ══════════════════════════════════════════════════════════════════════════
# کانال اجباری — مستقل برای هر زبان (fa/en)
# مدیر کلان می‌تواند برای هر زبان جدا فعال/غیرفعال و شناسه/یوزرنیم تعیین کند.
# مقادیر در جدول settings ذخیره می‌شوند؛ env فقط پیش‌فرضِ سازگاری برای زبان فارسی است.
# ══════════════════════════════════════════════════════════════════════════
MANDATORY_KEY = "mandatory_channels"
MANDATORY_DB_KEY = MANDATORY_KEY   # نام عمومی برای تست و ابزارها
MANDATORY_FIELDS = (("on", "bool"), ("id", "str"), ("user", "str"))

def _mand_env_default(lang):
    """پیش‌فرض سازگاری: نصب‌های قدیمی کانال اجباری را از env می‌خواندند (تک‌کاناله ⇒ زبان فارسی)."""
    if lang == "fa" and (config.MANDATORY_CHANNEL_ID or config.MANDATORY_CHANNEL_USERNAME):
        return {"on": True, "id": config.MANDATORY_CHANNEL_ID or "", "user": config.MANDATORY_CHANNEL_USERNAME or ""}
    return {"on": False, "id": "", "user": ""}

def mandatory_cfg(lang="fa"):
    """پیکربندی کانال اجباری همان زبان؛ نبودِ ردیف ⇒ پیش‌فرض env."""
    all_cfg = gget(MANDATORY_KEY) or {}
    cur = all_cfg.get(lang) if isinstance(all_cfg, dict) else None
    if isinstance(cur, dict):
        base = _mand_env_default(lang); base.update({k: cur[k] for k in base if k in cur}); return base
    return _mand_env_default(lang)

def mandatory_set(lang, **kw):
    """به‌روزرسانی پیکربندی یک زبان (بقیهٔ زبان‌ها دست‌نخورده می‌مانند)."""
    all_cfg = gget(MANDATORY_KEY) or {}
    if not isinstance(all_cfg, dict): all_cfg = {}
    cur = dict(mandatory_cfg(lang)); cur.update({k: v for k, v in kw.items() if k in dict(MANDATORY_FIELDS)})
    all_cfg[lang] = cur; gset(MANDATORY_KEY, all_cfg); return cur

def mandatory_configured(lang="fa"):
    """آیا برای این زبان کانال اجباریِ فعال و معتبری تعریف شده است؟"""
    c = mandatory_cfg(lang)
    return bool(c.get("on") and (str(c.get("id") or "").strip() or str(c.get("user") or "").strip()))

def mandatory_target(lang="fa"):
    """شناسهٔ عددی یا یوزرنیم کانال اجباری همان زبان؛ None اگر غیرفعال یا نامعتبر."""
    c = mandatory_cfg(lang)
    if not c.get("on"): return None
    raw = str(c.get("id") or "").strip()
    if raw:
        try: return int(raw)
        except (ValueError, TypeError): pass
    user = str(c.get("user") or "").strip()
    return "@" + user.lstrip("@") if user else None

async def mandatory_membership(bot, uid, lang="fa"):
    """بررسی عضویت در کانال اجباریِ زبان کاربر.
    خروجی: (ok, join_url). ok=False یعنی عضو نیست (یا بررسی ناموفق بود)."""
    tgt = mandatory_target(lang)
    if tgt is None: return True, None
    c = mandatory_cfg(lang); user = str(c.get("user") or "").strip().lstrip("@")
    join = None
    try:
        ch = await bot.get_chat(tgt)
        join = f"https://t.me/{ch.username}" if getattr(ch, "username", None) else None
    except Exception:
        join = f"https://t.me/{user}" if user else None
    try:
        mem = await bot.get_chat_member(tgt, uid)
        st = getattr(mem, "status", "")
        return (st not in ("left", "kicked")), join
    except Exception as e:
        # fail-closed: تا عضویت قطعاً تأیید نشود اجازهٔ ادامه داده نمی‌شود
        log.warning("mandatory membership check failed uid=%s lang=%s target=%s: %s", uid, lang, tgt, e)
        return False, join

# ══════════════════════════════════════════════════════════════════════════
# تنظیمات مستقل هر کانال
# ══════════════════════════════════════════════════════════════════════════
DEFAULT_PROMPT = {
    "fa": ("تو سردبیر حرفه‌ای یک کانال تلگرامی هستی و مثل یک انسانِ خبره و خوش‌قلم می‌نویسی؛ نه مثل ربات و نه مثل هوش مصنوعی. به زبانی می‌نویسی که در «زبان خروجی» تعیین شده است. "
           "متن را روان، دقیق، بی‌طرف و بدون اغراق بازنویسی کن؛ چیزی که در منبع نیست اضافه نکن و اعداد، تاریخ‌ها و اسامی را دقیقاً حفظ کن. "
           "تیتر جذاب در خط اول داخل <b> با یک ایموجی مرتبط؛ سپس لید یک‌جمله‌ای؛ سپس پاراگراف‌های کوتاه ۲ تا ۳ خطی با یک خط خالی بین آن‌ها. "
           "هر پاراگراف با یک ایموجی مرتبط شروع شود و در طول متن هم ایموجی‌های مناسب ادامه یابد (متنِ حدود ۲۰۰۰ کاراکتری حدود ۸ تا ۱۲ ایموجی می‌خواهد). "
           "نکات و فهرست‌ها را با «•» بنویس، اصطلاحات و اعداد مهم را <b> کن، مهم‌ترین نقل‌قول یا آمار را داخل <blockquote> بگذار. "
           "از خط جداکننده مثل «---»، جملات کلیشه‌ای و لحن قالبی پرهیز کن. جمله‌ها کوتاه و متنوع، بدون مقدمه‌چینی، با یک جمع‌بندی یک‌خطی در پایان."),
    "en": ("You are a professional editor of a Telegram channel who writes like a skilled, eloquent human — never like a bot or an AI. You write in the language set in “Output language”. "
           "Rewrite clearly, precisely, neutrally and without exaggeration; never add facts missing from the source and keep numbers, dates and names exact. "
           "Catchy headline on the first line inside <b> with one relevant emoji; then a one-sentence lead; then short 2–3 line paragraphs separated by a blank line. "
           "Every paragraph starts with a relevant emoji and suitable emojis continue naturally through the text (a ~2000-character post wants about 8–12 emojis). "
           "List points with “•”, bold key terms and numbers with <b>, put the most important quote or figure inside <blockquote>. "
           "Never use divider lines like “---”, no clichés, no robotic tone. Short, varied sentences, no preamble, one-line takeaway at the end."),
}
# پرامپت‌های پیش‌فرضِ نسخه‌های قبل — اگر کانال هنوز همان‌ها را دارد، خودکار با پیش‌فرض جدید جایگزین می‌شوند
_LEGACY_PROMPTS = (
    "تو سردبیر حرفه‌ای یک کانال تلگرامی فارسی هستی. متن را روان، دقیق، بی‌طرف و بدون اغراق بازنویسی کن؛ چیزی که در منبع نیست اضافه نکن و اعداد، تاریخ‌ها و اسامی را دقیقاً حفظ کن. "
    "تیتر جذاب در خط اول داخل <b> با یک ایموجی مرتبط؛ سپس لید یک‌جمله‌ای؛ سپس پاراگراف‌های کوتاه ۲ تا ۳ خطی با یک خط خالی بین آن‌ها. "
    "نکات کلیدی را با «•» فهرست کن، اصطلاحات مهم را <b> کن، نقل‌قول مهم را داخل <blockquote> بگذار. در هر پاراگراف حداکثر یک ایموجی مرتبط. جمله‌ها کوتاه، بدون مقدمه‌چینی، با یک جمع‌بندی یک‌خطی در پایان.",
    "You are a professional editor of an English Telegram channel. Rewrite clearly, precisely, neutrally and without exaggeration; never add facts missing from the source and keep numbers, dates and names exact. "
    "Catchy headline on the first line inside <b> with one relevant emoji; then a one-sentence lead; then short 2–3 line paragraphs separated by a blank line. "
    "List key points with “•”, bold key terms with <b>, put a key quote inside <blockquote>. At most one relevant emoji per paragraph. Short sentences, no preamble, one-line takeaway at the end.",
)
DEFAULT_CATEGORIES = {
    "fa": [{"name": "خبری", "emoji": "📰", "style": "تیتر کوتاه بولد، لید یک‌جمله‌ای، ۲ تا ۳ پاراگراف کوتاه، یک نکته‌ی کلیدی با «•»."},
           {"name": "آموزشی", "emoji": "🎓", "style": "تیتر بولد، مقدمه‌ی یک‌خطی، ۳ تا ۶ نکته با «•»، جمع‌بندی یک‌خطی."},
           {"name": "تحلیلی", "emoji": "🧠", "style": "تیتر بولد، یک نقل‌قول کلیدی داخل blockquote، تحلیل در دو پاراگراف کوتاه."},
           {"name": "معرفی و بررسی", "emoji": "🔍", "style": "تیتر بولد، خلاصه‌ی یک‌خطی، مزایا/معایب با «•»، جمع‌بندی بی‌طرف."},
           {"name": "علمی و پژوهشی", "emoji": "🔬", "style": "تیتر بولد، یافته‌ی اصلی در یک جمله، روش و اعداد مهم با «•»، محدودیت‌ها در یک خط."},
           {"name": "اقتصادی و بازار", "emoji": "📈", "style": "تیتر بولد، عدد/روند کلیدی در blockquote، دلیل و پیامد در دو پاراگراف کوتاه، بدون توصیه‌ی مالی."},
           {"name": "سلامت و سبک زندگی", "emoji": "🫁", "style": "تیتر بولد، توضیح ساده، توصیه‌های عملی با «•»، یک خط یادآوری که جایگزین پزشک نیست."},
           {"name": "ورزشی", "emoji": "🏆", "style": "تیتر بولد، نتیجه/رخداد در خط اول، آمار مهم با «•»، یک جمله درباره‌ی ادامه‌ی ماجرا."},
           {"name": "فرهنگ و هنر", "emoji": "🎭", "style": "تیتر بولد، معرفی کوتاه اثر/رخداد، یک نقل‌قول در blockquote، لحن روایی و بی‌قضاوت."},
           {"name": "شغلی و مهارت", "emoji": "💼", "style": "تیتر بولد، فرصت/مهارت در یک جمله، مراحل عملی با «•»، یک خط جمع‌بندی."}],
    "en": [{"name": "News", "emoji": "📰", "style": "Short bold headline, one-sentence lead, 2–3 short paragraphs, one key point as a “•” bullet."},
           {"name": "Tutorial", "emoji": "🎓", "style": "Bold headline, one-line intro, 3–6 “•” bullets, one-line takeaway."},
           {"name": "Analysis", "emoji": "🧠", "style": "Bold headline, one key quote in blockquote, analysis in two short paragraphs."},
           {"name": "Review", "emoji": "🔍", "style": "Bold headline, one-line summary, pros/cons as “•” bullets, neutral verdict."},
           {"name": "Science & research", "emoji": "🔬", "style": "Bold headline, the main finding in one sentence, method and key numbers as “•” bullets, limitations in one line."},
           {"name": "Business & markets", "emoji": "📈", "style": "Bold headline, the key figure or trend in a blockquote, cause and effect in two short paragraphs, no financial advice."},
           {"name": "Health & lifestyle", "emoji": "🫁", "style": "Bold headline, plain explanation, practical tips as “•” bullets, one line noting it is not medical advice."},
           {"name": "Sports", "emoji": "🏆", "style": "Bold headline, result or event on the first line, key stats as “•” bullets, one sentence on what comes next."},
           {"name": "Culture & arts", "emoji": "🎭", "style": "Bold headline, short introduction of the work or event, one quote in blockquote, narrative and non-judgmental tone."},
           {"name": "Career & skills", "emoji": "💼", "style": "Bold headline, the opportunity or skill in one sentence, actionable steps as “•” bullets, one-line takeaway."}],
}
DEFAULT_CRITERIA = {
    "fa": [{"name": "ارزش محتوایی", "weight": 40}, {"name": "غیرتبلیغاتی بودن", "weight": 15}, {"name": "کیفیت و کامل بودن", "weight": 20}, {"name": "ارتباط با موضوع کانال", "weight": 15}, {"name": "تازگی", "weight": 10}],
    "en": [{"name": "Content value", "weight": 40}, {"name": "Non-promotional", "weight": 15}, {"name": "Quality & completeness", "weight": 20}, {"name": "Relevance to channel topic", "weight": 15}, {"name": "Freshness", "weight": 10}],
}

def default_settings(lang="fa", channel_username=""):
    lang = lang if lang in config.LANGS else "fa"
    return {"ui_lang": lang, "enabled": True, "mode": "auto", "prompt": DEFAULT_PROMPT[lang], "topic": "", "language": "فارسی" if lang == "fa" else "English",
            "categories": [dict(c) for c in DEFAULT_CATEGORIES[lang]], "criteria": [dict(c) for c in DEFAULT_CRITERIA[lang]], "min_score": 60,
            "lookback_hours": 72, "allow_undated": True, "quiet_start": None, "quiet_end": None, "tz": DEFAULT_TZ, "utc_offset": config.DEFAULT_UTC_OFFSET,
            "include_media": True, "include_link": True, "signature": f"@{channel_username}" if channel_username else "@channel", "max_words": 500, "post_limit": config.POST_LIMIT_DEFAULT,
            "strict_ads": False, "interval_minutes": 60, "posts_per_cycle": 1, "hashtags": True, "daily_posts_cap": None,
            "last_run": None, "last_end": None, "last_result": "", "last_diag": None, "last_notified_diag": ""}

def get_settings(cid):
    ch = q("SELECT settings, username, admin_id FROM channels WHERE id=?", (cid,), one=True)
    stored = {}
    if ch and ch["settings"]:
        try: stored = json.loads(ch["settings"])
        except Exception: stored = {}
    base = stored.get("ui_lang") or (user_lang(ch["admin_id"]) if ch else None) or "fa"
    s = default_settings(base if base in config.LANGS else "fa", ch["username"] if ch else ""); s.update(stored); s["ui_lang"] = base if base in config.LANGS else "fa"
    # ARCH-10 (فاز ۷): ردیف‌های قدیمی فقط utc_offset دارند → نام IANA متناظر استخراج و آفست از
    # zoneinfo برای همین لحظه مشتق می‌شود (snapshot DST-aware؛ در هر خواندن به‌روز می‌شود).
    # توجه: پیش‌فرضِ default_settings حالا tz دارد؛ مهاجرت باید از مقدارِ «ذخیره‌شده» تصمیم بگیرد،
    # نه از دیکشنریِ ادغام‌شده — وگرنه ردیفِ قدیمیِ utc_offset=4.5 با tz پیش‌فرض رها می‌شد.
    tz_val = stored.get("tz")
    if not (isinstance(tz_val, str) and tz_val.strip()):
        legacy = stored.get("utc_offset") if ("tz" not in stored and "utc_offset" in stored) else s.get("utc_offset")
        s["tz"] = tz_name_for_offset(legacy)
    try: s["utc_offset"] = offset_now(s["tz"])
    except Exception: s["utc_offset"] = config.DEFAULT_UTC_OFFSET
    if s.get("prompt") in _LEGACY_PROMPTS: s["prompt"] = DEFAULT_PROMPT[s["ui_lang"]]   # مهاجرت پرامپت قدیمی
    for k in list(s):
        if k.startswith("_") and k != "_retry_until": s.pop(k)   # _retry_until وضعیت داخلیِ خواباندن صف است و باید بماند
    s["interval_minutes"] = max(config.MIN_INTERVAL, int(s.get("interval_minutes") or config.MIN_INTERVAL)); s["lookback_hours"] = min(config.MAX_LOOKBACK, max(1, int(s.get("lookback_hours") or 72)))
    # کپشن کانال هرگز از سقف سخت بالاتر نمی‌رود (وگرنه تلگرام پست را دو پیام می‌کند)
    s["post_limit"] = min(config.POST_LIMIT_MAX, max(300, int(s.get("post_limit") or config.POST_LIMIT_DEFAULT))); s["posts_per_cycle"] = min(config.MAX_PPC, max(1, int(s.get("posts_per_cycle") or 1)))
    return s

LOCALIZED_KEYS = ("prompt", "language", "categories", "criteria")

def _same(a, b):
    try: return json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(b, ensure_ascii=False, sort_keys=True)
    except Exception: return a == b

def relocalize_settings(cid, new_lang):
    """زبان مدیر عوض شد → صفر تا صدِ تنظیمات کانال هم عوض می‌شود: هر مقداری که همان «پیش‌فرض زبان قبلی» بوده (پرامپت، زبان محتوا، دسته‌ها، معیارها) به زبان جدید بازنویسی می‌شود؛ مقادیر دست‌ساز مدیر دست‌نخورده می‌مانند."""
    new_lang = new_lang if new_lang in config.LANGS else "fa"; s = get_settings(cid); old = s.get("ui_lang", "fa")
    if old != new_lang:
        old_d, new_d = default_settings(old, ""), default_settings(new_lang, "")
        for k in LOCALIZED_KEYS:
            if _same(s.get(k), old_d.get(k)): s[k] = new_d[k]
        s["ui_lang"] = new_lang; s["last_diag"] = None; s["last_notified_diag"] = ""; save_settings(cid, s); return s
    return s

def relocalize_all(uid, new_lang):
    n = 0
    for ch in q("SELECT id FROM channels WHERE admin_id=?", (uid,)):
        try: relocalize_settings(ch["id"], new_lang); n += 1
        except Exception as e: log.warning(f"relocalize {ch['id']}: {e}")
    return n

def save_settings(cid, s): q("UPDATE channels SET settings=? WHERE id=?", (json.dumps(s, ensure_ascii=False), cid), commit=True)
def update_settings(cid, **kw): s = get_settings(cid); s.update(kw); save_settings(cid, s); return s

def in_quiet(s):
    a, b = s.get("quiet_start"), s.get("quiet_end")
    if a is None or b is None or a == b: return False
    h = datetime.now(zone_of(s.get("tz") or s.get("utc_offset"))).hour
    return (a <= h < b) if a < b else (h >= a or h < b)
