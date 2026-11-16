# Phase 4 — Reply-to-channel-post Q&A + plan-level AI quota — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer questions posted as replies to a channel post in the channel's linked discussion group — fail-closed, at-most-once, charged to the asker's AI quota — and make that quota a first-class, per-plan, globally-enforced allowance shown before purchase and in the user panel.

**Architecture:** A new `newsbot/qa.py` owns post detection, capped context building, an atomic `INSERT OR IGNORE` claim, and the group handler. A new `newsbot/ai_quota.py` owns per-plan grants (`ai_plan_grants`) and atomic consumption, wired into the existing `ai_claim` gate in `newsbot/core_users.py`. Schema v12 adds `qa_answers`, `ai_plan_grants` and `plans.ai_quota`. Activation is a per-channel toggle in the admin panel plus an env kill-switch.

**Tech Stack:** Python 3.13, python-telegram-bot v20+, SQLite (default) and Cloudflare D1, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-16-phase4-reply-qa-design.md`

## Global Constraints

- UI/report text in Persian; technical identifiers in English.
- No secret values in code, logs, commits, PRs or artifacts — names/presence/length only.
- Every behaviour or schema change lands with a test; TDD: the test must fail first.
- `SCHEMA_VERSION` 11 → 12 exactly once (Task 1); sqlite **and** D1 must both work.
- Owner decisions are binding: **fail-closed** group validation; **at-most-once** delivery; quota charged to the **asker** (not the channel owner, not the public pool); **admin-panel toggle** for the feature; `qa_answers.channel_id` = **Telegram chat id**; plan-level AI quota is **global** (bot + Q&A), shown **before purchase** and in the **user panel**, and stops new AI requests when exhausted.
- `ruff check newsbot tests tools run.py` and `pyflakes newsbot tests` must be clean.
- `tests/test_phase1_10_state_alignment.py::test_readme_test_count_matches_collection` requires the README badge and the `# N تست` line to equal `pytest --collect-only -q` exactly.
- Each task ends with a commit; each PR is rebased on current `main`, squashed, gate lifted and restored.

## Review Focus

1. A group that is not the channel's linked discussion group — or a manually forwarded copy of the post — must get **no** answer (fail-closed). Test in Task 3.
2. The same Telegram update processed twice (restart, network retry, `concurrent_updates`) must produce exactly **one** delivered answer. Test in Task 3.
3. A user whose plan quota is exhausted must get **no** AI answer and must not consume another user's quota. Test in Task 5.
4. Two concurrent AI requests when one unit remains must not both succeed. Test in Task 5.
5. A media-only post or an empty question must get an honest fixed message — never an AI hallucination or a crash. Test in Task 3.

---

### Task 1: Schema v12 — `qa_answers`, `ai_plan_grants`, `plans.ai_quota`

**Files:**
- Modify: `newsbot/db.py` (`SCHEMA` block near line 32-86, `MIGRATIONS` near line 168)
- Modify: `newsbot/__init__.py` (line 12)
- Modify: `tools/migrate_sqlite_to_d1.py` (`TABLES`)
- Test: `tests/test_phase4_schema.py` (create)

**Interfaces:**
- Produces: tables `qa_answers(chat_id, msg_id, user_id, channel_id, post_id, status, answer, error, created_at, updated_at)` with `PRIMARY KEY(chat_id, msg_id)`; `ai_plan_grants(id, user_id, plan_id, quota, used, granted_at, expires_at)` with `UNIQUE(user_id, expires_at)`; column `plans.ai_quota INTEGER DEFAULT 0`; `SCHEMA_VERSION == 12`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phase4_schema.py
from newsbot import SCHEMA_VERSION
from newsbot.db import q

def test_schema_version_is_12(isolated_db):
    assert SCHEMA_VERSION == 12

def test_the_qa_answers_table_has_the_expected_columns(isolated_db):
    cols = {r["name"] for r in q("PRAGMA table_info(qa_answers)")}
    assert cols == {"chat_id", "msg_id", "user_id", "channel_id", "post_id",
                    "status", "answer", "error", "created_at", "updated_at"}

def test_qa_answers_is_keyed_by_chat_and_message(isolated_db):
    pk = [r["name"] for r in q("PRAGMA table_info(qa_answers)") if r["pk"]]
    assert pk == ["chat_id", "msg_id"]

def test_the_ai_plan_grants_table_has_a_unique_user_period(isolated_db):
    cols = {r["name"] for r in q("PRAGMA table_info(ai_plan_grants)")}
    assert cols == {"id", "user_id", "plan_id", "quota", "used", "granted_at", "expires_at"}
    idx = [r["name"] for r in q("PRAGMA index_list(ai_plan_grants)")]
    assert any("user" in (n or "") for n in idx)

def test_plans_has_an_ai_quota_column(isolated_db):
    assert "ai_quota" in {r["name"] for r in q("PRAGMA table_info(plans)")}

