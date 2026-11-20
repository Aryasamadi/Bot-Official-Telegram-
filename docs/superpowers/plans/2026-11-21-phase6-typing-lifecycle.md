# Phase 6 — Typing lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show native Telegram `typing` for every user-visible wait after a 1-second delay, refresh it every 4 seconds, and guarantee that the heartbeat is cancelled and reaped on success, exception, cancellation, and bot shutdown with no orphan tasks.

**Architecture:** A single reusable `typing_while(bot, chat_id, kind)` async context manager owns a per-chat heartbeat task registered in a module-level dict with a refcount. The context manager's `finally` (and `post_shutdown`'s explicit `stop_all_typing()`) cancels and awaits that task using the cancellation-safe pattern from `publish.py`; chat-action failures are swallowed, with a 30-second per-chat mute after three consecutive failures. The same context manager is installed in `run_user_operation` and in `on_group_reply`.

**Tech Stack:** Python 3.13, python-telegram-bot 22.8 (`ChatAction.TYPING`), asyncio, pytest.

**Spec:** `docs/superpowers/specs/2026-11-21-phase6-typing-lifecycle-design.md`

## Global Constraints

- Reports and UI text in Persian; technical identifiers in English. No new user-visible text or i18n keys.
- No secrets, chat text, or provider keys in logs; only ids and the stop reason.
- Never let a `send_chat_action` failure break the user's answer.
- Config only via env: `TYPING_ENABLED=1`, `TYPING_START_DELAY_S=1.0` (0–30), `TYPING_REFRESH_S=4.0` (1–5). Only the two numeric values enter `app.py`'s preflight range list.
- Start after 1 s, refresh every 4 s, mute for 30 s after 3 consecutive failures.
- Heartbeat task name must start with `typing:` and be included in `post_shutdown`'s cancellation list.
- `SCHEMA_VERSION` stays 12; no DB table or column changes. Tests fail first.
- Phase 4 live acceptance and Phase 5's quota scenario remain deferred to the single Phase 10 run (D-7); no live-verified claim in this phase.

## Review Focus

- **A fast command/callback (< 1 s)** must generate zero network calls — pinned by `test_no_action_for_an_operation_shorter_than_the_delay` in Task 2.
- **The work raising or being cancelled** must still leave no heartbeat and no registry entry — pinned by `test_exception_stops_and_reaps_the_heartbeat` and `test_cancellation_stops_and_reaps_the_heartbeat` in Task 2.
- **A failing `send_chat_action`** must not break the work and must mute that chat for 30 s — pinned by `test_three_failures_mute_the_chat_for_thirty_seconds` in Task 2.
- **Bot shutdown** must cancel `typing:` tasks, clear `_active`, and leave unrelated tasks intact — pinned by `test_shutdown_clears_typing_registry_and_keeps_unrelated_tasks` in Task 3.
- **Two concurrent operations in one chat** must share one heartbeat — pinned by `test_two_entrants_in_one_chat_share_one_heartbeat` in Task 2.

---

### Task 1: Config, env documentation and integrity allowlist

**Files:**
- Modify: `newsbot/config.py` (beside the `QA_*` block, near line 233)
- Modify: `newsbot/app.py` (preflight numeric ranges, near line 218)
- Modify: `.env.example` (Q&A block, near line 101)
- Modify: `tests/test_repo_integrity.py` (`PROPOSED_PATHS`, line 27)
- Test: `tests/test_phase6_typing_lifecycle.py`

**Interfaces:**
- Consumes: `_env_float(name, default, lo, hi)`.
- Produces: `config.TYPING_ENABLED`, `config.TYPING_START_DELAY_S`, `config.TYPING_REFRESH_S`.

- [ ] **Step 1: Write the failing tests**

```python
def test_typing_config_defaults_are_the_designed_values():
    assert config.TYPING_ENABLED is True
    assert (config.TYPING_START_DELAY_S, config.TYPING_REFRESH_S) == (1.0, 4.0)

def test_env_example_and_preflight_carry_the_three_names():
    env = (ROOT / ".env.example").read_text(encoding="utf-8", errors="replace")
    app = (ROOT / "newsbot" / "app.py").read_text(encoding="utf-8", errors="replace")
    assert all(n in env for n in ("TYPING_ENABLED", "TYPING_START_DELAY_S", "TYPING_REFRESH_S"))
    assert "TYPING_START_DELAY_S" in app and "TYPING_REFRESH_S" in app
```

