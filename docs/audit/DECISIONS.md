# DECISIONS — audit-time decisions and open questions

This file records (a) decisions the **project** has already made and this audit respects, (b) decisions the
**audit** made while investigating, and (c) decisions that belong to the **owner** and are currently open.

**No phase has been executed. There are no owner-approved code changes yet.** Every proposal below is a
recommendation, not an action.

---

## 1. Existing project decisions (verified in the code, respected by this audit)

### D-01 — Cloudflare D1 is officially unsupported; production stays on SQLite
- **Where:** `docs/D1_DECISION.md:8-16`; `newsbot/config.py:84` (`DB_BACKEND` default `sqlite`).
- **What it means:** the D1 findings (DB-001…DB-004, FIN-010, FIN-011, TEST-006) are graded **MEDIUM**, not
  CRITICAL, because the backend is not used in production.
- **Consequence for the audit:** the D1 defects are still reported — the code path exists, the documentation
  claims atomicity that the code does not deliver (`db.py:314-315`), and the "already fixed" claim for DB-01 is
  only half true (the batch path was never fixed). The cheapest correct action is to make the *documentation*
  honest and to refuse `DB_BACKEND=d1` at start-up, rather than to repair a backend nobody runs.

### D-02 — A "sent but unrecorded" publication is treated as a LOST post, never a duplicate
- **Where:** `newsbot/core_articles.py:149-191`; `newsbot/publish.py:342-359`.
- **Trade-off:** because the Telegram Bot API has no idempotency key, "the message was delivered but the row
  was not written" is indistinguishable from "the message was not delivered". The project resolves it
  conservatively: `delivery_unknown` ⇒ failed, quota kept, never recycled, never re-queued.
- **Cost:** a rare lost post. **Benefit:** a guaranteed no-duplicate guarantee, which is the correct priority
  for a channel bot.
- **Audit position:** **KEEP.** PUB-001 is recorded as an accepted trade-off, not a defect.

### D-03 — The referral wallet credit is implemented as a database trigger
- **Where:** `newsbot/db.py:82-94`.
- **Why it matters:** the balance change and its receipt are atomic *by construction*, with no read-then-write.
  This is the single best financial-control decision in the codebase.
- **Audit position:** **KEEP** and extend the pattern (see D-05).

### D-04 — The dual-currency tariff never falls back across units
- **Where:** `newsbot/core_plans.py:151-159` (`plan_num`), pinned by `tests/test_phase14_payment_ui.py:28-36`.
- **Why it matters:** `price_num` (USD, `en`) and `price_num_fa` (Toman, `fa`) are kept strictly separate.
- **Audit position:** **KEEP.** The currency-mixing defect is therefore confined to the wallet (FIN-001), which
  makes the fix tractable.

### D-05 — Proposed (audit recommendation, needs owner approval): the wallet must carry a currency
- **Proposal:** add a currency discriminator to `users.credit` (and to `credit_log`/`referrals.credit_amount`),
  or maintain per-currency balances; only credit whose unit matches the tariff unit may be spent.
- **Alternatives considered and rejected:**
  - *Convert at an FX rate* — rejected: the repository has no FX table, no rate source and no rate timestamp;
    inventing one would introduce a second source of truth and a daily-rate dependency.
  - *Restrict referrals to same-language pairs* — rejected as a product decision, not a technical one; it
    would silently disable the referral programme for a legitimate mixed-language audience.
  - *Store the reward in the referrer's unit at credit time* — **the preferred interim fix**: it requires only
    a conversion at the moment of crediting, which can be an explicit, visible, rate-free 1:1 declaration of
    "the reward is denominated in the referrer's tariff unit". The owner must decide the rate policy; this
    audit will not invent one.
- **Consequence:** a `SCHEMA_VERSION` bump, an additive migration, and a **manual decision on the existing
  balances**, whose currency is currently unknown. Freezing (not converting) existing balances is the safe
  default.
- **Status:** **OPEN — owner decision required before PH-01.**

### D-06 — Proposed (audit recommendation, needs owner approval): re-derive the quote at approval, freeze only the cash price
- **Proposal:** split the frozen quote into two contracts — the **cash price** is frozen at request time (so
  the customer is charged what they were quoted), while the **wallet share** is re-derived at approval from
  the live balance. If the derived amount differs from `final_price`, the approval is refused and the
  transaction is re-quoted.
- **Why:** FIN-002 (frozen wallet share against a changed wallet) and FIN-003 (two pending requests) both
  stem from freezing too much.
- **Alternative considered and rejected:** *re-quote everything* — rejected because the customer must be
  charged the price they were shown; silently changing it is worse than refusing the approval.
