# Phase 4 — Live Acceptance on a GitHub Actions Runner (design)

Date: 2026-11-16 · Approved: 2026-11-17 · Owner decisions this serves: "each phase must finish
fully and verifiably before the next starts" — a phase is not closed on unit tests alone. The owner
also asked for a 5h50m runner budget and for the test database to survive between runs.

## Intent

Prove the reply-to-post group Q&A of Phase 4 against the **real Telegram API**, not only against
stubs. Unit tests already prove the pure logic, the claim, the quota gate and the error mapping.
They cannot prove four things that only the live API can show:

1. the bot actually **receives** a user's reply to a channel post inside the discussion group
   (privacy mode / group-admin membership);
2. the group's `linked_chat_id` really matches the registered channel's `chat_id`;
3. the reply's automatic forward really carries `sender_chat.type == channel` and the post id;
4. the answer is really delivered in the group, exactly once, and the asker's AI quota is the one
   that is consumed.

## Why a GitHub Actions runner

- The owner can hold the bot token in **repository secrets** — it never touches this repo, a log,
  an artifact or a chat message.
- No VPS access is needed (B-2 is unresolved), and nothing runs against production.
- The runner is Linux and disposable, and a sqlite DB is isolated in the runner temp dir.
- Evidence (a JSON report + the bot log) is uploaded as an artifact, so the phase record is
  verifiable later.

## Goals

- A **manual-only** workflow that boots the real bot for a bounded time (default 350 minutes, early
  exit as soon as the gate passes).
- A machine-readable acceptance report with one record per acceptance check (ids and counts only).
- Deterministic exit codes so the run itself is the gate: `0` all required checks observed,
  `1` duration expired without all checks, `2` preconditions failed.
- No secret, no question text and no answer text in logs, report or artifacts.

## Non-goals

- Replacing the production deploy; touching the VPS; the D1 backend.
- Implementing the full Phase 9 harness (READY marker, clean-drain semantics). This is the minimal
  harness Phase 4 needs; Phase 9 may harden it and must keep it manual-only.
- Automating the human side. **Telegram bots never receive messages from other bots and cannot act
  as a user**, so a real person must post the channel post and reply to it with the question.
- Any change to the AI-quota *policy* (that is Phase 5).

## Constraints

- The owner must be present for the run and must already be able to reach the test bot (its token in
  secrets, its numeric id in `SUPER_ADMIN_IDS`).
- **A second, non-super Telegram account is required to ask the questions** (owner confirmed it is
  available). A super admin has no daily AI cap (`ai_cap_for` returns `None`) and can never receive
  an `ai_plan_grants` row, so `ai_claim` writes nothing and never returns `ai_limit`. With a super
  asker the two charging checks are unobservable — not merely hard.
- The model must be registered as a **public** model (super `/providers` path, `owner_id IS NULL`),
  because `ai_chat(owner_id=<asker>)` only sees the asker's personal models plus the public pool.
  A model registered through a per-admin "my AI" wizard would make the asker's call fail with
  `ai_fail`, not with a quota error, and the run would prove nothing about Q&A.
- GitHub job cap is 360 minutes (B-3) ⇒ job timeout **360**, default duration **350** (5h50m),
  bounded 5–355. The supervisor exits at once when every required check is observed, so the large
  budget is a ceiling, not a wait.
- Each run costs the owner 2–4 AI calls on their own provider key (the exhaustion stage makes none).
- A reused database must be encrypted with a **stable `SECRETS_KEY`**; without it, provider keys are
  stored in plaintext and restoring the cache would carry a plaintext API key.

## Approaches considered

**A — the workflow runs the bot and a checker supervises it (chosen).** One job: the checker starts
`run.py` as a child, waits for readiness, reads the DB while the owner interacts, stops the bot and
writes the report. The gate lives in code, not in a human reading logs.

**B — the workflow only runs the bot; a human reads the log afterwards.** Cheapest to write, but the
verdict is manual, non-repeatable, and cannot be recorded as evidence.

**C — run the bot on the owner's Windows machine.** No runner needed, but it needs the token on the
owner's disk (B-5 already records a plaintext token there), the machine must stay up, and it is not
the Linux environment production uses.

A is chosen: it needs three secrets, no VPS, and produces evidence.

## Design

### 1. `.github/workflows/qa-live.yml` (new, manual only)

