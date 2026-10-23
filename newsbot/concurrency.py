# -*- coding: utf-8 -*-
"""سمافورهای سراسری و قفل‌های هر کانال / هر مدل.

PH-07/PERF-002: pruning is amortised, bounded and DB-free — a lock acquisition never
runs a synchronous query on the event-loop thread. Idle locks expire after a TTL; a hard
cap evicts the oldest idle entries when the store grows too large.
"""
import asyncio, time, weakref
from newsbot import config

AI_SEM = asyncio.Semaphore(config.AI_CONCURRENCY)
FETCH_SEM = asyncio.Semaphore(config.FETCH_CONCURRENCY)
CYCLE_SEM = asyncio.Semaphore(config.CYCLE_CONCURRENCY)
_PUBLISH_SEMS = weakref.WeakKeyDictionary()


def publish_sem():
    """PH-07/PERF-007: one upload semaphore per event loop (tests may run several loops)."""
    loop = asyncio.get_running_loop()
    sem = _PUBLISH_SEMS.get(loop)
    if sem is None:
        sem = _PUBLISH_SEMS[loop] = asyncio.Semaphore(config.PUBLISH_CONCURRENCY)
    return sem

_LOCK_SOFT_LIMIT = 256
_LOCK_HARD_LIMIT = 512
_LOCK_TTL_SEC = 900
_PRUNE_EVERY = 64

_ch_locks = {}
_model_locks = {}
_user_op_locks = {}
_ch_last_used = {}
_model_last_used = {}
_user_op_last_used = {}
_prune_calls = {}


def _prune(store, last_used, calls_key, keep_key):
    """Evict unlocked/idle entries, amortised to once every N accesses.

    No DB lookup and no work proportional to the whole store on the common path.
    """
    calls = _prune_calls.get(calls_key, 0) + 1
    _prune_calls[calls_key] = calls
    over_soft = len(store) > _LOCK_SOFT_LIMIT
    over_hard = len(store) >= _LOCK_HARD_LIMIT
    if not over_soft or (calls % _PRUNE_EVERY and not over_hard):
        return
    now = time.monotonic()
    idle = []
    for k, lock in list(store.items()):
        if k == keep_key or lock.locked():
            continue
        if now - last_used.get(k, 0.0) >= _LOCK_TTL_SEC:
            store.pop(k, None)
            last_used.pop(k, None)
        else:
            idle.append((last_used.get(k, 0.0), k))
    if len(store) >= _LOCK_HARD_LIMIT:
        idle.sort()
        target = max(_LOCK_SOFT_LIMIT, _LOCK_HARD_LIMIT // 2)
        for _ts, k in idle[:max(0, len(store) - target)]:
            store.pop(k, None)
            last_used.pop(k, None)


def _get_lock(store, last_used, calls_key, key):
    now = time.monotonic()
    lock = store.get(key)
    if lock is None:
        _prune(store, last_used, calls_key, key)
        lock = store.get(key)
        if lock is None:
            lock = asyncio.Lock()
            store[key] = lock
    last_used[key] = now
    return lock


def ch_lock(cid):
    """قفل هر کانال: هرگز دو چرخه (تست/خودکار/انتشار دستی) هم‌زمان روی یک کانال اجرا نمی‌شود."""
    return _get_lock(_ch_locks, _ch_last_used, "ch", cid)


def model_lock(mid):
    """قفل هر مدل: دو فراخوان هم‌زمان به یک مدل (که بعضی سرویس‌ها تحمل نمی‌کنند) جلوگیری می‌شود."""
    return _get_lock(_model_locks, _model_last_used, "model", mid)


def model_busy(mid): return model_lock(mid).locked()


def user_op_lock(uid):
    """BIZ-05: هر کاربر حداکثر یک عملیات مالی/پلنِ در لحظه (پرداخت، پلن رایگان، درخواست خرید).
    ربات نه مفهوم «دستگاه» دارد و نه نشست چندگانهٔ واقعی؛ سقف معنادار = قفلِ هر کاربر روی
    همین عملیات‌های حساس است تا TOCTOU های read-then-act به هم نخورند (CASها لایهٔ دوم)."""
    return _get_lock(_user_op_locks, _user_op_last_used, "user_op", uid)