`ROOT = Path(__file__).resolve().parents[1]`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase6_typing_lifecycle.py -q`
Expected: FAIL with `AttributeError: module 'newsbot.config' has no attribute 'TYPING_ENABLED'`.

- [ ] **Step 3: Implement the config values**

In `newsbot/config.py`:

```python
TYPING_ENABLED = os.getenv("TYPING_ENABLED", "1").strip() == "1"
TYPING_START_DELAY_S = _env_float("TYPING_START_DELAY_S", 1.0, 0.0, 30.0)
TYPING_REFRESH_S = _env_float("TYPING_REFRESH_S", 4.0, 1.0, 5.0)
```

Add only the two numeric tuples to `app.py`'s preflight range list: `("TYPING_START_DELAY_S", config.TYPING_START_DELAY_S, 0, 30)` and `("TYPING_REFRESH_S", config.TYPING_REFRESH_S, 1, 5)`. Add the three variables with their defaults and a short Persian comment to `.env.example`. Add `"newsbot/chat_action.py"` and `"tests/test_phase6_typing_lifecycle.py"` to `PROPOSED_PATHS` so the docs PR can cite them before they exist.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase6_typing_lifecycle.py tests/test_repo_integrity.py -q`
Expected: 11+ passed (the guard now tolerates the two proposed paths).

- [ ] **Step 5: Commit**

```bash
git add newsbot/config.py newsbot/app.py .env.example tests/test_repo_integrity.py tests/test_phase6_typing_lifecycle.py
git commit -m "feat(phase6): typing config, env defaults and preflight ranges"
```

---

### Task 2: The `typing_while` context manager

**Files:**
- Create: `newsbot/chat_action.py`
- Test: `tests/test_phase6_typing_lifecycle.py`

**Interfaces:**
- Consumes: `config.TYPING_*`, `telegram.constants.ChatAction.TYPING`.
- Produces:
  - `typing_while(bot, chat_id: int, kind: str = "op")` — async context manager; no return value.
  - `mark_shutdown() -> None` — sets the flag used for `reason=shutdown` logging.
  - `stop_all_typing()` — async; cancels and reaps every heartbeat, clears `_active`.
  - `_active: dict[int, _Heartbeat]` and `_muted_until: dict[int, float]` — module-level test hooks.

- [ ] **Step 1: Write the failing core tests**

Use this fake bot and these monkeypatches in every test:

```python
class _Bot:
    def __init__(self, fail=0):
        self.calls, self.fail = [], fail

    async def send_chat_action(self, chat_id, action):
        self.calls.append((chat_id, action))
        if self.fail:
            self.fail -= 1
            raise RuntimeError("429")

@pytest.fixture(autouse=True)
def fast_clock(monkeypatch):
    from newsbot import config
    monkeypatch.setattr(config, "TYPING_START_DELAY_S", 0.01)
    monkeypatch.setattr(config, "TYPING_REFRESH_S", 0.01)
    monkeypatch.setattr(config, "TYPING_ENABLED", True)
    yield
```

Core assertions:

```python
async def _sleep_long(seconds=0.08): await asyncio.sleep(seconds)

async def test_disabled_emits_nothing(monkeypatch):
    from newsbot import config
    from newsbot.chat_action import typing_while
    monkeypatch.setattr(config, "TYPING_ENABLED", False)
    bot = _Bot()
    async with typing_while(bot, -555):
        await _sleep_long()
    assert bot.calls == []

async def test_no_action_for_an_operation_shorter_than_the_delay():
    bot = _Bot()
    async with typing_while(bot, -555): await asyncio.sleep(0)
    assert bot.calls == []

async def test_a_long_wait_sends_typing_repeatedly():
    bot = _Bot()
    async with typing_while(bot, -555, kind="qa"): await _sleep_long()
    assert len(bot.calls) >= 2
    assert all(cid == -555 and action == ChatAction.TYPING for cid, action in bot.calls)

async def test_normal_exit_reaps_the_heartbeat():
    from newsbot import chat_action as ca
    bot = _Bot()
    async with typing_while(bot, -555): await _sleep_long()
    assert -555 not in ca._active
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith("typing:")]

async def test_exception_stops_and_reaps_the_heartbeat():
    from newsbot import chat_action as ca
    bot = _Bot()
    with pytest.raises(ValueError):
        async with typing_while(bot, -555):
            await _sleep_long()
            raise ValueError("work failed")
    assert -555 not in ca._active
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith("typing:")]

async def test_cancellation_stops_and_reaps_the_heartbeat():
    from newsbot import chat_action as ca
    bot = _Bot()
    async def work():
        async with typing_while(bot, -555): await asyncio.sleep(30)
    t = asyncio.create_task(work())
    await asyncio.sleep(0.03); t.cancel()
    with pytest.raises(asyncio.CancelledError): await t
    assert -555 not in ca._active

async def test_three_failures_mute_the_chat_for_thirty_seconds():
    from newsbot import chat_action as ca
    bot = _Bot(fail=10)
    async with typing_while(bot, -555): await _sleep_long()
    assert len(bot.calls) <= 4
    assert -555 not in ca._active
    assert ca._muted_until[-555] > time.monotonic()
    before = len(bot.calls)
    async with typing_while(bot, -555): await _sleep_long()
    assert len(bot.calls) == before      # cooldown: no fresh heartbeat

async def test_two_entrants_in_one_chat_share_one_heartbeat():
    from newsbot import chat_action as ca
    bot = _Bot()
    async def entrant():
        async with typing_while(bot, -555): await _sleep_long()
    await asyncio.gather(entrant(), entrant())
    assert len(bot.calls) >= 2 and len({cid for cid, _ in bot.calls}) == 1
    assert -555 not in ca._active

async def test_two_chats_have_independent_heartbeats():
    bot = _Bot()
    async def one(cid):
        async with typing_while(bot, cid): await _sleep_long()
    await asyncio.gather(one(-1), one(-2))
    assert {cid for cid, _ in bot.calls} == {-1, -2}
```

