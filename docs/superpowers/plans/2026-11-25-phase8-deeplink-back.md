# Phase 8 — Deeplink "back to channel" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every newly created deeplink page a localized URL button that opens the exact source
channel post, with an honest note when the platform cannot guarantee the destination.

**Architecture:** The post-success path in `publish.py` stores `src: {chat_id, message_id,
username}` inside the existing deeplink JSON; one pure helper (`post_url`) turns those ids plus the
channel's current handle into `t.me/<user>/<id>` or `t.me/c/<internal>/<id>`; `send_deeplink`
attaches a single `InlineKeyboardButton(url=...)`, and the classic sender gains an opt-in
"markup on the last chunk only" flag. No schema change, no extra API call.

**Tech Stack:** Python 3.13, python-telegram-bot, SQLite/D1 JSON rows, pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-phase8-deeplink-back-design.md`

## Global Constraints

- `SCHEMA_VERSION` stays **13**; this phase adds no column, table or migration.
- Tests are written and **must fail first**; each task runs only its focused suites, Task 4 runs the
  full suite.
- Windows local full suite has exactly **19 known failures** (4 in `tests/test_deploy_script.py`,
  15 in `tests/test_session3_cicd_docker_deploy_docs.py`); any other failure is a regression.
- All commands run from the repo root with `.\.venv\Scripts\python.exe -m pytest ...`.
- Persian report/commit prose is allowed; identifiers, keys and code stay English.
- No secret, token, chat id or reader identifier may enter logs, commits or test names.
- **D-7:** this phase does **not** claim a live verification; the real-client check joins the single
  Phase 10 run.
- New test file: `tests/test_phase8_deeplink_back.py`. PR1 adds it to `PROPOSED_PATHS`; Task 1
  removes that entry when the file is created.
- Branch names are written **bold** in docs, never inside backticks.

## Review Focus

1. **A deeplink whose channel row was deleted** — must still render a working public URL from the
   payload snapshot, or the `t.me/c/...` fallback plus the "if you are a member" note; never crash,
   never claim the channel is private. (Task 3, test `test_row_deleted_uses_snapshot_then_unknown_note`)
2. **A body longer than 4096 characters** — must carry exactly one button, on the last part.
   (Task 3, test `test_split_legacy_body_has_one_button`)
3. **A deeplink row written by an older build (no `src`)** — must be byte-identical to today's page,
   with no button and no note. (Task 3, test `test_old_payload_has_no_button_and_no_note`)
4. **A corrupt or hostile payload** (`src` present but message id is a string/zero, or the JSON is
   not an object) — no button, no traceback, `dl_notfound` for a non-object. (Task 3, test
   `test_corrupt_payload_never_crashes`)
5. **A private channel** — the button is `t.me/c/...` and the member-only note appears once, so the
   reader is not promised something Telegram will refuse. (Task 3, test
   `test_private_channel_gets_member_note`)

---

### Task 1: One link rule — `post_url`

**Files:**
- Modify: `newsbot/core_channels.py:161`
- Create: `tests/test_phase8_deeplink_back.py`
- Modify: `tests/test_repo_integrity.py:27-32`

**Interfaces:**
- Consumes: nothing new.
- Produces: `post_url(chat_id, message_id, username="") -> str`, used by `msg_link` and, in Task 3,
  by `send_deeplink`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_phase8_deeplink_back.py
# -*- coding: utf-8 -*-
"""فاز ۸ — دکمهٔ «بازگشت به کانال» در دیپ‌لینک."""
from types import SimpleNamespace

from newsbot.core_channels import msg_link, post_url


def test_post_url_public_and_private_forms():
    assert post_url(-1001234567890, 42, "mychan") == "https://t.me/mychan/42"
    assert post_url(-1001234567890, 42, "") == "https://t.me/c/1234567890/42"
    assert post_url(-1001234567890, 0, "mychan") == ""
    assert post_url(-1001234567890, None, "mychan") == ""
    assert post_url(-1001234567890, "x", "mychan") == ""


def test_post_url_rejects_a_chat_id_that_cannot_form_a_c_link():
    assert post_url(-123456, 7, "") == ""
    assert post_url(None, 7, "") == ""


def test_msg_link_delegates_to_post_url():
    ch = {"chat_id": -1001234567890, "username": "mychan"}
    assert msg_link(ch, SimpleNamespace(message_id=42)) == post_url(-1001234567890, 42, "mychan")
    private = {"chat_id": -100999, "username": ""}
    assert msg_link(private, SimpleNamespace(message_id=7)) == "https://t.me/c/999/7"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase8_deeplink_back.py -q`
