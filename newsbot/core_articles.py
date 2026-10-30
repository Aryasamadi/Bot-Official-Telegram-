# -*- coding: utf-8 -*-
"""مقالات، dedup، ژورنال انتشار (idempotent و crash-safe) و پاک‌سازی دوره‌ای.
ماشین حالت انتشار: prepared → in_flight → partial → ack → settle (حذف ژورنال).
هیچ مقاله‌ای پس از ارسال موفق تلگرام، دوباره منتشر نمی‌شود (ژورنال + جدول posted).
"""
import asyncio, json, os, sqlite3, time
from datetime import datetime, timedelta
from telegram.error import BadRequest, RetryAfter, NetworkError, Forbidden
from newsbot import config
from newsbot.db import q, db, _lock, _safe_fields, _trx, gget, gset, arun
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
        return self._tracked(name, method, text_like=(name == "send_message"))
    def _tracked(self, name, method, text_like=False):
        """اجرای یک send تحتِ کنترلِ ژورنال؛ ``text_like`` یعنی پیامِ متنی (فاز ack)."""
        async def send(*args, **kwargs):
            operation_checkpoint()
            if self.parent.cancelling(): raise asyncio.CancelledError
            previous = self.journal["phase"]
            self.journal["phase"] = "in_flight"
            await arun(set_pub_journal, self.aid, self.journal)
            try: message = await method(*args, **kwargs)
            except (BadRequest, RetryAfter, Forbidden):
                self.journal["phase"] = previous
                await arun(set_pub_journal, self.aid, self.journal)
                raise
            except NetworkError:
                raise
            except Exception:
                raise
            if not text_like and not self.caption_complete:
                self.journal.update(phase="partial", media_id=message.message_id)
            else:
                self.journal.update(phase="ack", message_id=message.message_id)
            await arun(set_pub_journal, self.aid, self.journal)
            return message
        return send
    async def send_rich_message(self, chat_id, rich_html, **kwargs):
        """پیام بومی تلگرام (Rich Message) با همان کنترلِ ژورنالِ مسیرِ متنی."""
        from newsbot import rich_send
        return await self._tracked("send_rich_message",
                                   lambda *a, **kw: rich_send.send_rich_raw(self.bot, *a, **kw),
                                   text_like=True)(chat_id, rich_html, **kwargs)

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
        # PH-06/OBS-002: شاهدِ «انتشار موفق» برای dead-man/health — فقط پس از settle واقعی.
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('last_successful_publish',?)",
                     (json.dumps(now_iso(), ensure_ascii=False),))
    _trx(_go)
    if journal.get("test_user") is not None: rate_mark(f"test:{ch['id']}")

def recover_publications():
    """بازیابی ژورنال‌های گیرکرده: ack → settle · بقیه → release (هرگز پست تکراری).

    PH-03/PIPE-021: سطرِ ژورنال در فازِ ``unsettled`` یعنی «رهاسازی قبلاً انجام شده»؛
    نوشتنِ دوباره‌اش هیچ اثری ندارد جز **بارِ نوشتن** (هر ۵ دقیقه، یک تراکنش به‌ازای هر
    سطر). حالا چنین سطری دیگر بازنویسی نمی‌شود؛ اگر مقاله در حالتِ پایانی باشد
    (failed/delivery_unknown) ⇒ سطر حذف می‌شود (انتشارِ دوباره از قبل با
    ``soft_window_candidate`` ممنوع است ⇒ حذف، انتشارِ تکراری را باز نمی‌گرداند)."""
    for row in q("SELECT a.id,a.channel_id,a.reason,a.status FROM articles a JOIN pub_journal j ON j.article_id=a.id"):
        if ch_lock(row["channel_id"]).locked(): continue
        journal = get_pub_journal(row["id"])
        if not journal: continue
        try:
            phase = journal.get("phase")
            if phase == "unsettled":
                if row["status"] in ("failed", "published") or (row["reason"] or "") == "delivery_unknown":
                    q("DELETE FROM pub_journal WHERE article_id=?", (row["id"],), commit=True)
                continue
            if phase == "ack":
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

