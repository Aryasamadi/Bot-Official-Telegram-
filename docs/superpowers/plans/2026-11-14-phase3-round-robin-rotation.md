# Phase 3 — Round-robin model rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every article request takes the *next* model in a stable admin-defined order, the position is persisted with a compare-and-set so restarts and concurrent workers cannot lose or double-advance it, and every request leaves one auditable row saying which model was chosen and why.

**Architecture:** A new `ai_rotation` row holds the cursor and a `version` counter per scope. `generate()` advances the cursor exactly once per request through a CAS `UPDATE … WHERE version = ?` (retry on a lost race, fail-open if the DB is contended so publishing never blocks). Selection uses the cursor only to *start*; the existing quarantine/backoff/failover path then runs unchanged over the remaining pool, and **does not** advance the cursor again (ADR-4: round-robin is not failover). A new `ai_rotation_log` table records one row per request.

**Tech Stack:** Python 3.13, `sqlite3` / Cloudflare D1 (the existing `newsbot.db` abstraction), `pytest`, `ruff`.

**Spec:** `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md` §2 (phase 3), §3 (ADR-3, ADR-4), §4.3 (to be written by this phase).

## Global Constraints

- Additive migration only: `SCHEMA_VERSION` 10 → **11**; new tables plus `ALTER TABLE … ADD COLUMN` via `MIGRATIONS`. No drops, no rewrites.
- Must work on **both** SQLite and D1: no `PRAGMA` for writes, no `RETURNING`, no `FOR UPDATE`.
- `ruff check newsbot tests tools run.py` must stay clean; `python -m pyflakes` clean.
- No secret, token or key in code, tests, docs or log lines — names and presence only.
- Persian UI/doc text, English identifiers, matching the surrounding code.
- Tests must not touch the network: the rotation engine is pure DB + arithmetic.

## Review Focus

Five input classes this spec implies but a naive implementation gets wrong — each gets a test in the task named beside it:

1. **Pool shrinks between requests** (a model is disabled or enters backoff): `len(pool)` changes, so a stored absolute index would skip or repeat models. → `pick_rotation` must take the index modulo the *current* pool size; tested in Task 2.
2. **Empty pool / all models unready**: no model may be invented and the request must fail with the existing `ai_busy` / `ai_none` code, not raise. → Task 3.
3. **Two workers advance at once**: the loser must retry, not lose the advance or crash the request. → Task 3 (threaded test).
4. **A provider error inside one request**: the cursor must advance **once**, not once per retry — otherwise a failing provider silently burns the whole rotation. → Task 4.
5. **Cursor row missing on an old DB** (fresh install that never ran the migration path, or a row deleted by hand): selection must still work and self-heal the row. → Task 3.

---

### Task 1: Rotation state and audit schema

**Files:**
- Create: newsbot/rotation.py
- Modify: `newsbot/__init__.py` (`SCHEMA_VERSION = 11`)
- Modify: `newsbot/db.py` (`SCHEMA` + `MIGRATIONS`)
- Test: tests/test_phase3_rotation_state.py

**Interfaces:**
- Consumes: `newsbot.db.q`, `newsbot.db.SCHEMA`, `newsbot.db.MIGRATIONS`, `newsbot.timeutils.now_iso`.
- Produces: `ROTATION_SCOPES = ("public",)`; `rotation_get(scope) -> dict | None` with keys `scope, cursor, version, updated_at`; `rotation_set(scope, cursor, version) -> bool` (CAS); `rotation_ensure(scope) -> dict`; `rotation_log_add(scope, model_id, index, pool_size, version) -> int`; `rotation_log_recent(scope, limit=20) -> list[dict]`.

**Schema (exact):**

```sql
CREATE TABLE IF NOT EXISTS ai_rotation(scope TEXT PRIMARY KEY, cursor INTEGER DEFAULT 0, version INTEGER DEFAULT 0, updated_at TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS ai_rotation_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, scope TEXT, model_id INTEGER, idx INTEGER, pool_size INTEGER, version INTEGER);
CREATE INDEX IF NOT EXISTS idx_ai_rotation_log_scope ON ai_rotation_log(scope, id);
```