Expected: FAIL — `ImportError: cannot import name 'post_url'`.

- [ ] **Step 3: Implement `post_url` and delegate `msg_link`**

In `newsbot/core_channels.py`, replace the `msg_link` one-liner with:

```python
def post_url(chat_id, message_id, username=""):
    """URL of a channel post: public form when a username exists, else the private c/ form."""
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return ""
    if mid <= 0:
        return ""
    handle = str(username or "").lstrip("@").strip()
    if handle:
        return f"https://t.me/{handle}/{mid}"
    raw = str(chat_id if chat_id is not None else "")
    if not raw.startswith("-100") or len(raw) <= 4:
        return ""
    return f"https://t.me/c/{raw[4:]}/{mid}"


def msg_link(ch, msg): return post_url(ch["chat_id"], msg.message_id, ch["username"])
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase8_deeplink_back.py tests/test_exactly_once_limits.py -q`
Expected: PASS (the new tests plus the existing publish/deeplink suite).

- [ ] **Step 5: Drop the allowlist entry and verify repo integrity**

Remove the `"tests/test_phase8_deeplink_back.py"` line from `PROPOSED_PATHS` in
`tests/test_repo_integrity.py`.

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_repo_integrity.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add newsbot/core_channels.py tests/test_phase8_deeplink_back.py tests/test_repo_integrity.py
git commit -m "feat(phase8): one post-url rule for journal links and deeplinks"
```

---

### Task 2: Record the source post on publication

**Files:**
- Modify: `newsbot/publish.py:437-443`
- Test: `tests/test_phase8_deeplink_back.py` (append)

**Interfaces:**
- Consumes: `post_url` / `msg_link` from Task 1; `update_channel_meta(cid, username=...)` from
  `newsbot/core_channels.py:157`.
- Produces: the payload key `src` = `{"chat_id": int, "message_id": int, "username": str}`, and the
  module-level helper `_note_channel_handle(ch, msg) -> None` in `newsbot/publish.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phase8_deeplink_back.py`:

```python
import asyncio
import re

from newsbot import publish as pub_mod
from newsbot.core_articles import article_insert, article_update
from newsbot.core_channels import add_channel, channel_by_chat
from newsbot.net_http import load_deeplink


def _chan(chat_id, username=""):
    add_channel(1, chat_id, f"ch{chat_id}", username, 1)
    return channel_by_chat(chat_id)["id"]


def _ready(cid, h, post, full):
    aid = article_insert(1, cid, h, f"https://x.test/{h}", "t", 0, None, status="ready")
    article_update(aid, post_html=post, full_html=full)
    return aid


class _PubBot:
    id = 901
    def __init__(self, username=""):
        self.username = username
        self.sent = []
        self.attempted = []
    async def get_chat_member(self, chat_id, user_id):
        return type("M", (), {"status": "administrator", "can_post_messages": True})()
    async def send_message(self, chat_id, text, **kw):
        self.attempted.append(text)
        if getattr(self, "fail", False):
            raise RuntimeError("send failed")
        self.sent.append((chat_id, text))
        msg = type("Msg", (), {"message_id": len(self.sent)})()
        if self.username:
            msg.chat = type("Chat", (), {"username": self.username})()
        return msg


