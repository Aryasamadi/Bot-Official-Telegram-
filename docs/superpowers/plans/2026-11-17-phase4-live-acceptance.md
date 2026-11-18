# Phase 4 Live Acceptance Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual-only GitHub Actions harness that boots the real bot on a fresh-or-reused sqlite
DB, supervises it through the Phase 4 Q&A flow against the real Telegram API, and returns a
machine-checkable verdict with a redacted JSON report.

**Architecture:** One standalone tool (`tools/qa_live_acceptance.py`) starts `python run.py` as a
child, captures its stdout/stderr, snapshots the sqlite DB and the log every 10 s, attributes each
`usage`/`ai_plan_grants` delta to the one `qa_answers` row that changed in the same window, and
evaluates a fixed check list. One manual workflow (`qa-live.yml`) runs that tool on a GitHub runner,
caches the test DB between runs, and uploads the report + log as artifacts.

**Tech Stack:** Python 3.13, sqlite3 (read-only URI connections), python-telegram-bot (unchanged),
GitHub Actions (manual dispatch, actions/cache v4), pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-phase4-live-acceptance-design.md`

## Global Constraints

- Python 3.13 (`pyproject.toml` `target-version = "py313"`), ruff line length **120**, `ruff check
  newsbot tests tools run.py` and `python -m pyflakes newsbot tools run.py tests` must stay clean.
- Reports and UI/runbook text in **Persian**; technical identifiers, log keys and check names in
  English.
- **No secret value may be printed, stored in the report, written to a file, or committed.** Only
  names, presence and lengths. `BOT_TOKEN` and `SECRETS_KEY` must never appear in the captured log;
  `SUPER_ADMIN_IDS` is a numeric id, not a credential, and is excluded from that check.
- Test-first: every production change in this plan starts with a failing test that is run and seen to
  fail before the implementation.
- Every `uses:` in the new workflow is pinned to a **40-char commit SHA**. `ci.yml` must stay a
  single `test` job; the new workflow must never run on `push`/`pull_request`.
- The test DB is never uploaded as an artifact; the report carries ids/counts only.
- The repo-integrity guard rejects backticked path-like strings that do not exist: cite a file only
  after it exists, and write branch names in bold, not backticks.
- README's test badge and its "# N تست" line must equal the real collected count
  (`tests/test_phase1_10_state_alignment.py::test_readme_*` locks this).

## Review Focus

1. **A question posted before the bot is polling** — no row, so the run must time out with exit `1`,
   never pass. Locked by `test_a_missing_answer_is_not_observed` and by the supervisor's "required
   check" loop.
2. **Two questions inside one 10 s window** — counters must be reported `ERROR`, not guessed. Locked
   by `test_two_rows_in_one_window_are_ambiguous`.
3. **A reused DB with rows/grants from an earlier run** — earlier rows must not satisfy any check.
   Locked by `test_rows_from_an_earlier_run_are_ignored` and
   `test_an_earlier_grant_period_does_not_satisfy_the_quota_check`.
4. **A super-admin asker** — charging is unobservable; the run must exit `2`. Locked by
   `test_a_super_asker_is_a_precondition_failure`.
5. **The ignore path must prove receipt** — silence is indistinguishable from a bot that never got
   the message; the new log line must carry ids only, never question text. Locked by
   `test_the_ignore_line_carries_no_question_or_post_text` and
   `test_a_foreign_group_logs_one_ignore_line_with_reason`.

---

## File Structure

| File | Responsibility |
|---|---|
| `newsbot/qa.py` (modify) | One id-only `qa: ignored …` INFO line on the three security-relevant ignore paths, DEBUG on the two harmless ones. No other behaviour change. |
| `tools/qa_live_acceptance.py` (new) | Standalone operator tool: env loading, `--check` preconditions, DB snapshots, per-row attribution, check evaluation, child supervision, report writing. No import of `newsbot.config` (the child owns config). |
| `tools/qa_live_acceptance.py` may import `newsbot.timeutils` only if it needs the timestamp format — it must not, it computes the same UTC ISO format itself. | |
| `tests/test_phase4_qa_core.py` (modify) | Log-line tests for the ignore paths. |
| `tests/test_phase4_live_acceptance.py` (new) | All offline tests for the tool, the workflow and the runbook. |
| `.github/workflows/qa-live.yml` (new) | Manual runner workflow. |
| `docs/QA_LIVE_ACCEPTANCE.md` (new) | Persian runbook (secrets, Telegram steps, evidence, troubleshooting). |
| `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md` (modify) | Phase 4 live-acceptance status, new blockers/debts, evidence rows. |
| `README.md` (modify) | Test count only. |

---

### Task 1: Id-only ignore log lines in `newsbot/qa.py`

**Files:**
- Modify: `newsbot/qa.py` (the early returns in `on_group_reply`, lines ~140-157)
- Test: `tests/test_phase4_qa_core.py` (append to the existing file)

**Interfaces:**
- Produces: log lines the live tool parses:
  - `qa: ignored chat=<int> msg=<int> user=<int> reason=<unregistered_channel|toggle_off|foreign_group>` (INFO)
  - `qa: ignored chat=<int> msg=<int> user=<int> reason=<not_a_reply|not_a_post>` (DEBUG)
- Produces: no new function is required; a module-private helper
  `_log_ignored(msg, user, reason, level="info")` is acceptable if it keeps the handler readable.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phase4_qa_core.py` (it already imports `qa`, `config`, `add_channel`,
`update_settings`, `asyncio`, `NS`, `pytest`, and defines `_channel_post`, `_group_msg`, `_update`,
`_Bot`, `_stub_ai`, and the `chan` fixture — reuse them):

