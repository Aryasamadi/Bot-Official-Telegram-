# Phase 5 — Q&A quotas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add atomic per-user cooldown, per-user daily and per-post daily guardrails to group Q&A, with env defaults and per-channel overrides, without touching the AI plan quota or adding a schema version.

**Architecture:** A single guarded `INSERT … SELECT … WHERE … ON CONFLICT DO NOTHING` into the existing `qa_answers` table enforces all three caps atomically at claim time; no counter table is created. `qa_quota.py` computes effective limits and day windows, `qa.py` converts the claim reason into silence or one throttled Persian notice, and the admin panel exposes three optional per-channel overrides that fall back to env.

**Tech Stack:** Python 3, aiogram, SQLite + Cloudflare D1, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-20-phase5-qa-quotas-design.md`

## Global Constraints

- Reports and UI text in Persian; technical identifiers in English.
- No secrets in code, logs, commits, PR bodies or artifacts.
- No behaviour change without a test; tests fail first; small revertable commits.
- `SCHEMA_VERSION` stays **12** — only two `CREATE INDEX IF NOT EXISTS` statements are added to `DB_INDEXES`.
- SQLite and D1 must both work; enforcement must be a single atomic statement.
- at-most-once (Phase 4 decision 2) must not be weakened: a duplicate message never creates a row or spends quota.
- Phase 4 live acceptance stays deferred to Phase 10 (D-7). Do not claim Phase 5 is live-verified in this phase.
- Branch names in docs are written **bold**, never backticks.
- Known local-only failures (19: 4 in `tests/test_deploy_script.py`, 15 in `tests/test_session3_cicd_docker_deploy_docs.py`) are pre-existing on Windows and are not caused by this plan.

## Review Focus

- **A duplicate `(chat_id, msg_id)` arriving when a cap is already full** must be reported as `duplicate`, spend no quota and produce no second answer — pinned by Task 2 `test_the_same_message_is_a_duplicate_and_consumes_nothing`.
- **Two claims racing at a cap of 1** must leave exactly one row — pinned by Task 2 `test_concurrent_claims_at_cap_one_produce_exactly_one_row`.
- **A rejected ask must never reach `ai_chat`** (no AI units burned by a refused request) — pinned by Task 3 `test_a_cooldown_refusal_is_silent_and_never_calls_ai`.
- **An invalid or blank per-channel override** must fall back to the env default without an exception, and blank must mean "inherit" — pinned by Task 4 `test_effective_limits_fall_back_on_invalid_override` and `test_blank_channel_input_means_inherit`.
- **A day boundary and a `post_id` reused in two channels** must not mix counts — pinned by Task 2 `test_a_yesterday_ask_is_outside_today_window` and `test_the_same_post_id_in_two_channels_does_not_share_the_cap`.

---

### Task 1: Config surface and env documentation

**Files:**
- Modify: `newsbot/config.py` (after `QA_LINK_CACHE_MIN`, near line 233)
- Modify: `newsbot/app.py` (self-check ranges, near line 214)
- Modify: `.env.example` (Q&A block, near line 101)
- Test: `tests/test_phase5_qa_quotas.py`

**Interfaces:**
- Consumes: `_env_int(name, default, lo, hi)` from `newsbot.config`.
- Produces: `config.QA_COOLDOWN_S = 30`, `config.QA_USER_DAILY_MAX = 30`, `config.QA_POST_DAILY_MAX = 50`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_phase5_qa_quotas.py`:

```python
def test_the_config_defaults_and_ranges_are_the_designed_values():
    assert (config.QA_COOLDOWN_S, config.QA_USER_DAILY_MAX,
            config.QA_POST_DAILY_MAX) == (30, 30, 50)

def test_the_env_example_and_self_check_include_all_three_names():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    app = (ROOT / "newsbot" / "app.py").read_text(encoding="utf-8")
    for name in ("QA_COOLDOWN_S", "QA_USER_DAILY_MAX", "QA_POST_DAILY_MAX"):
        assert name in env and name in app
```