def _payload_of(bot):
    key = re.search(r"r_([A-Za-z0-9_\-]+)", bot.sent[0][1]).group(1)
    return asyncio.run(load_deeplink(key))


def test_publish_records_the_source_post():
    cid = _chan(820, "pubbot")
    post = "<b>" + "x" * 100 + "</b>"
    aid = _ready(cid, "h-src-1", post, post + " " + "y" * 250)
    bot = _PubBot(username="pubbot")
    ok, _ = asyncio.run(pub_mod.publish_article(bot, aid))
    assert ok is True
    src = _payload_of(bot)["src"]
    assert src == {"chat_id": 820, "message_id": 1, "username": "pubbot"}


def test_failed_publish_records_nothing():
    cid = _chan(821)
    post = "<b>" + "x" * 100 + "</b>"
    aid = _ready(cid, "h-src-2", post, post + " " + "y" * 250)
    bot = _PubBot()
    bot.fail = True
    ok, _ = asyncio.run(pub_mod.publish_article(bot, aid))
    assert ok is False
    from newsbot.db import q
    key = re.search(r"r_([A-Za-z0-9_\-]+)", bot.attempted[0]).group(1)
    assert q("SELECT 1 FROM deeplinks WHERE key=?", (key,), one=True) is None


def test_a_renamed_handle_is_refreshed_fail_safely(monkeypatch):
    cid = _chan(822, "oldhandle")
    ch = channel_by_chat(822)
    msg = type("Msg", (), {"message_id": 5, "chat": type("C", (), {"username": "newhandle"})()})()
    pub_mod._note_channel_handle(ch, msg)
    assert channel_by_chat(822)["username"] == "newhandle"

    def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(pub_mod, "update_channel_meta", boom)
    pub_mod._note_channel_handle(channel_by_chat(822), msg)   # must not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase8_deeplink_back.py -q`
Expected: FAIL — `KeyError: 'src'` / `AttributeError: module 'newsbot.publish' has no attribute '_note_channel_handle'`.

- [ ] **Step 3: Implement the payload key and the handle refresh**

In `newsbot/publish.py`, import `update_channel_meta` beside `msg_link`, add:

```python
def _note_channel_handle(ch, msg):
    """تلگرام در پاسخِ ارسال، یوزرنیمِ واقعیِ کانال را می‌دهد؛ rename را همان‌جا ترمیم می‌کنیم."""
    handle = str(getattr(getattr(msg, "chat", None), "username", "") or "").lstrip("@").strip()
    if not handle or handle == (ch["username"] or ""):
        return
    try:
        update_channel_meta(ch["id"], username=handle)
    except Exception as e:
        log.warning("channel username refresh failed: %s", type(e).__name__)
```

and inside the success block, immediately after `journal.update(phase="ack", link=link)`:

```python
            _note_channel_handle(ch, msg)
            if dl_key and dl_payload:
                dl_payload["src"] = {
                    "chat_id": int(ch["chat_id"]),
                    "message_id": int(msg.message_id),
                    "username": str(getattr(getattr(msg, "chat", None), "username", "") or ch["username"] or ""),
                }
```

(The existing `try: await save_deeplink(...)` line stays; the `src` assignment is inside the same
`if dl_key and dl_payload:` block, before the `try`.)

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase8_deeplink_back.py tests/test_exactly_once_limits.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add newsbot/publish.py tests/test_phase8_deeplink_back.py
git commit -m "feat(phase8): store the source post in the deeplink payload"
```

---

### Task 3: Render the button, the notes and the single-control split

**Files:**
- Modify: `newsbot/ui_commands.py:52-62`
- Modify: `newsbot/ui_i18n.py:347`
- Modify: `newsbot/rich_send.py:262-287`, `:290-293`, `:379-386`
- Test: `tests/test_phase8_deeplink_back.py` (append)

**Interfaces:**
- Consumes: `post_url` (Task 1), `channel_by_chat`, the `src` payload (Task 2), `U` from
  `newsbot/ui_widgets.py:59`.
- Produces: `send_legacy(..., markup_last_only=False)` and `send_content(..., markup_last_only=False)`;
  i18n keys `dl_back`, `dl_private_note`, `dl_unknown_note`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phase8_deeplink_back.py`:

