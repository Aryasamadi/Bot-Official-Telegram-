# -*- coding: utf-8 -*-
"""مقالات، dedup، ژورنال انتشار (idempotent و crash-safe) و پاک‌سازی دوره‌ای.
ماشین حالت انتشار: prepared → in_flight → partial → ack → settle (حذف ژورنال).
هیچ مقاله‌ای پس از ارسال موفق تلگرام، دوباره منتشر نمی‌شود (ژورنال + جدول posted).
"""
import asyncio, json, time
from datetime import timedelta
from telegram.error import BadRequest, RetryAfter, NetworkError, Forbidden
from newsbot import config
from newsbot.db import q, db, _lock, _safe_fields, _trx, gget, gset
from newsbot.logutils import log
from newsbot.timeutils import now_utc, now_iso, today_str
from newsbot.ratelimit import rate_mark, rate_cleanup
from newsbot.core_users import admin_limits, admin_offset
from newsbot.concurrency import ch_lock
from newsbot.core_channels import get_channel, msg_link
from newsbot.opctx import operation_checkpoint


def article_exists(cid, h): return bool(q("SELECT 1 FROM articles WHERE channel_id=? AND hash=?", (cid, h), one=True))

def title_fp(t):
    """اثر انگشت سبک از عنوان: برای تشخیص «همان خبر از آدرس/منبع دیگر» — کم‌حجم و بدون خواندن سنگین."""
    import re, hashlib
    t = re.sub(r"[\W_]+", " ", str(t or "").lower(), flags=re.UNICODE)
    return hashlib.sha1(" ".join(t.split()).encode("utf-8")).hexdigest()[:16]   # کل عنوان، نه ۸ کلمهٔ اول

def dedup_seen(cid, fp): return bool(q("SELECT 1 FROM dedup WHERE channel_id=? AND fp=?", (cid, fp), one=True))
def dedup_mark(cid, fp): q("INSERT OR REPLACE INTO dedup(channel_id,fp,ts) VALUES(?,?,?)", (cid, fp, now_iso()), commit=True)