- `on: workflow_dispatch` with inputs:
  - `duration_minutes` — default `350`, bounded 5–355;
  - `scenario` — `core` (default) or `full` (adds toggle, unrelated group and exhaustion stages);
  - `db_mode` — `reuse` (default) or `fresh`; `reuse` restores the sqlite DB cached by the previous
    run so the channel/model registration survives, `fresh` starts empty and proves the registration
    path again.
- `permissions: contents: read`; one job `qa-live`, `timeout-minutes: 360`;
  `concurrency: group: qa-live, cancel-in-progress: false`.
- Steps: `actions/checkout` and `actions/setup-python` pinned to full 40-char SHAs (the **same SHAs
  as `ci.yml`**), `actions/cache/restore` (only for `db_mode=reuse`) and `actions/cache/save`
  (`always()`) pinned the same way, install `requirements.txt`, then run the checker with the
  variables exported **into the step's `env:`** (`BOT_TOKEN`, `SUPER_ADMIN_IDS`, `SECRETS_KEY`,
  `DB_FILE=$RUNNER_TEMP/qa-live/newsbot.db`, `DB_BACKEND=sqlite`, `QA_ENABLED=1`) — no env file is
  written to disk — and **always** upload the redacted report + log (`retention-days: 14`).
- Cache: `path: $RUNNER_TEMP/qa-live/newsbot.db`, `key: qa-live-db-v1-${{ github.run_id }}`,
  `restore-keys: qa-live-db-v1-`. A unique key plus a prefix restore is the only supported way to get
  "latest wins" (caches are immutable). Restore and save are separate steps so a `fresh` run still
  replaces the newest cache. The cache holds only the throwaway test bot's DB; the supervisor blanks
  `qa_answers.answer` before the job ends so no answer text is stored there.
- Secrets: `BOT_TOKEN`, `SUPER_ADMIN_IDS`, `SECRETS_KEY` — exposed only to the single step that runs
  the bot. The token and `SECRETS_KEY` never reach a log line or the report; the numeric admin id is
  printed by `preflight` by design and is not a credential.

### 2. `tools/qa_live_acceptance.py` (new)

- `--supervise --duration MIN --report P --log P [--scenario core|full] [--env-file F]`:
  starts `python run.py` with the process environment (plus the file, if given), captures its
  stdout+stderr into the log file, waits for the sqlite file + the bot's "در حال اجرا…" line,
  polls the DB every 10 s until the duration elapses or every required check is observed, then
  stops the child (SIGTERM, SIGKILL after 15 s), blanks `qa_answers.answer`, and writes the report.
- `--check [--env-file F]`: preconditions only — env sanity, token shape, numeric admin id, writable
  DB path, and read-only `getMe` (plus `getChat` on a registered channel once one exists). It never
  calls `getUpdates`, so it never steals the bot's updates.
- Every check is attributed **per question row** from snapshots taken at the moment the row
  transitions (insert and status change are separate events), never from the end state alone. Rows
  from earlier runs are excluded by `created_at >= started_at`.
- Checks (each `OBSERVED` / `NOT_OBSERVED` / `NOT_APPLICABLE` / `ERROR`, ids and counts only):
  - `answer_delivered` — at least one row created in this run with `status='done'` for the expected
    group; the earliest such row is the *core row* the following per-row checks use;
  - `channel_id_is_telegram_chat_id` — that row's `channel_id` equals `channels.chat_id` of the
    channel it claims, and is not the internal `channels.id`;
  - `asker_charged_only` — for that row's asker: `usage.ai` rose by exactly one and, when a plan grant
    applies, `ai_plan_grants.used` rose by exactly one; a **whole-table diff** of `usage` taken at
    that moment shows no other user's counters moved (this replaces the vacuous "the channel owner is
    unchanged" half, since the owner is a super and has no row at all);
  - `answered_exactly_once` — exactly one `qa: answered chat=… msg=…` log line **and** exactly one
    `done` row for that message id. (The duplicate-suppression path cannot be triggered by a human,
    so it stays unit-tested; the report says so explicitly instead of pretending otherwise.)
  - `ignored_with_a_reason` (scenario `full`) — the bot must be seen **receiving** the message and
    refusing it, not merely staying silent. This needs one new id-only log line in `qa.py` on the
    security-relevant ignore paths:
    `qa: ignored chat=<id> msg=<id> user=<id> reason=<unregistered_channel|toggle_off|foreign_group>`
    (no question text, no answer text), written test-first. The two harmless paths (`not_a_reply`,
    `not_a_post`) log at DEBUG so a busy group does not flood the INFO log; the silent path where
    `message`/`user` is missing logs nothing (there is no id to attribute);
  - `unrelated_group_silent` (scenario `full`) — the `foreign_group` ignore line exists for the
    unrelated group's message and no `qa_answers` row exists for that group;
  - `toggle_off_silent` (scenario `full`) — the `toggle_off` ignore line exists and no row;
  - `quota_exhausted_message` (scenario `full`) — a row `status='failed'`, `error='ai_limit'` and one
    `qa: ai failed … err=ai_limit` line per such row (no retry, no provider call), with `usage.ai`
    and `grant.used` unchanged for that message; a further reply yields the same `failed/ai_limit`
    row and still no movement (there is no "silent" case here: every question message gets one row);
  - `no_secret_in_log` — the `BOT_TOKEN` and `SECRETS_KEY` values do not appear in the captured log
    (compared in memory, never printed). `SUPER_ADMIN_IDS` is excluded on purpose: it is a numeric
    Telegram id, not a credential, and `preflight` prints it by design.
