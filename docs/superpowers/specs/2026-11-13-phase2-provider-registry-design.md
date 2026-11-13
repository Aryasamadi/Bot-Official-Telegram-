# Phase 2 — Provider-centric model data + backend (design)

**Date:** 2026-11-13 · **Owner instruction:** "فاز ۲ را کامل طبق رودمپ شروع کن" · **Status:** approved by owner (option A on migration; remaining calls delegated to the implementer)

## 1. Intent and why

Today every AI model is a **flat row**: `ai_models(name, kind, base_url, api_key, model, …,
owner_id)`. One row mixes four different things — *who the provider is* (host + key), *which model*,
and *how the bot should call it* (priority / temperature / max_tokens). Consequences:

- the same key/host is duplicated across every model row, so rotating a key means editing N rows;
- there is no place to test a provider independently of a model, and no place to store "when did we
  last reach this host";
- discovering which models a provider offers is impossible from the UI — the admin must know model
  ids by heart.

Phase 2 introduces an **independent provider** entity, keeps models pointing at it, and adds the two
operations that were impossible before: **connection test** and **model discovery** (with manual
fallback). This is the data foundation Phase 3 (round-robin) and Phase 4/5 (Q&A) build on.

## 2. Goals / non-goals / anti-goals

**Goals**

1. `ai_providers` table: one row per (owner scope × host × key), with encrypted key.
2. `ai_models.provider_id` — additive, nullable, set by migration and by every new insert.
3. Connection test: real HTTPS call to the provider's model list, short timeout, SSRF guard, no key in logs.
4. Model discovery: parse the provider's response into candidate model ids; **manual entry always works**.
5. Super-admin command surface to add / test / discover / create models / delete providers.
6. Full test coverage of the new paths, including the migration itself on a pre-Phase-2 database copy.

**Non-goals**

- Round-robin rotation, atomic cursor, per-request audit — those are **Phase 3** (ADR-3/ADR-4).
- Any change to *how* an article is published, scored or retried.
- Changing the on-the-wire request format (the existing `kind` / URL logic is reused verbatim).
- Multi-tenant isolation beyond the existing `owner_id` semantics (super-admin = `NULL`).

**Anti-goals**

- No rewrite of `ai_models.py`'s call path.
- No destructive migration: `ai_models.base_url` / `api_key` columns stay (rollback path), they simply
  stop being the source of truth once `provider_id` is set.
- No key, token or secret in a log line, test fixture, doc or the state file — names and presence only.

## 3. Constraints

| Constraint | Value |
|---|---|
| Schema version | 9 → **10**; additive only (`CREATE TABLE`, `ALTER TABLE ADD COLUMN`), idempotent |
| D1 compatibility | the migration must run on both SQLite and Cloudflare D1 (no `ALTER … DROP`, no `IF NOT EXISTS` on columns) |
| Encryption | the existing newsbot/secret_store.py via SECRETS_KEY; never store a plaintext key |
| Egress | AI-provider requests go through `net_http`; the test must reuse the same SSRF / private-host guards |
| Delivery | squash only, one PR per merge, review gate lifted + restored per merge (§7.1 of the state file) |
| Language | code / comments / UI strings follow the repo: Persian UI text, English identifiers |

## 4. Approaches considered

**A. New table + additive FK (chosen).** `ai_providers` owns host + key + test state; `ai_models.provider_id`
points at it. Existing rows keep working while un-migrated; the migration fills `provider_id` by
grouping on `(owner_id, base_url)`.

- *Trades off:* one nullable column and a join in the read path; the two key columns stay until Phase 10.
- *Why:* it is the only option that satisfies "additive, rehearsed, revertable" without a write freeze.

**B. Replace `base_url`/`api_key` in place with a provider id.** Rejected: it forces a destructive
migration on a live DB, breaks the rollback path, and the D1 variant cannot drop columns.

**C. Keep one table, add a `provider_key` text column for grouping.** Rejected: it does not remove the
duplication (each model still carries host + key), and "test the provider" still has no single row to
report on. It also cannot store provider-level state (last test, error, enabled flag).

**Owner's ruling on the migration:** option **A with auto-migration** — existing models are grouped by
`(owner_id, base_url)` and re-pointed to a generated provider; no admin action is required for the bot
to keep publishing.

## 5. Decisions made by the implementer (owner delegated these)

| # | Decision | Reason |
|---|---|---|
| C1 | `ai_providers(id, owner_id, name, kind, base_url, api_key, enabled, last_test_at, last_test_ok, last_test_error, created_at)` | provider-level state needs a home; `kind` is derived from `base_url` exactly as today |
| C2 | `ai_models.provider_id` nullable, `NULL` = legacy row not yet migrated | additive + reversible; the read path falls back to `base_url`/`api_key` when `provider_id IS NULL` |
| C3 | Migration is **idempotent**: the column step runs through `MIGRATIONS`, the data step is a separate idempotent pass | D1 `PRAGMA` introspection is unreliable (DB-27); `_table_cols` is reused |
| C4 | Backfill groups by `(owner_id, base_url)`; the provider takes the **first** key seen and the name `hostname(base_url)`; models keep their own `priority/temperature/max_tokens` | no behaviour change: model-level tuning stays where it is |
| C5 | Connection test = `GET {base_url}/models` (OpenAI-compatible), 15 s cap, through `net_http.fetch`, never logs the key, stores `last_test_*` | every supported kind exposes `/models`; the call is read-only |
| C6 | Discovery = parse the same response (`data[].id`, `models[].name`), show candidates, and **always** allow manual entry; nothing is inserted automatically | manual fallback is a hard requirement |
| C7 | Provider delete is blocked while models reference it | prevents a dangling `provider_id` and a silent publishing outage |
| C8 | Surface = super-admin command `/providers` (`add` · `test` · `discover` · `model` · `del`); no user-facing UI | a text command gives all five operations now; the button wizard belongs with the Phase 3 rotation UI |
| C9 | Models keep `base_url`/`api_key` columns writable but they are no longer the source of truth once `provider_id` is set | rollback stays possible without a second migration |

## 6. What the builder must produce

1. newsbot/providers.py — table access: list / get / add / update / delete, `test_connection`, `discover_models`, `add_model_from_discovery`.
2. newsbot/db.py — `ai_providers` DDL + `MIGRATIONS` entry + idempotent backfill (`_backfill_providers`).
3. newsbot/ai_models.py — `provider_id` in `add_model`; `resolve_endpoint(m)` used by the call path.
4. Super-admin command `/providers` in newsbot/ui_views_super.py + registration in newsbot/app.py.
5. tests/test_phase2_provider_registry.py — migration on a v9 fixture, idempotent re-run, backfill grouping, encryption at rest, test/discovery parsing with a stubbed transport, delete guard.
6. Docs: README provider section, §4.2 of the state file.

**Definition of done:** `pytest -q` has no *new* failures, the new tests pass, `ruff`/`pyflakes` clean,
`tools/final_verification.py` 17/17, CI `test` green, PR merged, state file updated.

## 7. Rollout and rollback

- Ship behind no flag: the feature is additive, and a deployment with an un-migrated DB still works
  (models fall back to their own columns).
- Rollback = revert the squash commit; the DB keeps `ai_providers` + `provider_id` (harmless), and the
  old code ignores both.