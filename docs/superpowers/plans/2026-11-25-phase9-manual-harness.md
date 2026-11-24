# Phase 9 — Manual GitHub Actions Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the single manual `qa-live` workflow honest about active time, secret inputs and the
real/simulated boundary, and give it a hold-to-budget burn-in mode for a ~340-minute run.

**Architecture:** Reuse `.github/workflows/qa-live.yml` and `tools/qa_live_acceptance.py` (no second
workflow). The supervisor captures a monotonic `ready_t` and stops from that point; a new `duration`
scenario holds to the configured budget while `core`/`full` keep early exit for D-7. The workflow
maps secret-backed inputs to the env names the code reads and validates them without echoing values.

**Tech Stack:** GitHub Actions (`workflow_dispatch`, SHA-pinned actions), Python 3.13, pytest with
injected fake clock, JSON report artifacts.

**Spec:** `docs/superpowers/specs/2026-11-25-phase9-manual-harness-design.md`

## Global Constraints

- One workflow only: extend `.github/workflows/qa-live.yml`; never add a second harness.
- `workflow_dispatch` is the **only** trigger; `permissions: contents: read`; all actions SHA-pinned;
  `concurrency.cancel-in-progress: false`; `timeout-minutes: 360`.
- `duration_minutes` default **340**; valid 5–340 in the workflow. The tool keeps 5–355 for direct use.
- `scenario` default stays **core**; add `duration` as a third choice.
- Secure inputs are repo/environment secrets, never typed dispatch inputs; values never appear in
  `run:` text, `if:` expressions, logs, report JSON, artifact names or commits.
- Map `TELEGRAM_BOT_TOKEN` → `BOT_TOKEN` and `SUPER_ADMIN_ID` → `SUPER_ADMIN_IDS`, with the old
  secret names as backward-compatible fallbacks.
- No bot product-code change, no schema change, no VPS/production DB, no provider keys, no real
  channel publication in this phase.
- **No live workflow dispatch** — that needs real credentials and 340 minutes; the artifact belongs
  to the Phase 10 release run (D-7). Verify by unit/static tests only.
- Windows local full suite has exactly **19 known failures**; any other failure is a regression.
- Persian prose in docs/tests is fine; identifiers, keys and field names stay English.

## Review Focus

1. A plural super-id secret containing **two ids** must validate and pass through unchanged — the repo
   already accepts comma/space/semicolon lists. (Task 3, workflow validation test)
2. A short super id such as `1` must not be treated as a leaked secret in QA logs, where ordinary chat
   ids are common. (Task 2, `test_short_super_id_is_not_a_false_positive`)
3. A bot that never prints READY must fail in bounded time even in `duration` mode. (Task 2,
   `test_duration_mode_still_fails_when_ready_never_appears`)
4. The report must contain no token, key, six-plus-digit super id or answer text. (Task 2,
   `test_report_carries_boundaries_without_any_secret_value`)
5. The test database must never be uploaded as an artifact. (Task 3, keeps and extends the existing
   `test_the_database_is_never_uploaded_as_an_artifact`)

---

### Task 1: Honest active-time accounting

**Files:**
- Modify: `tools/qa_live_acceptance.py:686-779` (`supervise`), `:557-567` (`SuperviseConfig`)
- Test: `tests/test_phase4_live_acceptance.py` (append)

**Interfaces:**
- Consumes: existing `SuperviseConfig`, `FakeTime`, `_cfg` and `_scripted` helpers.
- Produces: report fields `ready_at`, `stopped_at`, `startup_duration_s`, `active_duration_s`,
  `cleanup_duration_s`, `configured_duration_s`, `delta_vs_350_s`, `duration_basis`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phase4_live_acceptance.py`:

```python
def test_active_time_starts_at_ready_not_spawn(tmp_path, monkeypatch):
    _scripted(monkeypatch, [PASS])
    ft, proc = FakeTime(), FakeProc()
    reads = {"n": 0}

    def log(_path):
        reads["n"] += 1
        return "" if reads["n"] <= 3 else "در حال اجرا…"

    code, report = acc.supervise(
        _cfg(tmp_path, duration_s=300), spawn=lambda: proc,
        snapshot=lambda p: acc.EMPTY_SNAPSHOT, read_log=log,
        clock=ft.clock, sleep=ft.sleep, resolve=lambda s: (-100111, -555))
    assert code == 0
    assert report["duration_basis"] == "ready_to_shutdown"
    assert report["startup_duration_s"] == 6
    assert report["active_duration_s"] == 10
    assert report["cleanup_duration_s"] == 0