Use `ROOT = Path(__file__).resolve().parents[1]`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase5_qa_quotas.py -q`
Expected: FAIL with `AttributeError: module 'newsbot.config' has no attribute 'QA_COOLDOWN_S'`.

- [ ] **Step 3: Implement the config values**

In `newsbot/config.py` add, beside the other `QA_*` values:

```python
QA_COOLDOWN_S = _env_int("QA_COOLDOWN_S", 30, 0, 3600)
QA_USER_DAILY_MAX = _env_int("QA_USER_DAILY_MAX", 30, 1, 1000)
QA_POST_DAILY_MAX = _env_int("QA_POST_DAILY_MAX", 50, 1, 5000)
```

In `newsbot/app.py` add the three `(name, value, lo, hi)` tuples to the self-check tuple list using the same ranges. In `.env.example` add the three names with their defaults and a comment that a per-channel value in the admin panel overrides them.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase5_qa_quotas.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add newsbot/config.py newsbot/app.py .env.example tests/test_phase5_qa_quotas.py
git commit -m "feat(phase5): add Q&A quota env defaults and self-check ranges"
```

---

### Task 2: Atomic quota claim core

**Files:**
- Modify: `newsbot/db.py` (`DB_INDEXES`, near line 78)
- Create: `newsbot/qa_quota.py`
- Modify: `newsbot/qa.py` (`qa_claim`, lines 81–98)
- Test: `tests/test_phase5_qa_quotas.py`

**Interfaces:**
- Consumes: `config.QA_*` (Task 1), `db.write`, `qa.now_iso`, `timeutils.iso_fixed/offset_now/parse_dt`, `core_users.admin_offset/is_super`, `core_channels.get_settings`.
- Produces:
  - `qa_quota.effective_limits(settings: dict | None) -> tuple[int, int, int]` — `(cooldown_s, user_max, post_max)`, with `None`/invalid values falling back to env.
  - `qa_quota.day_start(local_day: str, offset: float) -> str` — local midnight expressed as UTC ISO string.
  - `qa_quota.windows(uid: int, settings: dict | None, now: datetime) -> tuple[str, str, str]` — `(since_user, since_post, cooldown_cutoff)`.
  - `qa_quota.claim(conn, chat_id, msg_id, user_id, channel_id, post_id, since_user, since_post, cooldown_cutoff, user_max, post_max, bypass=False) -> str`.
  - `qa.claim(chat_id, msg_id, user_id, channel_id, post_id=0, settings=None, now=None) -> str` with reasons `"" | "duplicate" | "cooldown" | "user_cap" | "post_cap"`.
  - `qa.qa_claim` keeps the old `bool` signature and delegates to `qa.claim` (compatibility wrapper, `True` only on `""`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phase5_qa_quotas.py`. Use `now = datetime(2026, 11, 20, 12, 0, tzinfo=config.UTC)` and `settings = {"qa_cooldown_s": 30, "qa_user_daily_max": 1, "qa_post_daily_max": 50}`; count rows with `q("SELECT COUNT(*) AS n FROM qa_answers", one=True)["n"]`.

```python
def test_an_accepted_claim_returns_empty_and_creates_one_row(isolated_db):
    assert qa.claim(1, 1, 7, -100, 55, settings=settings, now=now) == ""
    assert q("SELECT COUNT(*) AS n FROM qa_answers", one=True)["n"] == 1

def test_the_same_message_is_a_duplicate_and_consumes_nothing(isolated_db):
    qa.claim(1, 1, 7, -100, 55, settings=settings, now=now)
    assert qa.claim(1, 1, 7, -100, 55, settings=settings, now=now) == "duplicate"
    assert q("SELECT COUNT(*) AS n FROM qa_answers", one=True)["n"] == 1

def test_a_second_ask_inside_cooldown_is_rejected_without_a_row(isolated_db):
    qa.claim(1, 1, 7, -100, 55, settings=settings, now=now)
    later = now + timedelta(seconds=10)
    assert qa.claim(1, 2, 7, -100, 55, settings=settings, now=later) == "cooldown"
    assert q("SELECT COUNT(*) AS n FROM qa_answers", one=True)["n"] == 1

def test_an_ask_after_cooldown_is_accepted(isolated_db):
    qa.claim(1, 1, 7, -100, 55, settings=settings, now=now)
    assert qa.claim(1, 2, 7, -100, 55, settings=settings,
                    now=now + timedelta(seconds=31)) == ""

def test_the_user_daily_cap_rejects_the_next_ask(isolated_db):
    qa.claim(1, 1, 7, -100, 55, settings=settings, now=now)
    nxt = dict(settings, qa_cooldown_s=0)
    assert qa.claim(1, 2, 7, -200, 99, settings=nxt, now=now) == "user_cap"

def test_the_post_daily_cap_is_per_post(isolated_db):
    cap = {"qa_cooldown_s": 0, "qa_user_daily_max": 30, "qa_post_daily_max": 1}
    qa.claim(1, 1, 7, -100, 55, settings=cap, now=now)
    assert qa.claim(1, 2, 8, -100, 55, settings=cap, now=now) == "post_cap"
    assert qa.claim(1, 3, 8, -100, 56, settings=cap, now=now) == ""

def test_the_same_post_id_in_two_channels_does_not_share_the_cap(isolated_db):
    cap = {"qa_cooldown_s": 0, "qa_user_daily_max": 30, "qa_post_daily_max": 1}
    qa.claim(1, 1, 7, -100, 55, settings=cap, now=now)
    assert qa.claim(1, 2, 8, -200, 55, settings=cap, now=now) == ""

def test_a_super_admin_bypasses_all_three_guards(isolated_db, monkeypatch):
    monkeypatch.setattr(config, "SUPER_ADMIN_IDS", (7,))
    tight = {"qa_cooldown_s": 3600, "qa_user_daily_max": 1, "qa_post_daily_max": 1}
    assert qa.claim(1, 1, 7, -100, 55, settings=tight, now=now) == ""
    assert qa.claim(1, 2, 7, -100, 55, settings=tight, now=now) == ""
    assert q("SELECT COUNT(*) AS n FROM qa_answers", one=True)["n"] == 2

def test_concurrent_claims_at_cap_one_produce_exactly_one_row(isolated_db):
    from concurrent.futures import ThreadPoolExecutor
    cap = {"qa_cooldown_s": 0, "qa_user_daily_max": 1, "qa_post_daily_max": 100}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda i: qa.claim(1, i, 7 + i, -100, 55, settings=cap, now=now), range(8)))
    assert results.count("") == 1 and results.count("user_cap") == 7
    assert q("SELECT COUNT(*) AS n FROM qa_answers", one=True)["n"] == 1

