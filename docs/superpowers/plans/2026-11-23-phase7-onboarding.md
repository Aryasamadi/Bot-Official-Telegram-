# Phase 7 — Onboarding, home menu and message routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A brand-new user picks a language, sees a one-time welcome, taps start, and can add their first channel **without pressing a separate free-plan button** — with free-plan capacity granted once through the existing plan machinery, a persistent `onboarding_state`, a simpler home menu, and slash commands preserved.

**Architecture:** An ADD-only `users.onboarding_state` column carries the flow (`'' → welcome → adding_channel → done`). `cmd_start` renders the welcome and sets state; the `onb:start` callback reuses `free_claim` + `assign_plan` + `admin_limits` to grant the seeded free plan exactly once before routing to the existing `a:chadd` form, whose admin/mandatory/cap guards stay untouched. `go_home` sends only users in the active onboarding state to the welcome screen; the normal user home drops help/support/plans/referral buttons while their slash commands remain.

**Tech Stack:** Python 3.13, python-telegram-bot 22.8, SQLite/D1 additive migration, pytest.

**Spec:** `docs/superpowers/specs/2026-11-23-phase7-onboarding-closure-design.md` (which itself implements `PHASE0_AUDIT_REPORT.md:489-501`, AUD-006)

## Global Constraints

- Persian UI/report text; English technical identifiers. No secrets, chat text or chat ids in logs.
- Schema change is **ADD-only** and idempotent; `SCHEMA_VERSION` 12 → **13**; rollback = revert code (the extra column stays, unused).
- Reuse `admin_limits` / `assign_plan` / `free_claim`; **no parallel quota or role logic**.
- Existing `ch_add` guards remain: bot must be channel admin, user must be channel admin, mandatory-channel gate, per-plan channel cap.
- Slash commands `/help`, `/man`, `/ref`, `/create`, `/lang`, `/start` keep working; only their home buttons are removed.
- Reply-to-post → Q&A (`qa.on_group_reply`) and ordinary private message → home must keep working.
- Tests fail first. `SCHEMA_VERSION` guards in tests/docs must be updated in the same change as the bump.
- Phases 4–6 live acceptance stay deferred to the single Phase 10 run (D-7); no live-verified claim.
- Branch names in docs are written **bold**, never in backticks.

## Review Focus

- **A new user with no plan and no channel** must reach the add-channel form without clicking a plan button — pinned by `test_new_user_reaches_add_channel_without_a_plan_button` (Task 2).
- **A double tap / repeated `/start`** must not grant the free plan twice or replay the welcome — pinned by `test_start_and_onb_start_are_idempotent` (Task 2).
- **A paid user with an active plan** must keep their caps and must not receive the free plan — pinned by `test_paid_user_is_not_downgraded_by_onboarding` (Task 2).
- **A user who is not a channel admin** (or a bot without post rights) must still be refused by `ch_add` — pinned by `test_ch_add_security_guards_survive_onboarding` (Task 3).
- **Removing home buttons** must not break discovery via commands or Reply routing — pinned by `test_home_has_no_shortcut_buttons_but_commands_still_work` and `test_reply_still_routes_to_qa` (Task 3).
- **A downgraded build** must refuse a v13 database — pinned by `test_v13_db_is_refused_by_a_v12_build` (Task 1).

---

### Task 1: Schema v13 and the onboarding-state helpers

**Files:**
- Modify: `newsbot/__init__.py:13` (`SCHEMA_VERSION`)
- Modify: `newsbot/db.py` (`MIGRATIONS`, near line 182)
- Modify: `newsbot/core_users.py` (small helpers near `admin_limits`)
- Modify: `tests/test_phase4_schema.py`, `tests/test_phase10_db_quality.py`, `tests/test_phase12_config_docs.py`, `tests/test_phase13_tech_debt.py`
- Test: `tests/test_phase7_onboarding.py` (new)

**Interfaces:**
- Consumes: `db()` / `q()` / `write()` transaction helpers; `MIGRATIONS` pattern.
- Produces:
  - `SCHEMA_VERSION == 13` everywhere (code and the four guard tests).
  - `newsbot.core_users.get_onboarding_state(uid) -> str` — one of `''`, `'welcome'`, `'adding_channel'`, `'done'`.
  - `newsbot.core_users.set_onboarding_state(uid, state: str) -> None` — accepts only those four values (silently ignores unknown values).

- [ ] **Step 1: Write failing tests**