```python
def _ignored_lines(caplog):
    return [r.getMessage() for r in caplog.records if "qa: ignored" in r.getMessage()]


def test_an_unregistered_channel_logs_one_ignore_line_with_reason(isolated_db, caplog, monkeypatch):
    _stub_ai(monkeypatch)
    with caplog.at_level("INFO", logger="core"):
        asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=_Bot())))
    lines = _ignored_lines(caplog)
    assert len(lines) == 1 and "reason=unregistered_channel" in lines[0]
    assert "chat=-555" in lines[0] and "msg=10" in lines[0] and "user=900001" in lines[0]


def test_a_toggle_off_logs_one_ignore_line_with_reason(chan, caplog, monkeypatch):
    _stub_ai(monkeypatch)
    update_settings(chan["id"], qa_enabled=False)
    with caplog.at_level("INFO", logger="core"):
        asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=_Bot())))
    assert [l for l in _ignored_lines(caplog) if "reason=toggle_off" in l]


def test_a_foreign_group_logs_one_ignore_line_with_reason(chan, caplog, monkeypatch):
    _stub_ai(monkeypatch)
    with caplog.at_level("INFO", logger="core"):
        asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=_Bot(linked=-999999))))
    assert [l for l in _ignored_lines(caplog) if "reason=foreign_group" in l]


def test_a_reply_to_a_user_message_is_logged_only_at_debug(chan, caplog, monkeypatch):
    _stub_ai(monkeypatch)
    user_msg = NS(text="سلام", caption=None, sender_chat=None, forward_from_chat=None,
                  forward_from_message_id=None, is_automatic_forward=False, message_id=4)
    with caplog.at_level("DEBUG", logger="core"):
        asyncio.run(qa.on_group_reply(_update(_group_msg(reply=user_msg)), NS(bot=_Bot())))
    assert [l for l in _ignored_lines(caplog) if "reason=not_a_post" in l]


def test_the_ignore_line_carries_no_question_or_post_text(chan, caplog, monkeypatch):
    _stub_ai(monkeypatch)
    with caplog.at_level("DEBUG", logger="core"):
        asyncio.run(qa.on_group_reply(
            _update(_group_msg(text="سؤالِ محرمانه", reply=_channel_post(text="متنِ محرمانهٔ پست"))),
            NS(bot=_Bot())))
    assert "محرمانه" not in caplog.text
```

Note: `update_settings(chan["id"], qa_enabled=False)` is the existing helper signature used by the
Phase 4 tests — check `newsbot/core_channels.py:update_settings` and adapt the call if its signature
differs; do not change production code to fit the test.

- [ ] **Step 2: Run the tests and see them fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_phase4_qa_core.py -k "ignore" -v`
Expected: FAIL — no `qa: ignored` line in `caplog` (5 failures).

- [ ] **Step 3: Implement the log lines**

In `newsbot/qa.py`, inside `on_group_reply`, at the silent returns:

```python
    if reply is None:
        log.debug("qa: ignored chat=%s msg=%s user=%s reason=not_a_reply", ...)
        return
    post = detect_post(reply)
    if not post:
        log.debug("qa: ignored chat=%s msg=%s user=%s reason=not_a_post", ...)
        return
    ch = await arun(channel_by_chat, post["channel_chat_id"])
    if not ch:
        log.info("qa: ignored chat=%s msg=%s user=%s reason=unregistered_channel", ...)
        return
    st = await arun(get_settings, ch["id"])
    if not st.get("qa_enabled", True):
        log.info("qa: ignored chat=%s msg=%s user=%s reason=toggle_off", ...)
        return
    ...
    if linked is None or int(linked) != int(ch["chat_id"]):
        log.info("qa: ignored chat=%s msg=%s user=%s reason=foreign_group", ...)
        return
```

The ids come from `getattr(msg, "chat_id", 0)` and `getattr(msg, "message_id", 0)` and
`getattr(user, "id", 0)` — compute them once near the top (`chat_id`, `img_id`, `uid`) and reuse the
existing names so the later `qa: answered` line is unchanged. Keep the `if not config.QA_ENABLED`
and `msg is None or user is None` returns silent (no attributable id). Never interpolate
`post_text`/`question_text`.

- [ ] **Step 4: Run the tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_phase4_qa_core.py -v`
Expected: PASS (all existing + 5 new).

- [ ] **Step 5: Lint and commit**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_phase4_qa_core.py -q
ruff check newsbot tests
python -m pyflakes newsbot tests
git add newsbot/qa.py tests/test_phase4_qa_core.py
git commit -m "feat(qa): log ignored group replies with an id-only reason"
```

---

### Task 2: Tool skeleton, env loading and `--check`

**Files:**
- Create: `tools/qa_live_acceptance.py`
- Test: `tests/test_phase4_live_acceptance.py` (new)

**Interfaces:**
- `def load_env(path: str | None) -> dict[str, str]` — parse `KEY=VALUE` lines (skip blank/`#`,
  strip surrounding quotes), start from that mapping, then overlay `os.environ` (process env wins).
- `def token_shape_ok(token: str) -> bool` — `^\d{6,}:[A-Za-z0-9_-]{30,}$`.
- `def admin_ids(value: str) -> set[int]`
- `def secret_values_present(text: str, values: dict[str, str]) -> list[str]` — returns **names** of
  the secrets whose value (length ≥ 8) occurs in `text`; never returns the values. The CLI passes
  only `BOT_TOKEN` and `SECRETS_KEY` (never `SUPER_ADMIN_IDS`: `preflight` prints the numeric id by
  design and it is not a credential).
- `def http_get_json(url: str, timeout: float = 10) -> dict` — `urllib.request`, raises
  `RuntimeError(f"http {status}")` on HTTP errors without including the URL (it contains the token).
- `def run_check(env: dict[str, str], http_get=http_get_json) -> tuple[int, dict]` — `0` ok, `2`
  failed; payload `{"ok": bool, "problems": [codes], "token_len": int, "admins": int}`.
- CLI: `qa_live_acceptance.py --check [--env-file PATH]` prints the payload as JSON.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_phase4_live_acceptance.py` with the header and these tests:

```python
# -*- coding: utf-8 -*-
"""فاز ۴ — ابزار پذیرشِ زنده (workflow دستیِ qa-live) و نگهبان‌های فایل‌های آن."""
import json
import os

import pytest

from tools import qa_live_acceptance as acc

TOKEN = "123456789:AA" + "x" * 34