- **Consequence:** the existing test `tests/test_payment_atomicity.py::test_approve_uses_persisted_final_price_not_requote`
  (which pins the current contract) must be updated **deliberately**, and the "frozen" wording in the
  documentation must change.
- **Status:** **OPEN — owner decision required before PH-01.**

### D-07 — `run.py --check` is read-only, and the pre-restart deploy gate depends on it
- **Where:** `newsbot/app.py:126-139`, `newsbot/db.py:463-506`.
- **Audit position:** **KEEP.** It is verified by `tests/test_deployment.py:31-59`. The gate ordering in
  `deploy/deploy.sh` (backup → sync → gate → restart → health) is correct; only the gate's *budget* and
  *journal window* are wrong (OPS-002, OPS-003).

### D-08 — Deferred: secrets at rest (SEC-003…SEC-006)
- **Why deferred:** SEC-005 (an unsalted SHA-256 KDF) and SEC-003 (customer source-API keys in plaintext) would
  change the on-disk format of existing key material. Migrating existing ciphertext needs a versioned prefix
  and a compatibility path; a mistake would make stored AI keys **unusable** — a production outage of every
  AI feature.
- **Alternative considered and rejected:** *fix SEC-005 now* (use scrypt) — rejected for the same reason: it
  invalidates every existing ciphertext unless a version prefix is introduced, and the owner has not been
  asked. SEC-004 (WAL/sidecar permissions) is a one-line fix and is *not* blocked by this decision — it is
  deferred only because it sits in the same file as the other three.
- **Status:** **OPEN — needs a dedicated "secrets at rest" phase, not yet authorised.**

### D-09 — The AI-provider egress must be validated like the source and media egress
- **Proposal:** route `ai_models.http_post` through the same guarded requester as `net_http.fetch`
  (`public_url_strict` per hop, `follow_redirects=False`, credential headers stripped on a host change).
- **Alternative considered and rejected:** *validate only at input time* — rejected because that is the current
  state and it is bypassable by a redirect (SEC-001). Input-time validation is necessary but not sufficient.
- **Consequence:** `ALLOW_PRIVATE_MODEL_HOSTS` must become an allowlist rather than a blanket switch (SEC-007);
  local providers (Ollama / LM Studio) must keep working, which the allowlist preserves.
- **Status:** proposed in PH-02; owner approval required.

### D-10 — The bot runs as a single process, guarded by an advisory file lock
- **Where:** `newsbot/app.py:173-193`.
- **Audit position:** **KEEP.** The single-process assumption is what makes the per-channel locks, the
  publication journal and the SQLite CAS statements sufficient. Scaling out would require a real distributed
  lock; that is out of scope for this programme and is recorded as a future constraint (T-domain).
- **Note:** the lock is bypassed on the D1 backend (`app.py:179` returns early) — harmless while D1 is
  unsupported, but it must be re-examined if D1 is ever re-enabled.

### D-11 — The scheduler drops an overlapping tick instead of queueing it
- **Where:** `newsbot/scheduler.py:78-79`.
- **Trade-off:** accepted drift under load, in exchange for no tick pile-up and no unbounded memory.
- **Audit position:** **KEEP**, and make the drop **observable** (a counter/log) so that "the bot is not
  running cycles" can be distinguished from "the bot is dropping ticks" (part of PH-06).

### D-12 — Audit methodology decisions (taken by the auditor, recorded for transparency)

| Decision | Rationale |
|---|---|
| The audit was performed on a **fresh clone** at `main` @ `08bc876`, not on the stale local working copy at `C:\Users\Mustafa\WorkBuddy AI\2026-10-13-00-32-45\work` (branch `phase-15-watchdog`, 3 modified + 1 untracked file) | The local copy is behind `main` and has uncommitted changes; using it would have produced a misleading baseline. **It was left completely untouched.** |
| The test suite was executed with an **out-of-tree venv** and `-p no:cacheprovider` | Nothing was installed and nothing was written into the repository. `git status --porcelain` was verified empty afterwards. |
| Destructive database experiments used **throwaway temp databases** in `%TEMP%` | No production or repository database was ever opened for writing. |
| The suspected "uncommitted writes" defect (10 `q()` write sites without `commit=True`) was **disproved empirically** and **not** filed | The flag was raised by a single-line static heuristic; the actual call sites pass `commit=True` on a continuation line. A temporary harness proved all writes are committed. Reporting it would have been a false positive. |
| `docs/PERFORMANCE.md` was **not** re-measured and is marked unverified | The audit host lacks some dependencies and no network; claiming a re-measurement would have been false. |
| Production claims are marked **UNVERIFIED** rather than assumed | No VPS access. "Is production running 08bc876?" is an open question, not an assumption. |
| The severity of the DB-001 finding was raised to **HIGH** although D1 is unsupported | The finding is not about D1's use in production; it is about the code claiming a fix that is only half applied — the *documentation* of the fix is wrong. |
| The D1 migration-atomicity finding (DB-002) was raised to **HIGH** | The function's own docstring makes an atomicity guarantee the code does not deliver on one supported path; that is a documentation-correctness defect as much as a code defect. |
| No finding was raised for "the bot is a single host with no off-host backup" | It is recorded as a *risk* in the T-domain and in OPS-009, not as a defect: the current design may well be intentional for a one-operator product. |