def test_timeout_counts_only_the_active_budget(tmp_path, monkeypatch):
    _scripted(monkeypatch, [WAIT])
    ft, proc = FakeTime(), FakeProc()
    code, report = acc.supervise(
        _cfg(tmp_path, duration_s=300), spawn=lambda: proc,
        snapshot=lambda p: acc.EMPTY_SNAPSHOT, read_log=lambda p: "در حال اجرا…",
        clock=ft.clock, sleep=ft.sleep, resolve=lambda s: (-100111, -555))
    assert code == 1 and proc.terminated
    assert report["active_duration_s"] == 300 and report["startup_duration_s"] == 0


def test_report_states_the_delta_from_350_minutes(tmp_path, monkeypatch):
    _scripted(monkeypatch, [WAIT])
    class BigStep:
        now = 0.0
        def clock(self): return self.now
        def sleep(self, _s): self.now += 600
    ft = BigStep()
    code, report = acc.supervise(
        _cfg(tmp_path, duration_s=340 * 60), spawn=lambda: FakeProc(),
        snapshot=lambda p: acc.EMPTY_SNAPSHOT, read_log=lambda p: "در حال اجرا…",
        clock=ft.clock, sleep=ft.sleep, resolve=lambda s: (-100111, -555))
    assert code == 1
    assert report["configured_duration_s"] == 340 * 60
    assert report["delta_vs_350_s"] == -600
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_live_acceptance.py -q`
Expected: FAIL — `KeyError: 'duration_basis'` / `startup_duration_s`.

- [ ] **Step 3: Implement the timing**

In `supervise`: keep `t0 = clock()` before spawn; on detecting the READY marker set
`ready_t = clock()`. Replace the timeout check with `clock() - ready_t >= cfg.duration_s`. In
`finally`, after `_stop(proc)` set `stop_t = clock()`, then before `write_report` set
`report_t = clock()`. Add the eight report fields above, with `delta_vs_350_s =
round(active - 350 * 60)` and all duration values rounded integers. Do not change exit codes,
preconditions, `blank_answers`, or the early-exit behaviour of `core`/`full`.

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_live_acceptance.py -q`
Expected: PASS (existing tests plus the three new ones).

- [ ] **Step 5: Commit**

```bash
git add tools/qa_live_acceptance.py tests/test_phase4_live_acceptance.py
git commit -m "feat(phase9): count active time from READY, not from spawn"
```

---

### Task 2: Burn-in mode, boundary table and leak-proof report

**Files:**
- Modify: `tools/qa_live_acceptance.py:686-779`, `:784-823` (`main`)
- Test: `tests/test_phase4_live_acceptance.py` (append)

**Interfaces:**
- Consumes: Task 1's timing fields and `SuperviseConfig.env`.
- Produces: `scenario_mode` (`"hold_to_budget"` / `"early_exit"`), `provider_credentials_present`,
  `boundaries` (list of `{"surface", "status", "note"}`), and a pure helper
  `supervised_secret_values(env) -> dict[str, str]` that adds only six-plus-digit super ids.

- [ ] **Step 1: Write the failing tests**