There is no `asyncio_mode` in `pyproject.toml`, so every `async def` test above is called from a thin sync test via `asyncio.run(...)` (the same convention as `tests/test_phase4_qa_core.py`), e.g. `def test_a_long_wait_sends_typing_repeatedly(): asyncio.run(_long_wait())`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase6_typing_lifecycle.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'newsbot.chat_action'`.

- [ ] **Step 3: Implement `newsbot/chat_action.py`**

Public API exactly as in Interfaces. Implementation rules:
- `typing_while` checks `bot is None` or `not TYPING_ENABLED` → yields immediately.
- If `chat_id in _muted_until` and `_muted_until[chat_id] > time.monotonic()` → yields immediately.
- If `chat_id in _active` → increment `count` and yield; on exit decrement, and only the last exit stops.
- Otherwise create `_Heartbeat`, store in `_active`, start task named `typing:<chat_id>:<kind>`.
- Heartbeat coroutine sleeps the delay, then loops `send_chat_action` + refresh sleep; on exception increment failures, debug-log, and after 3 set `_muted_until[chat_id] = time.monotonic()+30` and return; never re-raise.
- Stop protocol: `task.cancel()`; loop `await asyncio.shield(task)` swallowing `CancelledError` until `task.done()`; pop `_active` only if the record is still ours. Log the stop reason: `shutdown` if `mark_shutdown()` was called, else `exception`/`cancel`/`normal` passed from the context manager.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase6_typing_lifecycle.py -q`
Expected: all task tests passed. Repeat five times with `-x` to rule out flakes.

- [ ] **Step 5: Commit**

```bash
git add newsbot/chat_action.py tests/test_phase6_typing_lifecycle.py
git commit -m "feat(phase6): typing_while context manager with cancellation-safe cleanup"
```

---

### Task 3: Shutdown integration

**Files:**
- Modify: `newsbot/app.py` (`post_shutdown`, lines 128–138)
- Test: `tests/test_phase6_typing_lifecycle.py`

**Interfaces:**
- Consumes: `chat_action.mark_shutdown()`, `chat_action.stop_all_typing()` (Task 2).
- Produces: shutdown also cancels `typing:` tasks and clears the registry.

- [ ] **Step 1: Write the failing tests**

```python
def test_shutdown_clears_typing_registry_and_keeps_unrelated_tasks(monkeypatch):
    import newsbot.app as APP
    from newsbot import chat_action as ca
    monkeypatch.setattr(APP, "_SHUTDOWN_CANCEL_TIMEOUT", 0.3)
    monkeypatch.setattr(APP, "_SHUTDOWN_GRACE", 0.2)

    async def scenario():
        async def long_typing():
            bot = _Bot()
            async with ca.typing_while(bot, -777, kind="qa"):
                await asyncio.sleep(30)
        async def keeper():
            try: await asyncio.sleep(30)
            except asyncio.CancelledError: raise
        t1 = asyncio.create_task(long_typing(), name="typing:-777:qa")
        t2 = asyncio.create_task(keeper(), name="other:keep")
        await asyncio.sleep(0.05)
        await APP.post_shutdown(None)
        return t1, t2

    t1, t2 = asyncio.run(scenario())
    assert t1.done()
    assert -777 not in ca._active
    assert not t2.done()
    t2.cancel()
    try: await t2
    except asyncio.CancelledError: pass
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase6_typing_lifecycle.py -q -k shutdown`
Expected: FAIL — `typing:` is not in the cancellation tuple and `_active` is not cleared.

- [ ] **Step 3: Wire shutdown**

