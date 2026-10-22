# -*- coding: utf-8 -*-
"""PH-03/OBS-011 — صفِ پایدارِ اعلان‌های مدیر کلان (dead-letter).

پیش از این ماژول، اعلانِ مدیر کلان یک تلاشِ درون‌حافظه‌ای بود: اگر تلگرام خطا می‌داد،
پیام فقط در یک خطِ لاگ گم می‌شد (و اگر صفِ ۱۰۰تایی پر بود، پیام **دراپ** می‌شد).
یعنی تنها کانالِ هشدارِ کلِ سیستم، دقیقاً وقتی که سیستم خراب است، بی‌صدا می‌شد.

حالا هر اعلانِ تحویل‌نشده در جدولِ ``notifications`` می‌نشیند:
  * ``zaman-bند`` آن را با فاصلهٔ نمایی دوباره می‌فرستد (``next_attempt_at``)؛
  * اگر از سقفِ تلاش گذشت، سطر با ``dead=1`` «مرده» می‌شود — **حذف نمی‌شود** تا
    مدیر کلان آن را در بخشِ سلامت ببیند؛
  * ``/health`` شمارِ «در انتظار» و «مرده» را نشان می‌دهد.

جدول و ستون‌ها **افزایشی** هستند ⇒ برگشتِ کد، داده‌ای را خراب نمی‌کند.
"""
import json
import time

from newsbot.db import q
from newsbot.timeutils import now_iso

ALERT_MAX_ATTEMPTS = 8
# فاصلهٔ تلاشِ دوباره (ثانیه): ۱ دقیقه، ۵، ۱۵، ۱ ساعت، ۲، ۴، ۶ ساعت
ALERT_BACKOFF_SEC = (60, 300, 900, 3600, 7200, 14400, 21600)


def alert_enqueue(text, kb=None, kind="super", next_attempt_at=None):
    """اعلانِ تحویل‌نشده را durable می‌کند (دیتابیس) ⇒ بعداً زمان‌بند دوباره می‌فرستد.

    ``next_attempt_at`` اختیاری است: مسیرِ ارسالِ فوری چند ثانیه فرصت می‌دهد تا worker
    اول تلاش کند و زمان‌بند همان پیام را موازی نفرستد."""
    payload = json.dumps({"kb": kb}, ensure_ascii=False) if kb else ""
    due = time.time() if next_attempt_at is None else float(next_attempt_at)
    return q("INSERT INTO notifications(kind,text,payload,attempts,next_attempt_at,dead,last_error,created_at) "
             "VALUES(?,?,?,0,?,0,'',?)",
             (kind, text or "", payload, due, now_iso()), commit=True)


def alert_pending(limit=20):
    """اعلان‌های نرسیده‌ای که وقتِ تلاشِ بعدی‌شان رسیده (مرده‌ها برنمی‌گردند)."""
    return q("SELECT * FROM notifications WHERE delivered_at IS NULL AND dead=0 AND next_attempt_at<=? "
             "ORDER BY id LIMIT ?", (time.time(), int(limit)))


def alert_mark_delivered(nid):
    q("UPDATE notifications SET delivered_at=?, attempts=attempts+1, last_error='' WHERE id=?",
      (now_iso(), nid), commit=True)


def alert_mark_failed(nid, err):
    """تلاشِ ناموفق ⇒ شمارش + فاصلهٔ بعدی؛ از سقف گذشت ⇒ dead-letter (نه حذف)."""
    row = q("SELECT attempts FROM notifications WHERE id=?", (nid,), one=True)
    n = int(row["attempts"] or 0) + 1 if row is not None else 1
    msg = (str(err) or "")[:500]
    if n >= ALERT_MAX_ATTEMPTS:
        q("UPDATE notifications SET attempts=?, dead=1, last_error=? WHERE id=?", (n, msg, nid), commit=True)
        return True
    delay = ALERT_BACKOFF_SEC[min(n - 1, len(ALERT_BACKOFF_SEC) - 1)]
    q("UPDATE notifications SET attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
      (n, time.time() + delay, msg, nid), commit=True)
    return False


def alert_counts():
    """(در انتظار, مرده) — برای بخشِ سلامت."""
    row = q("SELECT COUNT(*) total, SUM(CASE WHEN dead=1 THEN 1 ELSE 0 END) dead FROM notifications "
            "WHERE delivered_at IS NULL", one=True)
    total = int(row["total"] or 0) if row is not None else 0
    dead = int(row["dead"] or 0) if row is not None else 0
    return total - dead, dead


def alert_dead(limit=20):
    """سطرهای مرده — برای نمایش/پاک‌سازی."""
    return q("SELECT * FROM notifications WHERE delivered_at IS NULL AND dead=1 ORDER BY id LIMIT ?", (int(limit),))


def alert_purge_delivered(older_than_days=30):
    """پاک‌سازیِ اعلان‌های تحویل‌شدهٔ قدیمی (نگهداریِ ۳۰ روزه)."""
    from newsbot.timeutils import now_utc
    from datetime import timedelta
    dtx = (now_utc() - timedelta(days=int(older_than_days))).isoformat()
    q("DELETE FROM notifications WHERE delivered_at IS NOT NULL AND delivered_at<?", (dtx,), commit=True)