```python
def test_duration_scenario_holds_to_budget_even_when_all_checks_pass(tmp_path, monkeypatch):
    _scripted(monkeypatch, [PASS])
    ft, proc = FakeTime(), FakeProc()
    code, report = acc.supervise(
        _cfg(tmp_path, duration_s=300, scenario="duration"), spawn=lambda: proc,
        snapshot=lambda p: acc.EMPTY_SNAPSHOT, read_log=lambda p: "در حال اجرا…",
        clock=ft.clock, sleep=ft.sleep, resolve=lambda s: (-100111, -555))
    assert code == 0 and report["scenario_mode"] == "hold_to_budget"
    assert report["active_duration_s"] == 300


def test_core_scenario_still_exits_early(tmp_path, monkeypatch):
    _scripted(monkeypatch, [PASS])
    ft, proc = FakeTime(), FakeProc()
    code, report = acc.supervise(
        _cfg(tmp_path, duration_s=300), spawn=lambda: proc,
        snapshot=lambda p: acc.EMPTY_SNAPSHOT, read_log=lambda p: "در حال اجرا…",
        clock=ft.clock, sleep=ft.sleep, resolve=lambda s: (-100111, -555))
    assert code == 0 and report["scenario_mode"] == "early_exit"
    assert report["active_duration_s"] == 10


def test_duration_mode_still_fails_when_ready_never_appears(tmp_path):
    ft, proc = FakeTime(), FakeProc()
    code, report = acc.supervise(
        _cfg(tmp_path, duration_s=300, scenario="duration"), spawn=lambda: proc,
        snapshot=lambda p: acc.EMPTY_SNAPSHOT, read_log=lambda p: "",
        clock=ft.clock, sleep=ft.sleep, resolve=lambda s: (-100111, -555))
    assert code == 2 and "BOT_NOT_READY" in report["problems"] and proc.terminated


def test_report_carries_boundaries_without_any_secret_value(tmp_path, monkeypatch):
    _scripted(monkeypatch, [PASS])
    cfg = _cfg(tmp_path,
               secret_values=acc.supervised_secret_values(
                   {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "987654321"}),
               env={"PROVIDER_CREDENTIALS_PRESENT": "0"})
    _, report = acc.supervise(
        cfg, spawn=lambda: FakeProc(), snapshot=lambda p: acc.EMPTY_SNAPSHOT,
        read_log=lambda p: "در حال اجرا…", clock=FakeTime().clock,
        sleep=lambda s: None, resolve=lambda s: (-100111, -555))
    surfaces = {row["surface"] for row in report["boundaries"]}
    assert surfaces == {"telegram_bot", "temporary_db", "provider_ai",
                        "real_channel_publication", "runner_hard_cap"}
    assert report["provider_credentials_present"] is False
    body = json.dumps(report, ensure_ascii=False)
    assert TOKEN not in body and "987654321" not in body and "TEXT_OF_ANSWER" not in body


def test_short_super_id_is_not_a_false_positive(tmp_path, monkeypatch):
    _scripted(monkeypatch, [PASS])
    short = acc.supervised_secret_values({"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1"})
    long = acc.supervised_secret_values({"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "987654321"})
    assert all(v != "1" for v in short.values()) and "987654321" in long.values()
    cfg = _cfg(tmp_path, secret_values=short)
    _, report = acc.supervise(
        cfg, spawn=lambda: FakeProc(), snapshot=lambda p: acc.EMPTY_SNAPSHOT,
        read_log=lambda p: "در حال اجرا… chat=1 msg=44", clock=FakeTime().clock,
        sleep=lambda s: None, resolve=lambda s: (-100111, -555))
    leak = next(c for c in report["checks"] if c["name"] == "no_secret_in_log")
    assert leak["status"] == "OBSERVED"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_live_acceptance.py -q`
Expected: FAIL — `KeyError: 'scenario_mode'` / `boundaries` / `provider_credentials_present`.

- [ ] **Step 3: Implement burn-in, boundaries and leak check**

- `main()` adds `--scenario` choice `duration` and passes
  `provider_credentials_present` from `PROVIDER_CREDENTIALS_PRESENT` (truthy only for `1`/`true`/`yes`).
