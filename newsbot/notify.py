# -*- coding: utf-8 -*-
"""اعلان‌های هسته (مدیر کلان و کاربر) — پیاده‌سازی تلگرامی در post_init سیم‌کشی می‌شود.
لایه‌ی هسته هرگز مستقیم به telegram وابسته نیست؛ فقط تابع‌های ثبت‌شده در newsbot/runtime
صدا زده می‌شوند (ARCH-05 — فاز ۱۲: این state از ماژول پیکربندی خارج شد).

ARCH-09 (فاز ۱۳): اعلان‌های مدیران کلان صفِ محدود + ری‌ترای نمایی دارند. صف فقط وقتی فعال
است که worker در post_init اپلیکیشن استارت شده باشد؛ در نبودش رفتار قبلی (تلاش مستقیم
تک‌شات) حفظ می‌شود تا فراخوانی‌های خارج از اپلیکیشن از کار نیفتند.
"""
import asyncio
import json
import time
from newsbot import config
from newsbot import runtime
from newsbot.logutils import log, redact

SUPER_QUEUE_MAX = 100                  # صف فقط «بیدارباش» است؛ پیام در DB ذخیره می‌شود
SUPER_RETRY_DELAYS = (1.0, 3.0, 8.0)   # پس از تلاش اول؛ جمعاً ۴ تلاش با فاصله‌های نمایی
_WORKER_GRACE_SEC = 2.0                # فرصت worker برای تحویل اولیه پیش از رقابت زمان‌بند
_DIRECT_GRACE_SEC = 30.0               # بدون worker: فرصت تلاش مستقیم پیش از رقابت زمان‌بند
_super_q = None
_super_worker = None
_inflight = set()                     # idهایی که همین لحظه در حال ارسال‌اند (ضد ارسال تکراری)


def _queue():
    if _super_q is None:
        globals()["_super_q"] = asyncio.Queue(maxsize=SUPER_QUEUE_MAX)
    return _super_q


def super_queue_size():
    """اندازهٔ فعلی صفِ بیدارباش (برای پایش/تست؛ بدون ساخت صف)."""
    return _super_q.qsize() if _super_q is not None else 0


def is_inflight(nid):
    """آیا همین id در حال ارسال است؟ زمان‌بند برای جلوگیری از ارسال موازی می‌پرسد."""
    return int(nid) in _inflight


def ensure_super_worker():
    """worker را (دوباره) راه می‌اندازد — در post_init اپلیکیشن صدا زده می‌شود."""
    global _super_worker
    if _super_worker is None or _super_worker.done():
        _super_worker = asyncio.get_running_loop().create_task(_super_loop(), name="notify-super")
    return _super_worker


def _result_delivered(res):
    """قرارداد تحویل: None = سازگاری با runtimeهای قدیمی، عدد = تعداد گیرندهٔ موفق."""
    if res is None:
        return True
    if isinstance(res, bool):
        return res
    try:
        return int(res) > 0
    except (TypeError, ValueError):
        return bool(res)


async def _send_super_once(text, kb):
    """یک تلاش؛ True فقط اگر دستِ‌کم یک گیرنده تحویل را تأیید کند."""
    if not runtime.NOTIFY_SUPER:
        return False
    return _result_delivered(await runtime.NOTIFY_SUPER(text, kb))


async def _send_super_with_retry(text, kb):
    """ارسال با ری‌ترای نمایی؛ خروجی True اگر (حتی با تلاش دوباره) واقعاً رسید."""
    attempts = len(SUPER_RETRY_DELAYS) + 1
    last = None
    for i in range(attempts):
        if i:
            await asyncio.sleep(SUPER_RETRY_DELAYS[i - 1])
        try:
            if await _send_super_once(text, kb):
                return True
            last = RuntimeError("no super admin received the message")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last = e
    log.error("notify super: پس از %d تلاش ناموفق ماند (%s)", attempts, type(last).__name__)
    return False


def _mark_delivered(nid):
    if nid is None:
        return
    try:
        from newsbot.core_alerts import alert_mark_delivered
        alert_mark_delivered(nid)
    except Exception as e:
        log.error("notify super: ثبت تحویل ناموفق بود id=%s (%s)", nid, type(e).__name__)


def _mark_failed(nid, err):
    if nid is None:
        return
    try:
        from newsbot.core_alerts import alert_mark_failed
        alert_mark_failed(nid, err)
    except Exception as e:
        log.error("notify super: ثبت شکست ناموفق بود id=%s (%s)", nid, type(e).__name__)


