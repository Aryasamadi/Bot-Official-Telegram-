# -*- coding: utf-8 -*-
"""تست‌های فاز ۳ — مشاهده‌پذیری و یکپارچگی ارجاعی دیتابیس:
  • OBS-001: خروجی و ذخیرهٔ اتمیک متریک‌ها در قالب متنی Prometheus
  • OBS-003: چسبیدن خودکار شناسهٔ همبستگی (_logctx) به همهٔ فراخوانی‌های مستقیم log.*
  • OBS-008: سینک فایل چرخشی (RotatingFileHandler) با قالب متنی و JSON و ماسک خودکار رازها
  • OBS-010: دسته‌بندی استاندارد خطا (classify_error) و درج خودکار error_class در log_event
  • OBS-014: نمایش قفل‌های فعال کانال، پرداخت‌های processing و رکوردهای یتیم در health_snapshot / view_health
  • DB-007: تشخیص رکوردهای یتیم (db_orphan_counts) و حذف آبشاری کامل هنگام حذف کانال (شامل pub_journal)
"""
import asyncio
import json

from newsbot import concurrency, logutils
from newsbot.core_articles import article_insert, set_pub_journal
from newsbot.core_channels import add_channel, del_channel, issue_transfer_token
from newsbot.core_sources import add_source
from newsbot.core_users import ensure_user
from newsbot.db import db_orphan_counts
from newsbot.ui_views_super import health_snapshot


def test_obs001_prometheus_metrics_render_and_atomic_export(tmp_path):
    logutils.bump("posts_ok", 2)
    logutils.bump("model_fail", 1, sub="ai_timeout")
    txt = logutils.render_prometheus_metrics()
    assert "# TYPE newsbot_posts_ok_total counter" in txt
    assert "newsbot_posts_ok_total " in txt
    assert 'newsbot_model_fail_total{reason="ai_timeout"} ' in txt
    assert "newsbot_alerts_pending " in txt
    assert "newsbot_last_successful_publish_timestamp_seconds " in txt

    out_file = tmp_path / "metrics" / "newsbot.prom"
    written = logutils.export_prometheus_metrics(str(out_file))
    assert written == str(out_file)
    assert out_file.exists()
    assert "newsbot_posts_ok_total" in out_file.read_text(encoding="utf-8")


def test_obs003_direct_log_calls_carry_correlation_context(caplog):
    tok = logutils._logctx.set({"op": "cy99-abcd", "channel": 99, "stage": "extract"})
    try:
        with caplog.at_level("INFO", logger="core"):
            logutils.log.info("fetching article body")
        assert any("[op=cy99-abcd channel=99 stage=extract] fetching article body" in r.message for r in caplog.records)
    finally:
        logutils._logctx.reset(tok)


def test_obs008_rotating_file_handler_and_json_format_redact_secrets(tmp_path):
    log_path = tmp_path / "logs" / "bot.jsonl"
    tok = logutils._logctx.set({"op": "op-json-1", "channel": 42})
    h = logutils.configure_file_logging(str(log_path), fmt="json", max_bytes=1024 * 64, backup_count=2)
    assert h is not None
    try:
        logutils.log.warning("provider failed with Bearer secret_token_1234567890")
        h.flush()
    finally:
        logutils.log.removeHandler(h)
        h.close()
        logutils._logctx.reset(tok)

    lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines
    rec = json.loads(lines[-1])
    assert rec["level"] == "WARNING"
    assert rec["op"] == "op-json-1"
    assert rec["channel"] == 42
    assert "secret_token_1234567890" not in rec["msg"]
    assert "Bearer •••" in rec["msg"]


def test_obs010_classify_error_and_log_event_attaches_error_class(caplog):
    assert logutils.classify_error("HTTP 401 Unauthorized: invalid api key") == "auth"
    assert logutils.classify_error("429 Too Many Requests: retry_after=10") == "rate_limit"
    assert logutils.classify_error("ReadTimeout: request timed out after 45s") == "timeout"
    assert logutils.classify_error("insufficient_quota: billing limit reached") == "quota"
    assert logutils.classify_error("503 Service Unavailable / DNS failure") == "network"
    assert logutils.classify_error("400 Bad Request: invalid JSON payload") == "content"

    with caplog.at_level("WARNING", logger="core"):
        logutils.log_event("WARN", "AI call failed with HTTP 429 rate limit")
    assert any("error_class=rate_limit" in r.message for r in caplog.records)


def test_obs014_and_db007_health_snapshot_and_channel_cascade_leave_zero_orphans():
    uid = 881001
    ensure_user(uid, "u881001", "U")
    cid = add_channel(uid, -100881001, "Ch881001", "ch881001", uid)
    add_source(uid, cid, "https://cascade.example.com/rss")
    issue_transfer_token(cid, uid)
    aid = article_insert(uid, cid, "h-cascade-1", "https://cascade.example.com/a1", "Title", 0, None, status="failed")
    # ژورنالِ unsettled (که مانعِ حذفِ کانال نیست ولی قبلاً پس از حذفِ کانال یتیم می‌ماند)
    set_pub_journal(aid, {"phase": "unsettled", "admin_id": uid})

    async def check_active_lock():
        lk = concurrency.ch_lock(cid)
        async with lk:
            assert cid in concurrency.active_channel_locks()
            snap = health_snapshot()
            assert cid in snap["active_locks"]
            assert "processing_payments" in snap
            assert "orphan_rows" in snap

    asyncio.run(check_active_lock())
    assert cid not in concurrency.active_channel_locks()

    # حذف کانال باید منابع، مقاله‌ها، توکن‌ها و pub_journal آن کانال را کامل پاک کند
    del_channel(cid, uid)
    orphans = db_orphan_counts()
    assert sum(orphans.values()) == 0, orphans
