# -*- coding: utf-8 -*-
"""سمافورهای سراسری و قفل‌های هر کانال / هر مدل.
پاک‌سازی قفل‌های بی‌صاحب با import تنبل انجام می‌شود تا وابستگی دایره‌ای
با core_channels و ai_models ساخته نشود.
"""
import asyncio
from newsbot import config

AI_SEM = asyncio.Semaphore(config.AI_CONCURRENCY)
FETCH_SEM = asyncio.Semaphore(config.FETCH_CONCURRENCY)
CYCLE_SEM = asyncio.Semaphore(config.CYCLE_CONCURRENCY)

_ch_locks = {}
_model_locks = {}
_user_op_locks = {}

def ch_lock(cid):
    """قفل هر کانال: هرگز دو چرخه (تست/خودکار/انتشار دستی) هم‌زمان روی یک کانال اجرا نمی‌شود."""
    if len(_ch_locks) > 200:   # قفلِ کانالِ حذف‌شده تا ابد می‌ماند؛ قفل‌های آزادِ بی‌صاحب پاک می‌شوند
        from newsbot.core_channels import get_channel
        for k in [k for k, v in _ch_locks.items() if k != cid and not v.locked() and not get_channel(k)]: _ch_locks.pop(k, None)
    if cid not in _ch_locks: _ch_locks[cid] = asyncio.Lock()
    return _ch_locks[cid]

def model_lock(mid):
    """قفل هر مدل: دو فراخوان هم‌زمان به یک مدل (که بعضی سرویس‌ها تحمل نمی‌کنند) جلوگیری می‌شود."""
    if len(_model_locks) > 200:   # همان قاعدهٔ ch_lock برای مدل‌های حذف‌شده
        from newsbot.ai_models import get_model
        for k in [k for k, v in _model_locks.items() if k != mid and not v.locked() and not get_model(k)]: _model_locks.pop(k, None)
    if mid not in _model_locks: _model_locks[mid] = asyncio.Lock()
    return _model_locks[mid]

def model_busy(mid): return model_lock(mid).locked()

def user_op_lock(uid):
    """BIZ-05: هر کاربر حداکثر یک عملیات مالی/پلنِ در لحظه (پرداخت، پلن رایگان، درخواست خرید).
    ربات نه مفهوم «دستگاه» دارد و نه نشست چندگانهٔ واقعی؛ سقف معنادار = قفلِ هر کاربر روی
    همین عملیات‌های حساس است تا TOCTOU های read-then-act به هم نخورند (CASها لایهٔ دوم)."""
    if len(_user_op_locks) > 500:
        for k in [k for k, v in _user_op_locks.items() if not v.locked()]: _user_op_locks.pop(k, None)
    if uid not in _user_op_locks: _user_op_locks[uid] = asyncio.Lock()
    return _user_op_locks[uid]
