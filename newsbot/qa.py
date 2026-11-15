# -*- coding: utf-8 -*-
"""فاز ۴ — پاسخ به سؤالِ replyشده به پستِ کانال (هستهٔ Q&A).

قراردادها (design: docs/superpowers/specs/2026-11-16-phase4-reply-qa-design.md):
  • فقط پستِ کانال + متنِ سؤال به مدل می‌رسد؛ هر دو با سقفِ طول.
  • فقط گروهِ بحثِ رسمیِ همان کانال پاسخ می‌گیرد (fail-closed، تصمیمِ مالک ۱).
  • هر پیامِ کاربر حداکثر یک پاسخ می‌گیرد (at-most-once، تصمیمِ مالک ۲).
  • سهمیهٔ AI از خودِ پرسشگر کم می‌شود (تصمیمِ مالک ۳).

این ماژول در این مرحله فقط توابعِ خالص را دارد؛ claim/هندلر در گامِ بعد می‌آیند.
"""
from newsbot.text_format import strip_tags


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
