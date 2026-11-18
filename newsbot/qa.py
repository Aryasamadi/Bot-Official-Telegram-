# -*- coding: utf-8 -*-
"""فاز ۴ — پاسخ به سؤالِ replyشده به پستِ کانال (هستهٔ Q&A).

قراردادها (design: docs/superpowers/specs/2026-11-16-phase4-reply-qa-design.md):
  • فقط پستِ کانال + متنِ سؤال به مدل می‌رسد؛ هر دو با سقفِ طول.
  • فقط گروهِ بحثِ رسمیِ همان کانال پاسخ می‌گیرد (fail-closed، تصمیمِ مالک ۱).
  • هر پیامِ کاربر حداکثر یک پاسخ می‌گیرد (at-most-once، تصمیمِ مالک ۲).
  • سهمیهٔ AI از خودِ پرسشگر کم می‌شود (تصمیمِ مالک ۳).

نکتهٔ امنیتی: متنِ سؤال و متنِ پاسخ هرگز نوشته نمی‌شوند؛ لاگ فقط شناسه و طول دارد.
"""
from newsbot import ai_models, config
from newsbot.ai_models import budget_reset
from newsbot.core_channels import channel_by_chat, get_settings
from newsbot.core_users import ensure_user
from newsbot.db import arun, gget, gset, q, write
from newsbot.logutils import log
from newsbot.text_format import strip_tags
from newsbot.timeutils import now_iso, now_utc, parse_dt

# پیام‌های صادقانهٔ ما (بدونِ AI) — متنِ ثابت، تا مدل هرگز چیزی از خودش نسازد.
NO_TEXT = "این پست متنِ قابلِ خواندنی ندارد؛ فقط می‌توانم بر پایهٔ متنِ پست‌ها پاسخ بدهم."
NO_QUESTION = "سؤالِ متنی پیدا نکردم؛ لطفاً پرسش‌تان را در همان پیامِ reply بنویسید."
NO_QUOTA = "سهمیهٔ هوش مصنوعیِ شما تمام شده است؛ پس از تمدیدِ پلن دوباره تلاش کنید."
AI_FAIL = "فعلاً نتوانستم پاسخ بدهم؛ کمی بعد دوباره بپرسید."


# ── توابعِ خالص ───────────────────────────────────────────────────────────────────
def detect_post(reply):
    """``{"channel_chat_id": int, "post_id": int}`` اگر پیامِ replyشده پستِ یک کانال است، وگرنه None.

    در گروهِ بحث، پستِ کانال به‌صورتِ «فورواردِ خودکار» ظاهر می‌شود: ``sender_chat`` نوعِ channel
    دارد. فورواردِ دستی ``forward_from_chat`` دارد. هر دو شناسهٔ کانال را می‌دهند تا با
    ``channels.chat_id`` نگاشت شوند.
    """
    ch = getattr(reply, "sender_chat", None)
    if ch is not None and getattr(ch, "type", "") == "channel":
        cid = int(ch.id)
    else:
        fw = getattr(reply, "forward_from_chat", None)
        if fw is None or getattr(fw, "type", "") != "channel":
            return None
        cid = int(fw.id)
    pid = int(getattr(reply, "forward_from_message_id", None) or getattr(reply, "message_id", 0) or 0)
    return {"channel_chat_id": cid, "post_id": pid}


def _clip(text, max_chars):
    """متن سادهٔ بریده‌شده: تگ‌ها حذف می‌شوند و طول از سقف رد نمی‌شود."""
    text = strip_tags(text or "").strip()
    if not text:
        return ""
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "…"


def post_text(reply, max_chars):
    """متنِ پست (text یا کپشن)، بدونِ HTML و با سقفِ طول."""
    return _clip(getattr(reply, "text", None) or getattr(reply, "caption", None), max_chars)


def question_text(msg, max_chars):
    """متنِ سؤالِ کاربر، با سقفِ طول."""
    return _clip(getattr(msg, "text", None) or getattr(msg, "caption", None), max_chars)