- In `supervise`, when `cfg.scenario == "duration"`, all-checks-observed must **not** break the loop;
  record a boolean `observed` and finish with code 0 at budget if all required checks were observed,
  otherwise with code 1. `core`/`full` keep `early_exit`.
- Build `boundaries` with exactly the five surfaces from the test, `status` values
  `real` / `real` / (`real` if flag else `simulated`) / `not_performed` / `real`, and the note strings
  from the design. Add `scenario_mode` and `provider_credentials_present` to the report.
- Add `supervised_secret_values(env)` next to `main()`: start with `BOT_TOKEN` / `SECRETS_KEY`
  (names unchanged), then add each super id from `admin_ids(env.get("SUPER_ADMIN_IDS"))` only when it
  has **six or more digits**. `main()` uses this helper. Extend `redactions` to `["BOT_TOKEN",
  "TELEGRAM_BOT_TOKEN", "SUPER_ADMIN_ID", "SUPER_ADMIN_IDS", "SECRETS_KEY"]`. Leave the value-based
  leak logic and `evaluate` unchanged.

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_live_acceptance.py -q`
Expected: PASS.

- [ ] **Step 5: Update the existing exact-redaction assertion**

In `test_the_report_never_contains_a_secret_or_an_answer`, replace the exact `redactions` list with
the five-name list above (the new names are documentation-only). Re-run the same command: PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/qa_live_acceptance.py tests/test_phase4_live_acceptance.py
git commit -m "feat(phase9): hold-to-budget scenario, honest boundaries and id leak check"
```

---

### Task 3: Workflow inputs, validation and runbook

**Files:**
- Modify: `.github/workflows/qa-live.yml`
- Test: `tests/test_phase4_live_acceptance.py` (update workflow guards, append new ones)
- Modify: `docs/QA_LIVE_ACCEPTANCE.md`

**Interfaces:**
- Consumes: Task 2's `duration` scenario and report fields.
- Produces: the dispatch contract (secrets → env), the boundary documentation and the honest
  340-vs-350 runbook section.

- [ ] **Step 1: Write the failing tests**

Update existing tests in `tests/test_phase4_live_acceptance.py`:

```python
# test_the_workflow_has_the_required_settings
assert 'default: "340"' in src          # was "350"
assert "timeout-minutes: 360" in src    # unchanged
assert "name: Validate secure inputs" in src
assert "TELEGRAM_BOT_TOKEN" in src and "SUPER_ADMIN_ID" in src

# test_the_run_step_receives_the_three_secrets
for needle in ("BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN || secrets.BOT_TOKEN }}",
               "SUPER_ADMIN_IDS: ${{ secrets.SUPER_ADMIN_ID || secrets.SUPER_ADMIN_IDS }}",
               "SECRETS_KEY: ${{ secrets.SECRETS_KEY }}"):
    assert needle in block
```

Append:

```python
def test_the_workflow_offers_a_duration_burn_in_scenario():
    src = _wf()
    block = src.split("scenario:", 1)[1].split("db_mode:", 1)[0]
    assert "default: core" in block
    assert "options: [core, full, duration]" in block


def test_the_workflow_validates_a_super_id_list_without_echoing_values():
    src = _wf()
    block = src.split("name: Validate secure inputs", 1)[1].split("name:", 1)[0]
    assert "SUPER_ID_INVALID" in block and "TOKEN_MISSING" in block
    assert "[0-9,;[:space:]]" in block
    assert 'echo "$SUPER' not in block and 'echo "$TOKEN' not in block


def test_the_workflow_does_not_put_secrets_in_conditions():
    for line in _wf().splitlines():
        if line.strip().startswith("if:"):
            assert "secrets." not in line
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_live_acceptance.py -q`
Expected: FAIL — the old default `"350"` and old secret names no longer match.

- [ ] **Step 3: Harden the workflow**