In `newsbot/app.py:130`, change the name tuple to `("publication", "user", "thinking", "typing")`. Immediately before that cancellation, call `chat_action.mark_shutdown()` and `await chat_action.stop_all_typing()`. Import `chat_action` at module top. Keep the existing two cancellation rounds and timeouts untouched.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase6_typing_lifecycle.py tests/test_phase8_concurrency.py -q`
Expected: all passed; the existing shutdown tests still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add newsbot/app.py tests/test_phase6_typing_lifecycle.py
git commit -m "feat(phase6): include typing heartbeats in graceful shutdown"
```

---

### Task 4: Install typing in user operations and group Q&A

**Files:**
- Modify: `newsbot/ui_widgets.py` (`run_user_operation`, lines 420–432)
- Modify: `newsbot/qa.py` (`on_group_reply`, lines 200–210)
- Test: `tests/test_phase6_typing_lifecycle.py`

**Interfaces:**
- Consumes: `typing_while` (Task 2) and the existing `_Bot`/`_group_msg` helpers from `tests/test_phase4_qa_core.py`.

- [ ] **Step 1: Write the failing integration tests**

```python
def test_run_user_operation_sends_typing_when_bot_is_present(monkeypatch):
    # reuse a fake update/context with a recordable bot; long work sleeps 0.08s
    ...
    assert any(a == ChatAction.TYPING for _, a in bot.calls)

def test_run_user_operation_without_a_bot_is_safe(...):
    ...  # context has no .bot → no exception, no typing

def test_group_qa_uses_typing_during_ai_wait(chan, monkeypatch):
    ...  # slow fake ai_chat, registered channel, valid reply
    assert any(a == ChatAction.TYPING for _, a in bot.calls)

def test_a_quota_refusal_sends_no_chat_action(chan, monkeypatch):
    monkeypatch.setattr(qa, "claim", lambda *a, **k: "cooldown")
    ...
    assert bot.calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase6_typing_lifecycle.py -q -k "run_user or qa or refusal"`
Expected: FAIL — no `send_chat_action` calls are made.

- [ ] **Step 3: Install the context managers**

In `ui_widgets.py`, wrap only `result = await work()` inside `run()` with
`async with chat_action.typing_while(getattr(context, "bot", None), op.chat_id, kind=kind):`.
In `qa.py`, wrap the AI-and-delivery block starting at `system, model_user = build_prompt(...)`
through `qa_finish` with `async with chat_action.typing_while(context.bot, chat_id, kind="qa"):`.
Use module-level imports; `chat_action` imports only config/logutils/telegram, so there is no cycle.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase6_typing_lifecycle.py tests/test_phase4_qa_core.py tests/test_phase5_qa_quotas.py tests/test_phase5_qa_quota_ui.py -q`
Expected: all passed; Phase 4/5 behaviour unchanged.

- [ ] **Step 5: Commit**

```bash
git add newsbot/ui_widgets.py newsbot/qa.py tests/test_phase6_typing_lifecycle.py
git commit -m "feat(phase6): native typing for user operations and group Q&A"
```

---

### Task 5: Full verification, repeat runs and delivery record

**Files:**
- Modify: `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`
- Modify: `tests/test_repo_integrity.py` (remove the two proposed paths now that files exist)

**Interfaces:**
- Consumes: all merged code from Tasks 1–4 and the real evidence collected below.
- Produces: state file with Phase 6 code complete and no live-verified claim; D-7 untouched.

- [ ] **Step 1: Repeat the new tests five times**

```bash
for i in 1 2 3 4 5; do python -m pytest tests/test_phase6_typing_lifecycle.py -q -x || exit 1; done
```

Expected: five consecutive green runs; record the five pass lines in the state file.

- [ ] **Step 2: Run the full verification suite**

```bash
python -m pytest -q
python -m ruff check .
python -m mypy newsbot
python tools/final_verification.py
python run.py --check    # with the CI dummy env
```

Expected: no new failures beyond the 19 known Windows-only ones; copy exact numbers, exit codes and CI run id into the state file. Update the README test badge and Persian count if collection changed.

- [ ] **Step 3: Update the state file**

Record: design/plan PR SHA, code PR SHA, test count, five repeat runs, CI conclusion, ruff/mypy/final_verification/`--check`, and 15/15 protection verification. Keep D-7 and Phase 4's deferral intact.

- [ ] **Step 4: Commit and follow the gate protocol for every PR**

Plan PR (spec + plan + integrity allowlist) first, then code PR, then records PR. For each: CI `test` SUCCESS → `gate.py <PR> lift` → `gh pr merge --squash --delete-branch` → `gate.py <PR> restore` → `verify_protection.py` reports `IDENTICAL on all 15 fields`.

## Delivery map

- **PR 1 (docs):** spec + plan + integrity allowlist.
- **PR 2 (code):** Tasks 1–4, one commit each.
- **PR 3 (records):** Task 5.