def test_the_d1_migration_carries_the_phase4_tables():
    from tools.migrate_sqlite_to_d1 import TABLES
    assert {"qa_answers", "ai_plan_grants"} <= set(TABLES)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_schema.py -q`
Expected: FAIL — `SCHEMA_VERSION == 11`, tables missing, `ImportError` on `TABLES` membership.

- [ ] **Step 3: Implement the schema**

In `newsbot/db.py` append to `SCHEMA` (after the Phase 3 block, comments in Persian):

```sql
-- فاز ۴: پاسخِ سؤالِ replyشده به پستِ کانال (کلید = پیامِ کاربر ⇒ at-most-once)
CREATE TABLE IF NOT EXISTS qa_answers(chat_id INTEGER, msg_id INTEGER, user_id INTEGER, channel_id INTEGER, post_id INTEGER DEFAULT 0, status TEXT DEFAULT 'pending', answer TEXT DEFAULT '', error TEXT DEFAULT '', created_at TEXT, updated_at TEXT, PRIMARY KEY(chat_id, msg_id));
CREATE INDEX IF NOT EXISTS idx_qa_answers_user ON qa_answers(user_id);
CREATE INDEX IF NOT EXISTS idx_qa_answers_channel ON qa_answers(channel_id);
-- فاز ۴: سهمیهٔ AI هر دورهٔ اشتراک (سراسری: ربات + Q&A)
CREATE TABLE IF NOT EXISTS ai_plan_grants(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan_id INTEGER, quota INTEGER DEFAULT 0, used INTEGER DEFAULT 0, granted_at TEXT, expires_at TEXT, UNIQUE(user_id, expires_at));
CREATE INDEX IF NOT EXISTS idx_ai_grants_user ON ai_plan_grants(user_id);
```

Add `MIGRATIONS += [("plans", "ai_quota", "INTEGER DEFAULT 0")]` next to the Phase 2 migration line.
Set `SCHEMA_VERSION = 12` in `newsbot/__init__.py` and add the v12 comment line in the same style as v11.
Add `"qa_answers"` and `"ai_plan_grants"` to `TABLES` in `tools/migrate_sqlite_to_d1.py`.

- [ ] **Step 4: Run it to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_schema.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add newsbot/db.py newsbot/__init__.py tools/migrate_sqlite_to_d1.py tests/test_phase4_schema.py
git commit -m "feat(phase4): schema v12 - qa_answers, ai_plan_grants, plans.ai_quota"
```

---

### Task 2: Pure Q&A helpers — detect, cap, prompt

**Files:**
- Create: `newsbot/qa.py`
- Modify: `newsbot/config.py` (near the AI settings, line ~130)
- Test: `tests/test_phase4_qa_core.py` (create)

**Interfaces:**
- Produces:
  - `detect_post(reply) -> dict | None` → `{"channel_chat_id": int, "post_id": int}`
  - `post_text(reply, max_chars: int) -> str`
  - `question_text(msg, max_chars: int) -> str`
  - `build_prompt(post: str, question: str) -> tuple[str, str]`
  - `cap_answer(text: str, max_chars: int) -> str`
  - config: `QA_ENABLED` (bool, default `True`), `QA_CONTEXT_MAX_CHARS=4000`, `QA_QUESTION_MAX_CHARS=1000`, `QA_ANSWER_MAX_CHARS=900`, `QA_LINK_CACHE_MIN=360`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phase4_qa_core.py
from types import SimpleNamespace as NS
from newsbot import config, qa

def _channel_post(text="متن پست", mid=77, auto=True):
    return NS(sender_chat=NS(id=-100111, type="channel"), forward_from_chat=None,
              forward_from_message_id=mid, text=text, caption=None,
              is_automatic_forward=auto, message_id=5)

def test_a_reply_to_a_channel_post_is_detected():
    assert qa.detect_post(_channel_post()) == {"channel_chat_id": -100111, "post_id": 77}

def test_a_manual_forward_of_a_channel_post_is_detected():
    r = NS(sender_chat=None, forward_from_chat=NS(id=-100111, type="channel"),
           forward_from_message_id=77, text="x", caption=None,
           is_automatic_forward=False, message_id=5)
    assert qa.detect_post(r) == {"channel_chat_id": -100111, "post_id": 77}

def test_a_reply_to_a_normal_user_message_is_not_a_post():
    r = NS(sender_chat=None, forward_from_chat=None, text="سلام", caption=None,
           is_automatic_forward=False, message_id=5)
    assert qa.detect_post(r) is None

def test_the_post_text_is_stripped_and_capped():
    r = _channel_post(text="<b>" + "ا" * 50 + "</b>")
    out = qa.post_text(r, 20)
    assert "<b>" not in out and len(out) <= 21 and out.endswith("…")

def test_the_caption_is_used_when_the_post_has_no_text():
    r = _channel_post(text=None); r.caption = "کپشن"
    assert qa.post_text(r, 100) == "کپشن"

