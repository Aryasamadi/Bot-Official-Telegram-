# -*- coding: utf-8 -*-
"""زمینه‌ی عملیات per-task + قفل‌های هر کاربر + ثبت مرحله‌ها.
این ماژول هیچ وابستگی به لایه‌ی UI یا موتور ندارد (فقط logutils) تا
هسته‌ی داده (core_articles/publish/cycle) بدون وابستگی دایره‌ای از آن استفاده کنند.
"""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
import time

_current_operation = ContextVar("current_operation", default=None)
_operations, _user_controls = {}, {}
CLEANUP_HOOKS = []     # هوک‌های پاک‌سازی دوره‌ای (ui_widgets sweeper خودش را ثبت می‌کند)


@dataclass
class Operation:
    user: int
    channel: int | None
    operation_type: str
    chat_id: int
    task: asyncio.Task | None = None
    cancelled: bool = False
    started: bool = False
    replaced_status: bool = False
    published_count: int = 0
    failed: bool = False
    status_message_id: int | None = None
    status_text: str = ""
    final_status: bool = False
    status_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    oid: str = ""            # شناسهٔ کوتاه همبستگی (در همهٔ لاگ‌های همین عملیات می‌آید)
    thinking_step: int = 0   # اسپینر Thinking/Working
    thinking_task: asyncio.Task | None = None
    terminalized: bool = False
    status_display: str = ""
    steps: list = field(default_factory=list)   # هر قدم: {category,key,status,variant,model,fmt,entered}
    header_kind: str = "thinking"               # thinking | working | none
    t0: float = field(default_factory=time.time)


def operation_checkpoint():
    """اگر عملیات لغو شده باشد، هر نقطهٔ بررسی بلافاصله CancelledError می‌دهد."""
    op = _current_operation.get()
    if op and op.cancelled: raise asyncio.CancelledError


def clear_interaction(context, keep=()):
    for key in ("await", "notice", "disc", "bc_src", "qs", "qs_channel", "status_message_id"):
        if key not in keep:
            context.user_data.pop(key, None)


def user_control(uid):
    """قفل هر کاربر: دو عملیات هم‌زمان یک کاربر هرگز اجرا نمی‌شوند."""
    if uid not in _user_controls: _user_controls[uid] = asyncio.Lock()
    return _user_controls[uid]


def cleanup_controls():
    """قفل‌های آزادِ بی‌صاحب پاک می‌شوند (قفلِ در حال استفاده هرگز)."""
    free = [k for k, v in list(_user_controls.items()) if not v.locked()]
    if len(free) > 5000:
        for k in free[:len(free) - 5000]: _user_controls.pop(k, None)
CLEANUP_HOOKS.append(cleanup_controls)


def step_add(op, category, key=None, status="run", model=None, **fmt):
    """سابقه‌ی مرحله‌ای با سه وارینت از هر دسته؛ upsert بر (category,key)."""
    if op is None: return
    steps = getattr(op, "steps", None)
    if steps is None: return
    rec = None
    for st in reversed(steps):
        if st.get("category") == category and st.get("key") == key: rec = st; break
    if rec is None:
        rec = {"category": category, "key": key, "variant": __import__("random").randrange(3), "entered": time.time()}
        steps.append(rec)
    rec.update(status=status, model=model, fmt=fmt or {})
    # Thinking غالب می‌ماند: فقط مرحله‌ی تولید و انتشار Working است؛ هر مرحله‌ی بررسی/منبع، هدر را به Thinking برمی‌گرداند
    if category.startswith(("ai_", "model_")) or category in ("post_gen", "post_ready", "published", "queued", "queue"):
        op.header_kind = "working"
    elif category.startswith("src_") or category in ("extract", "low", "found", "ad", "prep"):
        op.header_kind = "thinking"
