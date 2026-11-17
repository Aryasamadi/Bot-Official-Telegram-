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
import sys
import urllib.error
import urllib.request

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