---

## 2. Owner decisions required before PH-01 can start

| # | Question | Why it blocks | Default if the owner does not answer |
|---|---|---|---|
| **Q-1** | How should a reward earned in one currency be denominated in the referrer's wallet? (a) explicit conversion at a stated rate; (b) restrict the reward to same-language pairs; (c) per-currency balances with no conversion | D-05 | (c) per-currency balances, existing balances **frozen** in a "legacy / unknown" bucket — the only option that cannot lose money |
| **Q-2** | At approval, if the wallet changed since the request, should the approval be **refused** (re-quote) or **proceed with the re-derived amount**? | D-06 | Refuse and re-quote — it never charges a number the customer was not shown |
| **Q-3** | Should the 20 % referral reward remain **per purchase** (current behaviour) or become **once per referred user**? | FIN-023, the super-admin panel wording | Keep the current behaviour and fix the documentation (no code change) |
| **Q-4** | Is the seeded paid plan's free-text price ("توافقی / Contact us") intentional, or should a numeric tariff with a currency unit be mandatory? | UI-017 | Keep the free text but require a unit when a numeric tariff is set; do not change the seeded offer without an explicit instruction |
| **Q-5** | Is `ALLOW_PRIVATE_MODEL_HOSTS` used in this installation (local Ollama / LM Studio)? | SEC-007, the allowlist migration | Assume it may be used; keep an `=all` escape hatch and document it |
| **Q-6** | Should the deployment identity be renamed to `newsbot.service` / `/opt/bot-official-telegram`, or should the existing `bot-ai-automatic` identity be kept and the *intent* corrected? | OPS-001 | **Read the host first (PH-00)**, then rename only if the host confirms the intended identity; support both via `SERVICE=` during the transition |

---

## 2b. Decisions taken during the PH-00 verification pass (2026-10-20)

### D-13 — The `bot-ai-automatic` identity is **not** renamed yet
- **Decision:** the deploy identity, the systemd unit name and the install path are left **exactly as they
  are**, even though the repository's own project identity is `newsbot.service` /
  `/opt/bot-official-telegram` and the strings `newsbot.service` and `bot-official-telegram` appear **zero**
  times in the tree.
- **Rationale:** the production host could not be read (no SSH access, no `gh` CLI, no VPS environment
  variables — see `FINDINGS.md` §13.1). The live host may genuinely run `bot-ai-automatic`; renaming the unit
  in the deploy chain without reading it would make a **currently working service unstartable** and would
  also break the "restart the old unit" rollback path. The rename is therefore **blocked on the PH-00
  production read**, not merely "not started yet".
- **What was done instead:** the repository-side evidence was re-verified mechanically and the evidence set
  was completed (the `deploy/systemd/10-restart-always.conf:7` logger tag was missing from the original
  register and has been added), so that the PH-00 read has a complete checklist to confirm or refute.

### D-14 — The project's own `ROADMAP.md` is added to the repository **verbatim**
- **Decision:** `ROADMAP.md` (228 780 bytes) is committed to the repository root, **with the original text
  unchanged except for a five-line status note inserted directly under the title** (2026-10-20), because six
  places in the repository cite it (`docs/PERMISSIONS.md:3,98`, `docs/SECURITY.md:3,78`,
  `newsbot/ratelimit.py:20`, `tests/test_authz_matrix.py:4`) and the cited sections (§۶.۳ and §۶.۶) exist
  only in that file.
- **Why the status note rather than a byte-identical copy:** the document's own first status line reads
  «وضعیت این سند: در حال اجرا» (*status: in progress*), which was true on 2026-10-14 and is **false** today.
  Committing it unchanged would introduce a *new* documentation mismatch of exactly the class this audit
  reports. The note states that the document is historical, that the live roadmap is
  `docs/audit/ROADMAP.md`, and that the original text follows unaltered.