```python
from telegram import InlineKeyboardMarkup

from newsbot import rich_send
from newsbot import ui_commands as uc
from newsbot.ui_i18n import tr
from newsbot.ui_widgets import U


class _Reply:
    def __init__(self): self.calls = []
    async def reply_text(self, text, **kw): self.calls.append((text, kw))


class _Upd:
    def __init__(self, uid=77):
        self.effective_chat = type("C", (), {"id": uid})()
        self.effective_user = type("U", (), {"id": uid})()
        self.message = _Reply()


class _Ctx:
    bot = type("B", (), {})()


def _render(monkeypatch, payload, lang="fa"):
    sent = []
    async def fake_send(bot, chat_id, text, **kw): sent.append((text, kw))
    async def fake_load(key): return payload
    monkeypatch.setattr(uc.rich_send, "send_content", fake_send)
    monkeypatch.setattr(uc, "load_deeplink", fake_load)
    upd = _Upd()
    asyncio.run(uc.send_deeplink(upd, _Ctx(), "k1"))
    return sent, upd


def test_public_channel_gets_one_url_button(monkeypatch):
    payload = {"short": "s", "full": "full body", "url": "https://x.test/a", "show_source": True,
               "src": {"chat_id": -1001234567890, "message_id": 42, "username": "mychan"}}
    sent, _ = _render(monkeypatch, payload)
    text, kw = sent[0]
    btn = kw["reply_markup"].inline_keyboard[0][0]
    assert btn.url == "https://t.me/mychan/42"
    assert btn.text == tr("fa", "dl_back")
    assert tr("fa", "dl_private_note") not in text and tr("fa", "dl_unknown_note") not in text
    assert kw["markup_last_only"] is True


def test_old_payload_has_no_button_and_no_note(monkeypatch):
    payload = {"short": "s", "full": "full body", "url": "https://x.test/a", "show_source": True}
    sent, _ = _render(monkeypatch, payload)
    text, kw = sent[0]
    assert kw["reply_markup"] is None
    assert tr("fa", "dl_private_note") not in text and tr("fa", "dl_unknown_note") not in text
    assert "t.me/c/" not in text


def test_private_channel_gets_member_note(monkeypatch):
    _chan(-100000830)                            # row exists, username empty ⇒ private
    payload = {"short": "s", "full": "full body",
               "src": {"chat_id": -100000830, "message_id": 9, "username": ""}}
    sent, _ = _render(monkeypatch, payload)
    text, kw = sent[0]
    assert kw["reply_markup"].inline_keyboard[0][0].url == "https://t.me/c/830/9"
    assert text.count(tr("fa", "dl_private_note")) == 1


def test_row_deleted_uses_snapshot_then_unknown_note(monkeypatch):
    payload = {"short": "s", "full": "full body",
               "src": {"chat_id": -100777, "message_id": 3, "username": "ghost"}}
    sent, _ = _render(monkeypatch, payload)      # no row for -100777
    text, kw = sent[0]
    assert kw["reply_markup"].inline_keyboard[0][0].url == "https://t.me/ghost/3"
    assert tr("fa", "dl_unknown_note") not in text

    payload["src"]["username"] = ""
    sent, _ = _render(monkeypatch, payload)
    text, kw = sent[0]
    assert kw["reply_markup"].inline_keyboard[0][0].url == "https://t.me/c/777/3"
    assert text.count(tr("fa", "dl_unknown_note")) == 1


def test_corrupt_payload_never_crashes(monkeypatch):
    for bad in ({"src": {"chat_id": None, "message_id": "x"}},
                {"src": {"chat_id": -1001, "message_id": 0}},
                {"src": "oops"}):
        sent, _ = _render(monkeypatch, bad)
        assert sent[0][1]["reply_markup"] is None
    sent, upd = _render(monkeypatch, ["not", "a", "dict"])
    assert sent == [] and upd.message.calls[0][0] == tr("fa", "dl_notfound")


def test_split_legacy_body_has_one_button(monkeypatch):
    calls = []
    class Bot:
        async def send_message(self, chat_id, text, **kw):
            calls.append((text, kw))
            return type("M", (), {"message_id": len(calls)})()
    monkeypatch.setattr(rich_send.text_format, "split_html", lambda t: ["a", "b", "c"])
    kb = InlineKeyboardMarkup([[U("back", "https://t.me/mychan/42")]])
    asyncio.run(rich_send.send_legacy(Bot(), 1, "abc", reply_markup=kb,
                                      markup_last_only=True, delay=0))
    assert "reply_markup" not in calls[0][1] and "reply_markup" not in calls[1][1]
    assert calls[2][1]["reply_markup"] is kb


def test_split_default_still_marks_every_chunk(monkeypatch):
    calls = []
    class Bot:
        async def send_message(self, chat_id, text, **kw):
            calls.append((text, kw))
            return type("M", (), {"message_id": len(calls)})()
    monkeypatch.setattr(rich_send.text_format, "split_html", lambda t: ["a", "b"])
    kb = InlineKeyboardMarkup([[U("back", "https://t.me/mychan/42")]])
    asyncio.run(rich_send.send_legacy(Bot(), 1, "ab", reply_markup=kb, delay=0))
    assert all("reply_markup" in kw for _, kw in calls)


def test_url_and_ids_never_reach_the_logs(monkeypatch, caplog):
    payload = {"short": "s", "full": "full body",
               "src": {"chat_id": -100424242, "message_id": 7, "username": "quietchan"}}
    with caplog.at_level("DEBUG"):
        _render(monkeypatch, payload)
    assert "quietchan" not in caplog.text and "424242" not in caplog.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase8_deeplink_back.py -q`
