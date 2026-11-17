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
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

SCHEMA = 1
POLL_SECONDS = 10
STOP_GRACE_SECONDS = 15
MIN_DURATION_MIN = 5
MAX_DURATION_MIN = 355

KNOWN_ENV = (
    "BOT_TOKEN", "SUPER_ADMIN_IDS", "SECRETS_KEY", "DB_FILE", "DB_BACKEND",
    "QA_ENABLED", "TELEGRAM_API_BASE", "LOG_FILE",
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
    """نامِ رازهایی که مقدارشان در متن آمده — هرگز خودِ مقدار برنمی‌گردد."""
    names = []
    for name, value in values.items():
        if value and len(value) >= 8 and value in text:
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


def attribute(prev, cur):
    """رویدادهای پنجره + انتسابِ شمارنده‌ها.

    اگر در یک پنجره فقط **یک** ردیفِ سؤال تغییر کرده باشد، اختلافِ شمارنده‌ها به همان
    ردیف نسبت داده می‌شود؛ اگر دو ردیفِ متفاوت تغییر کرده باشند، نتیجه `ambiguous` است و
    ابزار حدس نمی‌زند (اجرا شکست می‌خورد و آن مرحله تکرار می‌شود).
    """
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
    if not events:
        return []
    if len({tuple(e["key"]) for e in events}) > 1:
        for e in events:
            e["ambiguous"] = True
        return events
    u, g = usage_delta(prev, cur), grant_delta(prev, cur)
    if u or g:
        events[0]["usage"], events[0]["grants"] = u, g
    return events


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
    out.append(_check("ignored_with_a_reason",
                      "NOT_OBSERVED" if (not ignored or unknown) else "OBSERVED",
                      "count=%d unknown=%d" % (len(ignored), len(unknown))))

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



def main(argv=None):
    ap = argparse.ArgumentParser(description="پذیرشِ زندهٔ Q&A فاز ۴")
    ap.add_argument("--check", action="store_true", help="فقط پیش‌شرط‌ها")
    ap.add_argument("--env-file", default=os.environ.get("NEWSBOT_ENV_FILE", ""),
                    help="مسیرِ فایل env (مقدارها هرگز چاپ نمی‌شوند)")
    args = ap.parse_args(argv)

    env = load_env(args.env_file or None)
    if args.check:
        code, out = run_check(env)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return code
    ap.error("یکی از --check یا --supervise لازم است")
    return 2


if __name__ == "__main__":
    sys.exit(main())