- Attribution rule: if two question rows land inside one 10 s poll window, the attribution check
  reports `ERROR` ("two questions in one window; counters not attributable") instead of guessing —
  the run fails and the step is repeated.
- A super-admin asker is a **precondition failure**: the checks report `NOT_APPLICABLE` and the run
  exits `2` with "ask from a non-super account" — it must not pass silently.
- Reuse of the DB across runs does not weaken the gate: every check is scoped to this run's rows and
  this run's log, and the quota stage requires a **grant period created in this run**
  (`ai_plan_grants.granted_at >= started_at`, `quota=1`, `used=1`).
- Exit codes: `0` all required checks observed; `1` timeout without them; `2` precondition failure.
- The report is JSON: `schema`, `started_at`, `duration_s`, `scenario`, `db_reused`, `checks[]`,
  `exit_code`, `redactions: ["BOT_TOKEN", "SECRETS_KEY"]`, `bot_log_lines`, `log_sha256`, `problems`.
  Never the token, never a question or an answer, and the DB itself is never uploaded.

### 2.1 Ordered sequence of the `full` scenario

The order matters: the core question is asked **before** any plan is assigned, so its charging
evidence is a clean "daily cap +1, no grant row". The quota stage comes last because it leaves the
asker's grant exhausted. The media-only post is free, so it can close the run.

| # | Actor | Action in Telegram | What the supervisor must observe |
|---|---|---|---|
| 0 | runner | `--check`: env shape, numeric admin id, writable DB, `getMe` | else exit `2` |
| 1a | super (owner) | **first run only:** panel → add channel (bot must already be channel admin and a member/admin of its discussion group); `/providers` → add provider → add model → **activate** (public model) | `channels` row exists; one active public model; supervisor resolves the linked group id via read-only `getChat(channel).linked_chat_id` and prints it as a notice |
| 1b | super | **later runs (`db_mode=reuse`):** do nothing; the cache supplied the channel and model | supervisor confirms both and prints the linked group id |
| 2 | **asker (non-super)** | post in the channel with a unique test marker, then **reply to that post** in the discussion group with a question | one `done` row; `channel_id == channels.chat_id`; one `qa: answered`; asker `usage.ai` **+1**; no grant row for this run; no other user's counters moved |
| 3 | owner + asker | owner turns the channel's Q&A toggle **off** in the panel; asker replies again to the same post | `qa: ignored … reason=toggle_off`; no new row; counters unchanged. Owner turns it back **on** |
| 4 | asker | **manually forward** the same channel post into an unrelated group where the bot is admin; reply to that forwarded copy | `qa: ignored … reason=foreign_group`; no `qa_answers` row whose `chat_id` is that group; counters unchanged |
| 5 | owner + asker | owner assigns the test plan (`ai_quota=1`) to the asker **again** (same plan ⇒ expiry extends ⇒ a new grant period at `used=0`); asker replies to the original post (new message) | `done` row; a grant row created in this run with `quota=1, used=1`; asker `usage.ai` +1 |
| 6 | asker | replies to the original post **again** (new message) | row `failed` / `error=ai_limit`; **one** `qa: ai failed … err=ai_limit` line; `usage.ai` and `grant.used` **unchanged**; a further reply behaves identically — one `failed/ai_limit` row per message, never `done`, no counter movement and no provider call (no token burn) |
| 7 (optional) | asker | post a photo with **no caption**; reply to it with a question | constant `NO_TEXT` reply; row `failed` / `error=no_text`; `usage.ai` unchanged (the handler returns before any AI call) |
| 8 | runner | every required check observed, or duration reached | stop the bot, blank answer text, write the report, exit `0`/`1` |