```python
def test_schema_is_thirteen_and_the_column_is_additive(isolated_db):
    import newsbot
    assert newsbot.SCHEMA_VERSION == 13
    row = q("SELECT onboarding_state FROM users LIMIT 1", one=True)
    assert row is not None and row["onboarding_state"] in (None, "")

def test_onboarding_state_roundtrip_and_validation(isolated_db):
    from newsbot.core_users import get_onboarding_state, set_onboarding_state
    ensure_user(4242, "u", "U")
    assert get_onboarding_state(4242) == ""
    set_onboarding_state(4242, "welcome")
    assert get_onboarding_state(4242) == "welcome"
    set_onboarding_state(4242, "bogus")
    assert get_onboarding_state(4242) == "welcome"

def test_v13_db_is_refused_by_a_v12_build(isolated_db, monkeypatch):
    # set meta.schema_version='13', monkeypatch newsbot.db.SCHEMA_VERSION=12,
    # call init_core() and assert SystemExit mentioning "newer than this build"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase7_onboarding.py -q -k schema_or_onboarding_state_or_v13`
Expected: FAIL — `SCHEMA_VERSION` is 12 / column missing / helpers absent.

- [ ] **Step 3: Implement**

Add `MIGRATIONS += [("users", "onboarding_state", "TEXT DEFAULT ''")]` beside the Phase 4 migration; bump `SCHEMA_VERSION = 13` in `newsbot/__init__.py`; add the two helpers in `core_users.py` using the module's existing single-row write pattern with an allow-list of states. Update the four schema-guard tests from 12 to 13 with messages naming this phase.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase7_onboarding.py tests/test_phase4_schema.py tests/test_phase10_db_quality.py tests/test_phase12_config_docs.py tests/test_phase13_tech_debt.py -q`
Expected: all passed.

- [ ] **Step 5: Commit**

`git commit -m "feat(phase7): additive onboarding_state column and v13 schema"`

---

### Task 2: Stateful `/start` and the free-plan auto-grant

**Files:**
- Modify: `newsbot/ui_commands.py` (`cmd_start`)
- Modify: `newsbot/ui_dispatch.py` (`onb:start` route in the user section)
- Modify: `newsbot/ui_views_user.py` (`view_onboarding_welcome`)
- Modify: `newsbot/ui_i18n.py` (two fa/en `TXT` keys for the welcome button and the granted notice)
- Test: `tests/test_phase7_onboarding.py`

**Interfaces:**
- Consumes: `get_onboarding_state` / `set_onboarding_state` (Task 1), `free_claim`, `assign_plan`, `admin_limits`, `list_plans`, `view_user_home`, `go_home`.
- Produces:
  - `view_onboarding_welcome(update, context)` — renders `gtext("welcome", lang, name=…)` plus one `onb:start` button.
  - callback `onb:start` — grants the seeded free plan once (only when no active plan, `free_used == 0`, no channels), sets state `'adding_channel'`, dispatches `a:chadd`.
  - `cmd_start` — no language ⇒ `view_lang`; state `''` ⇒ welcome + state `'welcome'`; state `'done'` or channel exists ⇒ `go_home`.

- [ ] **Step 1: Write failing tests**

```python
def test_new_user_reaches_add_channel_without_a_plan_button(fresh_user):
    # /start ⇒ picker ⇒ welcome ⇒ onb:start
    u = get_user(uid)
    assert u["free_used"] == 1 and u["role"] == "admin"
    assert get_onboarding_state(uid) == "adding_channel"
    assert any("ch_add_prompt" in t for t in ctx.renders)   # a:chadd was reached

def test_start_and_onb_start_are_idempotent(fresh_user):
    first = get_user(uid)["plan_expires"]
    # repeat /start then onb:start
    assert get_user(uid)["plan_expires"] == first
    assert get_user(uid)["free_used"] == 1
    assert get_onboarding_state(uid) in ("adding_channel", "done")

def test_paid_user_is_not_downgraded_by_onboarding(paid_user):
    # onb:start with an active paid plan
    assert get_user(uid)["free_used"] == 0
    assert get_user(uid)["plan_id"] == paid_plan_id
    assert get_onboarding_state(uid) == "adding_channel"

def test_ref_and_deeplink_start_args_are_unchanged(fresh_user, monkeypatch):
    # cmd_start with context.args = ["ref_900"] ⇒ record_referral called
    # cmd_start with ["r_key"] ⇒ send_deeplink called, no welcome view
```

- [ ] **Step 2: Run them to verify they fail** — expect no `onb:start` route and no free-plan grant.

- [ ] **Step 3: Implement**