def soft_window_candidate(cid, chat_id, h):
    """PUB-01: واجد شرایطِ بازچرخانیِ پنجرهٔ نرم. مقالهٔ «تحویل نامعلوم» هرگز دوباره ارسال نمی‌شود
    (شاید پست واقعاً رفته باشد؛ ری‌کیو آن پست تکراری می‌سازد). خروجی: article id یا None."""
    row = q("SELECT id,status,reason FROM articles WHERE channel_id=? AND hash=?", (cid, h), one=True)
    if not row or row["status"] not in ("skipped", "rejected", "failed"): return None
    if (row["reason"] or "") == "delivery_unknown": return None
    if posted_before(chat_id, h): return None
    return row["id"]

def count_articles(uid=None, hours=24, status=None, cid=None):
    since = (now_utc() - timedelta(hours=hours)).isoformat(); sql, p = "SELECT COUNT(*) c FROM articles WHERE created_at >=?", [since]
    if cid is not None: sql += " AND channel_id=?"; p.append(cid)
    elif uid is not None: sql += " AND admin_id=?"; p.append(uid)
    if status: sql += " AND status=?"; p.append(status)
    return q(sql, tuple(p), one=True)["c"]

def _backup_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(config.DB_FILE)) or ".", "backups")
    os.makedirs(d, exist_ok=True)
    return d

def archive_backup(tag="articles"):
    """DB-23 / تصمیم ۱۴: بکاپ اتمیک SQLite پیش از حذف آرشیوی (فقط بک‌اند sqlite؛ D1 خودش Time Travel دارد).
    خروجی: مسیر فایل بکاپ یا None. حداکثر ARCHIVE_BACKUP_KEEP فایل نگه داشته می‌شود."""
    if config.DB_BACKEND == "d1": return None
    d = _backup_dir()
    dest = os.path.join(d, f"newsbot-archive-{tag}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.db")
    src = db()
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)   # پروتکل رسمی backup — سازگار با WAL و اجرای هم‌زمان ربات
    finally:
        dst.close()
    keep = max(1, int(getattr(config, "ARCHIVE_BACKUP_KEEP", 2)))
    files = sorted(f for f in os.listdir(d) if f.startswith("newsbot-archive-"))
    for f in files[:-keep]:
        try: os.remove(os.path.join(d, f))
        except OSError: pass
    log.info("archive backup written: %s", os.path.basename(dest))
    return dest

VACUUM_FREELIST_MIN = 2000      # آستانهٔ صفحات آزاد برای VACUUM ماهانه (DB-23/بهداشت DB)

def _maybe_vacuum(conn):
    """بهداشت DB: VACUUM حداکثر ماهی یک‌بار و فقط اگر فضای آزاد قابل‌توجهی انباشته شده باشد.
    VACUUM روی SQLite crash-safe است، ولی کامل بازنویسی می‌کند؛ پس پشت آستانه + throttle است."""
    try:
        if (time.time() - float(gget("last_vacuum", 0))) < 30 * 86400: return False
        if config.DB_BACKEND == "d1": return False
        free = conn.execute("PRAGMA freelist_count").fetchone()
        free = int(free[0]) if free else 0
        if free < VACUUM_FREELIST_MIN: return False
        conn.execute("VACUUM")
        gset("last_vacuum", time.time())
        log.info("VACUUM done (freelist was %d pages)", free)
        return True
    except Exception:
        log.debug("vacuum skipped", exc_info=True)
        return False

