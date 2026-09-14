# Databases

There are two planes, and they must stay separate.

## Data plane (read-only analytics)

| Store | Holds | Access |
| --- | --- | --- |
| PostgreSQL `baseball_analytics` | recent Statcast (hot) | read-only |
| Parquet archive (`PARQUET_ARCHIVE_PATH`) | historical Statcast (cold) | read-only via DuckDB |

The agent runtime may **only** read these. `INSERT`, `UPDATE`, `DELETE`, DDL, `COPY TO`,
`ATTACH`, extension loading and filesystem writes are rejected by the AST guard before a
connection is opened. See [security.md](security.md).

## Control plane (agent runtime, writable)

| Store | Implementation | Holds |
| --- | --- | --- |
| Operational store | `SqliteOperationalStore` (local/dev), `PostgresOperationalStore` (production) | runs, objectives, requirements, plans, states, artifact metadata, assessments, reports, checkpoints |
| Artifact storage | `LocalFilesystemArtifactStorage` (S3/MinIO later) | large artifact payloads, content-addressed |

The operational store never holds analytics data, and the analytics database never holds
agent runtime tables.

## Why SQLite exists

`SqliteOperationalStore` is the **local/dev and test** implementation of the
`OperationalStore` Protocol. It is the default because it needs no server, which keeps the
project runnable and the tests hermetic. It is not a silent replacement for production:
production should inject a PostgreSQL connection into `PostgresOperationalStore`.

Both share one `SqlOperationalStore` base, so versioning, upsert and row parsing are
identical across engines. `PostgresOperationalStore` accepts an injected DB-API
connection; the application owns credentials and pooling.

```python
import psycopg2
from app.persistence.store import PostgresOperationalStore

store = PostgresOperationalStore(psycopg2.connect(dsn_from_environment()))
```

Schema is created idempotently on construction (`CREATE TABLE IF NOT EXISTS objects`,
`checkpoints`, plus indexes). There is no separate migration tool yet.

## What is verified

- SQLite: round-trip, versioning, checkpoints, file reopen — tested.
- PostgreSQL: the emitted SQL and parameter style are tested with a recording fake.
  **UNVERIFIED_LIVE** — no row has been written to a real PostgreSQL from this repository.

## Cold/hot boundary

Cross-year questions may span the Parquet archive (cold) and PostgreSQL (hot). The Router
selects a source; the analytical layer must unify types and avoid double-counting boundary
days. Coverage of each source is not yet declared to the Router (see the matrix).
