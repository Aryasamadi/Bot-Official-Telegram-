# -*- coding: utf-8 -*-
"""PIPE-017: سقف روزانهٔ هر کانال باید در زمان رزرو (reserve_publication) به‌صورت اتمیک اعمال شود."""
from newsbot.core_articles import article_insert, release_publication, reserve_publication
from newsbot.core_channels import add_channel, list_channels, update_settings
from newsbot.core_users import remaining


def test_channel_daily_cap_enforced_atomically_at_reservation_time():
    uid = 1   # سوپرادمین: سقف پلن بی‌نهایت است تا فقط سقف خودِ کانال (daily_posts_cap=2) آزموده شود
    add_channel(uid, -100920001, "pipe017_ch1", "Pipe017 Ch1", 1)
    ch = [c for c in list_channels(uid) if c["chat_id"] == -100920001][0]
    cid = ch["id"]
    update_settings(cid, daily_posts_cap=2, tz="UTC")

    a1 = article_insert(uid, cid, "h-p17-1", "https://example.com/p1", "T1", 0, None, status="ready")
    a2 = article_insert(uid, cid, "h-p17-2", "https://example.com/p2", "T2", 0, None, status="ready")
    a3 = article_insert(uid, cid, "h-p17-3", "https://example.com/p3", "T3", 0, None, status="ready")

    j1 = reserve_publication(a1, uid, True, None, None)
    assert j1 is not None and j1["ch_id"] == cid and j1["ch_reserved_day"] is not None
    assert remaining(uid, "posts", cid) == 1

    # حتی قبل از درج در posted، رزرو دوم سقف کانال را پر می‌کند
    j2 = reserve_publication(a2, uid, True, None, None)
    assert j2 is not None
    assert remaining(uid, "posts", cid) == 0

    # رزرو سوم باید به خاطر سقف کانال رد شود (حتی وقتی سقف پلن بی‌نهایت است)
    assert reserve_publication(a3, uid, True, None, None) is None

    # آزادسازی رزرو اول (قبل از in_flight) یک واحد به سقف کانال برمی‌گرداند
    release_publication(a1, j1, "send_failed")
    assert remaining(uid, "posts", cid) == 1
    j3 = reserve_publication(a3, uid, True, None, None)
    assert j3 is not None