def cleanup():
    """پاک‌سازی دوره‌ای در یک تراکنش واحد (قبلاً ۸ commit جدا داشت و crash وسط آن، دیتابیس را نیمه‌پاک می‌گذاشت).
    DB-23: مقالات قدیمی‌تر از ARCHIVE_DAYS (تصمیم ۱۴: ۳۶۵ روز) آرشیو می‌شوند — *قبل از* حذف، بکاپ اتمیک گرفته می‌شود."""
    if (time.time() - float(gget("last_cleanup", 0))) < 3600: return
    ttl = (now_utc() - timedelta(hours=config.DATA_TTL_HOURS)).isoformat()
    dtx = (now_utc() - timedelta(days=config.DEEPLINK_TTL_DAYS)).isoformat() if config.DEEPLINK_TTL_DAYS > 0 else None
    arc = (now_utc() - timedelta(days=config.ARCHIVE_DAYS)).isoformat()
    # ── سقف + تخلیهٔ زمانی کش‌های درون‌حافظه ──
    rate_cleanup()
    import newsbot.core_users as _cu
    _now_m = time.monotonic()
    for k in [k for k, v in list(_cu._limits_cache.items()) if _now_m - v[0] > _cu._LIMITS_TTL * 5]: _cu._limits_cache.pop(k, None)
    from newsbot.opctx import CLEANUP_HOOKS
    for hook in list(CLEANUP_HOOKS):
        try: hook()
        except Exception: log.debug("cleanup hook failed", exc_info=True)
    # بکاپ پیش از حذف آرشیوی: فقط وقتی واقعاً ردیفی برای حذف هست (تراکنش جدا — بکاپ در تراکنش معنا ندارد)
    try:
        due = q("SELECT COUNT(*) c FROM articles WHERE created_at <?", (arc,), one=True)["c"]
    except Exception:
        due = 0
    if due:
        try: archive_backup()
        except Exception: log.exception("archive backup failed — archival skipped this round")
        else: log.info("archiving %d article(s) older than %d days", due, config.ARCHIVE_DAYS)
    def _go(conn):
        conn.execute("DELETE FROM articles WHERE created_at <? AND status NOT IN ('ready','published')", (ttl,))
        # C4 (فاز ۱): حذف زودهنگام محتوای منتشرشده/آماده باطل شد؛ در فاز ۱۰ (DB-23/تصمیم ۱۴) تنها
        # مسیر مجازِ حذف این‌ها «آرشیو پس از ARCHIVE_DAYS با بکاپ اتمیک» است:
        conn.execute("DELETE FROM articles WHERE created_at <?", (arc,))
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
        # ── PH-03/DB-008: جدول‌هایی که بی‌حدّ رشد می‌کردند ────────────────────────────
        # نشانگرهای idempotencyِ ارجاع («refpaid:pay:<id>») یک ردیف به‌ازای هر پرداختِ
        # تأییدشده در settings می‌ساختند و هرگز پاک نمی‌شدند. نشانگر فقط تا وقتی لازم
        # است که سطرِ پرداخت وجود داشته باشد (تأییدِ دوباره از روی سطرِ حذف‌شده ممکن
        # نیست) ⇒ نشانگرِ بی‌پرداخت حذف می‌شود (پنجرهٔ replay باز نمی‌شود).
        conn.execute("DELETE FROM settings WHERE key LIKE 'refpaid:pay:%' AND "
                     "CAST(substr(key, length('refpaid:pay:')+1) AS INTEGER) NOT IN (SELECT id FROM pay_requests)")
        # توکن‌های انتقالِ مالکیتِ کانال: منقضی‌شده یا مصرف‌شده ⇒ بی‌فایده.
        conn.execute("DELETE FROM ch_tokens WHERE (expires_at IS NOT NULL AND expires_at < ?) OR "
                     "(used_at IS NOT NULL AND used_at < ?)",
                     (now_iso(), (now_utc() - timedelta(days=7)).isoformat()))
        # آمارِ روزانهٔ مصرفِ AI (لاگِ تحلیلی) — ۹۰ روز کافی است.
        conn.execute("DELETE FROM ai_usage WHERE day <?", ((now_utc() - timedelta(days=90)).strftime("%Y-%m-%d"),))
        # اعلان‌های تحویل‌شده (PH-03/OBS-011) — ۳۰ روز نگهداری.
        conn.execute("DELETE FROM notifications WHERE delivered_at IS NOT NULL AND delivered_at < ?",
                     ((now_utc() - timedelta(days=30)).isoformat(),))
    _trx(_go)
    try: q("PRAGMA wal_checkpoint(TRUNCATE)")   # خارج از تراکنش (checkpoint داخل تراکنش معنا ندارد)
    except Exception: pass
    try: q("PRAGMA optimize")                   # آمار planner — سبک و بی‌خطر
    except Exception: pass
    _maybe_vacuum(db())
    gset("last_cleanup", time.time())
