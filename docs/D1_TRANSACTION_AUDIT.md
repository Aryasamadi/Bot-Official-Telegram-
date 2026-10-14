# D1 Transaction Readiness Audit

**Status: D1 is not yet certified for production use. Do not switch `DB_BACKEND=d1` or run the migration against production until the items below are resolved and tested against a real D1 database.**

> Adapted from the draft in PR #1 so every statement below matches the current `main`
> code (batch-based `_trx`, atomic referral crediting, cursor/last_row_id fixes).

## Why this matters

The Python D1 adapter (`newsbot/db.py`, `D1Conn`) sends each `execute()` as a separate
Cloudflare REST request. On that path `BEGIN`/`COMMIT`/`ROLLBACK` are no-ops and each
statement auto-commits. Interactive transactions do not exist over the REST API.

`_trx(fn)` does **not** use that path for writes: on D1 it collects `fn`'s statements in
`D1BatchConn` and flushes them as a single `batch` request, which Cloudflare documents as
all-or-nothing. That gives `_trx` callers atomicity, subject to one caveat: the guarantee
comes from the batch API's semantics (see the Cloudflare links below) — it is exercised
only by simulated tests (`tests/test_trx_atomicity.py`), never against a real D1 database.

- https://developers.cloudflare.com/d1/worker-api/d1-database/
- https://developers.cloudflare.com/api/resources/d1/subresources/database/methods/query/

## Event-loop blocking and request amplification

The adapter uses synchronous `httpx.Client.post()` for D1 calls while the Telegram
application is asynchronous. A remote D1 round trip therefore blocks the event loop, and
multi-step operations multiply HTTP round trips. This is a responsiveness/throughput
blocker independent of transaction correctness.

A production D1 design needs an explicit async database boundary or a durable local write
queue/outbox with clearly defined consistency semantics. Wrapping individual HTTP calls in
a thread would not solve the transaction problem.

## Multi-statement operations that must stay transaction-safe

Every operation below touches more than one row/table. Each must either run inside
`_trx` (single atomic batch on D1) or be redesigned as one provably atomic statement
(single statement / trigger). Any code path that sequences plain `q()` calls outside
`_trx` is **not** atomic on D1 and must be reviewed before enabling the backend:

| Location | Operation | Risk if a later statement fails |
| --- | --- | --- |
| `newsbot/core_articles.py` — `release_publication` | Release quota, update article reason, update publication journal | Quota may be released without journal state changing; retry may release it again |
| `newsbot/core_articles.py` — `reserve_publication` | Reserve post/test quota and write publication journal | Partial quota reservation can be left behind without a journal |
| `newsbot/core_articles.py` — `settle_publication` | Mark dedup/post state, mark article published, delete journal | States can diverge; recovery helps but does not make the writes atomic |
| `newsbot/core_channels.py` — `transfer_channel` | Delete old owner's source/queue/token/dedup data, then change owner | Irreversible partial data deletion before ownership changes |
| `newsbot/core_channels.py` — `reset_channel_queue` | Delete queue and update channel settings | Queue may be deleted while automation settings remain unchanged |
| `newsbot/core_channels.py` — `del_channel` | Delete related rows, then delete channel | Channel may remain while related data has already been removed |
| `newsbot/core_plans.py` — `apply_plan_change` | Adjust wallet credit and insert plan-change audit row | Wallet can change without a matching audit record |
| `newsbot/ui_views_super.py` — payment rollback callback | Restore wallet credit and delete plan-change record | Accounting rollback may be partial |

## Improvements already in main

- Referral crediting is one conditional `UPDATE ... RETURNING` claim; a SQLite/D1 trigger
  (`trg_referral_credit_wallet`, schema v3) updates the wallet inside the same statement —
  no read-then-write race, no double credit under concurrent confirmations
  (tests: `tests/test_phase1_fixes.py`).
- `_trx` on D1 flushes a single atomic `batch` (tests: `tests/test_trx_atomicity.py`).
- D1 cursor `fetchone()`/`fetchall()` advance exactly like sqlite3.
- D1 INSERT ids come from `meta.last_row_id` of the same response — no separate
  `last_insert_rowid()` round trip (that value belongs to a connection session and is
  meaningless across REST requests).
- The SQLite→D1 migration tool creates `meta`, installs the referral trigger, and verifies
  data with canonical row-content fingerprints (not only row counts).

## Required next step

Audit each site in the table above: pull it inside `_trx` or prove it single-statement,
then test against a real D1 database. Do not assume REST-batch atomicity transfers from
the Workers binding docs without verifying the REST endpoint's own guarantee.

The current CI suite runs against SQLite with dummy configuration. It is useful
regression coverage, but it is **not** a real-D1 integration test.