def article_insert(uid, cid, h, url, title, source_id, published_at, status="discovered", reason=""):
    """اگر رکورد از قبل موجود باشد، INSERT OR IGNORE چیزی درج نمی‌کند و lastrowid بی‌اعتبار می‌شود؛
    در آن حالت None برمی‌گردانیم تا محتوای تولیدشده هرگز به مقاله‌ی اشتباه نچسبد."""
    with _lock:
        cur = db().execute("INSERT OR IGNORE INTO articles(admin_id,channel_id,hash,url,title,source_id,published_at,created_at,status,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (uid, cid, h, url, title or "", source_id, published_at, now_iso(), status, reason))
        db().commit(); return cur.lastrowid if cur.rowcount else None

def article_update(aid, **f):
    if not f: return
    _safe_fields("articles", f)
    q("UPDATE articles SET " + ", ".join(f"{k}=?" for k in f) + " WHERE id=?", (*f.values(), aid), commit=True)

def get_article(aid): return q("SELECT * FROM articles WHERE id=?", (aid,), one=True)

class PublicationQuotaExceeded(Exception): pass

def get_pub_journal(aid):
    r = q("SELECT journal FROM pub_journal WHERE article_id=?", (aid,), one=True)
    return json.loads(r["journal"]) if (r and r["journal"]) else None

def set_pub_journal(aid, entry):
    q("INSERT OR REPLACE INTO pub_journal(article_id,journal,updated_at) VALUES(?,?,?)",
      (aid, json.dumps(entry, ensure_ascii=False), now_iso()), commit=True)

def release_publication(aid, journal, reason):
    def _go(conn):
        day = journal.get("reserved_day")
        if day is not None and journal.get("phase") != "in_flight":
            conn.execute("UPDATE usage SET posts=MAX(0,posts-1) WHERE admin_id=? AND day=?", (journal["admin_id"], day))
        tday = journal.get("test_day")
        if tday is not None and journal.get("phase") != "in_flight":
            conn.execute("UPDATE usage SET tests=MAX(0,tests-1) WHERE admin_id=? AND day=?", (journal.get("test_user"), tday))
        if journal.get("phase") == "in_flight":
            # تحویل نامعلوم: روزِ رزروشده می‌ماند (شاید واقعاً منتشر شده باشد) ولی فاز از in_flight بیرون می‌رود،
            # وگرنه publication_pending تا ۴۸ ساعت کانال را برای ریست صف/انتقال مالکیت/حذف کانال قفل می‌کند.
            jr = dict(journal, phase="unsettled", reserved_day=day)
        elif journal.get("phase") == "partial":
            # رسانه آپلود شده ولی ارسال نشده (کرش وسط آپلود): سهمیه برمی‌گردد و فاز به unsettled می‌رود؛
            # اگر partial بماند، publication_pending کانال را برای همیشه busy نگه می‌دارد.
            jr = dict(journal, phase="unsettled", reserved_day=None)
        else:
            jr = dict(journal, reserved_day=None)
        conn.execute("UPDATE articles SET reason=? WHERE id=?", (reason, aid))
        if jr.get("phase") == "prepared":
            conn.execute("DELETE FROM pub_journal WHERE article_id=?", (aid,))
        else:
            conn.execute("INSERT OR REPLACE INTO pub_journal(article_id,journal,updated_at) VALUES(?,?,?)", (aid, json.dumps(jr), now_iso()))
        return jr
    return _trx(_go)

class PublicationTransport:
    """پوشش ارسال: پیش از هر send، فاز ژورنال به in_flight می‌رود و پس از موفقیت به partial/ack.
    لغو وسط کار ⇒ CancelledError؛ خطای تلگرام ⇒ فاز قبلی برمی‌گردد (ژورنال هرگز نیمه‌راه نمی‌ماند)."""
    def __init__(self, bot, aid, journal, parent):
        self.bot, self.aid, self.journal, self.parent = bot, aid, journal, parent
        self.caption_complete = False
    def __getattr__(self, name):
        method = getattr(self.bot, name)
        if not name.startswith("send_"): return method
        async def send(*args, **kwargs):
            operation_checkpoint()
            if self.parent.cancelling(): raise asyncio.CancelledError
            previous = self.journal["phase"]
            self.journal["phase"] = "in_flight"
            set_pub_journal(self.aid, self.journal)
            try: message = await method(*args, **kwargs)
            except (BadRequest, RetryAfter, Forbidden):
                self.journal["phase"] = previous
                set_pub_journal(self.aid, self.journal)
                raise
            except NetworkError:
                raise
            except Exception:
                raise
            if name != "send_message" and not self.caption_complete:
                self.journal.update(phase="partial", media_id=message.message_id)
            else:
                self.journal.update(phase="ack", message_id=message.message_id)
            set_pub_journal(self.aid, self.journal)
            return message
        return send

def reserve_publication(aid, admin_id, count_usage, test_user, previous):
    day = today_str(admin_offset(admin_id))
    lim = admin_limits(admin_id)
    cap = lim["daily_posts"] if count_usage else None
    tcap = lim["daily_tests"] if test_user is not None else None
    journal = dict(previous or {}, phase="partial" if previous and previous.get("media_id") else "prepared",
                   admin_id=admin_id, test_user=test_user, day=day, reserved_day=day if count_usage and cap is not None else None,
                   test_day=day if (test_user is not None and tcap is not None) else None)
    def _go(conn):
        if journal["reserved_day"] is not None:
            cur = conn.execute("INSERT INTO usage(admin_id,day,posts,tests) VALUES(?,?,1,0) ON CONFLICT(admin_id,day) DO UPDATE SET posts=posts+1 WHERE posts <?", (admin_id, day, cap))
            if not cur.rowcount: raise PublicationQuotaExceeded
        if journal["test_day"] is not None:
            # رزرو اتمی سهمیهٔ تست روزانه: یک UPDATE شرطی (WHERE tests <limit) که با rowcount بررسی می‌شود،
            # پس دو تست هم‌زمان نمی‌توانند هر دو از دروازه رد شوند.
            cur = conn.execute("INSERT INTO usage(admin_id,day,posts,tests) VALUES(?,?,0,1) ON CONFLICT(admin_id,day) DO UPDATE SET tests=tests+1 WHERE tests <?", (test_user, day, tcap))
            if not cur.rowcount: raise PublicationQuotaExceeded
        conn.execute("INSERT OR REPLACE INTO pub_journal(article_id,journal,updated_at) VALUES(?,?,?)", (aid, json.dumps(journal), now_iso()))
        return journal
    try: return _trx(_go)
    except PublicationQuotaExceeded: return None

def settle_publication(aid, ch, journal):
    link = journal.get("link") or msg_link(ch, __import__("types").SimpleNamespace(message_id=journal["message_id"]))
    def _go(conn):
        a = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
        if not a: raise RuntimeError("article_missing")
        if a["status"] != "published":
            conn.execute("INSERT OR IGNORE INTO posted VALUES(?,?,?)", (ch["chat_id"], a["hash"], now_iso()))
            conn.execute("UPDATE articles SET status='published',links=?,reason='' WHERE id=?", (json.dumps([link]), aid))
        # سهمیهٔ تست در reserve_publication اتمی رزرو شده است؛ اینجا دوباره شمرده نمی‌شود
        conn.execute("DELETE FROM pub_journal WHERE article_id=?", (aid,))
    _trx(_go)
    if journal.get("test_user") is not None: rate_mark(f"test:{ch['id']}")

def recover_publications():
    """بازیابی ژورنال‌های گیرکرده: ack → settle · بقیه → release (هرگز پست تکراری)."""
    for row in q("SELECT a.id,a.channel_id,a.reason,a.status FROM articles a JOIN pub_journal j ON j.article_id=a.id"):
        if ch_lock(row["channel_id"]).locked(): continue
        journal = get_pub_journal(row["id"])
        if not journal: continue
        try:
            if journal.get("phase") == "ack":
                ch = get_channel(row["channel_id"])
                if ch: settle_publication(row["id"], ch, journal)
            else:
                _unknown = journal.get("phase") == "in_flight"
                release_publication(row["id"], journal, "delivery_unknown" if _unknown else row["reason"] or "")
                if _unknown and row["status"] == "ready":
                    # نباید سرِ صف «آماده» بماند؛ وگرنه drain برای همیشه روی همین مقاله می‌ایستد.
                    article_update(row["id"], status="failed", reason="delivery_unknown")
        except Exception: log.exception("publication recovery failed article=%s", row["id"])

def articles_by_status(cid, status, limit=50, automatic=False):
    # پنل «ردشده‌ها» تنها جای دیدنِ مقالاتِ بی‌سرانجام است؛ پس failed و skipped هم همان‌جا فهرست می‌شوند
    st = ("rejected", "failed", "skipped") if status == "rejected" else (status,)
    order = "ASC" if status == "ready" else "DESC"
    eligible = " AND COALESCE(reason,'')!='cancelled'" if automatic else ""
    return q(f"SELECT id,title,score,category,created_at,url,reason,status FROM articles WHERE channel_id=? AND status IN ({','.join('?' * len(st))}){eligible} ORDER BY id {order} LIMIT ?", (cid, *st, limit))

def ready_count(cid): return q("SELECT COUNT(*) c FROM articles WHERE channel_id=? AND status='ready'", (cid,), one=True)["c"]

def delete_article(aid, uid):
    from newsbot.core_channels import publication_pending
    a = get_article(aid)
    if a and publication_pending(a["channel_id"]): raise RuntimeError("channel_busy")
    q("DELETE FROM articles WHERE id=? AND admin_id=?", (aid, uid), commit=True)

def posted_before(chat_id, h): return bool(q("SELECT 1 FROM posted WHERE channel_id=? AND hash=?", (chat_id, h), one=True))

def count_articles(uid=None, hours=24, status=None, cid=None):
    since = (now_utc() - timedelta(hours=hours)).isoformat(); sql, p = "SELECT COUNT(*) c FROM articles WHERE created_at >=?", [since]
    if cid is not None: sql += " AND channel_id=?"; p.append(cid)
    elif uid is not None: sql += " AND admin_id=?"; p.append(uid)
    if status: sql += " AND status=?"; p.append(status)
    return q(sql, tuple(p), one=True)["c"]

def cleanup():
    """پاک‌سازی دوره‌ای در یک تراکنش واحد (قبلاً ۸ commit جدا داشت و crash وسط آن، دیتابیس را نیمه‌پاک می‌گذاشت)."""
    if (time.time() - float(gget("last_cleanup", 0))) < 3600: return
    ttl = (now_utc() - timedelta(hours=config.DATA_TTL_HOURS)).isoformat()
    dtx = (now_utc() - timedelta(days=config.DEEPLINK_TTL_DAYS)).isoformat() if config.DEEPLINK_TTL_DAYS > 0 else None
    # ── سقف + تخلیهٔ زمانی کش‌های درون‌حافظه ──
    rate_cleanup()
    import newsbot.core_users as _cu
    _now_m = time.monotonic()
    for k in [k for k, v in list(_cu._limits_cache.items()) if _now_m - v[0] > _cu._LIMITS_TTL * 5]: _cu._limits_cache.pop(k, None)
    from newsbot.opctx import CLEANUP_HOOKS
    for hook in list(CLEANUP_HOOKS):
        try: hook()
        except Exception: log.debug("cleanup hook failed", exc_info=True)
    def _go(conn):
        conn.execute("DELETE FROM articles WHERE created_at <? AND status NOT IN ('ready','published')", (ttl,))
        # ردیف‌های status='published' هرگز حذف نمی‌شوند.
        # (قبلاً این‌جا DELETE ... status='published' AND created_at<72h بود که با بخش ۴ مأموریت
        #  «حفظ دقیق محتوای تولیدشده توسط ایجنت» در تضاد مستقیم بود — تصمیم C4: حذف کامل آن.)
        conn.execute("DELETE FROM posted WHERE posted_at <?", ((now_utc() - timedelta(days=90)).isoformat(),))
        conn.execute("DELETE FROM dedup WHERE ts <?", ((now_utc() - timedelta(hours=config.DEDUP_TTL_HOURS)).isoformat(),))
        conn.execute("DELETE FROM pub_journal WHERE article_id NOT IN (SELECT id FROM articles)")
        conn.execute("DELETE FROM rate_limits WHERE ts <?", (time.time() - 3600,))
        if dtx:
            # سیاست نگهداری «بیشتر»: فقط ردیف‌های قدیمی‌تر از TTL پاک می‌شوند و هرگز جدیدترین DEEPLINK_KEEP_MIN ردیف
            conn.execute("DELETE FROM deeplinks WHERE created_at <? AND key IN (SELECT key FROM deeplinks WHERE created_at <? ORDER BY created_at DESC LIMIT -1 OFFSET ?)", (dtx, dtx, config.DEEPLINK_KEEP_MIN))
            conn.execute("DELETE FROM kv_cache WHERE cached_at <? AND key IN (SELECT key FROM kv_cache WHERE cached_at <? ORDER BY cached_at DESC LIMIT -1 OFFSET ?)", (dtx, dtx, config.DEEPLINK_KEEP_MIN))
        if config.AI_CACHE_TTL_DAYS > 0:
            conn.execute("DELETE FROM ai_cache WHERE created_at <?", ((now_utc() - timedelta(days=config.AI_CACHE_TTL_DAYS)).isoformat(),))
        conn.execute("DELETE FROM usage WHERE day <?", ((now_utc() - timedelta(days=60)).strftime("%Y-%m-%d"),))
        conn.execute("DELETE FROM support_map WHERE ts <?", ((now_utc() - timedelta(days=7)).isoformat(),))
        conn.execute("DELETE FROM pay_requests WHERE status!='pending' AND COALESCE(decided_at, created_at) <?", ((now_utc() - timedelta(days=90)).isoformat(),))
    _trx(_go)
    try: q("PRAGMA wal_checkpoint(TRUNCATE)")   # خارج از تراکنش (checkpoint داخل تراکنش معنا ندارد)
    except Exception: pass
    gset("last_cleanup", time.time())
