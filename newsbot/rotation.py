# -*- coding: utf-8 -*-
"""مکان‌نمای چرخشِ مدل‌ها (فاز ۳ · ADR-۳/ADR-۴).

ADR-۳ می‌گوید مکان‌نما باید **در دیتابیس و با CAS** باشد، نه در حافظهٔ پروسه: ربات می‌تواند
چند worker داشته باشد و ری‌استارت هم نباید جای قبلی را گم کند.ADR-۴ می‌گوید چرخش **failover
نیست**: یک advance در هر درخواست، نه به‌ازای هر تلاش.

این ماژول فقط همین دو چیز را می‌داند: «کجا هستیم» (ai_rotation) و «چه شد» (ai_rotation_log).
انتخابِ مدل و مسیرِ نشر در ai_models/generate می‌مانند.
"""
import sqlite3

from newsbot.db import q, qrow
from newsbot.timeutils import now_iso

# محدودهٔ چرخش. فعلاً فقط «public» (استخرِ عمومیِ مقاله‌ها)؛ مدلِ شخصی و مدلِ تستِ
# پین‌شده عمداً خارج‌اند: انتخابِ صریحِ مدیر/کاربر را نباید چرخش بازنویسی کند.
ROTATION_SCOPES = ("public",)

_ADVANCE_RETRIES = 5      # تلاشِ دوباره روی باختِ مسابقهٔ CAS


class RotationCASMiss(Exception):
    """CAS باخت: ردیفِ مکان‌نما با ``version``ِ خوانده‌شده جور نبود (کسی زودتر advance کرده).

    این یک خطای «باید دوباره تلاش کنم» است، نه یک باگ — و نباید با خطاهای واقعی (مثلاً
    اسکیمای خراب) یکی گرفته شود، وگرنه یک باگ تا ابد زیرِ fail-open پنهان می‌ماند.
    """


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
    return {"cursor": int(_field(row, "cursor") or 0), "version": int(_field(row, "version") or 0),
            "log_rows": int(n["n"]) if n else 0}


# ── انتخابِ محض (بدونِ دیتابیس، بدونِ شبکه) ────────────────────────────────────────
def _field(m, name, default=None):
    """خواندنِ یک فیلد از dict یا sqlite3.Row (Row متدِ ``.get`` ندارد)."""
    try:
        return m[name]
    except (KeyError, IndexError, TypeError):
        return default


def order_pool(models):
    """ترتیبِ چرخش: اول ``priority`` (کم‌تر = زودتر)، بعد ``id`` — قطعی و قابلِ پیش‌بینی.

    ``priority`` گم‌شده/None به‌عنوان «کم‌ترین اولویت» (۹۹) گرفته می‌شود تا یک ردیفِ
    ناقص، کلِ چرخش را به‌هم نریزد. ورودی می‌تواند dict یا sqlite3.Row باشد.
    """
    return sorted(models, key=lambda m: (_field(m, "priority", 99) if _field(m, "priority", 99) is not None else 99,
                                         _field(m, "id")))


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


def advance(scope, models):
    """مکان‌نما را **یک بار** جلو می‌برد و مدلِ همین درخواست را برمی‌گرداند.

    ``(model, index, version)`` — و در استخرِ خالی ``(None, 0, version)`` بدونِ advance.

    ADR-۳: مکان‌نما در دیتابیس و با CAS است. خواندن و نوشتن در **یک** تراکنش انجام می‌شود
    (``db.write``): اگر SELECT و UPDATE جدا بروند، دو worker می‌توانند نسخه را جابه‌جا کنند
    و یک advance گم شود. CAS هم می‌ماند (برای ایمنی در برابرِ write از مسیرِ دیگر).

    اگر پس از چند تلاش هم record قابلِ advance نبود، **fail-open** می‌کنیم: انتخابِ محلی
    برمی‌گردد ولی مکان‌نما جلو نمی‌رود — چرخش هرگز نباید نشر را متوقف کند.

    این تابع هم‌گام است (DB خالص)؛ صدازدنِ آن از مسیرِ async با ``await arun(advance, ...)``.
    """
    from newsbot.db import write

    for _ in range(_ADVANCE_RETRIES):
        try:
            model, idx, _cursor, version = write(lambda conn: _advance_rmw(conn, scope, models))
            return model, idx, version
        except RotationCASMiss:
            continue                                # مسابقه را باخت: دوباره بخوان و تلاش کن
        except sqlite3.OperationalError:
            continue                                # busy/locked: همان retryِ کوتاه
    # fail-open: انتخابِ محلی بدونِ advance (چرخش نباید نشر را متوقف کند)
    row = rotation_ensure(scope)
    model, idx = pick_rotation(models, int(row["cursor"])) if models else (None, 0)
    return model, idx, int(row["version"])


def _advance_rmw(conn, scope, models):
    """یک «خواندن-تغییر-نوشتن» روی مکان‌نما: ``(model, idx, cursor, version)``.

    از ``advance`` جدا شده تا **قابلِ سنجش** باشد: با یک کانکشنِ جعلی می‌توان دقیقاً همان
    حالتی را ساخت که در آن UPDATE هیچ ردیفی عوض نمی‌کند (باختِ مسابقه) و مطمئن شد که
    تابع آن را «موفق» گزارش نمی‌کند.

    rowcountِ UPDATE این همان چیزی است که در نسخه‌های اول نادیده گرفته می‌شد:
    مکان‌نما جلو نمی‌رفت ولی تابع version+۱ برمی‌گرداند و یک advance گم می‌شد.
    """
    cur = conn.execute("SELECT cursor, version FROM ai_rotation WHERE scope=?", (scope,)).fetchone()
    if cur is None:
        conn.execute("INSERT OR IGNORE INTO ai_rotation(scope, cursor, version, updated_at) VALUES(?,0,0,?)",
                     (scope, now_iso()))
        cur = conn.execute("SELECT cursor, version FROM ai_rotation WHERE scope=?", (scope,)).fetchone()
    cursor = int(cur["cursor"]); version = int(cur["version"])
    if not models:
        return None, 0, cursor, version
    model, idx = pick_rotation(models, cursor)
    upd = conn.execute("UPDATE ai_rotation SET cursor=?, version=version+1, updated_at=? WHERE scope=? AND version=?",
                       (cursor + 1, now_iso(), scope, version))
    if int(getattr(upd, "rowcount", 0) or 0) != 1:
        raise RotationCASMiss("advance lost the race for scope=%s" % scope)
    return model, idx, cursor + 1, version + 1
