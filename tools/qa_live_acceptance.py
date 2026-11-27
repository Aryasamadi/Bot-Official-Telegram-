#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""فاز ۴ — ابزارِ پذیرشِ زندهٔ Q&A روی یک رانرِ GitHub Actions.

این ابزار رباتِ واقعی را بالا می‌آورد، دیتابیس و لاگ را هر ۱۰ ثانیه می‌خواند و
در پایان یک گزارشِ JSON با شناسه/شمارش می‌نویسد. هیچ توکنی، هیچ متنی از سؤال و
هیچ متنی از پاسخ در گزارش یا لاگِ ابزار نمی‌آید.

    python tools/qa_live_acceptance.py --check [--env-file PATH]
    python tools/qa_live_acceptance.py --supervise --duration 350 \
        --report REPORT.json --log BOT.log [--scenario core|full] [--env-file PATH]

کدهای خروج: ۰ = همهٔ بررسی‌های لازم دیده شد · ۱ = وقت تمام شد · ۲ = پیش‌شرط شکست خورد.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

SCHEMA = 1
POLL_SECONDS = 10
STOP_GRACE_SECONDS = 15
MIN_DURATION_MIN = 5
MAX_DURATION_MIN = 355

KNOWN_ENV = (
    "BOT_TOKEN", "SUPER_ADMIN_IDS", "SECRETS_KEY", "DB_FILE", "DB_BACKEND",
    "QA_ENABLED", "TELEGRAM_API_BASE", "LOG_FILE", "PROVIDER_CREDENTIALS_PRESENT",
)

_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")


# ── env ────────────────────────────────────────────────────────────────────────────
def load_env(path=None):
    """مقادیرِ فایلِ env را می‌خواند و env فرایند روی آن می‌نشیند (فرایند برنده است)."""
    env = {}
    if path:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                env[key.strip()] = value
    for key, value in os.environ.items():
        if key in env or key in KNOWN_ENV:
            env[key] = value
    return env


def token_shape_ok(token):
    return bool(_TOKEN_RE.match((token or "").strip()))


def admin_ids(value):
    """«1, 22 33» ⇒ {1, 22, 33} — جداکنندهٔ ویرگول یا فاصله."""
    out = set()
    for part in re.split(r"[,;\s]+", str(value or "").strip()):
        if part:
            try:
                out.add(int(part))
            except ValueError:
                continue
    return out


def secret_values_present(text, values):
    """نامِ رازهایی که مقدارشان در متن آمده — هرگز خودِ مقدار برنمی‌گردد.

    شناسهٔ عددی فقط وقتی «نشت» حساب می‌شود که **مستقل** باشد: لاگ، شناسهٔ
    سوپرادمین را به‌صورت هش (sha256) می‌نویسد و یک هشِ hex ممکن است تصادفاً
    شاملِ رقم‌های همان شناسه باشد؛ آن وقت «نشت» کاذب گزارش می‌شد (F-3 —
    شاهد: اجرای زندهٔ 46013627630 که ۲۶۱ بار هش، «نشتِ» کاذب گزارش کرد).
    """
    names = []
    for name, value in values.items():
        if not value or len(value) < 8:
            continue
        pattern = r"(?<![0-9A-Za-z])%s(?![0-9A-Za-z])" % re.escape(value)
        if re.search(pattern, text):
            names.append(name)
    return sorted(names)