def test_a_yesterday_ask_is_outside_today_window(isolated_db):
    q("INSERT INTO qa_answers(chat_id,msg_id,user_id,channel_id,post_id,status,answer,error,created_at,updated_at)"
      " VALUES(1,1,7,-100,55,'done','','','2026-11-19T12:00:00.000000+00:00','')")
    cap = {"qa_cooldown_s": 0, "qa_user_daily_max": 1, "qa_post_daily_max": 1}
    assert qa.claim(1, 2, 7, -100, 55, settings=cap, now=now) == ""

def test_a_failed_answer_still_counts_as_an_ask(isolated_db):
    q("INSERT INTO qa_answers(chat_id,msg_id,user_id,channel_id,post_id,status,answer,error,created_at,updated_at)"
      " VALUES(1,1,7,-100,55,'failed','','ai_fail','2026-11-20T11:00:00.000000+00:00','')")
    cap = {"qa_cooldown_s": 0, "qa_user_daily_max": 1, "qa_post_daily_max": 50}
    assert qa.claim(1, 2, 7, -100, 55, settings=cap, now=now) == "user_cap"
    assert qa.claim(1, 3, 8, -100, 56, settings=cap, now=now) == ""

def test_the_two_daily_indexes_exist_after_init(isolated_db):
    names = {r["name"] for r in q("PRAGMA index_list(qa_answers)")}
    assert {"idx_qa_answers_user_day", "idx_qa_answers_post_day"} <= names

def test_effective_limits_inherit_env_when_settings_have_none():
    assert qa_quota.effective_limits({}) == (
        config.QA_COOLDOWN_S, config.QA_USER_DAILY_MAX, config.QA_POST_DAILY_MAX)

def test_effective_limits_fall_back_on_invalid_override():
    bad = {"qa_cooldown_s": -5, "qa_user_daily_max": "x", "qa_post_daily_max": 0}
    assert qa_quota.effective_limits(bad) == (
        config.QA_COOLDOWN_S, config.QA_USER_DAILY_MAX, config.QA_POST_DAILY_MAX)