def test_env_file_values_are_loaded_and_process_env_wins(tmp_path, monkeypatch):
    p = tmp_path / "run.env"
    p.write_text('BOT_TOKEN="from-file"\nSUPER_ADMIN_IDS=42\n# comment\n\n', encoding="utf-8")
    monkeypatch.setenv("BOT_TOKEN", "from-process")
    env = acc.load_env(str(p))
    assert env["BOT_TOKEN"] == "from-process" and env["SUPER_ADMIN_IDS"] == "42"


def test_the_token_shape_is_checked():
    assert acc.token_shape_ok(TOKEN)
    assert not acc.token_shape_ok("not-a-token")
    assert not acc.token_shape_ok("123456789:short")


def test_admin_ids_accept_commas_and_spaces():
    assert acc.admin_ids(" 1, 22 33 ") == {1, 22, 33}


def test_secret_presence_reports_names_only():
    names = acc.secret_values_present("x " + TOKEN + " y", {"BOT_TOKEN": TOKEN, "SECRETS_KEY": "k" * 64})
    assert names == ["BOT_TOKEN"]


def test_check_rejects_a_missing_token(tmp_path):
    env = {"BOT_TOKEN": "", "SUPER_ADMIN_IDS": "1", "DB_FILE": str(tmp_path / "db.sqlite")}
    code, out = acc.run_check(env, http_get=lambda url, timeout=10: {"ok": True})
    assert code == 2 and "BOT_TOKEN" in out["problems"]