Expected: FAIL — `KeyError: 'reply_markup'` / `TypeError: send_legacy() got an unexpected keyword argument 'markup_last_only'`.

- [ ] **Step 3: Add the i18n keys**

In `TXT` in `newsbot/ui_i18n.py`, next to `dl_source`:

```python
    "dl_back": ("↩️ بازگشت به کانال", "↩️ Back to channel"),
    "dl_private_note": ("این دکمه فقط برای اعضای کانالِ خصوصی باز می‌شود.", "This button opens only for members of the private channel."),
    "dl_unknown_note": ("اگر عضو کانال هستید، این دکمه همان پست را باز می‌کند.", "If you are a member of the channel, this opens that post."),
```

- [ ] **Step 4: Implement the last-chunk-only flag**

- `send_legacy(..., markup_last_only=False)`: attach `reply_markup` only when
  `not markup_last_only or n == len(chunks) - 1`.
- `_legacy_or_none(..., markup_last_only=False)`: forward it to `send_legacy`.
- `send_content(..., markup_last_only=False)`: forward it to **every** `_legacy_or_none` call in the
  function (six call sites).
- Default `False` everywhere, so no existing caller changes behaviour.

- [ ] **Step 5: Implement the rendering**

Replace the body of `send_deeplink` so that it:

1. treats a non-dict payload as missing (`if not isinstance(data, dict): data = None`), keeping the
   existing `dl_notfound` reply;
2. reads `src`, resolves `ch = channel_by_chat(chat_id)` when `chat_id` is present, and picks
   `name = (ch["username"] if ch else "") or src.get("username") or ""`;
