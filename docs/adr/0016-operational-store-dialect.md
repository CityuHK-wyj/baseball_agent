# Operational store as one dialect-agnostic SQL implementation

Status: accepted

## Context

D055 and O005 require an Operational PostgreSQL control plane separate from the baseball
analytics data plane. Only a SQLite implementation existed. Rather than write a second,
divergent store, the logic should be shared and only the dialect should differ.

## Decision

- `app/persistence/store.py` now has `SqlOperationalStore`, a dialect-agnostic base:
  positional row parsing, an `ON CONFLICT ... DO UPDATE` upsert (supported by both
  SQLite and PostgreSQL), `created_at` id-based ordering (no `rowid`/`BIGSERIAL`
  dependency), and a per-subclass `placeholder`.
- `SqliteOperationalStore` (`placeholder="?"`) is the local/dev and test implementation.
  `PostgresOperationalStore` (`placeholder="%s"`) accepts an **injected DB-API
  connection**, so callers own credentials, pooling and transactions.
- Ordering by `created_at` plus id keeps `latest_checkpoint` deterministic on both
  engines without an autoincrement column.

## Alternatives

- A separate hand-written Postgres store: rejected; it would duplicate versioning and
  parsing logic and risk divergence.
- Requiring a live PostgreSQL for tests: rejected; the environment has none and the
  brief allows fakes plus an explicit UNVERIFIED_LIVE marker.
- A `BIGSERIAL seq` ordering column: rejected as unnecessary; timestamp ordering with an
  id tiebreak is portable and deterministic here.

## Consequences

- Existing SQLite tests still pass unchanged; the Postgres SQL contract is asserted with
  a recording fake.
- **UNVERIFIED_LIVE:** no row has been written to or read from a real PostgreSQL through
  this store. A live integration test remains a documented follow-up.
- Sub-second checkpoint creation relies on ISO microsecond timestamps plus the
  checkpoint id tiebreak for ordering; a collision would only affect ordering between
  checkpoints created in the same microsecond.