def test_check_requires_secrets_key_when_the_db_exists(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    env = {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1", "SECRETS_KEY": "", "DB_FILE": str(db)}
    code, out = acc.run_check(env, http_get=lambda url, timeout=10: {"ok": True})
    assert code == 2 and "SECRETS_KEY_REQUIRED" in out["problems"]


def test_check_calls_get_me_and_maps_failures_without_the_url(tmp_path):
    env = {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1", "SECRETS_KEY": "k" * 64,
           "DB_FILE": str(tmp_path / "db.sqlite")}
    seen = {}

    def boom(url, timeout=10):
        seen["url"] = url
        raise RuntimeError("http 401")

    code, out = acc.run_check(env, http_get=boom)
    assert code == 2 and "GETME_FAILED" in out["problems"]
    assert TOKEN in seen["url"] and TOKEN not in json.dumps(out)


def test_check_passes_with_valid_inputs(tmp_path):
    env = {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1", "SECRETS_KEY": "k" * 64,
           "DB_FILE": str(tmp_path / "db.sqlite")}
    code, out = acc.run_check(env, http_get=lambda url, timeout=10: {"ok": True, "result": {"id": 1}})
    assert code == 0 and out["ok"] is True and out["token_len"] == len(TOKEN)
```

`tools/` has no `__init__.py`, but the repo already imports tool modules that way
(`tests/test_schema_ordering.py` does `from tools import migrate_sqlite_to_d1 as tool`), so
`from tools import qa_live_acceptance as acc` is the expected pattern. Add
`ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` to the test module; it is
reused by the workflow/runbook guards in Tasks 5 and 6. Importing the module must have **no side
effects** (no env read, no network, no file write at import time).

- [ ] **Step 2: Run and see them fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_phase4_live_acceptance.py -v`
Expected: FAIL — `ModuleNotFoundError` / missing attributes.

- [ ] **Step 3: Implement the module skeleton**

`tools/qa_live_acceptance.py` starts with a Persian docstring (what it proves, how to run, "no secret
is ever printed"), then:

```python
SCHEMA = 1
POLL_SECONDS = 10
STOP_GRACE_SECONDS = 15
MIN_DURATION_MIN = 5
MAX_DURATION_MIN = 355
```

`run_check` problems are fixed codes: `BOT_TOKEN_MISSING`, `BOT_TOKEN_SHAPE`, `ADMIN_IDS_MISSING`,
`SECRETS_KEY_REQUIRED`, `DB_DIR_NOT_WRITABLE`, `GETME_FAILED`. `GETME_FAILED` carries only
`type(e).__name__` and, for `RuntimeError("http 401")`, the status text — never the URL.
`DB_DIR_NOT_WRITABLE`: create the parent dir with `os.makedirs(..., exist_ok=True)` and test
`os.access(parent, os.W_OK)`.

`http_get_json` must build `https://api.telegram.org/bot<token>/getMe` (honour
`TELEGRAM_API_BASE` when set) and parse the JSON body; any non-200, any `ok: false`, or any network
exception becomes `RuntimeError` with a status/class only.

- [ ] **Step 4: Run the tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_phase4_live_acceptance.py -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
ruff check tools tests && python -m pyflakes tools tests
git add tools/qa_live_acceptance.py tests/test_phase4_live_acceptance.py
git commit -m "feat(qa-live): standalone preconditions checker for the live acceptance run"
```

---

### Task 3: DB snapshots, per-row attribution and check evaluation

**Files:**
- Modify: `tools/qa_live_acceptance.py`
- Test: `tests/test_phase4_live_acceptance.py`

**Interfaces:**
- `@dataclass(frozen=True) class Snapshot` with `qa_rows`, `usage`, `grants`, `channels`, `models`
  (each a tuple of `dict`), and `EMPTY_SNAPSHOT`.
- `def read_snapshot(db_path: str) -> Snapshot` — read-only sqlite URI
  (`file:...?mode=ro`, `uri=True`); missing file ⇒ `EMPTY_SNAPSHOT`; a missing table ⇒ empty tuple
  for that table.
- `def usage_delta(prev: Snapshot, cur: Snapshot) -> dict[int, int]` — by `admin_id`, summed over
  days, only non-zero deltas.
- `def grant_delta(prev: Snapshot, cur: Snapshot) -> dict[int, int]` — by grant `id`.
- `def attribute(prev: Snapshot, cur: Snapshot) -> list[dict]` — one entry per change:
  `{"key": [chat_id, msg_id], "kind": "insert"|"status", "to": status,
    "usage": {...}, "grants": {...}, "ambiguous": bool}`. Deltas are attached only when the window
  contains exactly **one distinct row key** (any number of events on that same row is fine), and
  they attach to the **earliest** event of that key only; every later event of the same key in that
  window carries empty dicts (so summing a row's transitions never double-counts). Otherwise
  `ambiguous=True` and both dicts are empty.
- `@dataclass class EvalContext` — `scenario`, `started_at`, `expected_group`, `expected_channel`,
  `transitions`, `snapshot`, `log_text`, `super_ids`, `secret_values`, `precondition`.
- `REQUIRED = {"core": (...), "full": (...)}` — check names per scenario.
- `def evaluate(ctx: EvalContext) -> list[dict]` — each `{"name", "status", "detail"}`.
- `def parse_ignore_lines(text) -> list[dict]` and `def parse_answered_lines(text) -> list[dict]`.

Exact check names: `preconditions`, `channel_registered`, `public_model_active`,
`answer_delivered`, `channel_id_is_telegram_chat_id`, `asker_charged_only`,
`answered_exactly_once`, `ignored_with_a_reason`, `unrelated_group_silent`, `toggle_off_silent`,
`quota_exhausted_message`, `no_text_no_ai`, `no_secret_in_log`.

- [ ] **Step 1: Write the failing tests**

Add helpers that seed the **real** schema through the existing `isolated_db` fixture (session
`conftest.py`) with plain SQL, then these tests:

```python
def _seed(db_path, rows=(), usage=(), grants=(), channels=(), models=()):
    import sqlite3
    c = sqlite3.connect(db_path)
    for r in rows:
        c.execute("INSERT OR REPLACE INTO qa_answers(chat_id,msg_id,user_id,channel_id,post_id,status,answer,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", r)
    for u in usage:
        c.execute("INSERT OR REPLACE INTO usage(admin_id,day,posts,tests,ai) VALUES(?,?,0,0,?)", u)
    for g in grants:
        c.execute("INSERT INTO ai_plan_grants(id,user_id,plan_id,quota,used,granted_at,expires_at) VALUES(?,?,?,?,?,?,?)", g)
    for ch in channels:
        c.execute("INSERT INTO channels(id,admin_id,chat_id,title) VALUES(?,?,?,?)", ch)
    for m in models:
        c.execute("INSERT INTO ai_models(id,name,active,status,owner_id) VALUES(?,?,?,?,?)", m)
    c.commit(); c.close()
```

Use `START = "2026-11-17T10:00:00.000000+00:00"` and rows created after it.

- `test_snapshot_reads_the_real_schema` — seed 1 row/usage/grant/channel/model, assert shapes.
- `test_rows_from_an_earlier_run_are_ignored` — one `done` row created before `START`; every
  run-scoped check must be `NOT_OBSERVED`/empty.
- `test_answer_delivered_and_channel_id_are_checked` — a done row with `channel_id == -100111`
  while `channels` has `id=1, chat_id=-100111`; both checks `OBSERVED`. Flip `channel_id` to `1`
  and `channel_id_is_telegram_chat_id` becomes `NOT_OBSERVED`.
- `test_two_rows_in_one_window_are_ambiguous` — `attribute` over a window with two inserts ⇒ both
  entries `ambiguous` and `asker_charged_only` `ERROR`.
- `test_asker_charged_only_requires_exactly_one_unit_for_that_user` — transition with
  `usage={900001:1}` ⇒ `OBSERVED`; `{900001:2}` or `{900001:1, 5:1}` ⇒ `NOT_OBSERVED`.
- `test_a_done_row_needs_exactly_one_answered_line` — log with one `qa: answered` for the row ⇒
  `OBSERVED`; zero or two ⇒ `NOT_OBSERVED`.
- `test_a_foreign_group_ignore_line_and_no_row_is_observed` — log line for `chat=-777` plus a row
  for `chat=-555`; `unrelated_group_silent` `OBSERVED`. Add a row for `chat=-777` ⇒ `NOT_OBSERVED`.
- `test_toggle_off_requires_a_line_and_no_row` — line only ⇒ `OBSERVED`; a row with that `(chat,msg)`
  ⇒ `NOT_OBSERVED`.
- `test_quota_exhaustion_needs_a_failed_row_and_no_movement` — a `failed/ai_limit` row with empty
  deltas and one `qa: ai failed … err=ai_limit` line ⇒ `OBSERVED`; a non-empty usage delta or a
  second matching log line ⇒ `NOT_OBSERVED`.
- `test_an_earlier_grant_period_does_not_satisfy_the_quota_check` — the only `failed/ai_limit` row is
  from before `START` ⇒ `NOT_OBSERVED`.
- `test_no_text_row_is_free_and_optional` — a `failed/no_text` row with no movement ⇒ `OBSERVED`;
  no such row ⇒ `NOT_APPLICABLE`.
- `test_a_super_asker_is_a_precondition_failure` — `precondition="SUPER_ASKER"` ⇒ `preconditions`
  `ERROR`; the required set is not satisfied.
- `test_the_secret_check_reports_absent_values_only` — log containing the token ⇒
  `no_secret_in_log` `NOT_OBSERVED` and its `detail` names the secret, never the value.

- [ ] **Step 2: Run and see them fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_phase4_live_acceptance.py -v`
Expected: FAIL — `read_snapshot`/`evaluate` missing.

- [ ] **Step 3: Implement**

Rules to encode exactly:
- *run scope*: `created_at >= started_at` (both are the same ISO string format).
- *core row*: earliest `done` run row with `chat_id == expected_group`.
- *asker_charged_only* (per `done` run row with at least one transition): aggregate its transitions;
  OBSERVED when every row has `ambiguous is False`, `usage == {row.user_id: 1}`, and
  `grants == {}` or `grants == {gid: 1}` for a grant `gid` whose `user_id == row.user_id`.
- *answered_exactly_once* (per `done` run row): exactly one `qa: answered` line for
  `(chat_id, msg_id)`.
- *quota_exhausted_message*: at least one run row with `status='failed' and error='ai_limit'`; each
  such row must have empty aggregated `usage` and `grants` and exactly one
  `qa: ai failed … err=ai_limit` line for its key.
- `preconditions` is `ERROR` when `ctx.precondition` is set, else `OBSERVED`.
- `channel_registered` is `NOT_OBSERVED` when `expected_channel`/`expected_group` is falsy.
- `public_model_active` counts `owner_id is None and int(active) == 1`.
- `no_text_no_ai` is never required.

- [ ] **Step 4: Run the tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_phase4_live_acceptance.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
ruff check tools tests && python -m pyflakes tools tests
git add tools/qa_live_acceptance.py tests/test_phase4_live_acceptance.py
git commit -m "feat(qa-live): per-row attribution and the acceptance checks"
```

---

### Task 4: Child supervisor, exit codes and the report

**Files:**
- Modify: `tools/qa_live_acceptance.py`
- Test: `tests/test_phase4_live_acceptance.py`

**Interfaces:**
- `@dataclass class SuperviseConfig` — `db_path`, `log_path`, `duration_s`, `scenario`, `super_ids`,
  `secret_values`, `poll_s=POLL_SECONDS`, `env`.
- `def supervise(cfg, *, spawn, snapshot=read_snapshot, read_log=read_text, clock, sleep, resolve, now=utc_now_iso) -> tuple[int, dict]`
  — `spawn() -> proc` (`.terminate()`, `.wait(timeout)`, `.kill()`, `.poll()`),
  `resolve(snapshot) -> (channel_chat_id, group_chat_id)` raising `Precondition(code)`,
  `read_log(path) -> str`.
- `def utc_now_iso() -> str` — `datetime.now(timezone.utc).isoformat(timespec="microseconds")`.
- `def blank_answers(db_path)` — `UPDATE qa_answers SET answer=''` on a writable connection.
- `def main(argv=None) -> int`. CLI: `--supervise --duration MIN --report PATH --log PATH
  [--scenario core|full] [--env-file PATH]` and `--check [--env-file PATH]`.
- `Precondition` exception with a `.code`.

Flow: preconditions (`BOT_NOT_READY`, `CHANNEL_NOT_REGISTERED`, `NO_LINKED_GROUP`, `BAD_DURATION`,
`SUPER_ASKER`) → spawn → readiness loop of **60 attempts × 2 s** looking for `"در حال اجرا…"` (do
not use the injected clock for this loop, or a fake clock would spin forever) → first snapshot →
resolve → loop {sleep, snapshot, attribute, evaluate; stop when all required checks are `OBSERVED`
or the duration elapsed} → stop child (terminate, wait 15 s, kill) → blank answers → write the
report → return `0` when all required are `OBSERVED`, `2` when a precondition/`ERROR` on
`preconditions`, else `1`.

- [ ] **Step 1: Write the failing tests**

```python
class FakeProc:
    def __init__(self):
        self.terminated = self.killed = False
    def terminate(self): self.terminated = True
    def wait(self, timeout=None):
        if not self.terminated: raise RuntimeError("still running")
        return 0
    def kill(self): self.killed = True
    def poll(self): return 0 if self.terminated else None


class FakeTime:
    def __init__(self): self.now = 0.0
    def clock(self): return self.now
    def sleep(self, s): self.now += s


def _cfg(tmp_path, **kw):
    base = dict(db_path=str(tmp_path / "db.sqlite"), log_path=str(tmp_path / "bot.log"),
                duration_s=600, scenario="core", super_ids={1}, secret_values={"BOT_TOKEN": TOKEN},
                poll_s=10, env={})
    base.update(kw)
    return acc.SuperviseConfig(**base)


def _scripted(monkeypatch, checks_by_call):
    calls = {"n": 0}
    def fake_eval(ctx):
        i = min(calls["n"], len(checks_by_call) - 1); calls["n"] += 1
        return checks_by_call[i]
    monkeypatch.setattr(acc, "evaluate", fake_eval)


PASS = [{"name": n, "status": "OBSERVED", "detail": ""} for n in acc.REQUIRED["core"]]
WAIT = [{"name": n, "status": "NOT_OBSERVED", "detail": ""} for n in acc.REQUIRED["core"]]


def test_the_supervisor_stops_early_when_every_check_is_observed(tmp_path, monkeypatch):
    _scripted(monkeypatch, [WAIT, PASS])
    ft, proc = FakeTime(), FakeProc()
    code, report = acc.supervise(
        _cfg(tmp_path), spawn=lambda: proc, snapshot=lambda p: acc.EMPTY_SNAPSHOT,
        read_log=lambda p: "در حال اجرا…", clock=ft.clock, sleep=ft.sleep,
        resolve=lambda s: (-100111, -555))
    assert code == 0 and proc.terminated and ft.now == 20


def test_the_supervisor_times_out_with_exit_1(tmp_path, monkeypatch):
    _scripted(monkeypatch, [WAIT])
    ft, proc = FakeTime(), FakeProc()
    code, _ = acc.supervise(
        _cfg(tmp_path, duration_s=30), spawn=lambda: proc, snapshot=lambda p: acc.EMPTY_SNAPSHOT,
        read_log=lambda p: "در حال اجرا…", clock=ft.clock, sleep=ft.sleep,
        resolve=lambda s: (-100111, -555))
    assert code == 1 and proc.terminated


def test_a_bot_that_never_starts_is_a_precondition_failure(tmp_path):
    proc = FakeProc()
    code, report = acc.supervise(
        _cfg(tmp_path), spawn=lambda: proc, snapshot=lambda p: acc.EMPTY_SNAPSHOT,
        read_log=lambda p: "", clock=FakeTime().clock, sleep=lambda s: None,
        resolve=lambda s: (-100111, -555))
    assert code == 2 and "BOT_NOT_READY" in report["problems"] and proc.terminated


def test_a_duration_outside_the_bounds_is_a_precondition_failure(tmp_path):
    spawned = {"n": 0}
    code, report = acc.supervise(
        _cfg(tmp_path, duration_s=60), spawn=lambda: spawned.update(n=1),
        snapshot=lambda p: acc.EMPTY_SNAPSHOT, read_log=lambda p: "در حال اجرا…",
        clock=FakeTime().clock, sleep=lambda s: None, resolve=lambda s: (-100111, -555))
    assert code == 2 and "BAD_DURATION" in report["problems"] and spawned["n"] == 0


def test_the_report_never_contains_a_secret_or_an_answer(tmp_path, monkeypatch):
    _scripted(monkeypatch, [PASS])
    code, report = acc.supervise(
        _cfg(tmp_path), spawn=lambda: FakeProc(), snapshot=lambda p: acc.EMPTY_SNAPSHOT,
        read_log=lambda p: "در حال اجرا… " + TOKEN + " TEXT_OF_ANSWER",
        clock=FakeTime().clock, sleep=lambda s: None, resolve=lambda s: (-100111, -555))
    body = json.dumps(report, ensure_ascii=False)
    assert code == 0 and TOKEN not in body and "TEXT_OF_ANSWER" not in body
```

Implement the fakes with a fake clock and a no-op `sleep`; `evaluate` is monkeypatched so these tests
prove the loop/exit-code contract only (Task 3 owns the checks).

- [ ] **Step 2: Run and see them fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_phase4_live_acceptance.py -k supervisor -v`
Expected: FAIL — `supervise` missing.

- [ ] **Step 3: Implement**

- The child is spawned with `subprocess.Popen([sys.executable, "run.py"], cwd=repo_root,
  env=cfg.env, stdout=log_fh, stderr=subprocess.STDOUT)`; the log file is opened in `"w"`.
- Readiness loop must not consume the bot's updates (no HTTP), only the log file.
- The report is written atomically (`report.json.tmp` → `os.replace`) as JSON with
  `sort_keys=True, ensure_ascii=False, indent=2`.
- Report fields: `schema`, `started_at`, `duration_s`, `scenario`, `db_reused`, `checks`, `exit_code`,
  `redactions: ["BOT_TOKEN", "SECRETS_KEY"]`, `bot_log_lines`, `log_sha256`, `problems`.
- `supervise` treats `duration_s` outside `[5*60, 355*60]` as `Precondition("BAD_DURATION")`; the
  report is still written on that path.
- `--check` remains a separate mode; `--supervise` runs the same preconditions first.

- [ ] **Step 4: Run the tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_phase4_live_acceptance.py -v`
Expected: PASS.

- [ ] **Step 5: Manual smoke test (offline)**

Run the tool against a dummy token with network stubbed is not possible from the CLI; instead run
`--check` with a dummy token and confirm it exits `2` with `GETME_FAILED` (or `0` if the token is
real and the network answers) and prints no secret. Record the output in the commit message notes.

- [ ] **Step 6: Commit**

```bash
ruff check tools tests && python -m pyflakes tools tests
git add tools/qa_live_acceptance.py tests/test_phase4_live_acceptance.py
git commit -m "feat(qa-live): supervise the bot, attribute counters and write the report"
```

---

### Task 5: The manual workflow

**Files:**
- Create: `.github/workflows/qa-live.yml`
- Test: `tests/test_phase4_live_acceptance.py`

**Interfaces:**
- Inputs: `duration_minutes` (number, default `"350"`), `scenario` (choice `core`/`full`, default
  `core`), `db_mode` (choice `reuse`/`fresh`, default `reuse`).
- Job `qa-live`, `timeout-minutes: 360`, `permissions: contents: read`,
  `concurrency: group: qa-live, cancel-in-progress: false`.

- [ ] **Step 1: Write the failing guard tests**

```python
WF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  ".github", "workflows", "qa-live.yml")


def _wf():
    with open(WF, encoding="utf-8") as f:
        return f.read()


def test_the_workflow_is_manual_only():
    src = _wf()
    assert "workflow_dispatch:" in src
    for forbidden in ("\n  push:", "\n  pull_request:", "\n  schedule:"):
        assert forbidden not in src


def test_every_action_is_pinned_to_a_commit_sha():
    for line in _wf().splitlines():
        s = line.strip()
        if s.startswith("- uses:") or s.startswith("uses:"):
            ref = s.split("uses:", 1)[1].strip().split()[0]
            assert re.fullmatch(r"[\w.-]+(?:/[\w.-]+)+@[0-9a-f]{40}", ref), ref


def test_the_workflow_has_the_required_settings():
    src = _wf()
    assert "permissions:\n  contents: read" in src
    assert "timeout-minutes: 360" in src
    assert "default: \"350\"" in src
    assert "qa-live-db-v1-${{ github.run_id }}" in src
    assert "restore-keys: qa-live-db-v1-" in src
    assert "actions/cache/restore@" in src and "actions/cache/save@" in src
    assert "name: Save the test DB for the next run" in src
    assert "retention-days: 14" in src
    assert "on:" in src and "if: always()" in src


def test_no_secret_is_used_inside_an_if_expression():
    for line in _wf().splitlines():
        if line.strip().startswith("if:"):
            assert "secrets." not in line


def test_the_run_step_receives_the_three_secrets():
    src = _wf()
    block = src.split("name: Run the live acceptance", 1)[1].split("name:", 1)[0]
    for name in ("BOT_TOKEN", "SUPER_ADMIN_IDS", "SECRETS_KEY"):
        assert "${{ secrets.%s }}" % name in block
```

- [ ] **Step 2: Run and see them fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_phase4_live_acceptance.py -k workflow -v`
Expected: FAIL — file missing.

- [ ] **Step 3: Write the workflow**

Use exactly these pinned SHAs: checkout `11d5960a326750d5838078e36cf38b85af677262`, setup-python
`a26af69be951a213d495a4c3e4e4022e16d87065`, cache restore/save `0057852bfaa89a56745cba8c7296529d2fc39830`,
upload-artifact `ea165f8d65b6e75b540449e92b4886f43607fa02`.

```yaml
name: QA Live Acceptance

on:
  workflow_dispatch:
    inputs:
      duration_minutes:
        description: 'حداکثر مدت اجرا (دقیقه)'
        required: false
        default: "350"
        type: string
      scenario:
        description: 'سناریو'
        required: false
        default: core
        type: choice
        options: [core, full]
      db_mode:
        description: 'دیتابیس: reuse از کش، یا fresh تازه'
        required: false
        default: reuse
        type: choice
        options: [reuse, fresh]

permissions:
  contents: read

concurrency:
  group: qa-live
  cancel-in-progress: false

jobs:
  qa-live:
    name: qa-live
    runs-on: ubuntu-latest
    timeout-minutes: 360
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5
        with:
          python-version: "3.13"
          cache: "pip"
          cache-dependency-path: requirements.txt
      - name: Restore the test DB
        if: inputs.db_mode == 'reuse'
        uses: actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830 # v4
        with:
          path: ${{ runner.temp }}/qa-live/newsbot.db
          key: qa-live-db-v1-${{ github.run_id }}
          restore-keys: qa-live-db-v1-
      - name: Install dependencies
        run: |
          python -m pip install "pip==26.2.2"
          pip install -r requirements.txt
      - name: Run the live acceptance
        env:
          BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
          SUPER_ADMIN_IDS: ${{ secrets.SUPER_ADMIN_IDS }}
          SECRETS_KEY: ${{ secrets.SECRETS_KEY }}
          DB_BACKEND: sqlite
          DB_FILE: ${{ runner.temp }}/qa-live/newsbot.db
          QA_ENABLED: "1"
          REPORT: ${{ runner.temp }}/qa-live/report.json
          BOT_LOG: ${{ runner.temp }}/qa-live/bot.log
        run: |
          mkdir -p "$(dirname "$DB_FILE")"
          python tools/qa_live_acceptance.py --supervise \
            --duration "${{ inputs.duration_minutes }}" \
            --scenario "${{ inputs.scenario }}" \
            --report "$REPORT" --log "$BOT_LOG"
      - name: Upload the evidence
        if: always()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: qa-live-${{ github.run_id }}
          path: |
            ${{ runner.temp }}/qa-live/report.json
            ${{ runner.temp }}/qa-live/bot.log
          if-no-files-found: warn
          retention-days: 14
      - name: Save the test DB for the next run
        if: always()
        uses: actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830 # v4
        with:
          path: ${{ runner.temp }}/qa-live/newsbot.db
          key: qa-live-db-v1-${{ github.run_id }}
```

Why `restore` + `save` as two steps: a unique key means the cache never "hits", so `actions/cache`
would save automatically — but only when the restore step ran, which is skipped on `db_mode=fresh`.
A skipped restore would leave the **old** cache as the newest one, so the next `reuse` run would
restore a stale DB. The explicit save always replaces the newest prefix.

- The `uses:` guard must accept a sub-path action (`actions/cache/restore`), so its regex is
  `[\w.-]+(?:/[\w.-]+)+@[0-9a-f]{40}`.
- `test_the_workflow_has_the_required_settings` must also assert
  `"actions/cache/restore@" in src and "actions/cache/save@" in src` and
  `"name: Save the test DB for the next run" in src`.

- [ ] **Step 4: Run the guard tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_phase4_live_acceptance.py -k workflow -v`
Expected: PASS.

- [ ] **Step 5: Validate the YAML and commit**

There is no PyYAML in the project; validate with `python -c "import yaml"` only if available, else
rely on the textual guards and the GitHub parse at PR time. Then:

```bash
ruff check tests && python -m pyflakes tests
git add .github/workflows/qa-live.yml tests/test_phase4_live_acceptance.py
git commit -m "ci(qa-live): manual workflow that runs the live acceptance on a runner"
```

---

### Task 6: Runbook, state file, README count

**Files:**
- Create: `docs/QA_LIVE_ACCEPTANCE.md`
- Modify: `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`
- Modify: `README.md`
- Test: `tests/test_phase4_live_acceptance.py`

**Interfaces:**
- Runbook must name the three secrets, the prerequisites, the ordered stages, `db_mode`, the
  evidence and the troubleshooting table.
- State file: `Current phase` stays `Phase 5` (`NOT_STARTED`); the roadmap row for Phase 4 stays
  `COMPLETED` (the guard splits the status cell on whitespace); add a §4.5 subsection and §8 entries.

- [ ] **Step 1: Write the failing runbook guard test**

```python
def test_the_runbook_names_the_secrets_and_the_telegram_steps():
    body = _src("docs/QA_LIVE_ACCEPTANCE.md")
    for name in ("BOT_TOKEN", "SUPER_ADMIN_IDS", "SECRETS_KEY"):
        assert name in body
    for needle in ("گروهِ بحث", "reply", "ai_quota=1", "db_mode", "artifact"):
        assert needle in body
```

Add `_src` like the other tests (`open(os.path.join(ROOT, path), encoding="utf-8").read()`).

- [ ] **Step 2: Run and see it fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_phase4_live_acceptance.py -k runbook -v`
Expected: FAIL — file missing.

- [ ] **Step 3: Write `docs/QA_LIVE_ACCEPTANCE.md` (Persian)**

Sections in this order:
1. چه چیزی اثبات می‌شود (تلگرام واقعی، نه stub) — five bullets: وصول پیام در گروه بحث، تطابق
   `linked_chat_id`، فوروارد خودکار پست، تحویل یک‌باره، سهمیهٔ خودِ پرسشگر.
2. پیش‌نیازها: ربات تستی جدا (توکن production را در Secrets نگذارید)، کانال تستی + گروهِ بحث،
   ادمین‌بودن ربات در هر دو، حساب دومِ **غیرسوپر** برای پرسش، گروهِ بی‌ربط (سناریوی `full`).
3. سه Secret: `BOT_TOKEN` · `SUPER_ADMIN_IDS` · `SECRETS_KEY` (مقدار پایدار و مخصوص همین تست؛
   بدون آن کلید مدل در DB کش‌شده قابل رمزگشایی نیست).
4. اجرا: Actions → QA Live Acceptance → Run workflow → `scenario=full`، `db_mode=fresh` برای
   اولین اجرا، سپس `reuse`؛ `duration_minutes` پیش‌فرض ۳۵۰.
5. ترتیب دقیق مراحل (کپیِ جدول §2.1 سند طراحی، فارسی).
6. شواهد: گزارش JSON و لاگ به‌صورت artifact؛ خروجی ۰/۱/۲ و معنای هرکدام.
7. عیب‌یابی: ربات ساکت ⇒ ادمین‌نبودن در گروه / Privacy Mode / کانال ثبت‌نشده / مدل غیرفعال /
   پرسشگر سوپر / کش منقضی (۷ روز).
8. نکتهٔ هزینه: هر اجرا ۲ تا ۴ فراخوانی مدل؛ مرحلهٔ اتمام سهمیه هیچ فراخوانی‌ای نمی‌کند.

- [ ] **Step 4: Update the state file**

- Field **Branch base commit** → `a925994b3e5bf77ee4800bba711af0f697db8de3` (records PR #85).
- §1 "Where we are": append "The Phase 4 **live acceptance harness** (this PR) is merged/pending; the
  live run itself is `PENDING` on the owner's secrets and Telegram setup."
- §4.5 (new, after §4.4) — `Phase 4 live acceptance — PENDING`: what the harness proves, the three
  secrets, the 350-minute budget, `db_mode=reuse` with a stable `SECRETS_KEY`, the ordered
  `full` stages, and "Phase 4 is declared live-verified only after a green run's report artifact is
  recorded here".
- §5 evidence rows: local results of this PR (tests, lint, mypy, `final_verification.py`) and the CI
  `test` run.
- §7 delivery: "Phase 4 live-acceptance harness — this PR (SHA added by the records PR after the
  live run)".
- §8: update **B-4** ("harness exists; still open until the live run"), add **B-7** (owner must add
  the 3 secrets, build the test bot/channel/group, and ask from a non-super account) and **D-6**
  (the Actions cache holds the test DB; it needs `SECRETS_KEY`; it is never uploaded as an artifact).
- §8 or §4.5: record the owner's Phase 5 design constraint verbatim: "no absolute limit and no
  runaway consumption — no wasteful token/API burn" (Q&A uses the cheapest suitable model, per-post
  cooldown, non-zero plan caps for paid plans).

- [ ] **Step 5: Update the README count**

Run: `./.venv/Scripts/python.exe -m pytest --collect-only -q` and read the `N tests collected` line.
Replace both the badge (`tests-N%20collected`) and the `# N تست` line with the new `N`.

- [ ] **Step 6: Run the guard tests and the docs guards**

Run:
```bash
./.venv/Scripts/python.exe -m pytest tests/test_phase4_live_acceptance.py \
  tests/test_repo_integrity.py tests/test_phase1_10_state_alignment.py -v
```
Expected: PASS — no broken backticked path, the state file names the first open phase, and the
README count matches the collection.

- [ ] **Step 7: Commit**

```bash
git add docs/QA_LIVE_ACCEPTANCE.md docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md README.md tests/test_phase4_live_acceptance.py
git commit -m "docs(qa-live): runbook, state record and README counts"
```

---

### Task 7: Whole-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: only the **19 known Windows-only failures** (§5.1 of the state file: 4 in
`tests/test_deploy_script.py`, 15 in `tests/test_session3_cicd_docker_deploy_docs.py`). Any other
failure is a regression and must be fixed before the PR.