# ── HTTP ──────────────────────────────────────────────────────────────────────────
def api_base(env):
    return (env.get("TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/")


def get_me_url(env, token):
    return "%s/bot%s/getMe" % (api_base(env), token)


def http_get_json(url, timeout=10):
    """GET + JSON؛ خطا فقط با کدِ وضعیت/نوعِ استثنا بالا می‌رود (URL توکن دارد)."""
    req = urllib.request.Request(url, headers={"User-Agent": "qa-live-acceptance/%d" % SCHEMA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError("http %s" % e.code) from None
    except Exception as e:                                    # noqa: BLE001 — نوعِ خطا کافی است
        raise RuntimeError(type(e).__name__) from None
    if status != 200:
        raise RuntimeError("http %s" % status)
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:                                         # noqa: BLE001
        raise RuntimeError("bad json") from None
    if not (isinstance(data, dict) and data.get("ok")):
        raise RuntimeError("telegram not ok")
    return data


# ── پیش‌شرط‌ها ─────────────────────────────────────────────────────────────────────
def run_check(env, http_get=http_get_json):
    """پیش‌شرط‌های اجرا: توکن، شناسهٔ مدیر، کلیدِ رازها، مسیرِ DB و یک getMe."""
    problems = []
    detail = ""
    token = (env.get("BOT_TOKEN") or "").strip()
    if not token:
        problems.append("BOT_TOKEN_MISSING")
    elif not token_shape_ok(token):
        problems.append("BOT_TOKEN_SHAPE")
    ids = admin_ids(env.get("SUPER_ADMIN_IDS"))
    if not ids:
        problems.append("ADMIN_IDS_MISSING")
    if not (env.get("SECRETS_KEY") or "").strip():
        # بدونِ کلیدِ پایدار، کلیدِ مدل به‌صورتِ متنِ ساده در DBِ کش‌شده می‌نشیند.
        problems.append("SECRETS_KEY_REQUIRED")
    db = (env.get("DB_FILE") or "").strip()
    if not db:
        problems.append("DB_FILE_MISSING")
    else:
        parent = os.path.dirname(os.path.abspath(db))
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            problems.append("DB_DIR_NOT_WRITABLE")
        else:
            if not os.access(parent, os.W_OK):
                problems.append("DB_DIR_NOT_WRITABLE")
    if token and token_shape_ok(token):
        try:
            http_get(get_me_url(env, token), timeout=10)
        except Exception as e:                                # noqa: BLE001
            problems.append("GETME_FAILED")
            detail = str(e) or type(e).__name__
    out = {"ok": not problems, "problems": problems, "token_len": len(token),
           "admins": len(ids)}
    if detail:
        out["getme"] = detail                                  # هرگز URL/توکن نیست
    return (0 if not problems else 2), out


# ── اسنپ‌شاتِ دیتابیس ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Snapshot:
    """تصویرِ فقط-خواندنی از جدول‌هایی که بررسی‌ها به آن‌ها تکیه دارند."""
    qa_rows: tuple = ()
    usage: tuple = ()
    grants: tuple = ()
    channels: tuple = ()
    models: tuple = ()


EMPTY_SNAPSHOT = Snapshot()

_TABLES = {
    "qa_rows": "SELECT * FROM qa_answers",
    "usage": "SELECT * FROM usage",
    "grants": "SELECT * FROM ai_plan_grants",
    "channels": "SELECT * FROM channels",
    "models": "SELECT * FROM ai_models",
}


def read_snapshot(db_path):
    """فقط-خواندنی؛ فایلِ نبوده ⇒ اسنپ‌شاتِ خالی؛ جدولِ نبوده ⇒ تاپلِ خالی.

    هرگز `init_core()` صدا زده نمی‌شود: ابزار نباید دیتابیسِ ربات را تغییر بدهد.
    """
    if not db_path or not os.path.isfile(db_path):
        return EMPTY_SNAPSHOT
    uri = "file:%s?mode=ro" % urllib.parse.quote(os.path.abspath(db_path).replace("\\", "/"),
                                                 safe="/:")
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        out = {}
        for name, sql in _TABLES.items():
            try:
                out[name] = tuple(dict(r) for r in conn.execute(sql).fetchall())
            except sqlite3.OperationalError:
                out[name] = ()
        return Snapshot(**out)
    finally:
        conn.close()


def usage_delta(prev, cur):
    """اختلافِ مصرفِ AI به‌ازای هر ادمین (جمعِ روزها)؛ فقط تغییرهای غیرصفر."""
    def totals(snap):
        acc_ = {}
        for r in snap.usage:
            key = int(r.get("admin_id") or 0)
            acc_[key] = acc_.get(key, 0) + int(r.get("ai") or 0)
        return acc_
    a, b = totals(prev), totals(cur)
    out = {}
    for key in set(a) | set(b):
        d = b.get(key, 0) - a.get(key, 0)
        if d:
            out[key] = d
    return out


def grant_delta(prev, cur):
    """اختلافِ مصرفِ گرانت به‌ازای هر ردیفِ گرانت (id)؛ فقط تغییرهای غیرصفر."""
    a = {int(g.get("id") or 0): int(g.get("used") or 0) for g in prev.grants}
    b = {int(g.get("id") or 0): int(g.get("used") or 0) for g in cur.grants}
    out = {}
    for key in set(a) | set(b):
        d = b.get(key, 0) - a.get(key, 0)
        if d:
            out[key] = d
    return out


def _row_events(prev, cur):
    """رویدادهای پنجره بدونِ انتسابِ شمارندهها (پایهٔ هر دو انتساب)."""
    before = {(int(r.get("chat_id") or 0), int(r.get("msg_id") or 0)): r for r in prev.qa_rows}
    after = {(int(r.get("chat_id") or 0), int(r.get("msg_id") or 0)): r for r in cur.qa_rows}
    events = []
    for key, row in after.items():
        if key not in before:
            events.append({"key": list(key), "kind": "insert", "to": row.get("status"),
                           "usage": {}, "grants": {}, "ambiguous": False})
        elif (before[key].get("status") or "") != (row.get("status") or ""):
            events.append({"key": list(key), "kind": "status", "to": row.get("status"),
                           "usage": {}, "grants": {}, "ambiguous": False})
    if len({tuple(e["key"]) for e in events}) > 1:
        for e in events:
            e["ambiguous"] = True
    return events


def _merge_deltas(a, b):
    out = {int(k): int(v) for k, v in (a or {}).items()}
    for key, value in (b or {}).items():
        key, value = int(key), int(value)
        out[key] = out.get(key, 0) + value
        if not out[key]:
            del out[key]
    return out


def attribute(prev, cur):
    """رویدادهای پنجره + انتسابِ شمارندهها (همان پنجره).

    اگر در یک پنجره فقط **یک** ردیفِ سؤال تغییر کرده باشد، اختلافِ شمارندهها به همان
    ردیف نسبت داده میشود؛ اگر دو ردیفِ متفاوت تغییر کرده باشند، نتیجه `ambiguous` است و
    ابزار حدس نمیزند (اجرا شکست میخورد و آن مرحله تکرار میشود).
    """
    events = _row_events(prev, cur)
    if not events or any(e["ambiguous"] for e in events):
        return events
    events[0]["usage"], events[0]["grants"] = usage_delta(prev, cur), grant_delta(prev, cur)
    return events


def attribute_carry(prev, cur, carry):
    """مانند `attribute` ولی شمارندههای بیصاحب را به پنجرهٔ بعد منتقل میکند.

    `qa_claim` ردیف `pending` را می نویسد، سپس `ai_claim` شمارنده را زیاد می کند و بعد
    `qa_finish` وضعیت را `done` می کند. اگر مرزِ ۱۰ ثانیهای بینِ این دو نوشتن بیفتد،
    اختلافِ شمارنده هرگز به ردیف نمی رسد و بررسی «هزینه از پرسشگر» دروغین می شود.
    انتقال فقط به همان کاربر (و گرانتِ همان کاربر) چسبانده می شود؛ باقیماندهها
    منتقل می مانند، ولی هرگز به سؤالِ دیگری نسبت داده نمی شوند.
    """
    carry = carry or {}
    events = _row_events(prev, cur)
    pending_u = _merge_deltas(carry.get("usage"), usage_delta(prev, cur))
    pending_g = _merge_deltas(carry.get("grants"), grant_delta(prev, cur))
    if not events or any(e["ambiguous"] for e in events):
        return events, {"usage": pending_u, "grants": pending_g}
    key = tuple(events[0]["key"])
    row = next((r for r in cur.qa_rows
                if (int(r.get("chat_id") or 0), int(r.get("msg_id") or 0)) == key), None)
    uid = int((row or {}).get("user_id") or 0)
    attached_u = {k: v for k, v in pending_u.items() if k == uid}
    owned_grants = set()
    for g in cur.grants:
        if int(g.get("user_id") or 0) == uid:
            owned_grants.add(int(g.get("id") or 0))
    attached_g = {k: v for k, v in pending_g.items() if k in owned_grants}
    events[0]["usage"] = attached_u
    events[0]["grants"] = attached_g
    return events, {"usage": {k: v for k, v in pending_u.items() if k != uid},
                    "grants": {k: v for k, v in pending_g.items() if k not in owned_grants}}


# ── لاگ ────────────────────────────────────────────────────────────────────────────
_ANSWERED_RE = re.compile(r"qa: answered chat=(-?\d+) msg=(\d+) user=(-?\d+)")
_IGNORED_RE = re.compile(r"qa: ignored chat=(-?\d+) msg=(\d+) user=(-?\d+) reason=([a-z_]+)")
_AIFAIL_RE = re.compile(r"qa: ai failed chat=(-?\d+) msg=(\d+) user=(-?\d+) err=([a-z_]+)")

IGNORE_REASONS = ("unregistered_channel", "toggle_off", "foreign_group",
                  "not_a_reply", "not_a_post")


def parse_answered_lines(text):
    return [{"chat": int(m.group(1)), "msg": int(m.group(2)), "user": int(m.group(3))}
            for m in _ANSWERED_RE.finditer(text or "")]


def parse_ignore_lines(text):
    return [{"chat": int(m.group(1)), "msg": int(m.group(2)), "user": int(m.group(3)),
             "reason": m.group(4)} for m in _IGNORED_RE.finditer(text or "")]


def parse_ai_failed_lines(text):
    return [{"chat": int(m.group(1)), "msg": int(m.group(2)), "user": int(m.group(3)),
             "err": m.group(4)} for m in _AIFAIL_RE.finditer(text or "")]


# ── بررسی‌ها ──────────────────────────────────────────────────────────────────────
REQUIRED = {
    "core": ("preconditions", "channel_registered", "public_model_active",
             "answer_delivered", "channel_id_is_telegram_chat_id", "asker_charged_only",
             "answered_exactly_once", "no_secret_in_log"),
    "full": ("preconditions", "channel_registered", "public_model_active",
             "answer_delivered", "channel_id_is_telegram_chat_id", "asker_charged_only",
             "answered_exactly_once", "ignored_with_a_reason", "unrelated_group_silent",
             "toggle_off_silent", "quota_exhausted_message", "no_secret_in_log"),
}


@dataclass
class EvalContext:
    scenario: str = "core"
    started_at: str = ""
    expected_group: int | None = None
    expected_channel: int | None = None
    transitions: list = field(default_factory=list)
    snapshot: Snapshot = EMPTY_SNAPSHOT
    log_text: str = ""
    super_ids: set = field(default_factory=set)
    secret_values: dict = field(default_factory=dict)
    precondition: str | None = None


def _check(name, status, detail=""):
    return {"name": name, "status": status, "detail": detail}


def _run_rows(ctx, status=None):
    started = ctx.started_at or ""
    return [r for r in ctx.snapshot.qa_rows
            if str(r.get("created_at") or "") >= started
            and (status is None or r.get("status") == status)]


def _core_rows(ctx):
    if not ctx.expected_group:
        return []
    return [r for r in _run_rows(ctx, "done") if int(r.get("chat_id") or 0) == int(ctx.expected_group)]


def _row_deltas(ctx, key):
    usage, grants, ambiguous = {}, {}, False
    for t in ctx.transitions:
        if list(t.get("key") or []) != list(key):
            continue
        if t.get("ambiguous"):
            ambiguous = True
        for k, v in (t.get("usage") or {}).items():
            usage[int(k)] = usage.get(int(k), 0) + int(v)
        for k, v in (t.get("grants") or {}).items():
            grants[int(k)] = grants.get(int(k), 0) + int(v)
    return usage, grants, ambiguous


def evaluate(ctx):
    """یک رکورد برای هر بررسی؛ فقط شناسه و شمارش، هرگز متنِ سؤال/پاسخ/راز."""
    out = []
    cores = _core_rows(ctx)

    if ctx.precondition:
        out.append(_check("preconditions", "ERROR", "precondition: %s" % ctx.precondition))
    else:
        supers = [r for r in cores if int(r.get("user_id") or 0) in set(ctx.super_ids)]
        if supers:
            out.append(_check("preconditions", "ERROR",
                              "SUPER_ASKER: ask from a non-super account"))
        else:
            out.append(_check("preconditions", "OBSERVED"))

    known_channels = {int(c.get("chat_id") or 0) for c in ctx.snapshot.channels}
    if not ctx.expected_channel or not ctx.expected_group:
        out.append(_check("channel_registered", "NOT_OBSERVED", "channel/group not resolved"))
    elif int(ctx.expected_channel) in known_channels:
        out.append(_check("channel_registered", "OBSERVED"))
    else:
        out.append(_check("channel_registered", "NOT_OBSERVED", "no channels row for the channel"))

    public_models = [m for m in ctx.snapshot.models
                     if m.get("owner_id") is None and int(m.get("active") or 0) == 1]
    out.append(_check("public_model_active", "OBSERVED" if public_models else "NOT_OBSERVED",
                      "count=%d" % len(public_models)))

    out.append(_check("answer_delivered", "OBSERVED" if cores else "NOT_OBSERVED",
                      "count=%d" % len(cores)))

    if not cores:
        out.append(_check("channel_id_is_telegram_chat_id", "NOT_OBSERVED", "no done row"))
    elif all(int(r.get("channel_id") or 0) in known_channels for r in cores):
        out.append(_check("channel_id_is_telegram_chat_id", "OBSERVED"))
    else:
        out.append(_check("channel_id_is_telegram_chat_id", "NOT_OBSERVED",
                          "channel_id is not a Telegram chat id of a registered channel"))

    if not cores:
        out.append(_check("asker_charged_only", "NOT_OBSERVED", "no done row"))
    else:
        status, detail = "OBSERVED", ""
        for row in cores:
            usage, grants, ambiguous = _row_deltas(ctx, (int(row["chat_id"]), int(row["msg_id"])))
            if ambiguous:
                status, detail = ("ERROR",
                                  "two questions in one window; counters not attributable")
                break
            if not usage and not grants:
                status, detail = "NOT_OBSERVED", "no counter transition captured for the row"
                break
            if usage != {int(row.get("user_id") or 0): 1}:
                status, detail = "NOT_OBSERVED", "usage delta is not exactly one for the asker"
                break
            if grants:
                if len(grants) != 1 or list(grants.values())[0] != 1:
                    status, detail = "NOT_OBSERVED", "grant delta is not exactly one"
                    break
                gid = next(iter(grants))
                grant = next((g for g in ctx.snapshot.grants
                              if int(g.get("id") or 0) == gid), None)
                if not grant or int(grant.get("user_id") or 0) != int(row.get("user_id") or 0):
                    status, detail = "NOT_OBSERVED", "grant belongs to another user"
                    break
        out.append(_check("asker_charged_only", status, detail))

    answered = parse_answered_lines(ctx.log_text)
    if not cores:
        out.append(_check("answered_exactly_once", "NOT_OBSERVED", "no done row"))
    else:
        bad = [r for r in cores
               if len([a for a in answered if (a["chat"], a["msg"]) ==
                       (int(r["chat_id"]), int(r["msg_id"]))]) != 1]
        out.append(_check("answered_exactly_once", "NOT_OBSERVED" if bad else "OBSERVED",
                          "rows without exactly one answer line=%d" % len(bad)))

    ignored = parse_ignore_lines(ctx.log_text)
    unknown = [ln for ln in ignored if ln["reason"] not in IGNORE_REASONS]
    # فقط مسیرهای امنیتی میتوانند این بررسی را سبز کنند؛ not_a_reply/not_a_post
    # (که فقط DEBUG هستند) هیچ سدّی را اثبات نمیکنند و جای «سکوتِ عمدی» حساب نمیشوند.
    security = [ln for ln in ignored if ln["reason"] in
                ("unregistered_channel", "toggle_off", "foreign_group")]
    out.append(_check("ignored_with_a_reason",
                      "OBSERVED" if (security and not unknown) else "NOT_OBSERVED",
                      "count=%d security=%d unknown=%d" %
                      (len(ignored), len(security), len(unknown))))

    if not ctx.expected_group:
        out.append(_check("unrelated_group_silent", "NOT_OBSERVED", "group not resolved"))
    else:
        foreign = [ln for ln in ignored if ln["reason"] == "foreign_group"
                   and ln["chat"] != int(ctx.expected_group)]
        rows = {(int(r.get("chat_id") or 0), int(r.get("msg_id") or 0))
                for r in _run_rows(ctx)}
        bad = [ln for ln in foreign if (ln["chat"], ln["msg"]) in rows]
        out.append(_check("unrelated_group_silent",
                          "OBSERVED" if (foreign and not bad) else "NOT_OBSERVED",
                          "lines=%d with_row=%d" % (len(foreign), len(bad))))

    offs = [ln for ln in ignored if ln["reason"] == "toggle_off"]
    rows = {(int(r.get("chat_id") or 0), int(r.get("msg_id") or 0)) for r in _run_rows(ctx)}
    bad = [ln for ln in offs if (ln["chat"], ln["msg"]) in rows]
    out.append(_check("toggle_off_silent",
                      "OBSERVED" if (offs and not bad) else "NOT_OBSERVED",
                      "lines=%d with_row=%d" % (len(offs), len(bad))))

    exhausted = [r for r in _run_rows(ctx, "failed") if r.get("error") == "ai_limit"]
    failed_lines = parse_ai_failed_lines(ctx.log_text)
    if not exhausted:
        out.append(_check("quota_exhausted_message", "NOT_OBSERVED", "no failed/ai_limit row"))
    else:
        status, detail = "OBSERVED", ""
        for row in exhausted:
            key = (int(row["chat_id"]), int(row["msg_id"]))
            usage, grants, ambiguous = _row_deltas(ctx, key)
            if ambiguous:
                status, detail = ("ERROR",
                                  "two questions in one window; counters not attributable")
                break
            if usage or grants:
                status, detail = "NOT_OBSERVED", "counters moved for an exhausted row"
                break
            lines = [ln for ln in failed_lines if (ln["chat"], ln["msg"]) == key
                     and ln["err"] == "ai_limit"]
            if len(lines) != 1:
                status, detail = "NOT_OBSERVED", "ai_limit must be logged exactly once"
                break
            fresh = [g for g in ctx.snapshot.grants
                     if int(g.get("user_id") or 0) == int(row.get("user_id") or 0)
                     and str(g.get("granted_at") or "") >= (ctx.started_at or "")
                     and int(g.get("quota") or 0) == 1 and int(g.get("used") or 0) == 1]
            if not fresh:
                status, detail = "NOT_OBSERVED", "no exhausted grant period created in this run"
                break
        out.append(_check("quota_exhausted_message", status, detail))

    no_text = [r for r in _run_rows(ctx, "failed") if r.get("error") == "no_text"]
    if not no_text:
        out.append(_check("no_text_no_ai", "NOT_APPLICABLE", "optional stage not performed"))
    else:
        moved = [r for r in no_text
                 if any(_row_deltas(ctx, (int(r["chat_id"]), int(r["msg_id"])))[:2])]
        out.append(_check("no_text_no_ai", "NOT_OBSERVED" if moved else "OBSERVED",
                          "rows=%d charged=%d" % (len(no_text), len(moved))))

    leaked = secret_values_present(ctx.log_text, ctx.secret_values)
    out.append(_check("no_secret_in_log", "NOT_OBSERVED" if leaked else "OBSERVED",
                      "found=%s" % ",".join(leaked) if leaked else ""))
    return out


# ── سوپروایزرِ فرزند ──────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READY_MARKER = "در حال اجرا"
READY_ATTEMPTS = 60
READY_INTERVAL_S = 2


class Precondition(Exception):
    """پیش‌شرطی که اجرا را متوقف می‌کند؛ کد به گزارش می‌رود (خروجی ۲)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass
class SuperviseConfig:
    db_path: str
    log_path: str
    duration_s: int
    scenario: str = "core"
    super_ids: set = field(default_factory=set)
    secret_values: dict = field(default_factory=dict)
    poll_s: int = POLL_SECONDS
    env: dict = field(default_factory=dict)
    report_path: str = ""


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def blank_answers(db_path):
    """متنِ پاسخ‌ها را پیش از کش‌شدنِ DB پاک می‌کند؛ ردیف و status دست‌نخورده می‌مانند."""
    if not db_path or not os.path.isfile(db_path):
        return
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            conn.execute("UPDATE qa_answers SET answer=''")
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def write_report(path, report):
    if not path:
        return
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


class BotProcess:
    """فرزندِ ربات + فایلِ لاگش؛ فایل تا پایانِ اجرا باز می‌ماند."""

    def __init__(self, proc, fh):
        self.proc, self.fh = proc, fh

    def terminate(self):
        self.proc.terminate()

    def wait(self, timeout=None):
        return self.proc.wait(timeout=timeout)

    def kill(self):
        self.proc.kill()

    def poll(self):
        return self.proc.poll()

    def close(self):
        try:
            self.fh.close()
        except OSError:
            pass


def spawn_bot(env, log_path):
    parent = os.path.dirname(os.path.abspath(log_path))
    os.makedirs(parent, exist_ok=True)
    fh = open(log_path, "w", encoding="utf-8", errors="replace")
    child_env = dict(os.environ)
    child_env.update(dict(env or {}))
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen([sys.executable, "run.py"], cwd=REPO_ROOT, env=child_env,
                            stdout=fh, stderr=subprocess.STDOUT)
    return BotProcess(proc, fh)


def default_resolve(env, snapshot):
    """کانالِ ثبت‌شده و گروهِ بحثِ لینک‌شده‌اش را از DB و یک getChatِ فقط-خواندنی می‌گیرد."""
    rows = list(snapshot.channels)
    if not rows:
        raise Precondition("CHANNEL_NOT_REGISTERED")
    chat_id = int(rows[-1].get("chat_id") or 0)
    if not chat_id:
        raise Precondition("CHANNEL_NOT_REGISTERED")
    url = "%s/bot%s/getChat?chat_id=%d" % (api_base(env), (env.get("BOT_TOKEN") or "").strip(),
                                           chat_id)
    try:
        data = http_get_json(url, timeout=10)
    except Exception:                                        # noqa: BLE001
        raise Precondition("GETCHAT_FAILED") from None
    linked = (data.get("result") or {}).get("linked_chat_id")
    if not linked:
        raise Precondition("NO_LINKED_GROUP")
    return chat_id, int(linked)


def _stop(proc):
    try:
        proc.terminate()
    except Exception:                                        # noqa: BLE001
        pass
    try:
        proc.wait(timeout=STOP_GRACE_SECONDS)
    except Exception:                                        # noqa: BLE001
        try:
            proc.kill()
        except Exception:                                    # noqa: BLE001
            pass
    close = getattr(proc, "close", None)
    if callable(close):
        try:
            close()
        except Exception:                                    # noqa: BLE001
            pass


def supervise(cfg, *, spawn, snapshot=read_snapshot, read_log=read_text,
              clock=time.monotonic, sleep=time.sleep, resolve, now=utc_now_iso,
              evaluate_fn=None):
    """ربات را بالا می‌آورد، هر poll_s اسنپ‌شات می‌گیرد و در پایان گزارش می‌نویسد."""
    eval_fn = evaluate_fn if evaluate_fn is not None else evaluate
    started_at = now()
    db_reused = bool(cfg.db_path and os.path.isfile(cfg.db_path))
    problems, checks, proc, log_text = [], [], None, ""
    code = 1
    observed = False
    t0 = clock()
    ready_t, ready_at = None, None

    try:
        if not (MIN_DURATION_MIN * 60 <= int(cfg.duration_s) <= MAX_DURATION_MIN * 60):
            raise Precondition("BAD_DURATION")
        proc = spawn()
        # حلقهٔ آمادگی از clock تزریقشده استفاده نمیکند: یک fake clock میتوانست
        # بیپایان بچرخد. تعداد تلاشها محدود و ثابت است.
        ready = False
        for _ in range(READY_ATTEMPTS):
            log_text = read_log(cfg.log_path)
            if READY_MARKER in log_text:
                ready = True
                ready_t, ready_at = clock(), now()
                break
            sleep(READY_INTERVAL_S)
        if not ready:
            raise Precondition("BOT_NOT_READY")
        channel_chat_id = group_chat_id = None
        resolve_error = ""
        prev = snapshot(cfg.db_path)
        transitions = []
        carry = {}
        while True:
            sleep(cfg.poll_s)
            cur = snapshot(cfg.db_path)
            if channel_chat_id is None:
                # اجرای `fresh` با DB خالی شروع میشود: کانال و مدل را مالک *پس از*
                # بالا آمدن ربات در پنل ثبت میکند. تا آن زمان منتظر میمانیم، نه اینکه
                # پیش از رسیدنِ دستورِ ثبت، اجرا را با خطای پیششرط تمام کنیم.
                try:
                    channel_chat_id, group_chat_id = resolve(cur)
                    resolve_error = ""
                except Precondition as e:
                    resolve_error = e.code
            events, carry = attribute_carry(prev, cur, carry)
            transitions.extend(events)
            prev = cur
            log_text = read_log(cfg.log_path)
            ctx = EvalContext(scenario=cfg.scenario, started_at=started_at,
                              expected_group=group_chat_id, expected_channel=channel_chat_id,
                              transitions=list(transitions), snapshot=cur, log_text=log_text,
                              super_ids=set(cfg.super_ids or ()),
                              secret_values=dict(cfg.secret_values or {}))
            checks = eval_fn(ctx)
            by_name = {c["name"]: c for c in checks}
            pre = by_name.get("preconditions") or {}
            if pre.get("status") == "ERROR":
                problems.append("PRECONDITION:" + str(pre.get("detail") or ""))
                code = 2
                break
            required = REQUIRED.get(cfg.scenario, REQUIRED["core"])
            observed = all((by_name.get(n) or {}).get("status") == "OBSERVED" for n in required)
            # `duration` یک burn-in است: وظیفهاش پرکردنِ زمانِ فعال است، نه خروجِ زودهنگام.
            if observed and cfg.scenario != "duration":
                code = 0
                break
            if clock() - ready_t >= cfg.duration_s:
                if channel_chat_id is None:
                    problems.append(resolve_error or "CHANNEL_NOT_REGISTERED")
                    code = 2
                else:
                    code = 0 if observed else 1
                break
    except Precondition as e:
        problems.append(e.code)
        code = 2
    finally:
        if proc is not None:
            _stop(proc)
        stop_t = clock()
        stopped_at = now()
        if ready_t is None:
            # READY دیده نشد: کل زمانِ سپریشده راهاندازی است و زمانِ فعال صفر.
            ready_t = stop_t
        log_text = read_log(cfg.log_path) or log_text
        blank_answers(cfg.db_path)

    report_t = clock()
    active_s = stop_t - ready_t
    provider_credentials_present = _env_flag(cfg.env, "PROVIDER_CREDENTIALS_PRESENT")
    boundaries = [
        {"surface": "telegram_bot", "status": "real",
         "note": "real token, real client traffic"},
        {"surface": "temporary_db", "status": "real",
         "note": "throwaway data, never uploaded"},
        {"surface": "provider_ai",
         "status": "real" if provider_credentials_present else "simulated",
         "note": "the workflow supplies no provider keys, so a normal Phase 9 dispatch is "
                 "simulated; a D-7 dispatch with explicit provider credentials reports real"},
        {"surface": "real_channel_publication", "status": "not_performed",
         "note": "needs explicit owner authorization"},
        {"surface": "runner_hard_cap", "status": "real",
         "note": "platform limit; timeout-minutes: 360"},
    ]
    report = {
        "schema": SCHEMA,
        "started_at": started_at,
        "duration_s": int(cfg.duration_s),
        "scenario": cfg.scenario,
        "db_reused": db_reused,
        "checks": checks,
        "exit_code": code,
        "scenario_mode": "hold_to_budget" if cfg.scenario == "duration" else "early_exit",
        "provider_credentials_present": provider_credentials_present,
        "boundaries": boundaries,
        "redactions": ["BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "SUPER_ADMIN_ID",
                       "SUPER_ADMIN_IDS", "SECRETS_KEY"],
        "bot_log_lines": len((log_text or "").splitlines()),
        "log_sha256": hashlib.sha256((log_text or "").encode("utf-8")).hexdigest(),
        "problems": problems,
        "ready_at": ready_at,
        "stopped_at": stopped_at,
        "startup_duration_s": round(ready_t - t0),
        "active_duration_s": round(active_s),
        "cleanup_duration_s": round(report_t - stop_t),
        "configured_duration_s": round(cfg.duration_s),
        "delta_vs_350_s": round(active_s - 350 * 60),
        "duration_basis": "ready_to_shutdown",
    }
    write_report(cfg.report_path, report)
    return code, report




def _env_flag(env, name):
    """فقط پرچمِ نامدار، نه مقدارِ کلید: 1/true/yes به معنای حضورِ اعتبار است."""
    return str((env or {}).get(name) or "").strip().lower() in ("1", "true", "yes")


def supervised_secret_values(env):
    """مقدارهای بررسیِ نشت: توکن، کلید و فقط شناسهٔ سوپرِ شش رقمی یا بیشتر."""
    values = {"BOT_TOKEN": (env or {}).get("BOT_TOKEN") or "",
              "SECRETS_KEY": (env or {}).get("SECRETS_KEY") or ""}
    # برچسبها نباید خودِ شناسه را لو بدهند: secret_values_present نامِ کلید را
    # به تفصیلِ بررسی مینویسد و آن تفصیل در report ذخیره میشود.
    long_ids = sorted(sid for sid in admin_ids((env or {}).get("SUPER_ADMIN_IDS"))
                      if len(str(sid)) >= 6)
    for index, super_id in enumerate(long_ids, start=1):
        values["SUPER_ADMIN_ID#%d" % index] = str(super_id)
    return values


def main(argv=None):
    ap = argparse.ArgumentParser(description="پذیرشِ زندهٔ Q&A فاز ۴")
    ap.add_argument("--check", action="store_true", help="فقط پیش‌شرط‌ها")
    ap.add_argument("--supervise", action="store_true", help="اجرای کاملِ نظارت‌شده")
    ap.add_argument("--duration", type=int, default=350, help="سقفِ مدت به دقیقه (۵ تا ۳۵۵)")
    ap.add_argument("--scenario", choices=("core", "full", "duration"), default="core")
    ap.add_argument("--report", default="", help="مسیرِ گزارشِ JSON")
    ap.add_argument("--log", default="", help="مسیرِ لاگِ فرزند")
    ap.add_argument("--env-file", default=os.environ.get("NEWSBOT_ENV_FILE", ""),
                    help="مسیرِ فایل env (مقدارها هرگز چاپ نمی‌شوند)")
    args = ap.parse_args(argv)

    env = load_env(args.env_file or None)
    if args.check:
        code, out = run_check(env)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return code
    if not args.supervise:
        ap.error("یکی از --check یا --supervise لازم است")

    env["DB_BACKEND"] = env.get("DB_BACKEND") or "sqlite"
    env["QA_ENABLED"] = env.get("QA_ENABLED") or "1"
    check_code, check_out = run_check(env)
    if check_code != 0:
        print(json.dumps(check_out, ensure_ascii=False, indent=2))
        return check_code

    # پرچمِ اعتبارِ ارائهدهنده بهصورتِ نامِ متغیر منتقل میشود، نه مقدارِ کلید.
    env["PROVIDER_CREDENTIALS_PRESENT"] = (
        "1" if _env_flag(env, "PROVIDER_CREDENTIALS_PRESENT") else "0")
    db_path = (env.get("DB_FILE") or "").strip()
    log_path = args.log or os.path.join(os.path.dirname(os.path.abspath(db_path)), "bot.log")
    cfg = SuperviseConfig(db_path=db_path, log_path=log_path,
                          duration_s=int(args.duration) * 60, scenario=args.scenario,
                          super_ids=admin_ids(env.get("SUPER_ADMIN_IDS")),
                          secret_values=supervised_secret_values(env),
                          env=env, report_path=args.report)
    code, report = supervise(cfg, spawn=lambda: spawn_bot(env, log_path),
                             resolve=lambda snap: default_resolve(env, snap))
    print(json.dumps({"exit_code": code, "checks": report.get("checks"),
                      "problems": report.get("problems")}, ensure_ascii=False, indent=2))
    return code



if __name__ == "__main__":
    sys.exit(main())