def test_day_start_uses_the_given_offset():
    assert qa_quota.day_start("2026-11-20", 3.5) == "2026-11-19T20:30:00.000000+00:00"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase5_qa_quotas.py -q`
Expected: FAIL on `ModuleNotFoundError: newsbot.qa_quota` (and missing indexes).

- [ ] **Step 3: Add the two indexes**

In `newsbot/db.py`, inside `DB_INDEXES`, add:

```sql
CREATE INDEX IF NOT EXISTS idx_qa_answers_user_day ON qa_answers(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_qa_answers_post_day ON qa_answers(channel_id, post_id, created_at);
```

Do **not** change `newsbot/__init__.py` or any schema-version assertion.

- [ ] **Step 4: Implement `newsbot/qa_quota.py`**

Four public functions with exactly the signatures in Interfaces. `effective_limits` validates type and range, returning env values on any problem. `day_start` converts a local `YYYY-MM-DD` plus a numeric offset to a UTC ISO string via `timeutils`/`datetime`. `windows` uses `admin_offset(uid)` for the user day and `settings["tz"]`/`utc_offset` via `timeutils.offset_now` for the channel day; `cooldown_cutoff = now - timedelta(seconds=effective_limits(settings)[0])`. `claim` first checks `SELECT 1 FROM qa_answers WHERE chat_id=? AND msg_id=?` for `duplicate`, then runs the guarded INSERT from §6.4 of the spec; if `rowcount == 0`, derives the reason from a diagnostic `SELECT` (user count, post count, `MAX(created_at)`) with priority `duplicate → cooldown → user_cap → post_cap`. `bypass=True` (super admin) uses plain `INSERT OR IGNORE`.

- [ ] **Step 5: Rewire `qa.claim` and keep `qa_claim` compatible**

Replace the body of `newsbot/qa.py:81-98` with the `qa.claim` wrapper described in Interfaces; it computes `now = now or now_iso()`, calls `qa_quota.windows` and `qa_quota.effective_limits`, then `write(lambda conn: qa_quota.claim(conn, ...))`. Keep `qa_claim(...)` as a thin `bool` wrapper for any external caller. Import `qa_quota` lazily inside the function to avoid an import cycle.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase5_qa_quotas.py tests/test_phase4_qa_core.py tests/test_phase4_schema.py -q`
Expected: all passed; Phase 4's `test_the_qa_answers_table_has_the_expected_columns` and schema-version guards stay green.

- [ ] **Step 7: Commit**

```bash
git add newsbot/db.py newsbot/qa_quota.py newsbot/qa.py tests/test_phase5_qa_quotas.py
git commit -m "feat(phase5): atomic per-post, per-user and cooldown Q&A claim"
```

---

### Task 3: Handler behaviour on refusal

**Files:**
- Modify: `newsbot/qa.py` (`on_group_reply`, lines 134–199)
- Modify: `newsbot/ui_i18n.py` (new notice keys in both languages)
- Test: `tests/test_phase5_qa_quotas.py`

**Interfaces:**
- Consumes: `qa.claim(...) -> str` (Task 2), `ratelimit.rate_ok`, `ui_i18n.tr`.
- Produces: `qa._send` remains the only send path; no new public function required.

- [ ] **Step 1: Write the failing tests**

Reuse `_Bot`, `_group_msg`, `_update`, `_channel_post`, `_stub_ai`, `chan` from `tests/test_phase4_qa_core.py` (copy the small helpers into this file).

```python
def test_a_cap_refusal_sends_one_short_notice(chan, monkeypatch):
    monkeypatch.setattr(qa, "claim", lambda *a, **k: "user_cap")
    called = []
    async def fake_ai(*a, **k): called.append(1); return "x", "m", None
    monkeypatch.setattr(amod, "ai_chat", fake_ai)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert len(bot.sent) == 1 and called == []

def test_the_cap_notice_is_throttled(chan, monkeypatch):
    monkeypatch.setattr(qa, "claim", lambda *a, **k: "post_cap")
    ratelimit._rl.clear()
    bot = _Bot()
    for mid in (11, 12):
        asyncio.run(qa.on_group_reply(
            _update(_group_msg(mid=mid, reply=_channel_post())), NS(bot=bot)))
    assert len(bot.sent) == 1

def test_a_cooldown_refusal_is_silent_and_never_calls_ai(chan, monkeypatch):
    monkeypatch.setattr(qa, "claim", lambda *a, **k: "cooldown")
    called = []
    async def fake_ai(*a, **k): called.append(1); return "x", "m", None
    monkeypatch.setattr(amod, "ai_chat", fake_ai)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert bot.sent == [] and called == []

def test_an_empty_reply_in_cooldown_is_silent(chan, monkeypatch):
    monkeypatch.setattr(qa, "claim", lambda *a, **k: "cooldown")
    p = _channel_post(text=None)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=p)), NS(bot=bot)))
    assert bot.sent == []

def test_the_normal_phase4_path_still_answers_once(chan, monkeypatch):
    _stub_ai(monkeypatch)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert len(bot.sent) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase5_qa_quotas.py -q -k "refusal or throttled or still_answers"`
Expected: FAIL because `qa.claim` does not exist yet in the handler / notices are not wired.

- [ ] **Step 3: Wire the reason into `on_group_reply`**

After each of the two `qa_claim` call sites, evaluate the reason: `""` continues; `"duplicate"` and `"cooldown"` log at info level and return silently; `"user_cap"`/`"post_cap"` log, and send the matching short i18n notice **only when the post text and the question are both non-empty**, guarded by `rate_ok(f"qa:capmsg:{uid}:{ch['id']}", 300)`. Never call `ai_chat` on any non-empty reason. Log only ids and the reason.

- [ ] **Step 4: Add the two i18n notices**

In `newsbot/ui_i18n.py`, add a `qa_cap_user` and `qa_cap_post` key with the exact Persian and English wording from spec §6.5, including the `fa` and `en` entries.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase5_qa_quotas.py tests/test_phase4_qa_core.py -q`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add newsbot/qa.py newsbot/ui_i18n.py tests/test_phase5_qa_quotas.py
git commit -m "feat(phase5): silence on cooldown, throttled notice on daily caps"
```

---

### Task 4: Per-channel overrides in the admin panel

**Files:**
- Modify: `newsbot/core_channels.py` (`default_settings`, line 290)
- Modify: `newsbot/ui_i18n.py` (`FIELD_LABEL`, `INT_FIELDS`, new `QA_FIELDS`, button labels)
- Modify: `newsbot/ui_views_admin.py` (`view_channel`, line 89; new `view_qa`)
- Modify: `newsbot/ui_dispatch.py` (`a:set` branch, line 237; new `a:qa` branch)
- Modify: `newsbot/ui_inputs.py` (`_input_value`, line 151)
- Test: `tests/test_phase5_qa_quota_ui.py`

**Interfaces:**
- Consumes: `qa_quota.effective_limits` (Task 2), `get_settings`/`update_settings`, `ask`/`render`/`popup`/`B`/`tr`.
- Produces: `QA_FIELDS = ("qa_cooldown_s", "qa_user_daily_max", "qa_post_daily_max")` exported from `ui_i18n`; `view_qa(update, context, cid)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_default_settings_have_three_none_keys():
    from newsbot.core_channels import default_settings
    s = default_settings()
    assert all(s[k] is None for k in QA_FIELDS)

def test_view_qa_renders_three_buttons_and_inherited_values(monkeypatch):
    # stub `render` with a collector; call view_qa on a fake update/context
    assert all(f"a:set:{cid}:{k}" in buttons for k in QA_FIELDS)
    assert "پیش فرض" in text or "پیشفرض" in text

def test_a_set_callback_returns_to_view_qa(monkeypatch):
    # monkeypatch `ask` to capture the `back` argument
    # dispatch a fake `a:set:1:qa_cooldown_s` callback
    assert back == "a:qa:1"

def test_blank_channel_input_means_inherit():
    assert _input_value("qa_cooldown_s", "  ", "fa") is None

def test_out_of_range_channel_input_is_rejected():
    with pytest.raises(ValueError):
        _input_value("qa_user_daily_max", "999999", "fa")

def test_i18n_keys_exist_in_both_languages():
    for key in ("qa_panel_title", "qa_settings", "qa_cap_user", "qa_cap_post",
                *QA_FIELDS):
        assert tr("fa", key) and tr("en", key)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase5_qa_quota_ui.py -q`
Expected: FAIL on `ImportError: cannot import name 'QA_FIELDS'`.

- [ ] **Step 3: Add the settings keys and i18n**

In `core_channels.default_settings`, add `"qa_cooldown_s": None, "qa_user_daily_max": None, "qa_post_daily_max": None`. In `ui_i18n.py`, define `QA_FIELDS` with the exact tuple above, add the three entries to `FIELD_LABEL` and `INT_FIELDS` with ranges `(0, 3600)`, `(1, 1000)`, `(1, 5000)`, and add the two notice keys plus panel/button labels in both languages.

- [ ] **Step 4: Implement `view_qa` and wire the callbacks**

In `ui_views_admin.py`, add a Q&A settings button (`a:qa:{cid}`) to the channel panel and a `view_qa(update, context, cid)` page that shows each effective value from `qa_quota.effective_limits(get_settings(cid))` and marks inherited values as «پیشفرض». In `ui_dispatch.py`, handle `b == "qa"` and make `a:set` route the three `QA_FIELDS` back to `a:qa:{cid}`. In `ui_inputs.py`, return `None` for blank input on the three fields before the integer-range check.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase5_qa_quota_ui.py tests/test_phase5_qa_quotas.py -q`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add newsbot/core_channels.py newsbot/ui_i18n.py newsbot/ui_views_admin.py \
        newsbot/ui_dispatch.py newsbot/ui_inputs.py tests/test_phase5_qa_quota_ui.py
git commit -m "feat(phase5): per-channel Q&A quota overrides in the admin panel"
```

---

### Task 5: State file, full verification and delivery

**Files:**
- Modify: `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`
- Test: existing guards `tests/test_phase1_10_state_alignment.py`, `tests/test_repo_integrity.py`

**Interfaces:**
- Consumes: the merged code from Tasks 1–4 and the real evidence collected below.
- Produces: state file with Phase 5 `COMPLETED` or an honest `IN_PROGRESS` record if any check fails; D-7 untouched.

- [ ] **Step 1: Write the failing guard expectation**

Run the two existing state guards on the current file; they must pass before the state edit documents new evidence:

```bash
.venv\Scripts\python.exe -m pytest tests/test_repo_integrity.py tests/test_phase1_10_state_alignment.py -q
```

Expected: 9 passed. If `Current phase` guard fails, keep the wording from the Phase 5 record commit (Phase 4 named first because it is not `COMPLETED`, with Phase 5 called out as active).

- [ ] **Step 2: Update the state file with real evidence**

Record in `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`: Phase 5 tasks and PR/SHA, exact test counts, CI run id and conclusion, ruff/mypy result, `final_verification.py` result, and `run.py --check` result. Keep D-7 and the Phase 4 deferral intact — do not write that Phase 4 is live-verified.

- [ ] **Step 3: Run the full verification suite and capture the output**

```bash
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy newsbot
.venv\Scripts\python.exe tools/final_verification.py
.venv\Scripts\python.exe run.py --check
```

Expected: no new failures beyond the 19 known Windows-only ones; the full-suite line and each command's exit code are copied verbatim into the state file. If anything fails, record it honestly and do not mark Phase 5 `COMPLETED`.

- [ ] **Step 4: Commit and push**

```bash
git add docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md
git commit -m "docs(phase5): record delivery evidence and close Phase 5"
git push
```

- [ ] **Step 5: Open the records PR and merge it after CI/gate**

Open the PR, wait for CI `test` SUCCESS, then follow the owner's merge protocol exactly: confirm up to date → `gate.py <PR> lift` → `gh pr merge --squash --delete-branch` → `gate.py <PR> restore` → `verify_protection.py` must report `IDENTICAL on all 15 fields`.

---

## Delivery map

- **PR 1 (docs):** this plan + the approved spec — merged before code, as in Phase 4's #82.
- **PR 2 (code):** Tasks 1–4, each as its own commit, one review gate.
- **PR 3 (records):** Task 5 — merged with the full gate protocol above.