- [ ] **Step 2: Static gates**

```bash
ruff check newsbot tests tools run.py
python -m pyflakes newsbot tools run.py tests
mypy newsbot
python tools/final_verification.py
```
Expected: `All checks passed!`, clean, `Success`, `17/17` (or the current max; record the number).

- [ ] **Step 3: Config self-check (offline, PowerShell)**

```powershell
$env:BOT_TOKEN="123456789:AAci_dummy_token_for_preflight_check_only_0000"
$env:SUPER_ADMIN_IDS="1"
$env:DB_FILE="$env:TEMP\qa-check.db"
./.venv/Scripts/python.exe run.py --check
```
Expected: `خودآزمایی موفق`.

- [ ] **Step 4: Record the evidence in the state file §5**

Add one row per command with its exact result; then amend the Task 6 commit (or add a
`docs:` commit) so the PR body and the state file agree.

- [ ] **Step 5: Branch, PR and merge (gate protocol)**

Branch in **bold** in every document (`phase4/live-acceptance`), never backticked:

```bash
git switch -c phase4/live-acceptance
git push -u origin phase4/live-acceptance
```
Open the PR with a Persian body (goal, scope, test evidence, the new secrets needed, and "no
production behaviour change except one id-only log line"). Then follow the repo's merge protocol:
rebase on `main` → CI `test` SUCCESS → `gate.py <PR> lift` → `gh pr merge --squash
--delete-branch` → `gate.py <PR> restore` → verify all 15 protection fields.

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| `qa.py` ignore log line | 1 |
| `qa-live.yml` (manual, inputs, 360 timeout, cache, artifacts, secrets in `env:` only) | 5 |
| `tools/qa_live_acceptance.py` (`--check`, `--supervise`, exit 0/1/2, report) | 2, 3, 4 |
| Ordered `full` sequence + per-row attribution + reused-DB scoping + super-asker rule | 3, 4 |
| `docs/QA_LIVE_ACCEPTANCE.md` runbook | 6 |
| Tests (checks, reuse, ambiguity, redaction, lifecycle, workflow, runbook) | 2–6 |
| "No secret / no question / no answer text anywhere" | 1 (log line), 2 (redaction test), 4 (report test), 6 (runbook) |
| 5h50m budget, DB reuse, `SECRETS_KEY` | 4, 5, 6 |

**Type consistency:** check names are used identically in `REQUIRED`, `evaluate` and the tests;
`Snapshot` field names (`qa_rows`, `usage`, `grants`, `channels`, `models`) are the ones the tests
seed and the checks read; `attribute` transition keys are `[chat_id, msg_id]` everywhere;
`secret_values_present` returns names, never values.

**Left to the implementer (deliberate):** the exact wording of Persian strings, the internal
helpers' bodies, the child-command construction details, and the `--log` flag name (the plan uses
`--log`, the workflow passes it).