async def _super_loop():
    q = _queue()
    while True:
        item = await q.get()
        nid = None
        try:
            if isinstance(item, tuple) and len(item) >= 3:
                nid, text, kb = item[0], item[1], item[2]
            else:
                text, kb = item
            if nid is not None:
                _inflight.add(int(nid))
            ok = await _send_super_with_retry(text, kb)
            if nid is not None:
                if ok:
                    _mark_delivered(nid)
                else:
                    _mark_failed(nid, "delivery failed")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("notify super worker: %s", type(e).__name__)
            _mark_failed(nid, type(e).__name__)
        finally:
            if nid is not None:
                _inflight.discard(int(nid))
            q.task_done()


async def notify_super(text, kb=None):
    """پیام به همهٔ مدیران کلان؛ **پیش از هر ارسال** در outbox پایدار ثبت می‌شود.

    بنابراین سرریز صف، خاموشی یا crash هیچ پیامی را گم نمی‌کند: worker تلاش اول را
    می‌کند و زمان‌بند بقیه را با backoff می‌فرستد. متن پیش از ذخیره/ارسال redact است.
    """
    if not runtime.NOTIFY_SUPER:
        return False
    body = redact(text)
    worker = _super_worker is not None and not _super_worker.done()
    grace = _WORKER_GRACE_SEC if worker else _DIRECT_GRACE_SEC
    try:
        from newsbot.core_alerts import alert_enqueue
        nid = alert_enqueue(body, kb, next_attempt_at=time.time() + grace)
    except Exception as e:
        # حتی اگر outbox هم در دسترس نباشد، تلاش مستقیم بهتر از سکوت است.
        log.error("notify super: outbox unavailable (%s); direct attempt", type(e).__name__)
        try:
            return await _send_super_once(body, kb)
        except Exception:
            return False
    if worker:
        try:
            _queue().put_nowait((nid, body, kb))
        except asyncio.QueueFull:
            log.warning("notify super: wake queue full; durable id=%s will be retried", nid)
        return True
    try:
        ok = await _send_super_once(body, kb)
    except Exception as e:
        ok = False
        log.warning("notify super: direct attempt failed (%s)", type(e).__name__)
    if ok:
        _mark_delivered(nid)
    else:
        _mark_failed(nid, "delivery failed")
    return ok


async def deliver_pending_alerts(limit=10):
    """یک تلاش برای هر اعلانِ سررسیدشده (زمان‌بند) — id در حال ارسال دوباره فرستاده نمی‌شود."""
    if not runtime.NOTIFY_SUPER:
        return 0, 0
    from newsbot.core_alerts import alert_pending, alert_mark_delivered, alert_mark_failed
    from newsbot.db import arun
    delivered = failed = 0
    for a in await arun(alert_pending, limit):
        nid = int(a["id"])
        if nid in _inflight:
            continue
        _inflight.add(nid)
        try:
            kb = (json.loads(a["payload"]) or {}).get("kb") if a["payload"] else None
            if await _send_super_once(a["text"], kb):
                await arun(alert_mark_delivered, nid)
                delivered += 1
            else:
                await arun(alert_mark_failed, nid, "no super admin received the message")
                failed += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await arun(alert_mark_failed, nid, type(e).__name__)
            failed += 1
            log.warning("alert delivery failed id=%s (%s)", nid, type(e).__name__)
        finally:
            _inflight.discard(nid)
    return delivered, failed


async def drain_super_queue(timeout=10.0):
    """انتظار تا تخلیهٔ صفِ بیدارباش (تست‌ها)."""
    await asyncio.wait_for(_queue().join(), timeout)


async def stop_super_worker():
    """توقف تمیز worker (خاموشی اپلیکیشن/تست)."""
    global _super_worker
    t, _super_worker = _super_worker, None
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except BaseException:
            pass


async def notify_user(uid, text, kb=None):
    """ارسال پیام به یک کاربر؛ True فقط وقتی تحویل تأیید شده است.
    در حالت زمان‌بند (SCHED_NOTIF_FAILURES) شکست بالا می‌رود تا _plan_notifications
    کلید هشدار را آزاد کند و تیک بعدی دوباره تلاش کند."""
    if not runtime.NOTIFY_USER:
        return False
    try:
        return _result_delivered(await runtime.NOTIFY_USER(uid, text, kb))
    except Exception as e:
        log.warning("notify user failed (%s)", type(e).__name__)
        if config.SCHED_NOTIF_FAILURES.get():
            raise   # scheduled-warning failures must reach _plan_notifications' release branch
        return False
