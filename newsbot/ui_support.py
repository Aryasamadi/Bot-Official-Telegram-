# -*- coding: utf-8 -*-
"""پشتیبانی تک‌پیامی: فوروارد پیام کاربر به مدیران کلان، پاسخ با reply، و مسیر on_message."""
from telegram import InlineKeyboardMarkup
from telegram.constants import ChatType
from newsbot import config
from newsbot.logutils import log
from newsbot.core_users import ensure_user, is_super, user_lang
from newsbot.core_payments import support_is_open, support_map_get, support_map_set
from newsbot.ratelimit import rate_ok
from newsbot.text_format import split_html
from newsbot.ui_i18n import tr
from newsbot.ui_widgets import B, esc, popup, stale, L, uname, run_user_operation, go_home, send_temp
from newsbot.ui_views_user import view_lang


def _sup_header(u):
    who = f"@{u['username']}" if u["username"] else f"<code>{u['id']}</code>"
    return f"📨 <b>{esc(u['name'] or '')}</b> · {who}"

async def support_forward(context, user, msg):
    header = _sup_header(user); sent = 0
    for sid in config.SUPER_ADMIN_IDS:
        try:
            if msg.text:
                m = None
                for part in split_html(f"{header}\n{msg.text_html}")[:2]:   # پیام بلند بی‌صدا گم نمی‌شود
                    m = await context.bot.send_message(sid, part, parse_mode="HTML", disable_web_page_preview=True)
            else:
                try: m = await context.bot.copy_message(sid, msg.chat_id, msg.message_id, caption=(f"{header}\n{msg.caption_html or ''}")[:1000], parse_mode="HTML")
                except Exception: m = await context.bot.copy_message(sid, msg.chat_id, msg.message_id)
            support_map_set(sid * 10 ** 8 + m.message_id, user["id"]); sent += 1
        except Exception as e: log.warning(f"support fwd {sid}: {e}")
    return sent

async def support_reply(context, sid, msg, target):
    tl = user_lang(target) or "fa"; head = tr(tl, "sup_reply_head")
    if msg.text: await context.bot.send_message(target, head + msg.text_html, parse_mode="HTML", disable_web_page_preview=True)
    else:
        try: await context.bot.copy_message(target, msg.chat_id, msg.message_id, caption=(head + (msg.caption_html or ""))[:1000], parse_mode="HTML")
        except Exception: await context.bot.send_message(target, head, parse_mode="HTML"); await context.bot.copy_message(target, msg.chat_id, msg.message_id)

async def _ack(msg, fallback_text, kb=None):
    try: await msg.set_reaction("👌")
    except Exception:
        try: await msg.reply_text(fallback_text, reply_markup=kb)
        except Exception: pass

async def on_message(update, context):
    from newsbot.ui_inputs import handle_input   # import درون‌تابعی: شکستن چرخه‌ی support ↔ inputs ↔ dispatch
    msg = update.message; u = update.effective_user; uid = u.id
    if not msg or msg.chat.type != ChatType.PRIVATE: return
    if stale(update, "message"): return
    user = ensure_user(uid, u.username, u.full_name, premium=getattr(u, "is_premium", None))
    if user["banned"] and not is_super(uid): return
    lang = L(update); st = context.user_data.get("await")
    if st:
        return await run_user_operation(update, context, f"input:{st.get('kind')}", st.get("cid") or st.get("data", {}).get("cid"), lambda: handle_input(update, context, st))
    from newsbot.opctx import _operations
    if uid in _operations:
        from newsbot.ui_widgets import operation_text
        return await popup(update, context, operation_text(lang, "busy"), alert=True)
    if is_super(uid) and msg.reply_to_message:
        target = support_map_get(uid * 10 ** 8 + msg.reply_to_message.message_id)
        if target:
            try: await support_reply(context, uid, msg, target); await _ack(msg, tr(lang, "sup_sent"))
            except Exception as e: await msg.reply_text(tr(lang, "sup_fail", e=str(e)[:100]))
            return
    if support_is_open(uid) and not is_super(uid):
        if not rate_ok(f"sup:{uid}", 2): return
        sent = await support_forward(context, user, msg); await _ack(msg, tr(lang, "sup_received") if sent else tr(lang, "sup_fail", e="—"), InlineKeyboardMarkup([[B(tr(lang, "sup_close"), "u:man_close")]])); return
    if not user_lang(uid): return await view_lang(update, context)
    context.user_data["panel"] = None; await go_home(update, context)
