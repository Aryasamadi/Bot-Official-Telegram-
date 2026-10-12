# D1 Transaction Readiness Audit

**Status: D1 is not yet certified for production use. Do not switch `DB_BACKEND=d1` or run the migration against production until the items below are resolved and tested against a real D1 database.**

## Why this matters

The current Python D1 adapter sends each `execute()` as a separate Cloudflare REST request. Its `BEGIN`, `COMMIT`, and `ROLLBACK` handling is a no-op. Therefore, a callback passed to `_trx(fn)` is **not** an atomic transaction under D1, even though it is atomic under SQLite.

Cloudflare explicitly documents rollback semantics for the Workers binding's `D1Database.batch()`. The REST API documents its batch request format, but that is not enough to assume the same all-or-nothing guarantee:
- https://developers.cloudflare.com/d1/worker-api/d1-database/
- https://developers.cloudflare.com/api/resources/d1/subresources/database/methods/query/

## Event-loop blocking and request amplification

The adapter uses synchronous `httpx.Client.post()` for D1 calls, while the Telegram application is asynchronous and invokes the shared synchronous `q()` interface from handlers and scheduled work. A remote D1 round trip therefore blocks the event loop. Multi-step operations also multiply the number of HTTP round trips. This is a responsiveness/throughput blocker independent of transaction correctness.

A production D1 design needs an explicit async database boundary or a durable local write queue/outbox with clearly defined consistency semantics. Simply wrapping individual HTTP calls in a thread and waiting for each result would still block the caller and would not solve the transaction problem.

## Remaining multi-statement transaction sites

| Location | Operation | Risk if a later statement fails |
| --- | --- | --- |
| `newsbot/core_articles.py` — `release_publication` | Release quota, update article reason, update/delete publication journal | Quota may be released without journal state changing; retry may release it again |
| `newsbot/core_articles.py` — `reserve_publication` | Reserve post/test quota and write publication journal | Partial quota reservation can be left behind without a journal |
| `newsbot/core_articles.py` — `settle_publication` | Mark dedup/post state, mark article published, delete journal | Some states can diverge; recovery helps but does not make the writes atomic |
| `newsbot/core_articles.py` — `cleanup` | Several TTL deletes and cache cleanup | Usually retryable/idempotent, but not one atomic cleanup |
| `newsbot/core_channels.py` — `transfer_channel` | Delete old owner's source/queue/token/dedup data, then change owner | Irreversible partial data deletion before ownership changes |
| `newsbot/core_channels.py` — `reset_channel_queue` | Delete queue and update channel settings | Queue may be deleted while automation settings remain unchanged |
| `newsbot/core_channels.py` — `del_channel` | Delete related rows, then delete channel | Channel may remain while related data has already been removed |
| `newsbot/core_plans.py` — `apply_plan_change` | Adjust wallet credit and insert plan-change audit row | Wallet can change without a matching audit record |
| `newsbot/ui_views_super.py` — payment rollback callback | Restore wallet credit and delete plan-change record | Accounting rollback may be partial |

## Improvements already made in this branch

- Referral crediting no longer uses a multi-statement `_trx` callback. A conditional `UPDATE ... RETURNING` claims the referral, and a SQLite/D1 trigger updates the wallet as part of the same SQL statement.
- The referral trigger is installed by normal schema initialization and by the SQLite-to-D1 migration tool.
- D1 cursor `fetchone()`/`fetchall()` behavior now matches SQLite.
- D1 insert IDs use `meta.last_row_id` from the same REST response.
- Migration verification compares canonical row-content fingerprints, not only row counts.

## Required next step

Implement and test a transaction-safe path for the remaining operations. Viable approaches include a narrowly authenticated Cloudflare Worker that uses D1's binding-level transaction support for named operations, or carefully designed single-statement/trigger-based operations where the invariants can be proven. Do not replace `_trx` with a REST batch and assume it is transactional without an explicit Cloudflare guarantee.

The current CI suite runs against SQLite with dummy configuration. It is useful regression coverage, but it is **not** a real-D1 integration test.