In `cmd_start`, set state `'welcome'` before the language picker when no channel exists, and show `view_onboarding_welcome` for state `'welcome'`. In `ui_dispatch`, add `onb:start`: if a channel exists or state is `'done'` ⇒ `go_home`; if `admin_limits(uid)["active"]` ⇒ skip the grant; else pick the first active `is_free` plan via `list_plans()`, call `free_claim(uid)` and only then `assign_plan(uid, pid)`; on failure release the claim with `free_release`. Set state `'adding_channel'` and route to `a:chadd`. Add the two i18n keys with matching fa/en tuples.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase7_onboarding.py tests/test_phase4_qa_core.py -q`
Expected: all passed.

- [ ] **Step 5: Commit** — `git commit -m "feat(phase7): one-time welcome and free-plan auto-grant on start"`

---

### Task 3: Home menu, channel-add completion and routing guards

**Files:**
- Modify: `newsbot/ui_views_user.py` (`view_user_home`)
- Modify: `newsbot/ui_widgets.py` (`go_home`, near line 655)
- Modify: `newsbot/ui_inputs.py` (`ch_add` success branch, near line 387)
- Modify: `tests/test_ph05_ui_integrity.py`, `tests/test_user_features.py` (update the old button assertions)
- Test: `tests/test_phase7_onboarding.py`

**Interfaces:**
- Consumes: `set_onboarding_state(uid, 'done')`, `view_onboarding_welcome`, `qa.on_group_reply`, `on_message`.
- Produces: a normal home screen with no `u:plans` / `u:man` / `u:ref` / `u:help` buttons; commands remain.

- [ ] **Step 1: Write failing tests**

```python
def test_home_has_no_shortcut_buttons_but_commands_still_work(fresh_user):
    buttons = collect_callback_data(ctx)
    assert not {b for b in buttons if b.startswith(("u:man", "u:ref", "u:help", "u:plans"))}
    # /help, /man, /ref, /create each still render their own screen

def test_ch_add_security_guards_survive_onboarding(fresh_user, bot_double):
    bot_double.get_chat_administrators = raises_or_empty(...)
    # ch_add input ⇒ "ch_user_not_admin" notice, no channel row

def test_reply_still_routes_to_qa(chan, monkeypatch):
    # reuse Phase 4 fixtures: reply to a channel post in the linked group
    assert len(bot.sent) == 1 and bot.sent[0]["reply_to_message_id"] == 10

def test_first_channel_sets_done_and_welcome_is_not_repeated(fresh_user):
    # successful ch_add
    assert get_onboarding_state(uid) == "done"
    # then /start
    assert "onb:start" not in collect_callback_data(ctx)
```

- [ ] **Step 2: Run to verify they fail** — old home still emits buttons; state is never set to `done`.

- [ ] **Step 3: Implement**

Remove the four shortcut buttons from `view_user_home` (keep the mandatory-channel join/check row and the language row). In `go_home`, for non-admin/non-super users with no channels and state `'welcome'`, render `view_onboarding_welcome`; otherwise keep the current role-based routing. In `ui_inputs.py`'s `ch_add` success path, call `set_onboarding_state(uid, "done")` before dispatching to `a:ch:<cid>`. Update the two older tests to assert the new contract.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase7_onboarding.py tests/test_ui_routing.py tests/test_ph05_ui_integrity.py tests/test_user_features.py tests/test_phase2_politeness_and_qa.py tests/test_phase4_qa_core.py -q`
Expected: all passed.

- [ ] **Step 5: Commit** — `git commit -m "feat(phase7): simple home menu and onboarding completion"`

---

### Task 4: Full verification, repeat runs and delivery record

**Files:**
- Modify: `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`
- Modify: `tests/test_repo_integrity.py` (remove the proposed-path entry only after the new test file exists)

**Interfaces:**
- Consumes: all code from Tasks 1–3 and the real evidence collected below.
- Produces: state file with Phase 7 code-complete, no live claim, D-7 untouched.

- [ ] **Step 1: Repeat the new tests five times** — `pytest tests/test_phase7_onboarding.py -q -x` × 5, all green.
- [ ] **Step 2: Full verification** — `pytest -q`, `ruff check .`, `mypy newsbot`, `tools/final_verification.py`, `run.py --check` with dummy env; record exact numbers, known Windows-only failures, collection count; update both README numbers if collection changed.
- [ ] **Step 3: Update the state file** — roadmap row 7, §1 next action, §4.6 scope/decisions, §5 evidence, §7 delivery rows, D-7 unchanged.
- [ ] **Step 4: PR chain** — docs/plan first, schema+state code, flow code, home/routing code, then records; each CI SUCCESS → `gate.py lift` → squash merge → `gate.py restore` → 15/15 verification.

## Delivery map

- **PR 1 (docs):** spec + plan + `PROPOSED_PATHS` entry for `tests/test_phase7_onboarding.py`.
- **PR 2 (code):** Tasks 1–3, one commit each.
- **PR 3 (records):** Task 4.