`MIGRATIONS` gains nothing for these two tables (they are new, so `CREATE TABLE IF NOT EXISTS` is enough); the version bump is what forces existing DBs through the schema step.

- [ ] **Step 1: Write the failing test**

```python
def test_rotation_row_is_created_and_advanced_with_cas(isolated_db):
    from newsbot import rotation
    row = rotation.rotation_ensure("public")
    assert row["cursor"] == 0 and row["version"] == 0
    assert rotation.rotation_set("public", 1, 0) is True      # expected version matches
    assert rotation.rotation_set("public", 2, 0) is False     # stale version loses
    assert rotation.rotation_get("public")["cursor"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase3_rotation_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'newsbot.rotation'`

- [ ] **Step 3: Implement**

newsbot/rotation.py with the functions in the Interfaces block; `rotation_set` issues `UPDATE ai_rotation SET cursor=?, version=version+1, updated_at=? WHERE scope=? AND version=?` and returns `cursor.rowcount == 1`. `rotation_ensure` inserts the row with `INSERT OR IGNORE` then reads it. `rotation_log_add` inserts one row. `rotation_log_recent` returns the last `limit` rows, newest first.

Bump `SCHEMA_VERSION` to 11 in `newsbot/__init__.py` and add both `CREATE TABLE`/index statements to `db.SCHEMA`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_phase3_rotation_state.py -q`
Expected: PASS

- [ ] **Step 5: Update the version guards and commit**

The four schema-version guards now expect 11 (`tests/test_ph01_migration.py`, `tests/test_phase10_db_quality.py`, `tests/test_phase12_config_docs.py`, `tests/test_phase13_tech_debt.py`) — the same deliberate-bump rule used for v10.

```bash
git add newsbot/rotation.py newsbot/db.py newsbot/__init__.py tests/test_phase3_rotation_state.py tests/test_ph01_migration.py tests/test_phase10_db_quality.py tests/test_phase12_config_docs.py tests/test_phase13_tech_debt.py
git commit -m "feat(phase3): rotation state + audit tables (schema v11)"
```

---

### Task 2: Pure selection — which model does this request get?

**Files:**
- Create: newsbot/rotation.py (add)
- Test: tests/test_phase3_rotation_state.py (add)

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `order_pool(models) -> list[dict]` (sorted by `(priority or 99, id)`); `pick_rotation(pool, cursor) -> tuple[dict | None, int]` returning the chosen model and its index in the *ordered* pool, or `(None, 0)` for an empty pool.

- [ ] **Step 1: Write the failing tests**

```python
def test_pick_rotation_is_stable_ordered_and_wraps():
    from newsbot.rotation import pick_rotation, order_pool
    pool = [{"id": 3, "priority": 10}, {"id": 1, "priority": 10}, {"id": 2, "priority": 5}]
    assert [m["id"] for m in order_pool(pool)] == [2, 1, 3]      # priority first, then id
    assert pick_rotation(pool, 0)[0]["id"] == 2
    assert pick_rotation(pool, 1)[0]["id"] == 1
    assert pick_rotation(pool, 2)[0]["id"] == 3
    assert pick_rotation(pool, 3)[0]["id"] == 2                  # wrap

def test_pick_rotation_survives_a_shrinking_pool():
    """ADR-۳: اگر مدل‌ها کم شوند، ایندکسِ مطلق نباید مدل را رد کند یا دو بار انتخاب کند."""
    from newsbot.rotation import pick_rotation
    big = [{"id": i, "priority": 10} for i in range(1, 6)]
    assert pick_rotation(big, 4)[0]["id"] == 5
    small = big[:2]                                              # دو مدل Remaining
    assert pick_rotation(small, 4)[0]["id"] in (1, 2)             # 4 % 2 == 0 ⇒ مدل اول
    assert pick_rotation(small, 4)[1] == 0

