# Development status

Status: IN_PROGRESS

## Baseline — 2026-09-14

- Architecture: frozen; domain contracts are being formalized incrementally.
- Branch: `codex/architecture-implementation`; local baseline: `9f2f44b`; foundation commit: `102c993`.
- Target: `https://github.com/CityuHK-wyj/baseball_agent` (verified private, empty via GitHub connector).
- Existing untracked AGENTS.md, CONTEXT.md, docs and skills were provided by the user and are preserved.
- Tests before changes: no behavioral tests; default unittest discovery fails importing `app.data` because `app.config.settings` is missing.

## Source reconciliation

Read the blog repository's `content/knowledge/{current-state.md,decisions.md,project-state.json,sources.md}` and articles 1–9. Source content is updated through 2026-09-14; generated `llms-full.txt` and published copies are stale at 2026-09-07. Use the source content, not generated copies. References: [current state](https://github.com/CityuHK-wyj/cityuhk-wyj.github.io/blob/main/content/knowledge/current-state.md), [decisions](https://github.com/CityuHK-wyj/cityuhk-wyj.github.io/blob/main/content/knowledge/decisions.md), [architecture](https://github.com/CityuHK-wyj/cityuhk-wyj.github.io/blob/main/content/posts/09-planning-artifacts-and-evidence.md).

Confirmed: D034–D066 define immutable initial requirements, definition/state separation, contextual assessments, hard gates, Planner terminal ownership, accepted-product finalization, independent operational persistence, and architecture freeze. Blog schemas/algorithms remain designs, not implementation evidence. The earlier overall article's Planner-owned decomposition and quality on Artifact are superseded by articles 4/5/9 and D048/D058–D065.

Observed implementation: four tool adapters and an unbounded LLM loop partially migrated; models/planner/validation/context/formatter remain placeholders. Feature Engine and ingestion helpers still write analytics tables; raw SQL is executed without validation; errors may expose connection details; config lacks its imported settings instance. No analytics databases or Parquet files were modified or executed.

## Security audit

Current files and all reachable local commits were scanned for provider tokens, credential assignments, connection URLs and private keys. Findings: `SECRET_REDACTED` in legacy agent, database helpers and Compose, also present in local history. This is a heuristic audit, not a guarantee that no secret exists. Real credential values are never recorded here. No local `.env` was found at audit time.

Required user action: revoke/rotate exposed LLM and database credentials. Old commits retain the exposure; no history rewrite or force push is authorized or performed. Existing history also tracks nine Parquet archives. Do not push that history into the empty remote. A clean initial publication must exclude credentials, archives, caches and historical secret blobs while preserving the original local history.

## Completed milestone 1

- Frozen validated definitions and distinct initial runtime states; append-only, idempotent supporting requirements with same-objective parent validation. Every objective must have at least one core Initial Requirement, preventing vacuous completion.
- Environment credentials have no literal defaults and are hidden from Settings repr. Current source credential scan passes; heuristic coverage includes Python assignments/dicts, provider formats, credential URLs, YAML/env, JSON and notebook source.
- Unvalidated SQL, the legacy unbounded LLM loop, ingestion/snapshot writers and archive audit bypasses fail closed. The plotting compatibility entry point now uses the blocked feature connection.
- Parquet archives and IDE files removed from the index only; local files and historical commits remain intact. No data deletion occurred.
- Validation: 15 unittest tests pass; compileall passes; CRLF-aware diff whitespace check passes. The staged-scanner integration uses an isolated temporary Git repository. History scan intentionally reports 6 exposed locations in the baseline history.
- Code review: initial Standards findings 3 (audit SQL bypass, admin plotting connection, scanner format coverage), Spec findings 2 (audit bypass, staged test missing). All resolved with regression tests; both reviewers confirmed closure, no remaining blocking findings.

## Plan and handoff

1. Formalize glossary and accepted boundary ADR; specify public testing seams.
2. TDD credential configuration/scanning and safe disabled legacy entry points.
3. TDD immutable definitions, runtime projections and requirement baseline invariants.
4. Assessment/matching → state updates → planning/terminal → routing/execution → accepted response.
5. Separate operational persistence/checkpoints and Shared Context projections.
6. Integration/adversarial SQL tests, two-axis review, documentation and safe publication.

Not yet implemented: ArtifactAssessment feedback loop, SQL AST enforcement, operational store/resume, model provider contracts and end-to-end acceptance suite. Tickets 02–05 record exact public seams and acceptance cases. Git CLI access failed after connection timeout; connector read/write access works. A clean README bootstrap was published to the empty remote as `c93953d4c7e54dfd2b98ffd7d559e5efc946e6b9`; complete safe source publication is in progress. The clean remote lineage deliberately excludes local historical secret blobs and binary datasets; local history is retained, not rewritten. Never push the old local branch ancestry.

SQLGlot is not installed. Installation from the configured package index failed both within the sandbox (DNS) and after approved escalation (no available distribution returned). Ticket 02 requires resolving this dependency before implementing SQL safety; do not restore raw execution as a workaround. Ticket 03 can progress independently. No paid API call has been made, and no real analytics schema/coverage or runtime PostgreSQL is validated.