- **Alternatives considered and rejected:**
  - *Point the references at `docs/audit/ROADMAP.md`* — rejected: the audit roadmap is a different document
    (it covers the 181 findings) and has no §۶.۳/§۶.۶; pointing at it would be a **false** reference.
  - *Rewrite the six references to say "the project roadmap is not in the repository"* — rejected: it makes
    the text honest but leaves the traceability the project explicitly claims for its phase-3 and phase-6
    decisions permanently unreachable.
  - *Rewrite the six references to `docs/audit/DECISIONS.md`* — rejected: the audit's decision log records
    *new* decisions, not the phase-3/phase-6 ones the documents are citing.
- **Risk accepted:** the added document is the 2026-10-14 revision and describes a phase plan (phases 1–13)
  that has since been executed and merged as PRs #1–#16. It is committed **for traceability of the cited
  decisions**, not as a live plan — the live plan is `docs/audit/ROADMAP.md`, and the inserted status note
  says so at the top of the file. A secret scan over nine credential patterns returned **0 matches** before
  committing.
- **Owner note:** if the owner prefers a different location or a different treatment, this is a one-line
  revert (delete the file and adjust the six references).

### D-15 — A repository-integrity guard is added to the test suite
- **Decision:** `tests/test_repo_integrity.py` is added with three tests: the git-index executable bit for
  `deploy/deploy.sh` (OPS-028), the presence and cited sections of `ROADMAP.md` (DOC-001), and a general
  check that no backticked repository path in the tree points at a file that does not exist.
- **Design note:** the general check carries an **explicit allow-list** of the four paths the audit *proposes*
  to create (`deploy/systemd/newsbot.service`, `deploy/env.example`, `.github/CODEOWNERS`,
  `.github/dependabot.yml`). Each entry must be removed from that list when the file is actually created —
  this keeps the check honest instead of letting it silently widen.
- **Why a test and not a linter:** the project already has a static-guard precedent
  (`tests/test_static_guard.py`), the check needs no new dependency, and it runs in the existing CI gate.
- **Negative control:** removing `ROADMAP.md` was executed and made 2 of the 3 new tests fail, proving the
  tests detect the regression they claim to guard rather than passing vacuously.

---

## 3. Rejected remediation approaches (recorded so they are not re-litigated)

| Approach | Rejected because |
|---|---|
| **Revert to the pre-phase-1..15 code** to "undo" the regressions | The regressions are in features that are now load-bearing (the dual-currency tariff, the referral trigger, the publication journal). Reverting would lose working exactly-once publishing and the atomic payment approval. |
| **Rewrite the money layer in minor units in one step** | A full `Decimal`/integer refactor across `core_plans`, `core_referrals`, `core_discounts`, `core_payments` and `db` is a large blast radius for a codebase whose tests pass. FIN-015 is therefore **deferred to a later phase** and the interim fix is the ledger reconciliation (FIN-006). |
| **Make `credit_log` the authoritative balance** | It is not written on every movement (FIN-002/FIN-006) and is never read by production code. Making it authoritative before fixing the write path would make things worse. The **wallet stays authoritative**; the ledger is brought up to it. |
| **Add a `refunded` state to `pay_requests` immediately** | It is the right end state (FIN-013) but it interacts with the approval contract (D-06). It is sequenced **after** the re-quote decision. |
| **Re-enable `PRAGMA foreign_keys` in the data-integrity phase** | The production database may already contain orphan rows; enabling enforcement could make start-up fail. It is explicitly **out of scope** for PH-09 and is deferred to a later phase with a cleanup migration first. |
| **Repair the D1 backend** | D1 is officially unsupported (D-01). Repairing an unused backend is a poor use of the owner's risk budget. The recommendation is to make the documentation honest and refuse D1 at start-up. |
| **Rewrite the Telegram UI layer** | The UI is large but its problems are **localised rendering and routing defects** (UI-001…UI-026) — a rewrite would be a much larger risk than fixing 26 specific items. |
| **Add a metric/alerting platform** | The owner operates a single-host bot. A Prometheus/Grafana stack is disproportionate. PH-06 delivers the operator-visible `/health` panel, a durable notification outbox and a structured log format — the highest-value, lowest-cost step. |
| **Fix the source and media SSRF path** | It is already correct (`net_http.fetch` and `_GuardedStream`, 73 passing security tests). Changing it would introduce risk with no benefit. Only the **AI** path is changed. |
| **Enable the D1 startup guard immediately** | It changes the behaviour of an unsupported-but-configurable backend; a site that set `DB_BACKEND=d1` would stop starting. It is folded into PH-04 together with the honest documentation, and the owner is warned in PH-00. |
