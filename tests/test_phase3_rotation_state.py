# -*- coding: utf-8 -*-
"""نگهبانِ «چرخشِ round-robin مدل‌ها» (فاز ۳ · ADR-۳/ADR-۴).

دو seam سنجیده می‌شود (决策ِ آگاهانه، نه تستِ internals):

1. **تابع‌های عمومیِ newsbot.rotation** — مکان‌نما، CAS و انتخابِ مدل. این‌جا همهٔ
   خطاهای «شمارشی» (پرش، تکرار، گم‌شدنِ advance در مسابقه) گرفته می‌شوند.
2. **amod.generate()** با یک transport جعلی — «یک advance در هر درخواست» و
   «خطای فراهم‌کننده مکان‌نما را جلو نمی‌برد» (ADR-۴).

هیچ تستی به شبکه دست نمی‌زند و هیچ راز/کلیدی در فیکسچرها نیست.
"""


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


# ── Task 2: انتخابِ محض — این درخواست کدام مدل را می‌گیرد؟ ─────────────────────────
def test_pick_rotation_is_stable_ordered_and_wraps():
    from newsbot.rotation import order_pool, pick_rotation

    pool = [{"id": 3, "priority": 10}, {"id": 1, "priority": 10}, {"id": 2, "priority": 5}]
    assert [m["id"] for m in order_pool(pool)] == [2, 1, 3]      # اول priority، بعد id
    assert pick_rotation(pool, 0)[0]["id"] == 2
    assert pick_rotation(pool, 1)[0]["id"] == 1
    assert pick_rotation(pool, 2)[0]["id"] == 3
    assert pick_rotation(pool, 3)[0]["id"] == 2                  # دور می‌زند


def test_pick_rotation_survives_a_shrinking_pool():
    """ADR-۳: کوچک‌شدنِ استخر نباید مدل را رد کند یا دو بار انتخاب کند (modulo با طولِ فعلی)."""
    from newsbot.rotation import pick_rotation

    big = [{"id": i, "priority": 10} for i in range(1, 6)]
    assert pick_rotation(big, 4)[0]["id"] == 5
    small = big[:2]
    assert pick_rotation(small, 4)[1] == 0        # ۴ % ۲ == ۰
    assert pick_rotation(small, 4)[0]["id"] == 1
    assert pick_rotation(small, 5)[1] == 1        # ۵ % ۲ == ۱


def test_pick_rotation_on_empty_pool_returns_none():
    from newsbot.rotation import pick_rotation

    assert pick_rotation([], 7) == (None, 0)


def test_order_pool_treats_a_missing_priority_as_lowest():
    from newsbot.rotation import order_pool

    pool = [{"id": 1, "priority": None}, {"id": 2, "priority": 1}, {"id": 3}]
    assert [m["id"] for m in order_pool(pool)] == [2, 1, 3]


# ── Task 3: advanceِ پایدار با CAS، fail-open، و خودترمیمی ─────────────────────────
def test_advance_moves_the_cursor_once_per_call(isolated_db):
    from newsbot import rotation

    pool = [{"id": i, "priority": 10} for i in range(1, 4)]
    m1, i1, v1 = rotation.advance("public", pool)
    m2, i2, v2 = rotation.advance("public", pool)
    assert [m1["id"], m2["id"]] == [1, 2]
    assert (i1, i2) == (0, 1)
    assert v2 == v1 + 1


def test_advance_is_safe_when_several_callers_race(isolated_db):
    """ADR-۳: باختِ مسابقه باید تلاشِ دوباره بدهد، نه اینکه advance را گم کند."""
    import threading

    from newsbot import rotation

    pool = [{"id": i, "priority": 10} for i in range(1, 4)]
    seen, errors = [], []

    def worker():
        try:
            for _ in range(5):
                m, _i, _v = rotation.advance("public", pool)
                seen.append(m["id"])
        except Exception as e:                      # فقط با باگِ واقعی
            errors.append(repr(e))

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    assert len(seen) == 20
    assert rotation.rotation_get("public")["version"] == 20     # هر فراخوانی دقیقاً یک advance


def test_advance_on_a_missing_row_self_heals(isolated_db):
    from newsbot import rotation
    from newsbot.db import q

    q("DELETE FROM ai_rotation WHERE scope='public'", commit=True)
    m, _i, _v = rotation.advance("public", [{"id": 9, "priority": 10}])
    assert m["id"] == 9
    assert rotation.rotation_get("public") is not None


def test_advance_on_an_empty_pool_writes_nothing(isolated_db):
    from newsbot import rotation
    from newsbot.db import q

    q("DELETE FROM ai_rotation WHERE scope='public'", commit=True)
    m, idx, ver = rotation.advance("public", [])
    assert (m, idx) == (None, 0)
    row = rotation.rotation_get("public")
    assert row["cursor"] == 0 and row["version"] == 0      # بدونِ advance