def build_prompt(post, question):
    """``(system, user)`` — مدل فقط اجازه دارد از متنِ پست پاسخ بدهد."""
    system = ("تو دستیارِ کانال هستی. فقط و فقط بر پایهٔ «متنِ پست» که در ادامه می‌آید پاسخ بده. "
              "اگر پاسخ در متنِ پست نیست، صادقانه بگو که در این پست نیست. "
              "هم‌زبانِ سؤال جواب بده. حداکثر ۴ جمله، متنِ ساده، بدونِ Markdown و بدونِ HTML.")
    user = f"متنِ پست:\n<<<\n{post}\n>>>\n\nپرسش:\n{question}\n\nپاسخ:"
    return system, user


def cap_answer(text, max_chars):
    """پاسخِ نهایی: متنِ ساده و بریده، تا هرگز از سقفِ پیام رد نشود."""
    return _clip(text, max_chars)


# ── ادعا و پایانِ کار (at-most-once) ───────────────────────────────────────────────
def qa_claim(chat_id, msg_id, user_id, channel_id, post_id=0):
    """True فقط اگر همین فراخوانی ردیفِ تازه ساخت.

    ``INSERT OR IGNORE`` تک‌دستوری و اتمیک است (SQLite و D1 هر دو) ⇒ اگر همان پیام دوباره
    برسد (restart، retryِ شبکه، ``concurrent_updates``) ردیفِ موجود دست‌نخورده می‌ماند و
    پاسخِ دوم ساخته نمی‌شود: تصمیمِ مالک ۲ — «حتی اگر پاسخ گم شود، تکرار نمی‌کنیم».
    ``channel_id`` شناسهٔ چتِ تلگرامِ کانال است، نه شناسهٔ داخلی (تصمیمِ مالک ۵).
    """
    now = now_iso()

    def _go(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO qa_answers(chat_id,msg_id,user_id,channel_id,post_id,status,answer,error,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'pending','','',?,?)",
            (chat_id, msg_id, user_id, channel_id, post_id, now, now))
        return int(getattr(cur, "rowcount", 0) or 0) == 1

    return bool(write(_go))


def qa_finish(chat_id, msg_id, status, answer="", error=""):
    """وضعیتِ نهاییِ همان ردیف را می‌نویسد؛ هرگز ردیفِ تازه نمی‌سازد."""
    q("UPDATE qa_answers SET status=?, answer=?, error=?, updated_at=? WHERE chat_id=? AND msg_id=?",
      (status, answer, error, now_iso(), chat_id, msg_id), commit=True)


# ── اعتبارسنجیِ گروهِ بحث (fail-closed) ────────────────────────────────────────────
async def linked_channel_id(bot, group_id):
    """``linked_chat_id`` گروهِ بحث، با کشِ کوتاه در جدولِ settings.

    خروجی ``None`` یعنی «نامعلوم» و نامعلوم همیشه سکوت است: پرسش در گروهِ بحثِ
    ناشناخته هرگز پاسخ نمی‌گیرد (تصمیمِ مالک ۱).
    """
    key = f"qa:linked:{group_id}"
    ent = await arun(gget, key)
    if isinstance(ent, dict) and ent.get("at"):
        try:
            age = (now_utc() - parse_dt(ent["at"])).total_seconds()
            if age < config.QA_LINK_CACHE_MIN * 60:
                return int(ent["id"]) if ent.get("id") else None
        except Exception:
            pass                      # کشِ خراب فقط یعنی «دوباره بپرس»
    try:
        chat = await bot.get_chat(group_id)
        linked = getattr(chat, "linked_chat_id", None)
    except Exception as e:
        log.warning("qa: get_chat failed group=%s (%s)", group_id, type(e).__name__)
        return None
    await arun(gset, key, {"id": int(linked) if linked else 0, "at": now_iso()})
    return int(linked) if linked else None