Rationale: stages 3, 4 and 7 must leave every counter untouched (they prove the ignore paths), so
they cannot be interleaved with stages that legitimately charge. Stage 5 always starts a fresh grant
period, which makes stage 6's "exhausted and not consumed" evidence unambiguous on every run,
including reused-DB runs.

Steps 3/4/7 prove the bot **received** the message and refused it; without the new ignore log line
their only evidence would be silence, which is also what a broken bot produces.

### 3. `docs/QA_LIVE_ACCEPTANCE.md` (new, Persian runbook)

Secrets to add (`BOT_TOKEN`, `SUPER_ADMIN_IDS`, `SECRETS_KEY` — a **dedicated test bot**, so
production polling is never disturbed), how to trigger the workflow, the prerequisites (bot admin in
the channel **and** in its discussion group; a second non-super account as the asker; an unrelated
group where the bot is admin for `full`), the in-Telegram sequence exactly as in §2.1, the
`db_mode=reuse|fresh` behaviour, what evidence appears, and a troubleshooting table (bot silent ⇒
group admin / privacy mode / channel registration / model status / super asker / expired cache).

### 4. Tests — `tests/test_phase4_live_acceptance.py` (new, offline)

- Each check against a seeded temp sqlite DB: observed, not observed, "wrong channel id", and rows
  from an earlier run ignored (`created_at < started_at`).
- Reuse semantics: a second supervision over the same DB still passes, and a grant row from an
  earlier run does not satisfy the quota check.
- Attribution: two rows in one window ⇒ `ERROR`, not a guess.
- Report redaction: feed three fake secret values, assert none appears anywhere in the serialized
  report or log excerpt, and that the answer text is blanked.
- Supervisor lifecycle with a fake child process: start → poll → stop; and "timeout ⇒ exit 1";
  "super asker ⇒ exit 2".
- Workflow guards: `workflow_dispatch` only, no `pull_request`/`push` trigger, every `uses:` pinned to
  a 40-char SHA, `permissions: contents: read`, `timeout-minutes: 360`, `duration_minutes` default
  `350`, cache key/restore-keys present, no `secrets.` inside any `if:`.
- Ignore log line: each early return in `on_group_reply` emits exactly one id-only `qa: ignored` line
  with the right reason, and no question/answer text.
- Runbook guards: the three secret names and the in-Telegram steps are present.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| The bot is not an admin in the group ⇒ no updates | the runbook requires group-admin membership; the checker reports `answer_delivered=NOT_OBSERVED` with a hint |
| The channel is not registered (fresh DB) | step 1a of the runbook; the checker reports it explicitly before the wait |
| The 350-minute budget is wasted | the checker exits at once when every check is observed; the duration is only a ceiling |
| The token leaks into the log | the checker greps the log in memory for all three secrets and reports only presence/absence |
| The cache carries a provider key | the key is encrypted with a stable `SECRETS_KEY`; the cache belongs to the throwaway test bot; the report never contains the DB |
| The cache is evicted (7 days unused) | `db_mode=reuse` with no cache behaves like `fresh`; the runbook says to expect step 1a |
| The asker is the super admin | the runbook names a second account; the supervisor reports `NOT_APPLICABLE` and exits `2` |
| Telegram rate limits | one question per stage at human pace; the bot's own retry policy applies |

## Owner decisions (settled 2026-11-17)

1. The design is approved; implementation follows the plan-first, test-first workflow.
2. A second, non-super Telegram account is available as the asker.
3. Runner budget: 5h50m (default duration 350 minutes; job timeout 360, the hard cap).
4. The test database is kept across runs via the GitHub Actions cache (`db_mode=reuse` by default),
   with `db_mode=fresh` available to re-prove the registration path.

## What stays mine (builder's calls)

- The tool's CLI shape, exit codes, report schema, poll interval and artifact retention.
- The ignore log line's exact wording/reason set (after the failing test).
- The cache key/prefix and the "blank answer text before saving" step.
- Keeping the workflow manual-only and separate from `ci.yml` so the required `test` check and the
  Phase 9 harness stay untouched.