def test_the_question_text_is_capped():
    m = NS(text="س" * 50, caption=None)
    assert len(qa.question_text(m, 10)) == 11      # ۱۰ کاراکتر + …

def test_the_prompt_contains_only_the_post_and_the_question():
    system, user = qa.build_prompt("متنِ پست", "پرسش؟")
    assert "متنِ پست" in user and "پرسش؟" in user
    assert "فقط" in system and "پست" in system

def test_the_answer_is_stripped_and_capped():
    assert len(qa.cap_answer("<b>" + "پ" * 40 + "</b>", 15)) == 16
    assert "<b>" not in qa.cap_answer("<b>x</b>", 100)

def test_the_config_defaults_are_the_designed_values():
    assert config.QA_ENABLED is True
    assert (config.QA_CONTEXT_MAX_CHARS, config.QA_QUESTION_MAX_CHARS,
            config.QA_ANSWER_MAX_CHARS) == (4000, 1000, 900)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_qa_core.py -q`
Expected: FAIL — `ModuleNotFoundError: newsbot.qa` (and missing config names).

- [ ] **Step 3: Implement**

Create `newsbot/qa.py` with the module docstring (Persian) and:

```python
def detect_post(reply):
    """{channel_chat_id, post_id} اگر پیامِ replyشده پستِ یک کانال است، وگرنه None."""
    ch = getattr(reply, "sender_chat", None)
    if ch is not None and getattr(ch, "type", "") == "channel":
        cid = int(ch.id)
    else:
        fw = getattr(reply, "forward_from_chat", None)
        if fw is None or getattr(fw, "type", "") != "channel":
            return None
        cid = int(fw.id)
    pid = int(getattr(reply, "forward_from_message_id", None) or getattr(reply, "message_id", 0) or 0)
    return {"channel_chat_id": cid, "post_id": pid}

def _clip(text, max_chars):
    text = strip_tags(text or "").strip()
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "…"

def post_text(reply, max_chars):
    return _clip(getattr(reply, "text", None) or getattr(reply, "caption", None), max_chars)

def question_text(msg, max_chars):
    return _clip(getattr(msg, "text", None) or getattr(msg, "caption", None), max_chars)

def build_prompt(post, question):
    system = ("تو دستیارِ کانال هستی. فقط و فقط بر پایهٔ «متنِ پست» پاسخ بده. "
              "اگر پاسخ در متنِ پست نیست، صادقانه بگو که در این پست نیست. "
              "هم‌زبانِ سؤال جواب بده. حداکثر ۴ جمله، متنِ ساده، بدونِ Markdown و بدونِ HTML.")
    user = f"متنِ پست:\n<<<\n{post}\n>>>\n\nپرسش:\n{question}\n\nپاسخ:"
    return system, user

def cap_answer(text, max_chars):
    return _clip(text, max_chars)
```

Config, next to the AI settings: `QA_ENABLED = os.getenv("QA_ENABLED", "1").strip() == "1"` and `_env_int` entries for the three caps plus `QA_LINK_CACHE_MIN = _env_int("QA_LINK_CACHE_MIN", 360, 5, 10080)`.

- [ ] **Step 4: Run it to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_qa_core.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add newsbot/qa.py newsbot/config.py tests/test_phase4_qa_core.py
git commit -m "feat(phase4): pure Q&A helpers - detect channel post, cap context, build prompt"
```

---

### Task 3: Claim, group validation, handler, delivery

**Files:**
- Modify: `newsbot/qa.py`
- Test: `tests/test_phase4_qa_core.py` (append)

**Interfaces:**
- Consumes: Task 2 helpers; from `newsbot/db.py`: `write`, `q`, `qrow`, `arun`, `gget`, `gset`; from `newsbot/core_channels.py`: `channel_by_chat`, `get_settings`; from `newsbot/core_users.py`: `ensure_user`; from `newsbot/ai_models.py`: `ai_chat`, `budget_reset`.
- Produces:
  - `qa_claim(chat_id, msg_id, user_id, channel_id, post_id=0) -> bool` (True only for a fresh claim)
  - `qa_finish(chat_id, msg_id, status, answer="", error="") -> None`
  - `linked_channel_id(bot, group_id) -> int | None` (cached)
  - `on_group_reply(update, context) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phase4_qa_core.py`:

