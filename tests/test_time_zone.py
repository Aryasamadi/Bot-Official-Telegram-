# -*- coding: utf-8 -*-
"""فاز ۷ — QA-10: صحت منطقه‌ی زمانی (DST، برچسب تاریخِ بی‌tz، مهاجرت آفست→IANA، نیمه‌شب محلی کانال).
تصمیم ۱۱ (الف): zoneinfo ثابت — این تست‌ها همان قرارداد ARCH-10/ARCH-11 را قفل می‌کنند."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from newsbot import config
from newsbot.core_channels import add_channel, default_settings, get_settings, in_quiet
from newsbot.core_users import channel_posts_today
from newsbot.db import q
from newsbot.discover import _entry_dt
from newsbot.extract import _parse_page
from newsbot.timeutils import (DEFAULT_TZ, _sample_offsets, ago_text, off_label, parse_dt, tz_key_of,
                               tz_name_for_offset, zone_of)

TEHRAN = timedelta(hours=3, minutes=30)


def _cid(chat_id):
    return add_channel(1, chat_id, f"tz{chat_id}", "", 1)


def test_zone_of_named_values_and_fail_safe_fallback():
    assert zone_of("3.5").utcoffset(None) == TEHRAN
    assert zone_of(4).utcoffset(None) == timedelta(hours=4)
    assert zone_of("Bogus/Zone").utcoffset(None) == timedelta(hours=config.DEFAULT_UTC_OFFSET)
    assert zone_of(None).utcoffset(None) == timedelta(hours=config.DEFAULT_UTC_OFFSET)


def test_dst_samples_are_seasonal():
    assert _sample_offsets("Europe/Berlin") == {timedelta(hours=1), timedelta(hours=2)}, "DST باید هر دو آفست را بدهد"
    assert _sample_offsets("Asia/Tehran") == {TEHRAN}, "تهران DST ندارد"


def test_parse_dt_labels_naive_dates_with_requested_zone():
    d = parse_dt("2026-01-15 10:00", "Asia/Tehran")
    assert d.utcoffset() == TEHRAN and d.hour == 10, "ساعت محلی باید حفظ شود، فقط برچسب عوض شود"
    assert parse_dt("2026-07-15 10:00", "Europe/Berlin").utcoffset() == timedelta(hours=2)
    assert parse_dt("2026-01-15 10:00", "Europe/Berlin").utcoffset() == timedelta(hours=1)
    assert parse_dt("2026-01-15 10:00").utcoffset() == timedelta(0), "بدون tz ⇒ UTC"
    assert parse_dt("2026-01-15T10:00:00Z", "Asia/Tehran").utcoffset() == timedelta(0), "تاریخِ آگاه هرگز بازبرچسب نمی‌خورد"


def test_tz_name_migration_from_legacy_offsets():
    assert tz_name_for_offset(3.5) == "Asia/Tehran"
    assert tz_name_for_offset(4.5) == "Asia/Kabul"
    assert tz_name_for_offset(0) == "UTC"
    assert tz_name_for_offset(15) == "Asia/Tehran", "آفست ناشناخته ⇒ fallback امن"
    assert tz_key_of("Asia/Tehran") == "tehran"
    assert tz_key_of(4.5) == "kabul"


def test_off_label_is_dst_aware_for_names():
    od = datetime.now(ZoneInfo("Europe/Berlin")).utcoffset()
    h = int(abs(od.total_seconds()) // 3600)
    assert off_label("Europe/Berlin") == f"UTC+{h:02d}:00", "برچسب باید آفست فصلِ جاری را نشان دهد"
    assert off_label(3.5) == "UTC+03:30" and off_label(-3) == "UTC-03:00"


def test_ago_text_clamps_future_dates():
    fut = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    assert ago_text(fut, "fa") == "0 ثانیه", "تاریخ آینده نباید «منفی» نمایش داده شود"
    assert ago_text(fut, "en") == "0s"


def test_new_channel_settings_hold_iana_zone():
    s = get_settings(_cid(-100910001))
    assert s["tz"] == DEFAULT_TZ == "Asia/Tehran"
    assert abs(s["utc_offset"] - config.DEFAULT_UTC_OFFSET) < 1e-6, "snapshot آفست از zoneinfo مشتق می‌شود"


def test_legacy_offset_row_migrates_to_iana():
    cid = _cid(-100910002)
    q("UPDATE channels SET settings=? WHERE id=?", (json.dumps({"ui_lang": "fa", "utc_offset": 4.5}), cid), commit=True)
    s = get_settings(cid)
    assert s["tz"] == "Asia/Kabul", "ردیف قدیمیِ فقط-آفست باید به نام IANA مهاجرت کند"
    assert abs(s["utc_offset"] - 4.5) < 1e-6


def test_in_quiet_uses_place_zone():
    h = datetime.now(ZoneInfo("Asia/Dubai")).hour
    assert in_quiet({"quiet_start": h, "quiet_end": (h + 1) % 24, "tz": "Asia/Dubai"}) is True
    assert in_quiet({"quiet_start": (h + 2) % 24, "quiet_end": (h + 3) % 24, "tz": "Asia/Dubai"}) is False
    assert in_quiet(default_settings("fa")) is False


def test_entry_dt_threads_channel_zone():
    iso = _entry_dt({"published": "2026-01-15 10:00"}, "Asia/Tehran")
    assert parse_dt(iso).utcoffset() == TEHRAN
    assert parse_dt(_entry_dt({"published": "2026-01-15 10:00"})).utcoffset() == timedelta(0)


def test_parse_page_labels_naive_dates_with_zone():
    html = ('<html><head><title>خبر تست</title>'
            '<meta property="article:published_time" content="2026-01-15 10:00"></head>'
            '<body><article><p>' + "متن تست برای استخراج. " * 30 + '</p></article></body></html>')
    p = _parse_page(html, "https://s.test/a", "Asia/Tehran")
    assert p["published"], "تاریخ صفحه باید استخراج شود"
    assert parse_dt(p["published"]).utcoffset() == TEHRAN


def test_channel_posts_today_uses_local_midnight():
    cid = _cid(-100910003); chat = -100910003
    z = ZoneInfo("Europe/Berlin")
    loc = datetime.now(timezone.utc).astimezone(z)
    start = datetime(loc.year, loc.month, loc.day, tzinfo=z).astimezone(timezone.utc)
    q("INSERT INTO posted(channel_id,hash,posted_at) VALUES(?,?,?)", (chat, "tz-p1", datetime.now(timezone.utc).isoformat()), commit=True)
    q("INSERT INTO posted(channel_id,hash,posted_at) VALUES(?,?,?)", (chat, "tz-p2", (start - timedelta(minutes=1)).isoformat()), commit=True)
    assert channel_posts_today(cid, "Europe/Berlin") == 1, "نیمه‌شب محلیِ منطقهٔ کانال مبناست"
    assert channel_posts_today(cid, config.DEFAULT_UTC_OFFSET) == channel_posts_today(cid, DEFAULT_TZ), "نام IANA و آفست معادل یکسان می‌شمارند"
