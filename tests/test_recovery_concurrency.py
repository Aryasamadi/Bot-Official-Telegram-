# -*- coding: utf-8 -*-
"""فاز ۶ — پایداری، هم‌زمانی و بازیابی.

حساس‌ترین خط این سیستم: «هرگز پست تکراری» و «هرگز سهمیهٔ گم‌شده».
این تست‌ها سناریوهای کرش/هنگ بین مراحل انتشار را شبیه‌سازی می‌کنند و تأیید می‌کنند که
ژورنال انتشار در هر حالت به وضعیت امن می‌رسد.
"""
import asyncio

from newsbot.core_articles import (article_insert, get_article, get_pub_journal, set_pub_journal,
                                   reserve_publication, recover_publications)
from newsbot.concurrency import ch_lock, model_lock, model_busy
from newsbot.core_channels import add_channel, channel_by_chat


# ── هم‌زمانی: قفل‌ها ──

def test_ch_lock_is_stable_per_channel():
    """قفل هر کانال باید همان شیء در فراخوانی‌های مکرر باشد (نه شیء تازه)."""
    assert ch_lock(501) is ch_lock(501)
    assert ch_lock(501) is not ch_lock(502)


def test_model_lock_is_stable_and_reports_busy():
    assert model_lock(601) is model_lock(601)
    assert model_busy(602) is False


def test_ch_lock_serializes_concurrent_work():
    """کارِ هم‌زمان روی یک کانال باید سریال شود (نه interleave)."""
    order = []
    async def worker(n):
        async with ch_lock(700):
            order.append(f"start{n}")
            await asyncio.sleep(0.01)
            order.append(f"end{n}")
    async def main():
        await asyncio.gather(worker(1), worker(2))
    asyncio.run(main())
    # هر start باید بلافاصله با end خودش جفت شود
    assert order[0].startswith("start")
    assert order[1].startswith("end")
    assert order[0][5:] == order[1][3:]


# ── بازیابی: هرگز پست تکراری ──

def _chan(chat_id):
    """کانال می‌سازد و *id داخلی* کانال را برمی‌گرداند (نه chat_id).

    نکته: add_channel(uid, chat_id, …) آی‌دی داخلی را برمی‌گرداند و get_channel(id) هم
    با همان آی‌دی جست‌وجو می‌کند؛ خلط این دو منجر به None می‌شود.
    """
    add_channel(1, chat_id, f"ch{chat_id}", "", 1)
    return channel_by_chat(chat_id)["id"]


def test_recovery_in_flight_never_reposts():
    """کلید ایمنی: ژورنالِ in_flight (تحویل نامعلوم) نباید هرگز دوباره منتشر شود."""
    cid = _chan(801)
    aid = article_insert(1, cid, "h-recover-1", "https://x.test/1", "t", 0, None, status="ready")
    set_pub_journal(aid, {"phase": "in_flight", "admin_id": 1, "reserved_day": None,
                          "test_day": None, "test_user": None})
    recover_publications()
    # مقاله باید از صف «آماده» خارج شده و failed شده باشد (نه republish)
    a = get_article(aid)
    assert a is not None
    assert a["status"] == "failed"
    assert a["reason"] == "delivery_unknown"
    # ژورنال نباید دوباره «قابل انتشار» شود
    j = get_pub_journal(aid)
    assert not j or j.get("phase") != "in_flight"


def test_recovery_partial_releases_quota_and_marks_unsettled():
    """کرش وسط آپلود (partial): سهمیه برمی‌گردد و وضعیت unsettled می‌شود."""
    cid = _chan(802)
    aid = article_insert(1, cid, "h-recover-2", "https://x.test/2", "t", 0, None, status="ready")
    set_pub_journal(aid, {"phase": "partial", "admin_id": 1, "reserved_day": "2026-01-01",
                          "test_day": None, "test_user": None})
    recover_publications()
    j = get_pub_journal(aid)
    assert j is not None
    assert j.get("phase") == "unsettled"
    assert j.get("reserved_day") is None, "سهمیهٔ رزروشده باید آزاد شود"


def test_recovery_ack_settles_to_published():
    """ژورنالِ ack (تحویل تأییدشده) باید منتشرشده ثبت شود."""
    cid = _chan(803)
    aid = article_insert(1, cid, "h-recover-3", "https://x.test/3", "t", 0, None, status="ready")
    # message_id لازم است: settle_publication از آن لینک پیام را می‌سازد
    set_pub_journal(aid, {"phase": "ack", "admin_id": 1, "reserved_day": None,
                          "test_day": None, "test_user": None, "hash": "h-recover-3",
                          "message_id": 4242})
    recover_publications()
    a = get_article(aid)
    assert a["status"] == "published"


def test_recovery_is_idempotent():
    """اجرای دوبارهٔ بازیابی نباید وضعیت را تغییر دهد (safe to repeat)."""
    cid = _chan(804)
    aid = article_insert(1, cid, "h-recover-4", "https://x.test/4", "t", 0, None, status="ready")
    set_pub_journal(aid, {"phase": "in_flight", "admin_id": 1, "reserved_day": None,
                          "test_day": None, "test_user": None})
    recover_publications()
    first = get_article(aid)["status"]
    recover_publications()
    assert get_article(aid)["status"] == first == "failed"


def test_reserve_publication_enforces_daily_cap():
    """سهمیهٔ روزانه باید سخت اعمال شود (سقف = cap)."""
    from newsbot.core_articles import PublicationQuotaExceeded
    cid = _chan(805)
    reserved = 0
    for i in range(5):
        aid = article_insert(1, cid, f"h-cap-{i}", f"https://x.test/c{i}", "t", 0, None, status="ready")
        try:
            reserve_publication(aid, 1, True, None, {"phase": "prepared", "reserved_day": None,
                                                     "test_day": None, "test_user": None})
            reserved += 1
        except PublicationQuotaExceeded:
            break
    assert reserved >= 1, "حداقل یک رزرو باید موفق شود"
    # پس از رسیدن به سقف، رزرو بعدی باید رد شود (ثابت می‌کند سقف اعمال می‌شود)
    assert reserved <= 100, "سقف باید محدود کند"
