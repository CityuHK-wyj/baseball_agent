# Configuration

All configuration is read from environment variables at process start. Python does not
load `.env` automatically; export variables or load the file yourself. Never commit real
values — `.env` is git-ignored and `.env.example` contains names only.

## LLM provider

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `DEEPSEEK_API_KEY` | for LLM | — | Provider credential. Environment only. |
| `DEEPSEEK_BASE_URL` | no | `https://api.deepseek.com` | OpenAI-compatible base URL. |
| `DEEPSEEK_MODEL` | no | `deepseek-v4-pro` | Default model for every agent. |
| `PLANNER_MODEL` | no | `DEEPSEEK_MODEL` | Model for `LLMPlanner`. |
| `JUDGE_MODEL` | no | `DEEPSEEK_MODEL` | Model for `LLMJudge`. |
| `RESPONSE_MODEL` | no | `DEEPSEEK_MODEL` | Model for `LLMResponseComposer`. |
| `SEMANTIC_MODEL` | no | `DEEPSEEK_MODEL` | Reserved for an LLM semantic extractor. |
| `LLM_TIMEOUT_SECONDS` | no | `30` | Per-request timeout. |

The deterministic Planner/Judge/Response are the default; LLM implementations are an
explicit choice and fall back to the deterministic ones on failure. No provider SDK is
imported by the domain layer.

## Analytical (read-only) data plane

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `POSTGRES_HOST` | for hot data | `127.0.0.1` | Analytics PostgreSQL host. |
| `POSTGRES_PORT` | for hot data | `5433` | Analytics port. |
| `POSTGRES_DB` | for hot data | `baseball_analytics` | Analytics database. |
| `POSTGRES_USER` | for hot data | `baseball_readonly` | Must be a read-only role. |
| `POSTGRES_PASSWORD` | for hot data | — | Environment only. |
| `POSTGRES_ADMIN_PASSWORD` | no | — | Used only by the local Compose admin user. |
| `PARQUET_ARCHIVE_PATH` | for cold data | `./data_loader/parquet_archive` | Root that DuckDB reads are sandboxed to. |

These resources are **read-only** for the agent runtime.

## Operational (control plane) storage

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `ARTIFACT_STORAGE_PATH` | no | `./.runtime/artifacts` | Where artifact payloads are written. |
| `OPERATIONAL_STORE_PATH` | no | `./.runtime/operational.db` | SQLite operational store (local/dev). |

Production uses `PostgresOperationalStore` (an injected DB-API connection) instead of the
SQLite store; see [databases.md](databases.md).

## Budgets and rounds

`max_rounds` and `budget` are constructor arguments of `Orchestrator` and
`AnalysisPipeline` (defaults `3` and `10`). They bound planning and execution so a run
cannot loop forever. There is no environment variable for them yet.

## Precedence

```text
System Policy  >  User hard constraint  >  Source capability / Source Mapping
               >  Planner preference    >  Router optimization
```

A Planner source preference is never a constraint; the Router may override it but never
policy or a user constraint.