# ── هندلرِ گروهِ بحث ───────────────────────────────────────────────────────────────
async def on_group_reply(update, context):
    """پاسخِ یک‌باره به سؤالِ replyشده به پستِ کانال، با هزینهٔ خودِ پرسشگر."""
    if not config.QA_ENABLED:
        return                                    # kill-switchِ سراسریِ نصب
    msg = getattr(update, "effective_message", None)
    user = getattr(update, "effective_user", None)
    if msg is None or user is None:
        return                                    # پیامِ کانال/ناشناس
    chat_id = int(getattr(msg, "chat_id", 0) or 0)
    img_id = int(getattr(msg, "message_id", 0) or 0)
    uid = int(getattr(user, "id", 0) or 0)
    reply = getattr(msg, "reply_to_message", None)
    if reply is None:
        log.debug("qa: ignored chat=%s msg=%s user=%s reason=not_a_reply", chat_id, img_id, uid)
        return                                    # سؤال باید reply به پست باشد
    post = detect_post(reply)
    if not post:
        log.debug("qa: ignored chat=%s msg=%s user=%s reason=not_a_post", chat_id, img_id, uid)
        return                                    # reply به پیامِ یک کاربر، نه پستِ کانال
    ch = await arun(channel_by_chat, post["channel_chat_id"])
    if not ch:
        log.info("qa: ignored chat=%s msg=%s user=%s reason=unregistered_channel",
                 chat_id, img_id, uid)
        return                                    # کانالِ ثبت‌نشده ⇒ سکوت (fail-closed)
    st = await arun(get_settings, ch["id"])
    if not st.get("qa_enabled", True):
        log.info("qa: ignored chat=%s msg=%s user=%s reason=toggle_off", chat_id, img_id, uid)
        return                                    # کلیدِ per-channel در پنلِ مدیر خاموش است
    linked = await linked_channel_id(context.bot, chat_id)
    if linked is None or int(linked) != int(ch["chat_id"]):
        log.info("qa: ignored chat=%s msg=%s user=%s reason=foreign_group", chat_id, img_id, uid)
        return                                    # گروهِ دیگر / فورواردِ دستی در گروهِ بی‌ربط ⇒ سکوت

    text = post_text(reply, config.QA_CONTEXT_MAX_CHARS)
    ask = question_text(msg, config.QA_QUESTION_MAX_CHARS)
    if not text or not ask:
        # پیامِ صادقانهٔ ثابت، بدونِ هزینه و بدونِ AI؛ همچنان at-most-once.
        if not await arun(qa_claim, chat_id, img_id, uid, ch["chat_id"], post["post_id"]):
            return
        wait = NO_TEXT if not text else NO_QUESTION
        await _send(context, chat_id, img_id, wait)
        await arun(qa_finish, chat_id, img_id, "failed", "", "no_text" if not text else "no_question")
        log.info("qa: skipped chat=%s msg=%s user=%s reason=%s",
                 chat_id, img_id, uid, "no_text" if not text else "no_question")
        return

    await arun(ensure_user, uid, getattr(user, "username", "") or "", getattr(user, "full_name", "") or "")
    if not await arun(qa_claim, chat_id, img_id, uid, ch["chat_id"], post["post_id"]):
        log.info("qa: duplicate chat=%s msg=%s user=%s", chat_id, img_id, uid)
        return                                    # تصمیمِ مالک ۲: دوباره پاسخ نمی‌دهیم

    system, model_user = build_prompt(text, ask)
    budget_reset()                                # بودجهٔ ContextVar باید روی همین حلقه صفر شود
    out, _label, err = await ai_models.ai_chat(system, model_user, owner_id=uid)
    answer = cap_answer(out or "", config.QA_ANSWER_MAX_CHARS)
    if err or not answer:
        wait = NO_QUOTA if err == "ai_limit" else AI_FAIL
        await _send(context, chat_id, img_id, wait)
        await arun(qa_finish, chat_id, img_id, "failed", "", err or "empty_answer")
        log.warning("qa: ai failed chat=%s msg=%s user=%s err=%s",
                    chat_id, img_id, uid, err or "empty_answer")
        return

    await _send(context, chat_id, img_id, answer)
    await arun(qa_finish, chat_id, img_id, "done", answer, "")
    log.info("qa: answered chat=%s msg=%s user=%s qlen=%d alen=%d",
             chat_id, img_id, uid, len(ask), len(answer))


async def _send(context, chat_id, reply_to, text):
    """ارسالِ پاسخ به‌صورتِ reply. خطای شبکه بالا می‌رود: ردیف pending می‌ماند و
    at-most-once یعنی دوباره تلاش نمی‌کنیم (تصمیمِ مالک ۲)."""
    await context.bot.send_message(chat_id, text, reply_to_message_id=reply_to,
                                   disable_web_page_preview=True)
