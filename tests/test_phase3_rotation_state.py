# -*- coding: utf-8 -*-
"""نگهبانِ «چرخشِ round-robin مدل‌ها» (فاز ۳ · ADR-۳/ADR-۴).

دو seam سنجیده می‌شود (决策ِ آگاهانه، نه تستِ internals):

1. **تابع‌های عمومیِ newsbot.rotation** — مکان‌نما، CAS و انتخابِ مدل. این‌جا همهٔ
   خطاهای «شمارشی» (پرش، تکرار، گم‌شدنِ advance در مسابقه) گرفته می‌شوند.
2. **amod.generate()** با یک transport جعلی — «یک advance در هر درخواست» و
   «خطای فراهم‌کننده مکان‌نما را جلو نمی‌برد» (ADR-۴).

هیچ تستی به شبکه دست نمی‌زند و هیچ راز/کلیدی در فیکسچرها نیست.
"""
import asyncio

import pytest


# ── Task 1: وضعیتِ چرخش و CAS ─────────────────────────────────────────────────────
def test_rotation_row_is_created_and_advanced_with_cas(isolated_db):
    from newsbot import rotation

    row = rotation.rotation_ensure("public")
    assert row["cursor"] == 0 and row["version"] == 0

    assert rotation.rotation_set("public", 1, 0) is True      # نسخهٔ درست ⇒ می‌پذیرد
    assert rotation.rotation_set("public", 2, 0) is False     # نسخهٔ کهنه ⇒ رد می‌شود
    assert rotation.rotation_get("public")["cursor"] == 1
    assert rotation.rotation_get("public")["version"] == 1


def test_rotation_log_rows_are_written_and_read_back_newest_first(isolated_db):
    from newsbot import rotation

    rotation.rotation_ensure("public")
    rotation.rotation_log_add("public", 7, 0, 3, 0)
    rotation.rotation_log_add("public", 9, 1, 3, 1)

    rows = rotation.rotation_log_recent("public", limit=5)
    assert [r["model_id"] for r in rows] == [9, 7]
    assert rows[0]["idx"] == 1 and rows[0]["pool_size"] == 3