```python
import asyncio
import pytest
from newsbot import qa, rotation, ai_models as amod
from newsbot.core_channels import add_channel, channel_by_chat
from newsbot.db import q

class _Bot:
    def __init__(self, linked=-100111): self.linked, self.sent, self.fail = linked, [], False
    async def get_chat(self, gid): return NS(linked_chat_id=self.linked)
    async def send_message(self, chat_id, text, **kw):
        if self.fail: raise RuntimeError("network")
        self.sent.append({"chat_id": chat_id, "text": text, **kw}); return NS(message_id=1)

def _group_msg(gid=-555, mid=10, text="این پست دربارهٔ چیست؟", reply=None):
    return NS(chat_id=gid, message_id=mid, text=text, caption=None,
              chat=NS(type="supergroup"), reply_to_message=reply, date=None)

def _update(msg, uid=900001):
    return NS(effective_message=msg, effective_user=NS(id=uid, username="u", full_name="U"))

@pytest.fixture
def chan(isolated_db):
    add_channel(900001, -100111, "کانال", "", 900001)
    return channel_by_chat(-100111)

def _stub_ai(monkeypatch, out="پاسخِ مدل"):
    async def fake(system, user, **kw): return out, "m", None
    monkeypatch.setattr(amod, "ai_chat", fake)

def test_a_reply_in_the_linked_group_gets_one_answer(chan, monkeypatch):
    _stub_ai(monkeypatch); bot = _Bot()
    qa_ctx = NS(bot=bot)
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), qa_ctx))
    assert len(bot.sent) == 1
    assert bot.sent[0]["reply_to_message_id"] == 10
    assert bot.sent[0]["chat_id"] == -555

def test_the_prompt_carries_the_post_text_and_the_question(chan, monkeypatch):
    seen = {}
    async def fake(system, user, **kw): seen["user"] = user; return "ok", "m", None
    monkeypatch.setattr(amod, "ai_chat", fake)
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post(text="متنِ خاصِ پست"))),
                                  NS(bot=_Bot())))
    assert "متنِ خاصِ پست" in seen["user"] and "این پست دربارهٔ چیست؟" in seen["user"]

def test_the_ai_call_is_charged_to_the_asker(chan, monkeypatch):
    seen = {}
    async def fake(system, user, **kw): seen.update(kw); return "ok", "m", None
    monkeypatch.setattr(amod, "ai_chat", fake)
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post()), uid=900002),
                                  NS(bot=_Bot())))
    assert seen.get("owner_id") == 900002

def test_a_group_linked_to_another_channel_is_silent(chan, monkeypatch):
    _stub_ai(monkeypatch); bot = _Bot(linked=-999999)
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert bot.sent == []

def test_an_unknown_group_link_is_silent(chan, monkeypatch):
    _stub_ai(monkeypatch); bot = _Bot(linked=None)
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post(auto=False))), NS(bot=bot)))
    assert bot.sent == []

def test_a_message_without_a_reply_is_silent(chan, monkeypatch):
    _stub_ai(monkeypatch); bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=None)), NS(bot=bot)))
    assert bot.sent == []

def test_the_same_message_is_answered_at_most_once(chan, monkeypatch):
    _stub_ai(monkeypatch); bot = _Bot(); u = _update(_group_msg(reply=_channel_post()))
    asyncio.run(qa.on_group_reply(u, NS(bot=bot)))
    asyncio.run(qa.on_group_reply(u, NS(bot=bot)))
    assert len(bot.sent) == 1

def test_a_failed_delivery_is_not_retried(chan, monkeypatch):
    _stub_ai(monkeypatch); bot = _Bot(); bot.fail = True
    u = _update(_group_msg(reply=_channel_post()))
    with pytest.raises(RuntimeError): asyncio.run(qa.on_group_reply(u, NS(bot=bot)))
    bot.fail = False
    asyncio.run(qa.on_group_reply(u, NS(bot=bot)))
    assert bot.sent == []

def test_a_media_only_post_gets_an_honest_message_without_ai(chan, monkeypatch):
    called = {"n": 0}
    async def fake(*a, **k): called["n"] += 1; return "x", "m", None
    monkeypatch.setattr(amod, "ai_chat", fake)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post(text=None))), NS(bot=bot)))
    assert called["n"] == 0 and len(bot.sent) == 1

def test_an_ai_failure_delivers_one_honest_error(chan, monkeypatch):
    async def fake(*a, **k): return None, None, "ai_limit"
    monkeypatch.setattr(amod, "ai_chat", fake)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert len(bot.sent) == 1 and "هوش مصنوعی" in bot.sent[0]["text"]

def test_the_feature_toggle_off_means_silence(chan, monkeypatch):
    from newsbot.core_channels import update_settings
    update_settings(chan["id"], qa_enabled=False)
    _stub_ai(monkeypatch); bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert bot.sent == []

def test_the_log_does_not_leak_the_question(chan, monkeypatch, caplog):
    _stub_ai(monkeypatch)
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post(), text="رازِ سؤال")),
                                  NS(bot=_Bot())))
    assert "رازِ سؤال" not in caplog.text

def test_two_concurrent_handlers_send_one_answer(chan, monkeypatch):
    _stub_ai(monkeypatch); bot = _Bot()
    u = _update(_group_msg(reply=_channel_post()))
    async def both(): await asyncio.gather(qa.on_group_reply(u, NS(bot=bot)),
                                           qa.on_group_reply(u, NS(bot=bot)))
    asyncio.run(both())
    assert len(bot.sent) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_qa_core.py -q`