def test_pick_rotation_on_empty_pool_returns_none():
    from newsbot.rotation import pick_rotation
    assert pick_rotation([], 7) == (None, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phase3_rotation_state.py -q`
Expected: FAIL — `ImportError: cannot import name 'pick_rotation'`

- [ ] **Step 3: Implement** `order_pool` and `pick_rotation` (index = `cursor % len(pool)`).

- [ ] **Step 4: Run tests to verify they pass** — Expected: PASS

- [ ] **Step 5: Commit** — `feat(phase3): pure round-robin selection (stable order, modulo the live pool)`

---

### Task 3: Persisted advance with CAS, fail-open

**Files:**
- Modify: newsbot/rotation.py
- Test: tests/test_phase3_rotation_state.py (add)

**Interfaces:**
- Consumes: `rotation_ensure`, `rotation_set`, `pick_rotation`, `order_pool`.
- Produces: `async def advance(scope, models) -> tuple[dict | None, int, int]` → `(model, index, version_after)`; `def rotation_stats(scope) -> dict` → `{"cursor": int, "version": int, "log_rows": int}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_advance_moves_the_cursor_once_per_call(isolated_db):
    from newsbot import rotation
    pool = [{"id": i, "priority": 10} for i in range(1, 4)]
    m1, i1, v1 = asyncio.run(rotation.advance("public", pool))
    m2, i2, v2 = asyncio.run(rotation.advance("public", pool))
    assert [m1["id"], m2["id"]] == [1, 2]
    assert (i1, i2) == (0, 1) and v2 == v1 + 1

def test_advance_is_safe_when_two_callers_race(isolated_db):
    """ADR-۳: از دست دادنِ مسابقه باید تلاشِ دوباره بدهد، نه اینکه advance را گم کند."""
    import threading
    from newsbot import rotation
    pool = [{"id": i, "priority": 10} for i in range(1, 4)]
    seen, errors = [], []
    def worker():
        try:
            for _ in range(10):
                m, _i, _v = asyncio.run(rotation.advance("public", pool))
                seen.append(m["id"])
        except Exception as e:      # pragma: no cover - only on a real bug
            errors.append(repr(e))
    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors
    assert len(seen) == 40
    row = rotation.rotation_get("public")
    assert row["version"] == 40          # هر فراخوانی دقیقاً یک advance

def test_advance_on_missing_row_self_heals(isolated_db):
    from newsbot import rotation
    from newsbot.db import q
    q("DELETE FROM ai_rotation WHERE scope='public'", commit=True)
    m, _i, _v = asyncio.run(rotation.advance("public", [{"id": 9, "priority": 10}]))
    assert m["id"] == 9
    assert rotation.rotation_get("public") is not None
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL (`advance` missing)

- [ ] **Step 3: Implement** `advance`: read row (or ensure it), pick from the ordered pool, CAS to `cursor + 1`; on a lost CAS re-read and retry up to **5** times; if the row still cannot be advanced, return the locally computed pick anyway (fail-open — publishing must not break on a bookkeeping race). Advance never runs on an empty pool (returns `(None, 0, version)` without writing).

- [ ] **Step 4: Run tests to verify they pass** — Expected: PASS

- [ ] **Step 5: Commit** — `feat(phase3): persisted cursor advance with CAS and fail-open`

---

### Task 4: Wire into the request path — advance once, failover does not advance

**Files:**
- Modify: `newsbot/ai_models.py` (`_try_models`, `generate`)
- Test: tests/test_phase3_rotation_engine.py (new)

**Interfaces:**
- Consumes: `rotation.advance`, `rotation.rotation_log_add`, `rotation.order_pool`.
- Produces: no new public names; `_try_models` gains one keyword `rotate: bool = False`.

- [ ] **Step 1: Write the failing tests** (stub transport, no network)

```python
def test_rotation_advances_once_per_request_even_when_the_provider_fails(isolated_db, monkeypatch):
    """ADR-۴: خطای فراهم‌کننده cursor را جلو نمی‌برد؛ یک advance در هر درخواست."""
    ...
    # first model raises a transient error, second answers
    out, label, err = asyncio.run(amod.generate(settings, article))
    assert err is None
    assert rotation.rotation_get("public")["version"] == 1     # نه ۲

def test_each_request_takes_the_next_model(isolated_db, monkeypatch):
    ...
    ids = [asyncio.run(amod.generate(settings, article))[1] for _ in range(3)]
    assert ids == ["m1", "m2", "m3"]

def test_quarantined_model_is_skipped_without_burning_the_cursor(isolated_db, monkeypatch):
    ...
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL (cursor never moves)

- [ ] **Step 3: Implement**

In `generate()`: build the `public` pool as today (`shared`, ready-only), call `m, idx, ver = await arun(rotation.advance, "public", shared)`; if `m` is not None, move it to the front of `shared` and pass `rotate=True` down. In `_try_models`, when `rotate` is set, skip the `_sort_key` reordering for the **first** model only (keep the rest of the existing behaviour for failover), and after a model is actually called, write the audit row once with the chosen index and the pool size. `test_model()` (the probe path) passes `rotate=False`.

- [ ] **Step 4: Run tests to verify they pass** — Expected: PASS

- [ ] **Step 5: Commit** — `feat(phase3): rotate the public pool once per request; failover stays local`

---

### Task 5: Audit read-out and retention

**Files:**
- Modify: newsbot/rotation.py
- Modify: `newsbot/ui_views_super.py` (one line in the models screen)
- Test: tests/test_phase3_rotation_engine.py (add)

**Interfaces:**
- Consumes: `rotation_log_recent`, `rotation_stats`.
- Produces: `async def cmd_rotation(update, context)` — super-admin only, prints the cursor, the version, and the last 20 requests.

- [ ] **Step 1: Write the failing test**

```python
def test_rotation_log_records_the_chosen_model_and_is_queryable(isolated_db):
    ...
    rows = rotation.rotation_log_recent("public", limit=5)
    assert rows and rows[0]["model_id"] == 2 and rows[0]["idx"] == 1
    assert "public" in rotation.rotation_stats("public") or rotation.rotation_stats("public")["log_rows"] >= 1
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL

- [ ] **Step 3: Implement** the read-out plus a `/rotation` super-admin command (same pattern as `/providers`), and register it in `newsbot/app.py`.

- [ ] **Step 4: Run test to verify it passes** — Expected: PASS

- [ ] **Step 5: Commit** — `feat(phase3): /rotation read-out for cursor and per-request audit`

---

### Task 6: Docs and the state file

**Files:**
- Modify: `README.md` (AI section), `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md` (new §4.3)

- [ ] **Step 1:** Document the rotation rule in one short paragraph: what "next in order" means, that a provider error does not advance the cursor, and how to read the audit.
- [ ] **Step 2:** Write §4.3 (goal, decisions, acceptance criteria) and update the README test count to the real collected number (`pytest --collect-only -q | tail -1`) — the guard `test_readme_test_count_matches_collection` enforces it.
- [ ] **Step 3: Run the guards** — `pytest tests/test_phase1_10_state_alignment.py tests/test_repo_integrity.py -q` → PASS
- [ ] **Step 4: Commit** — `docs(phase3): record the rotation engine in the state file and README`

---

## Closing the phase (rule 10)

Phase 3 owns **#54** and **#55** as well as this feature:

- [ ] Merge **#54** (OBS-001/003/008/010/014, DB-007) — rebase onto `main`, CI, gate lift/restore.
- [ ] **#55** adds a `LICENSE`. The licence type is the owner's choice (B-6 in the state file) — ask before merging; do not assume.
- [ ] Update the state file: Phase 3 `COMPLETED`, evidence rows, PR SHAs; open and merge the close-out PR.

## Verification before claiming anything (explicit)

1. `pytest -q` on the branch: no failure beyond the recorded Windows-only 19 (§5.1 of the state file).
2. `ruff check newsbot tests tools run.py` and `python -m pyflakes newsbot tools run.py tests`: clean.
3. `python tools/final_verification.py`: 17/17.
4. GitHub CI `test`: SUCCESS.
5. Branch protection: compared field by field against the backup after every merge.