- `duration_minutes` description/default → 340; `scenario` options `[core, full, duration]`, default `core`.
- Add a `name: Validate secure inputs` step before install: reject duration outside 5–340; require a
  non-empty token; require the resolved super id to match `[0-9,;[:space:]]+` with at least one
  digit; print only `TOKEN_MISSING`, `SUPER_ID_INVALID`, `BAD_DURATION` or `INPUTS_OK`. Never echo a
  value. Use `env:` for the values.
- Map the two secure inputs in the existing run step with the fallback expressions from the test.
- Keep SHA pins, `if: always()` uploads, cache keys, `retention-days: 14` and the DB-out-of-artifact
  rule unchanged.

- [ ] **Step 4: Write the runbook section**

Append to `docs/QA_LIVE_ACCEPTANCE.md` a Phase 9 section: dispatch inputs (340 default, `duration`
scenario burns to budget while `core`/`full` exit early), the READY-based timing fields, the six
timing numbers in `report.json`, the 340-vs-350 statement (10 minutes, explicit), the five-row
real/simulated boundary table, that no provider keys/VPS/production DB are used, and rollback =
disable/delete the workflow. Add a test asserting the runbook mentions `340`, `350`,
`duration`, `active_duration_s` and the five surface names.

- [ ] **Step 5: Run to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_live_acceptance.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/qa-live.yml tests/test_phase4_live_acceptance.py docs/QA_LIVE_ACCEPTANCE.md
git commit -m "feat(phase9): secure inputs, burn-in dispatch and honest runbook"
```

---

### Task 4: Verification, counts and delivery

**Files:**
- Modify: `README.md` (badge + Persian count)
- Modify: `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`
- Test: whole suite + tooling

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: the recorded evidence that Phase 9 is code-complete without claiming a live run.

- [ ] **Step 1: Repeat-run the focused suite**

Run five times: `.\.venv\Scripts\python.exe -m pytest tests/test_phase4_live_acceptance.py -q -x`
Expected: PASS every run; no flake.

- [ ] **Step 2: Align counts**

Measure with `pytest -q --collect-only -p no:cacheprovider`, update **both** README numbers
(`tests-N%20collected` and `# N تست`), and run `tests/test_phase1_10_state_alignment.py`.

- [ ] **Step 3: Full local suite**

Run `.\.venv\Scripts\python.exe -m pytest -q --tb=line -rf` (~11 min).
Expected: exactly the 19 known Windows-only failures; anything else blocks the PR.

- [ ] **Step 4: Gates**

Run `ruff check .`, `mypy newsbot`, `tools/final_verification.py`, and `run.py --check` with dummy
`BOT_TOKEN` / `SUPER_ADMIN_IDS` / `DB_FILE`; record the real output (clean / 52 files / 17/17 / exit 0).

- [ ] **Step 5: State file**

- §2 row 9 → **`IN_PROGRESS`** with the design/plan and code PR numbers.
- New §4.8 with scope, D9-1…D9-10, the `duration` vs `core` semantics and the explicit 340-vs-350 budget.
- §5 evidence rows (five repeats, collection, full suite, tooling, CI, gate).
- §7 delivery rows for both PRs.
- §8: keep D-7 intact; note that the harness's live dispatch is the owner's action inside Phase 10,
  and record any real subagent/usage-limit event if it occurs.
- "Exact next action" → Phase 10, on explicit owner instruction only.

- [ ] **Step 6: Commit records**

```bash
git add README.md docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md
git commit -m "docs(phase9): record delivery evidence and close Phase 9 code"
```

- [ ] **Step 7: PR chain (gate protocol, no shortcuts)**

Three PRs in order — docs/plan (spec, plan, `PROPOSED_PATHS` entry only if a new test file is needed),
code (Tasks 1–3), records (Step 6). For each: confirm `mergeable`, CI `test` SUCCESS, `gate.py <PR>
lift`, `gh pr merge --squash --delete-branch`, `gate.py <PR> restore`, `verify_protection.py` 15/15
IDENTICAL. Record PR numbers, SHAs, CI durations and protection results in §5.