3. computes `url = post_url(chat_id, message_id, name)`;
4. builds `kb = InlineKeyboardMarkup([[U(tr(lang, "dl_back"), url)]]) if url else None`;
5. appends exactly one note: none for a public URL, `dl_private_note` when a row exists with no
   username, `dl_unknown_note` when there is no row and no username (after the source link, as the
   last line);
6. passes `reply_markup=kb, markup_last_only=True` to the **final** `rich_send.send_content` call
   (the media `send_photo` above it stays caption-only).

Imports: `channel_by_chat`, `post_url` from `newsbot.core_channels`; `InlineKeyboardMarkup` from
`telegram`; `U` from `newsbot.ui_widgets`.

- [ ] **Step 6: Run to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase8_deeplink_back.py tests/test_rich_messages.py tests/test_phase7_onboarding.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add newsbot/ui_commands.py newsbot/ui_i18n.py newsbot/rich_send.py tests/test_phase8_deeplink_back.py
git commit -m "feat(phase8): back-to-channel button with honest platform notes"
```

---

### Task 4: Verification, counts and delivery records

**Files:**
- Modify: `README.md` (badge + Persian count)
- Modify: `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`
- Test: whole suite + tooling

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: the evidence rows, roadmap status and delivery notes this phase is judged by.

- [ ] **Step 1: Repeat-run the new suite**

Run five times: `.\.venv\Scripts\python.exe -m pytest tests/test_phase8_deeplink_back.py -q -x`
Expected: PASS on every run; no flake.

- [ ] **Step 2: Measure the collection and align both README numbers**

Run: `.\.venv\Scripts\python.exe -m pytest -q --collect-only -p no:cacheprovider`
Then update **both** the badge (`tests-N%20collected`) and the Persian `# N تست` to the measured
number, and run `tests/test_phase1_10_state_alignment.py` until green.

- [ ] **Step 3: Run the full local suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q --tb=line -rf` (about 11 minutes)
Expected: `19 failed, <rest> passed` — exactly the known Windows-only failures; any other failure
blocks the PR.

- [ ] **Step 4: Run the gates**

Run each and record the real output:

```
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy newsbot
.\.venv\Scripts\python.exe tools/final_verification.py
$env:BOT_TOKEN="dummy"; $env:SUPER_ADMIN_IDS="123456789"; $env:DB_FILE="$env:TEMP\p8-check.db"; .\.venv\Scripts\python.exe run.py --check
```

Expected: ruff clean, mypy 52 files clean, 17/17, exit 0.

- [ ] **Step 5: Update the state file**

- §2 row 8 → **`IN_PROGRESS`** with the code PR number and SHA.
- New §4.7 with scope, the three notes, the decisions D8-1…D8-8 and the acceptance mapping.
- §5 evidence rows: five green repeats, collection, full-suite result, tooling, CI, gate.
- §7 delivery rows for the design/plan PR and the code PR.
- §8: extend D-7 to name Phase 8's real-client check; add a note only if a defect or subagent
  failure actually happened.
- The "Exact next action" line → Phase 9, on explicit owner instruction only.

- [ ] **Step 6: Commit the records**

```bash
git add README.md docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md
git commit -m "docs(phase8): record delivery evidence and close Phase 8 code"
```

- [ ] **Step 7: PR chain (gate protocol, no shortcuts)**

1. PR1 **docs/phase8-design-plan** — spec, this plan, and the `PROPOSED_PATHS` entry; CI `test`
   SUCCESS; `gate.py <PR> lift` → `gh pr merge --squash --delete-branch` → `gate.py <PR> restore` →
   `verify_protection.py` 15/15 IDENTICAL.
2. PR2 **phase8/deeplink-back** — Tasks 1–3; same gate protocol; confirm `mergeable` and
   `mergeStateStatus` before lifting.
3. PR3 **docs/phase8-records** — Step 6; same gate protocol.

Record every PR number, SHA, CI duration and the 15/15 check in §5 as the PRs land.