Expected: FAIL — `AttributeError: module 'newsbot.qa' has no attribute 'on_group_reply'`.

- [ ] **Step 3: Implement in `newsbot/qa.py`** (and register the handler in `newsbot/app.py` — see Task 7's guard test)

```python
NO_TEXT = "این پست متنِ قابلِ خواندن ندارد؛ فقط متنِ پست‌ها را می‌توانم پاسخ دهم."
NO_QUOTA = "سهمیهٔ هوش مصنوعیِ شما تمام شده است؛ پس از تمدیدِ پلن دوباره تلاش کنید."
AI_FAIL = "فعلاً نتوانستم پاسخ بدهم؛ کمی بعد دوباره بپرسید."

def qa_claim(chat_id, msg_id, user_id, channel_id, post_id=0):
    """True فقط اگر همین فراخوانی ردیف را ساخت (INSERT OR IGNORE اتمیک).
    تصمیمِ مالک (۲): at-most-once — ردیفِ موجود، حتی pending، هرگز دوباره پاسخ نمی‌گیرد."""
    now = now_iso()
    return bool(write(lambda conn: conn.execute(
        "INSERT OR IGNORE INTO qa_answers(chat_id,msg_id,user_id,channel_id,post_id,status,answer,error,created_at,updated_at)"
        " VALUES(?,?,?,?,?,'pending','','',?,?)",
        (chat_id, msg_id, user_id, channel_id, post_id, now, now)).rowcount == 1))

def qa_finish(chat_id, msg_id, status, answer="", error=""):
    q("UPDATE qa_answers SET status=?, answer=?, error=?, updated_at=? WHERE chat_id=? AND msg_id=?",
      (status, answer, error, now_iso(), chat_id, msg_id), commit=True)

async def linked_channel_id(bot, group_id):
    """linked_chat_id گروهِ بحث، با کشِ ۶ساعته در جدول settings. None یعنی «نامعلوم» ⇒ سکوت."""
    key = f"qa:linked:{group_id}"
    ent = await arun(gget, key)
    if isinstance(ent, dict) and ent.get("at"):
        try:
            age = (now_utc() - parse_dt(ent["at"])).total_seconds()
            if age < config.QA_LINK_CACHE_MIN * 60:
                return int(ent["id"]) if ent.get("id") else None
        except Exception: pass
    try:
        chat = await bot.get_chat(group_id); linked = getattr(chat, "linked_chat_id", None)
    except Exception as e:
        log.warning("qa: get_chat failed group=%s (%s)", group_id, type(e).__name__); return None
    await arun(gset, key, {"id": int(linked) if linked else 0, "at": now_iso()})
    return int(linked) if linked else None

async def on_group_reply(update, context):
    ...  # مراحلِ ۶.۱: گاردها، claim، prompt، ai_chat، ارسال، qa_finish
```

`on_group_reply` must: return silently when `config.QA_ENABLED` is false, there is no `reply_to_message`, `detect_post` returns None, `channel_by_chat` finds no channel, `get_settings(channel["id"]).get("qa_enabled")` is false, `linked_channel_id(...) is None`, or `linked != channel["chat_id"]`. It then calls `ensure_user`, `qa_claim`, builds the prompt, calls `budget_reset()` and `ai_chat(system, user, owner_id=uid)`, caps the answer, sends with `reply_to_message_id=msg.message_id` and `disable_web_page_preview=True`, and calls `qa_finish`. Missing post text or question ⇒ fixed honest message + `qa_finish("failed", error="no_text")`, no AI call. AI failure ⇒ `NO_QUOTA` **only** for `ai_limit`; **every other** AI error (`ai_budget`/`ai_busy`/`ai_none`/anything else) ⇒ `AI_FAIL` + `qa_finish("failed", error=err)`. *(Correction applied at implementation time, 2026-11-16: the first draft of this plan mapped `ai_busy`/`ai_none` to `NO_QUOTA` as well. That would have told a user their quota was exhausted when the real cause was a provider/busy failure — dishonest messaging. The implementation follows the corrected rule and `tests/test_phase4_qa_core.py` asserts it.)* A send failure propagates (the row stays `pending`, and at-most-one means it is never retried). Log lines carry ids and lengths only.

Also register the handler in `newsbot/app.py` (import `on_group_reply` from `newsbot.qa`; add `APP.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.REPLY & ~filters.COMMAND, on_group_reply))` before the private-chat handler).

- [ ] **Step 4: Run to verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_qa_core.py -q`
Expected: PASS (22 tests).

- [ ] **Step 5: Commit**

```bash
git add newsbot/qa.py tests/test_phase4_qa_core.py
git commit -m "feat(phase4): group Q&A handler - fail-closed, at-most-once, charge the asker"
```

---

### Task 4: Per-channel admin toggle

**Files:**
- Modify: `newsbot/core_channels.py` (`default_settings`, line ~285)
- Modify: `newsbot/ui_i18n.py` (`TOGGLABLE` line 349, `TXT` near line 223)
- Modify: `newsbot/ui_views_admin.py` (`view_channel`, line 84-89)
- Modify: `newsbot/ui_dispatch.py` (line 225-232)
- Test: `tests/test_phase4_qa_core.py` (append)

**Interfaces:**
- Produces: `settings["qa_enabled"]` defaults `True`; callback `a:tog:{cid}:qa_enabled` flips it and returns to the channel panel; i18n keys `tog_qa_enabled`, `togd_qa_enabled`, `qa_btn`.

- [ ] **Step 1: Write the failing test**

```python
def test_qa_enabled_defaults_on_for_a_channel(isolated_db):
    from newsbot.core_channels import get_settings
    add_channel(900001, -100111, "کانال", "", 900001)
    assert get_settings(channel_by_chat(-100111)["id"])["qa_enabled"] is True

def test_the_toggle_is_a_known_toggleable_setting():
    from newsbot.ui_i18n import TOGGLABLE, tr
    assert "qa_enabled" in TOGGLABLE
    assert tr("fa", "tog_qa_enabled") and tr("en", "tog_qa_enabled")

def test_the_channel_panel_has_the_qa_toggle_button():
    import inspect
    from newsbot import ui_views_admin
    assert "qa_enabled" in inspect.getsource(ui_views_admin.view_channel)
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_qa_core.py -q -k "toggle or defaults_on"`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `default_settings`: add `"qa_enabled": True`.
- `TOGGLABLE`: add `"qa_enabled"`.
- `TXT`: `"tog_qa_enabled": ("❓ پرسش‌وپاسخِ گروهی", "❓ Group Q&A")`, `"togd_qa_enabled": (...short explanation...)`, `"qa_btn": ("❓ پرسش‌وپاسخ: {i}", "❓ Q&A: {i}")`.
- `view_channel`: add `B(tr(lang, "qa_btn", i="🟢" if s.get("qa_enabled") else "🔴"), f"a:tog:{cid}:qa_enabled")` to the keyboard.
- `ui_dispatch` `b == "tog"`: after `update_settings`, if `d == "qa_enabled"` popup and `return await view_channel(update, context, cid)` (before the `view_sched`/`view_content` branch).

- [ ] **Step 4: Run to verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_qa_core.py -q`
Expected: PASS (25 tests).

- [ ] **Step 5: Commit**

```bash
git add newsbot/core_channels.py newsbot/ui_i18n.py newsbot/ui_views_admin.py newsbot/ui_dispatch.py tests/test_phase4_qa_core.py
git commit -m "feat(phase4): per-channel Q&A toggle in the admin panel"
```

---

### Task 5: Plan-level, global AI quota — the gate

**Files:**
- Create: `newsbot/ai_quota.py`
- Modify: `newsbot/core_users.py` (`ai_claim`, line 111)
- Modify: `newsbot/ai_models.py` (`test_model`, line 828)
- Test: `tests/test_phase4_plan_ai_quota.py` (create)

**Interfaces:**
- Produces:
  - `ai_quota.plan_grant(uid) -> dict | None` → `{"id", "quota", "used", "expires_at"}` for the active period
  - `ai_quota.plan_claim(uid) -> bool` (True = allowed; True when the plan has no cap)
  - `ai_quota.units_text(quota, lang) -> str`
  - `ai_quota.quota_lines(uid, lang) -> str` (سقف/مصرف/مانده)
  - `core_users.ai_claim(uid)` now consumes the daily cap **and** the plan grant in one transaction.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phase4_plan_ai_quota.py
import asyncio
import pytest
from newsbot import ai_quota, ai_models as amod
from newsbot.core_plans import create_plan, assign_plan, get_plan
from newsbot.core_users import ensure_user, ai_claim
from newsbot.db import q

UID = 700001

@pytest.fixture
def quota_user(isolated_db):
    ensure_user(UID, "u", "U")
    pid = create_plan(name="پلنِ تست", days=30, ai_quota=3, price_num=1)
    assign_plan(UID, pid)
    return pid

def test_the_plan_carries_its_quota(quota_user):
    assert get_plan(quota_user)["ai_quota"] == 3

def test_the_grant_is_created_for_the_active_period(quota_user):
    g = ai_quota.plan_grant(UID)
    assert g["quota"] == 3 and g["used"] == 0

def test_each_claim_uses_one_unit(quota_user):
    assert ai_claim(UID) is True
    assert ai_claim(UID) is True
    assert ai_quota.plan_grant(UID)["used"] == 2

def test_the_quota_stops_further_requests(quota_user):
    for _ in range(3): assert ai_claim(UID) is True
    assert ai_claim(UID) is False
    assert ai_quota.plan_grant(UID)["used"] == 3

def test_the_article_path_consumes_the_same_quota(quota_user, monkeypatch):
    """سهمیه سراسری است: مسیرِ تولیدِ مقاله همان دروازهٔ ai_claim را صدا می‌زند."""
    async def fake_try(*a, **k): return "ok", "m", None
    monkeypatch.setattr(amod, "_try_models", fake_try)
    amod.add_model("https://api.example.com/v1", "k", "m-1", name="quota-model", priority=10)
    out, label, err = asyncio.run(amod.ai_chat("s", "u", owner_id=UID))
    assert (out, err) == ("ok", None)
    assert ai_quota.plan_grant(UID)["used"] == 1

def test_a_renewal_resets_the_period(quota_user):
    for _ in range(3): ai_claim(UID)
    assign_plan(UID, quota_user, days=30)      # دورهٔ تازه
    g = ai_quota.plan_grant(UID)
    assert g["used"] == 0 and ai_claim(UID) is True

def test_an_unlimited_plan_never_blocks(isolated_db):
    ensure_user(700002, "u2", "U2")
    pid = create_plan(name="نامحدود", days=30, ai_quota=0)
    assign_plan(700002, pid)
    for _ in range(5): assert ai_claim(700002) is True
    assert ai_quota.plan_grant(700002) is None

def test_concurrent_claims_never_exceed_the_quota(isolated_db):
    ensure_user(700003, "u3", "U3")
    pid = create_plan(name="۲ واحد", days=30, ai_quota=2)
    assign_plan(700003, pid)
    ok = [ai_claim(700003) for _ in range(6)]
    assert ok.count(True) == 2

def test_a_model_test_consumes_only_the_plan_quota(quota_user, monkeypatch):
    amod.add_model("https://api.example.com/v1", "k", "m-2", name="test-model", priority=10, owner_id=UID)
    mid = q("SELECT id FROM ai_models WHERE name='test-model'", one=True)["id"]
    async def fake_try(*a, **k): return "READY", "test-model", None
    monkeypatch.setattr(amod, "_try_models", fake_try)
    assert amod.test_model(mid)[0] is True
    assert ai_quota.plan_grant(UID)["used"] == 1

def test_the_user_panel_shows_total_used_and_remaining(quota_user):
    ai_claim(UID)
    line = ai_quota.quota_lines(UID, "fa")
    assert "3" in line and "1" in line and "2" in line
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_plan_ai_quota.py -q`
Expected: FAIL — `ModuleNotFoundError: newsbot.ai_quota`.

- [ ] **Step 3: Implement**

`newsbot/ai_quota.py`:

```python
def plan_grant(uid):
    """گرانتِ دورهٔ فعال؛ None اگر پلنِ فعال یا سقف نداشته باشد. تنبل ساخته می‌شود."""
def plan_claim(uid):
    """True = مجاز. UPDATE اتمیک با شرطِ used<quota (rowcount==1). بدونِ سقف ⇒ True."""
def units_text(quota, lang):
def quota_lines(uid, lang):
```

- `plan_grant`: `lim = admin_limits(uid)`; return `None` when `not lim["active"]`, no plan, or `int(plan["ai_quota"] or 0) <= 0`. Otherwise `INSERT OR IGNORE INTO ai_plan_grants(user_id, plan_id, quota, used, granted_at, expires_at) VALUES(?,?,?,0,?,?)` with `expires_at = iso_fixed(lim["expires"])`, then `SELECT * … WHERE user_id=? AND expires_at=?`.
- `plan_claim`: `g = plan_grant(uid)`; `if not g: return True`; `return bool(write(lambda conn: conn.execute("UPDATE ai_plan_grants SET used=used+1 WHERE id=? AND used<quota", (g["id"],)).rowcount == 1))`.
- `core_users.ai_claim`: run the existing daily-cap statement **and** `ai_quota.plan_claim` inside one `_trx`, so a failed daily cap rolls back the grant consumption (and vice versa). Signature and callers unchanged.
- `ai_models.test_model`: after `m = get_model(mid)`, if `m["owner_id"]` and not `ai_quota.plan_claim(m["owner_id"])`, return `False, "ai_limit", 0.0`.
- `create_plan`/`update_plan`: accept and persist `ai_quota` (default 0).

- [ ] **Step 4: Run to verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_plan_ai_quota.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add newsbot/ai_quota.py newsbot/core_users.py newsbot/core_plans.py newsbot/ai_models.py tests/test_phase4_plan_ai_quota.py
git commit -m "feat(phase4): plan AI quota gate - per-period grant, atomic global consumption"
```

---

### Task 6: Plan-quota UI — editable, shown before purchase, visible in the panel

**Files:**
- Modify: `newsbot/ui_views_admin.py` (`PLAN_FIELDS`, line 33)
- Modify: `newsbot/ui_views_super.py` (`view_s_plan` text, line 398)
- Modify: `newsbot/ui_views_user.py` (`view_plan` text, line 132; `view_plans` line 101)
- Modify: `newsbot/ui_i18n.py` (`plan_details`, line 73; add keys)
- Test: `tests/test_phase4_plan_ai_quota.py` (append)

**Interfaces:**
- Consumes: `ai_quota.units_text`, `ai_quota.quota_lines` from Task 5.

- [ ] **Step 1: Write the failing test**

```python
def test_the_admin_can_edit_the_plan_quota():
    from newsbot.ui_views_admin import PLAN_FIELDS
    assert any(f[0] == "ai_quota" for f in PLAN_FIELDS)

def test_the_purchase_card_shows_the_quota():
    import inspect
    from newsbot import ui_i18n, ui_views_user
    assert "{ai}" in ui_i18n.tr("fa", "plan_details")
    assert "ai=" in inspect.getsource(ui_views_user.view_plan)

def test_the_super_plan_view_shows_the_quota():
    import inspect
    from newsbot import ui_views_super
    assert "ai=" in inspect.getsource(ui_views_super.view_s_plan)
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_plan_ai_quota.py -q -k "shows or edit"`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `PLAN_FIELDS`: add `("ai_quota", "سهمیهٔ هوش مصنوعی (۰=نامحدود)", "AI quota (0=unlimited)")`.
- `plan_details` (both languages): add `\n🤖 سهمیهٔ هوش مصنوعی: {ai}`.
- New keys: `"plan_ai_line": ("🤖 سهمیهٔ هوش مصنوعی: {total} · مصرف‌شده: {used} · باقی‌مانده: {left}", "🤖 AI quota: {total} · used: {used} · left: {left}")`, `"unlimited_word": ("نامحدود", "Unlimited")`.
- `view_plan` and `view_s_plan`: pass `ai=ai_quota.units_text(p["ai_quota"], lang)` into `plan_details`.
- `view_plan`: for the user's own active plan, append `ai_quota.quota_lines(uid, lang)` (renders `plan_ai_line`).

- [ ] **Step 4: Run to verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_plan_ai_quota.py -q`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add newsbot/ui_views_admin.py newsbot/ui_views_super.py newsbot/ui_views_user.py newsbot/ui_i18n.py tests/test_phase4_plan_ai_quota.py
git commit -m "feat(phase4): show and edit the plan AI quota in the panel and before purchase"
```

---

### Task 7: Wiring, docs, full verification

**Files:**
- Modify: `newsbot/app.py` (line 272-274)
- Modify: `README.md` (badge + test count line)
- Modify: `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md` (§1, §2, §4.x, §5, §7, §8)
- Test: `tests/test_phase4_qa_core.py` (append registration guard)

**Interfaces:**
- Consumes: `newsbot.qa.on_group_reply` from Task 3.

- [ ] **Step 1: Write the failing test**

```python
def test_the_group_reply_handler_is_registered():
    import inspect
    from newsbot import app
    src = inspect.getsource(app.main)
    assert "on_group_reply" in src
    assert "filters.ChatType.GROUPS" in src
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_qa_core.py -q -k registered`
Expected: FAIL.

- [ ] **Step 3: Implement wiring**

In `app.py`, import `on_group_reply` from `newsbot.qa` and register, before the private-chat handler:

```python
APP.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.REPLY & ~filters.COMMAND, on_group_reply))
```

Update the state file: Phase 4 row → `IN_PROGRESS` then `COMPLETED` when its PRs merge; §4.4 with the decision table; §5 evidence rows (real run counts, CI, protection 15/15); §7 delivery SHAs; §8 B-4 kept open until the owner adds the bot to the group.

- [ ] **Step 4: Full verification (record the real output)**

```bash
.\.venv\Scripts\python.exe -m pytest --collect-only -q          # count for the README
.\.venv\Scripts\python.exe -m pytest tests/test_phase4_qa_core.py tests/test_phase4_schema.py tests/test_phase4_plan_ai_quota.py -q
.\.venv\Scripts\python.exe -m pytest -q                          # full suite; expect only the 19 known Windows failures
.\.venv\Scripts\python.exe -m ruff check newsbot tests tools run.py
.\.venv\Scripts\python.exe -m pyflakes newsbot tests
.\.venv\Scripts\python.exe tools/final_verification.py
```

Update the README badge and `# N تست` line to the exact collection count and re-run the guard.

- [ ] **Step 5: Commit, PR, merge, record**

```bash
git add newsbot/app.py README.md docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md tests/test_phase4_qa_core.py
git commit -m "feat(phase4): register the group handler; records, README count, state file"
```

Then: push, open the PR, wait for CI `test` = SUCCESS, `gate.py <PR> lift`, `gh pr merge <PR> --squash --delete-branch`, `gate.py <PR> restore`, verify 15/15, and record the merge SHA.
