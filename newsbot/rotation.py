# -*- coding: utf-8 -*-
"""مکان‌نمای چرخشِ مدل‌ها (فاز ۳ · ADR-۳/ADR-۴).

ADR-۳ می‌گوید مکان‌نما باید **در دیتابیس و با CAS** باشد، نه در حافظهٔ پروسه: ربات می‌تواند
چند worker داشته باشد و ری‌استارت هم نباید جای قبلی را گم کند.ADR-۴ می‌گوید چرخش **failover
نیست**: یک advance در هر درخواست، نه به‌ازای هر تلاش.

این ماژول فقط همین دو چیز را می‌داند: «کجا هستیم» (ai_rotation) و «چه شد» (ai_rotation_log).
انتخابِ مدل و مسیرِ نشر در ai_models/generate می‌مانند.
"""
from newsbot.db import q, qrow
from newsbot.timeutils import now_iso

# محدودهٔ چرخش. فعلاً فقط «public» (استخرِ عمومیِ مقاله‌ها)؛ مدلِ شخصی و مدلِ تستِ
# پین‌شده عمداً خارج‌اند: انتخابِ صریحِ مدیر/کاربر را نباید چرخش بازنویسی کند.
ROTATION_SCOPES = ("public",)

_ADVANCE_RETRIES = 5      # تلاشِ دوباره روی باختِ مسابقهٔ CAS


def rotation_get(scope):
    return q("SELECT scope, cursor, version, updated_at FROM ai_rotation WHERE scope=?", (scope,), one=True)


def rotation_set(scope, cursor, expected_version) -> bool:
    """CAS: فقط اگر نسخهٔ فعلی همان ``expected_version`` باشد جلو می‌رود.

    خروجی ``True`` یعنی این تماس برندهٔ مسابقه بود؛ ``False`` یعنی کسی زودتر advance کرده و
    باید دوباره خواند و تلاش کرد (ADR-۳).
    """
    cur = qrow("UPDATE ai_rotation SET cursor=?, version=version+1, updated_at=? WHERE scope=? AND version=?",
           (int(cursor), now_iso(), scope, int(expected_version)))
    return cur == 1


def rotation_ensure(scope):
    """ردیفِ مکان‌نما را می‌سازد اگر نبود (idempotent) و ردیفِ فعلی را برمی‌گرداند."""
    q("INSERT OR IGNORE INTO ai_rotation(scope, cursor, version, updated_at) VALUES(?,0,0,?)",
      (scope, now_iso()), commit=True)
    row = rotation_get(scope)
    if row is None:                      # حفاظتِ مضاعف: هرگز None به决策‌کننده برنمی‌گردد
        q("INSERT OR REPLACE INTO ai_rotation(scope, cursor, version, updated_at) VALUES(?,0,0,?)",
          (scope, now_iso()), commit=True)
        row = rotation_get(scope)
    return row


def rotation_log_add(scope, model_id, idx, pool_size, version) -> int:
    """یک ردیفِ audit به‌ازای هر درخواست (ADR: «per-request audit»)."""
    return q("INSERT INTO ai_rotation_log(ts, scope, model_id, idx, pool_size, version) VALUES(?,?,?,?,?,?)",
             (now_iso(), scope, int(model_id), int(idx), int(pool_size), int(version)), commit=True)


def rotation_log_recent(scope, limit=20):
    return q("SELECT * FROM ai_rotation_log WHERE scope=? ORDER BY id DESC LIMIT ?", (scope, int(limit)))


def rotation_stats(scope):
    row = rotation_get(scope) or {}
    n = q("SELECT COUNT(*) AS n FROM ai_rotation_log WHERE scope=?", (scope,), one=True)
    return {"cursor": int(row.get("cursor") or 0), "version": int(row.get("version") or 0),
            "log_rows": int(n["n"]) if n else 0}


# ── انتخابِ محض (بدونِ دیتابیس، بدونِ شبکه) ────────────────────────────────────────
def order_pool(models):
    """ترتیبِ چرخش: اول ``priority`` (کم‌تر = زودتر)، بعد ``id`` — قطعی و قابلِ پیش‌بینی.

    ``priority`` گم‌شده/None به‌عنوان «کم‌ترین اولویت» (۹۹) گرفته می‌شود تا یک ردیفِ
    ناقص، کلِ چرخش را به‌هم نریزد.
    """
    return sorted(models, key=lambda m: (m["priority"] if m.get("priority") is not None else 99,
                                         m["id"]))


def pick_rotation(pool, cursor):
    """مدلِ این درخواست و اندیسِ آن در استخرِ **مرتب‌شده**.

    اندیس با ``cursor % len(pool)`` گرفته می‌شود، نه با یک اندیسِ مطلقِ ذخیره‌شده: اگر
    مدلی غیرفعال/در backoff شود، نباید یک مدل رد شود یا دو بار انتخاب شود (ADR-۳).
    """
    ordered = order_pool(pool)
    if not ordered:
        return None, 0
    idx = int(cursor) % len(ordered)
    return ordered[idx], idx
