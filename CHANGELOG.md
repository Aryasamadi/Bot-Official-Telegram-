# Changelog

All notable changes to **NewsBot** are documented in this file.

## [4.3.0] — 2026-11-12
### Added
- **Phase 1 — Operational Stability & WAL-Safe Restore:**
  - `tools/restore_sqlite_backup.py`: WAL-safe SQLite restore utility (`OPS-011`, `OPS-012`).
  - Independent `systemd` `READY=1` notification prior to Telegram Bot API calls (`OPS-014`).
- **Phase 2 — Financial, Publication & Pipeline Correctness:**
  - Atomic per-channel `daily_posts_cap` reservation in `reserve_publication` (`PIPE-017`).
  - Timezone change `carry_usage_day` carries `ai` usage counters (`PIPE-018`).
  - Post-send `settle_publication` retry with `settle_failed` journal state (`PUB-002`).
  - `tools/allocate_legacy_wallet.py` & `wallet_allocate_legacy` for legacy `users.credit` migration (`FIN-025`).
  - Outbound HTTP IP pinning at TCP connect time (`newsbot/pinned_transport.py`, `NET-003`).
  - Extraction deadline (`EXTRACT_ARTICLE_DEADLINE_SEC`, `PIPE-011`) and deterministic query-parameter sorting (`PIPE-014`).
  - Discovery politeness (`_host_pace`), `DISCOVER_PROBE_BUDGET`, and `robots.txt` enforcement (`PIPE-013`).
- **Phase 3 — Observability, Database Integrity & Repository Hygiene:**
  - Prometheus text metric export (`render_prometheus_metrics`, `export_prometheus_metrics`, `OBS-001`).
  - Correlation IDs on all `log.*` records (`OBS-003`), rotating file / JSON logging (`OBS-008`), and standardized `error_class` classification (`OBS-010`).
  - Active lock & orphan row inspection in `/health` and cascading `pub_journal` cleanup (`OBS-014`, `DB-007`).
  - Repository governance (`CODEOWNERS`, `dependabot.yml`, `CHANGELOG.md`, `LICENSE`, `OPS-023`) and semantic bilingual `fa`/`en` parity checks (`TEST-010`).
