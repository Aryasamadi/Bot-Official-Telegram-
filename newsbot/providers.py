# -*- coding: utf-8 -*-
"""رجیستریِ «فراهم‌کنندهٔ مدل» (فاز ۲) — یک ردیف به‌ازای (محدودهٔ مالک × میزبان × کلید).

پیش از این فاز هر مدل یک ردیفِ تخت در ``ai_models`` بود و همان ردیف هم «کدام فراهم‌کننده»
(میزبان + کلید) را نگه می‌داشت و هم «کدام مدل» را. نتیجه: تکرارِ کلید در N ردیف، چرخشِ
کلید با ویرایشِ N ردیف، و هیچ جای واحدی برای «آخرین بار این میزبان در دسترس بود؟».

حالا:

* ``ai_providers`` مالکِ **هویت و کلید** است (رمزنگاری‌شده با ``SECRETS_KEY``) + وضعیتِ
  آخرین آزمونِ اتصال.
* ``ai_models.provider_id`` (افزایشی و nullable) به آن اشاره می‌کند؛ ردیف‌های قدیمی تا
  مهاجرت، ``NULL`` می‌مانند و مسیرِ خواندن به ``base_url``/``api_key`` خودشان برمی‌گردد.

ADR-۲/ADR-۴ فایلِ وضعیت (فاز ۱) این مرز را الزامی کرده‌اند. چرخشِ round-robin و مکان‌نمای
اتمی **فاز ۳** است و اینجا عمداً پیاده نشده.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlparse

from newsbot.db import q
from newsbot.secret_store import decrypt as _dec, encrypt as _enc

# مهلتِ آزمونِ اتصال — کوتاه و固定: این تماس روی مسیرِ راه‌اندازی/مدیریت است، نه مسیرِ نشر.
TEST_TIMEOUT = 15
# سقفِ حجمِ پاسخِ لیستِ مدل‌ها (هدرِ content-length و شمارشِ واقعی بایت)
MAX_DISCOVERY_BYTES = 2 * 1024 * 1024
# نامِ فیلدهایی که در پیامِ خطا/لاگ **هرگز** ظاهر نمی‌شوند
_SECRET_FIELDS = ("api_key", "token", "authorization", "x-api-key")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def kind_of(base_url: str) -> str:
    """نوعِ فراهم‌کننده از روی میزبان — همان منطقِ ``ai_models.detect_kind`` (یک منبعِ حقیقت)."""
    b = (base_url or "").lower()
    if "anthropic.com" in b:
        return "anthropic"
    if "generativelanguage.googleapis.com" in b:
        return "gemini"
    return "openai"


def normalize_base_url(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


def _hostname(u: str) -> str:
    try:
        return urlparse(u).netloc.replace("www.", "") or u
    except Exception:
        return str(u)


def _mask(key: str) -> str:
    """نمایشِ امنِ کلید: فقط طول و ۴ کاراکترِ آخر (SEC-۹). هرگز کلیدِ کامل در UI/لاگ."""
    k = str(key or "")
    if not k:
        return ""
    return "…" + k[-4:] if len(k) > 4 else "…"


def _public(row, with_key: bool = False):
    """ردیفِ آمادهٔ JSON — کلید فقط رمزنگاری‌شده/ماسک‌شده خارج می‌شود."""
    if row is None:
        return None
    d = dict(row)
    enc = d.get("api_key") or ""
    d["api_key"] = enc if with_key else _mask(_dec(enc) if enc else "")
    d["has_key"] = bool(enc)
    return d


# ── خواندن ────────────────────────────────────────────────────────────────────────
def list_providers(owner_id=None, include_disabled: bool = True):
    """owner_id=None ⇒ فراهم‌کننده‌های عمومیِ مدیر کلان · عدد ⇒ محدودهٔ همان مدیر."""
    sql = "SELECT * FROM ai_providers WHERE " + ("owner_id=?" if owner_id else "owner_id IS NULL")
    if not include_disabled:
        sql += " AND enabled=1"
    rows = q(sql + " ORDER BY name, id", (owner_id,) if owner_id else ())
    return [_public(r) for r in rows]


def get_provider(pid):
    return _public(q("SELECT * FROM ai_providers WHERE id=?", (pid,), one=True))


def get_provider_row(pid):
    return q("SELECT * FROM ai_providers WHERE id=?", (pid,), one=True)


def provider_model_count(pid) -> int:
    row = q("SELECT COUNT(*) AS n FROM ai_models WHERE provider_id=?", (pid,), one=True)
    return int(row["n"]) if row else 0


def provider_models(pid, active_only: bool = False):
    sql = "SELECT id, name, model, priority, active, status FROM ai_models WHERE provider_id=?"
    if active_only:
        sql += " AND active=1"
    return q(sql + " ORDER BY priority, id", (pid,))


def find_provider(base_url: str, owner_id=None):
    """تشخیصِ تکراری‌نبودن: یک (محدوده × میزبان) فقط یک فراهم‌کننده دارد."""
    return q("SELECT * FROM ai_providers WHERE base_url=? AND " + ("owner_id=?" if owner_id else "owner_id IS NULL"),
             (normalize_base_url(base_url), owner_id) if owner_id else (normalize_base_url(base_url),), one=True)


# ── نوشتن ────────────────────────────────────────────────────────────────────────
def create_provider(name: str, base_url: str, api_key: str, owner_id=None, enabled: int = 1) -> int:
    """افزودنِ فراهم‌کننده. کلید همیشه رمزنگاری‌شده ذخیره می‌شود (SEC-۹)."""
    base_url = normalize_base_url(base_url)
    if not base_url:
        raise ValueError("base_url الزامی است")
    if find_provider(base_url, owner_id):
        raise ValueError("فراهم‌کننده‌ای با همین نشانی از قبل ثبت شده است")
    name = (str(name or "").strip() or _hostname(base_url))[:60]
    return q("INSERT INTO ai_providers(owner_id,name,kind,base_url,api_key,enabled,created_at) "
             "VALUES(?,?,?,?,?,?,?)",
             (owner_id, name, kind_of(base_url), base_url, _enc(str(api_key or "").strip()), int(enabled), _now()),
             commit=True)


def update_provider(pid: int, **fields):
    """ویرایشِ فیلدهای مجاز. ``api_key`` پیش از ذخیره رمزنگاری می‌شود."""
    allowed = ("name", "base_url", "api_key", "enabled")
    f = {k: v for k, v in fields.items() if k in allowed}
    if not f:
        return
    if "base_url" in f:
        f["base_url"] = normalize_base_url(f["base_url"])
        f["kind"] = kind_of(f["base_url"])
    if "api_key" in f:
        f["api_key"] = _enc(str(f["api_key"]).strip())
    if "enabled" in f:
        f["enabled"] = int(bool(f["enabled"]))
    cols = []
    for k in f:
        if k not in ("name", "base_url", "api_key", "enabled", "kind"):
            continue
        cols.append(k)
    if not cols:
        return
    q("UPDATE ai_providers SET " + ", ".join(f"{c}=?" for c in cols) + " WHERE id=?",
      (*[f[c] for c in cols], pid), commit=True)


def delete_provider(pid: int):
    """حذف فقط وقتی هیچ مدلی به آن اشاره نکند (C7) — حذفِ مدل‌ها کارِ مدیر است، نه پیامدِ حذف."""
    n = provider_model_count(pid)
    if n:
        raise ValueError("این فراهم‌کننده به %d مدل وصل است؛ اول مدل‌ها را حذف یا جابه‌جا کنید" % n)
    q("DELETE FROM ai_providers WHERE id=?", (pid,), commit=True)


def set_test_result(pid: int, ok: bool, error: str = ""):
    """نتیجهٔ آخرین آزمونِ اتصال — برای نمایش در UI، بدونِ افشای کلید."""
    q("UPDATE ai_providers SET last_test_at=?, last_test_ok=?, last_test_error=? WHERE id=?",
      (_now(), 1 if ok else 0, str(error or "")[:300] if not ok else "", pid), commit=True)


# ── آزمونِ اتصال و کشفِ مدل ────────────────────────────────────────────────────────
def _models_url(base_url: str) -> str:
    return normalize_base_url(base_url) + "/models"


def _auth_headers(api_key: str, kind: str):
    if not api_key:
        return {}
    if kind == "anthropic":
        # Anthropic کلید را در x-api-key می‌خواهد، نه Authorization
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    if kind == "gemini":
        return {"x-goog-api-key": api_key}
    return {"Authorization": "Bearer " + api_key}


def _parse_model_ids(payload: str):
    """فهرستِ شناسهٔ مدل از پاسخِ OpenAI-compatible (``{"data":[{"id":…}]}``) یا Gemini (``{"models":[…]}``)."""
    try:
        data = json.loads(payload)
    except Exception:
        return None, "پاسخِ سرور JSON معتبر نبود"
    ids = []
    if isinstance(data, dict):
        items = data.get("data")
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    ids.append(str(it["id"]))
                elif isinstance(it, str):
                    ids.append(it)
        items = data.get("models")
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    name = it.get("name") or it.get("id")
                    if name:
                        # Gemini شناسه را به شکلِ «models/gemini-…» می‌دهد
                        ids.append(str(name).split("models/", 1)[-1])
    elif isinstance(data, list):
        for it in data:
            if isinstance(it, dict) and it.get("id"):
                ids.append(str(it["id"]))
            elif isinstance(it, str):
                ids.append(it)
    seen, out = set(), []
    for i in ids:
        i = i.strip()
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    if not out:
        return None, "پاسخِ سرور هیچ مدلی نداشت"
    return out, ""


async def probe(base_url: str, api_key: str, *, kind: str | None = None, timeout: int = TEST_TIMEOUT):
    """تماسِ واقعی با ``GET {base_url}/models``.

    از مسیرِ امنِ ``net_http.fetch`` می‌رود (SSRF + سقفِ حجم + مهلتِ کل). کلید هرگز در
    ``FetchError`` یا پیامِ خطا تکرار نمی‌شود. خروجی: ``(ok, ids, error)``.
    """
    from newsbot.net_http import fetch

    base_url = normalize_base_url(base_url)
    if not base_url:
        return False, [], "نشانیِ فراهم‌کننده خالی است"
    kind = kind or kind_of(base_url)
    url = _models_url(base_url)
    try:
        r = await fetch(url, headers=_auth_headers(api_key, kind), timeout=timeout)
    except Exception as e:                      # FetchError و خطاهای شبکه
        return False, [], _safe_error(e, api_key)
    body = r.text if r.status_code < 400 else ""
    if r.status_code >= 400:
        return False, [], "HTTP %s از %s" % (r.status_code, _hostname(base_url))
    if len(body.encode("utf-8", "replace")) > MAX_DISCOVERY_BYTES:
        return False, [], "پاسخِ بزرگ‌تر از سقفِ مجاز"
    ids, err = _parse_model_ids(body)
    if ids is None:
        return False, [], err
    return True, ids, ""


def _safe_error(e, api_key: str) -> str:
    """متنِ خطا بدونِ افشای کلید یا هدرِ Authorization."""
    txt = str(e)
    key = str(api_key or "")
    if key:
        txt = txt.replace(key, "***")
    for f in _SECRET_FIELDS:
        if f in txt:
            txt = "خطای شبکه/میزبان (جزئیات در لاگ)"
            break
    return txt[:300] or "خطای نامشخص"


async def test_connection(pid: int):
    """آزمونِ اتصالِ یک فراهم‌کنندهٔ ثبت‌شده و ثبتِ نتیجه در همان ردیف."""
    row = get_provider_row(pid)
    if row is None:
        return {"ok": False, "error": "چنین فراهم‌کننده‌ای وجود ندارد", "ids": []}
    key = _dec(row["api_key"]) if row["api_key"] else ""
    ok, ids, err = await probe(row["base_url"], key, kind=row["kind"])
    set_test_result(pid, ok, err)
    return {"ok": ok, "error": err, "ids": ids,
            "provider": _hostname(row["base_url"]), "model_count": len(ids)}


async def discover_models(pid: int):
    """کشفِ مدل‌های یک فراهم‌کننده. خروجی فقط **کاندید** است؛ ثبتِ آن‌ها تصمیمِ مدیر است (C6)."""
    row = get_provider_row(pid)
    if row is None:
        return {"ok": False, "error": "چنین فراهم‌کننده‌ای وجود ندارد", "ids": []}
    key = _dec(row["api_key"]) if row["api_key"] else ""
    ok, ids, err = await probe(row["base_url"], key, kind=row["kind"])
    set_test_result(pid, ok, err)
    return {"ok": ok, "error": err, "ids": ids, "provider": _hostname(row["base_url"])}


def add_model_from_discovery(pid: int, model_id: str, *, name: str = "", priority: int = 10):
    """افزودنِ یک مدلِ انتخاب‌شده از میانِ کاندیدها (هرگز خودکار «همه» را اضافه نمی‌کند)."""
    from newsbot import ai_models

    row = get_provider_row(pid)
    if row is None:
        raise ValueError("فراهم‌کننده نامعتبر است")
    model_id = str(model_id or "").strip()
    if not model_id:
        raise ValueError("شناسهٔ مدل خالی است")
    # مدل از نظرِ هویت به فراهم‌کننده وصل است؛ کلید در ``ai_models`` خالی می‌ماند تا
    # تنها منبعِ حقیقتِ کلید همان یک ردیف باشد.
    return ai_models.add_model(row["base_url"], "", model_id, name=name, priority=priority,
                               owner_id=row["owner_id"], provider_id=pid)


def health_snapshot():
    """خلاصهٔ آماری برایِ پنلِ مدیر کلان — بدونِ کلید، بدونِ محتوای مدل‌ها."""
    rows = q("SELECT COUNT(*) AS n, SUM(enabled) AS en FROM ai_providers", (), one=True)
    total = int(rows["n"]) if rows else 0
    enabled = int(rows["en"] or 0) if rows else 0
    bad = q("SELECT COUNT(*) AS n FROM ai_providers WHERE last_test_ok=0 AND last_test_at<>''", (), one=True)
    return {"providers": total, "enabled": enabled, "failing": int(bad["n"]) if bad else 0}
