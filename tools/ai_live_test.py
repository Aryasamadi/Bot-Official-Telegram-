#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""آزمونِ زندهٔ تولیدِ محتوا با AI (روی همان دیتابیس و کلیدهای واقعی).

این ابزار یک تولیدِ **واقعی** انجام می‌دهد: از مدلِ واقعی (با کلیدِ واقعیِ ذخیره‌شده
در دیتابیس) یک مقالهٔ نمونه می‌سازد، خروجی را اعتبارسنجی می‌کند، متنِ نهاییِ قابلِ
انتشار را نشان می‌دهد و مصرفِ توکن و زمان را گزارش می‌کند.

    python tools/ai_live_test.py --env-file /opt/bot-official-telegram/.env

هیچ کلید یا توکنی چاپ نمی‌شود. اگر چند مدل فعال باشد، همه امتحان می‌شوند تا
مشخص شود خرابیِ یک مدل اتوماسیون را متوقف نمی‌کند.
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_ARTICLE = {
    "url": "https://example.test/live-sample",
    "title": "نمونهٔ زندهٔ تولید محتوا",
    "text": ("این یک متنِ نمونه برای آزمونِ مسیرِ تولیدِ محتوا با هوش مصنوعی است. "
             "سامانه باید آن را خلاصه کند، امتیاز بدهد و یک پستِ فارسیِ آمادهٔ انتشار بسازد. "
             "همچنین یک نقل‌قولِ مهم در میانهٔ متن قرار می‌گیرد. " * 6),
    "media": None,
    "published": None,
}


def load_env_file(path):
    if path and os.path.exists(path):
        from dotenv import load_dotenv
        load_dotenv(path, override=False)


def _public_settings():
    """تنظیماتِ یک کانالِ واقعی (اگر باشد) تا پرامپت همان قالبِ تولید باشد."""
    from newsbot.core_channels import get_settings
    from newsbot.db import q
    rows = q("SELECT id FROM channels ORDER BY id LIMIT 1")
    if not rows:
        return None, None
    cid = rows[0]["id"]
    try:
        return cid, get_settings(cid)
    except Exception:
        return cid, None


def _default_settings():
    """کانال نداریم ⇒ تنظیماتِ پیش‌فرضِ معقول (همان ساختارِ مورد انتظارِ generate)."""
    return {
        "language": "فارسی", "topic": "عمومی", "prompt": "", "max_words": 220,
        "post_limit": 1800, "hashtags": False,
        "categories": [{"name": "خبری", "style": "خبری و کوتاه", "emoji": "📰"}],
        "criteria": [{"name": "دقت", "weight": 1}, {"name": "غیرتبلیغی", "weight": 1}],
        "signature": "",
    }


async def run_once(owner_id=None, tag="live"):
    """یک تولیدِ واقعی؛ خروجی: دیکشنریِ گزارش (بدونِ رازها)."""
    from newsbot import ai_models as amod
    from newsbot import generate as gen_mod
    from newsbot.timeutils import now_iso

    cid, settings = _public_settings()
    s = settings or _default_settings()
    art = dict(SAMPLE_ARTICLE)
    art["published"] = art["published"] or now_iso()

    models = amod.list_models(active_only=True, owner_id=owner_id)
    if not models:
        print("هیچ مدلِ فعالی در دیتابیس پیدا نشد — این ابزار باید روی VPS (یا با "
              "همان دیتابیس و کلیدها) اجرا شود.", file=sys.stderr)
    report = {
        "channel_id": cid,
        "settings_from_real_channel": bool(settings),
        "owner_id": owner_id,
        "models_tried": [{"id": m["id"], "name": m["name"], "model": m["model"],
                          "status": m["status"], "fail_count": m["fail_count"]} for m in models],
        "model_count": len(models),
    }

    before_posts = amod.ai_posts_total()
    before_usage = amod.tokens_today()
    t0 = time.monotonic()
    gen, model, err = await gen_mod.generate(s, art)
    ms = int((time.monotonic() - t0) * 1000)
    after_usage = amod.tokens_today()

    report.update({
        "error_code": err,
        "model_used": model,
        "duration_ms": ms,
        "provider_posts": amod.ai_posts_total() - before_posts,
        "tokens_delta": {"calls": after_usage["calls"] - before_usage["calls"],
                         "prompt": after_usage["prompt"] - before_usage["prompt"],
                         "completion": after_usage["completion"] - before_usage["completion"]},
    })
    if gen:
        post = gen.get("post") or ""
        report["post_valid"] = bool(post.strip())
        report["post_length"] = len(post)
        report["has_blockquote"] = "<blockquote" in post.lower()
        report["score"] = gen.get("score")
        report["title"] = gen.get("title")
        try:
            composed, full = gen_mod.compose(s, {"username": "", "signature": ""}, art, gen)
            report["composed_length"] = len(composed)
            report["composed_preview"] = composed[:400]
            report["full_present"] = bool(full)
        except Exception as e:
            report["compose_error"] = "%s: %s" % (type(e).__name__, e)
    # وضعیتِ مدل‌ها بعد از تولید (آیا مدلِ خراب down شد و سالم ماند؟)
    report["models_after"] = [{"id": m["id"], "name": m["name"], "status": m["status"],
                               "fail_count": m["fail_count"]} for m in amod.list_models(active_only=True, owner_id=owner_id)]
    return report


async def main_async(owner_id, runs):
    out = []
    for i in range(max(1, int(runs))):
        out.append(await run_once(owner_id, tag="run-%d" % (i + 1)))
        if i + 1 < runs:
            await asyncio.sleep(1)
    return out


def main():
    ap = argparse.ArgumentParser(description="آزمون زندهٔ تولید محتوا با AI")
    ap.add_argument("--env-file", default=os.environ.get("NEWSBOT_ENV_FILE", ""),
                    help="مسیرِ فایل .env (توکن/کلید هرگز چاپ نمی‌شود)")
    ap.add_argument("--owner-id", default="", help="شناسهٔ مالک برای تستِ مدلِ شخصی")
    ap.add_argument("--runs", default=1, help="تعدادِ تولیدهای پشت‌سرهم")
    args = ap.parse_args()

    load_env_file(args.env_file)

    from newsbot.app import init_core
    init_core()

    owner_id = int(args.owner_id) if str(args.owner_id).strip() else None
    report = asyncio.run(main_async(owner_id, int(args.runs)))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
