# Baseball Agent v0.1 — usage

This is the end-to-end guide for running the agent in the current WSL environment:
environment activation, required variables, database requirements, commands, output
reading, inspection and tests. It assumes the repository at
`/home/158112/baseball_agent/baseball_agent`.

For architecture see [`docs/adr/0022-dual-semantic-runtime.md`](adr/0022-dual-semantic-runtime.md)
and [`CONTEXT.md`](../CONTEXT.md).

## 1. Activate the environment

```bash
cd /home/158112/baseball_agent/baseball_agent
source /root/.virtualenvs/baseball_agent/bin/activate
python3 --version    # verified against Python 3.14
```

The virtual environment already contains the pinned dependencies from
`requirements.txt`.

## 2. Required runtime environment variables

Export placeholders only — never commit real credentials.

```bash
# LLM (semantic extractor + reviewer, and the optional Planner/Judge/Response seams)
export DEEPSEEK_API_KEY='<your API key>'
export DEEPSEEK_BASE_URL='https://api.deepseek.com'   # default

# Dual semantic roles (optional; independently configurable, provider-agnostic).
# Both default to a fast interactive model when unset.
export SEMANTIC_EXTRACTOR_MODEL='deepseek-chat'
export SEMANTIC_REVIEWER_MODEL='deepseek-chat'
export LLM_TIMEOUT_SECONDS='30'

# PostgreSQL analytics data plane (read-only)
export POSTGRES_PASSWORD='<baseball_readonly password>'
# Defaults: POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5433
#           POSTGRES_DB=baseball_analytics POSTGRES_USER=baseball_readonly
```

The application reads `os.environ` directly. `.env.example` documents every variable;
values there are never loaded automatically. If `DEEPSEEK_API_KEY` is absent the agent
still runs, but only through the deterministic high-confidence semantic path.

## 3. Database requirements

* Docker/PostgreSQL must be running (`docker compose up -d`) and reachable at
  `127.0.0.1:5433`, database `baseball_analytics`.
* The runtime user is `baseball_readonly`. The agent refuses to use any other runtime
  user for analytics.
* The historical Parquet archive must exist under `data_loader/parquet_archive/`
  (`mlb_statcast_*.parquet`, 2015–2023).
* The shared knowledge store is created/seeded on first use under
  `.runtime/knowledge.db`.

Safe connectivity test without printing secrets:

```bash
python3 -m app.cli doctor
# or
python3 scripts/smoke_test.py
```

`doctor` checks config, PostgreSQL connectivity and read-only identity, the Parquet
archive, the knowledge store and model reachability, and prints a concise PASS/WARN/FAIL
report. It performs no writes and never prints credentials.

## 4. Run the agent

The CLI is `python3 -m app.cli`. All examples below work from the repository root with
the environment activated.

### Simple knowledge query

```bash
python3 -m app.cli ask "DFA是什么意思？"
```

### Player/team query

```bash
python3 -m app.cli ask "道奇属于哪个分区？"
```

### Real analytics query (PostgreSQL, 2025)

```bash
python3 -m app.cli ask \
  "top 5 by maximum exit velocity on fastballs at least 95 mph with at least 20 BBE in 2025"
```

### Historical analytics query (Parquet, 2023)

```bash
python3 -m app.cli ask \
  "top 5 by maximum exit velocity on fastballs at least 95 mph with at least 20 BBE in 2023"
```

### Cross-source query (2023 vs 2024)

```bash
python3 -m app.cli ask \
  "top 5 by maximum exit velocity in the regular season in 2023 vs 2024"
```

### Ambiguous query (produces a Clarification)

```bash
python3 -m app.cli ask \
  "During the 2025 regular season, on fastballs at least 95 mph in 0-2 or 1-1 counts \
near the batter-relative upper edge, rank hitters by maximum exit velocity, \
requiring at least 20 batted balls." --persist
```

This returns a `run_id` and a location-definition clarification with three options
(`ZONE_UPPER_THIRD`, `ZONE_UPPER_OUTSIDE`, `BATTER_RELATIVE_UPPER_EDGE`). Resume the same
run with the chosen option:

```bash
python3 -m app.cli answer --run-id <run_id> --request-id <clarification_id> --choice loc-2
```

`answer` persists the confirmed constraint and continues execution. `resume --run-id
<run_id>` inspects recovery state; `resume --run-id <run_id> --execute` continues a
durable run without consuming an answer again.

### Useful flags

* `--persist` — store the run in the operational store (required for `answer`/`resume`).
* `--trace` — print structured semantic decisions (canonical semantics, review outcome,
  models, per-call latency, material differences, objective status, accepted sources).
  It never prints hidden model reasoning.
* `--json` — machine-readable result.
* `--no-llm` — force the deterministic high-confidence semantic path.
* `--demo` — explicitly enable the synthetic data tool (demo only; never real analytics).

## 5. Understanding the output

Each objective is reported with one status:

* `COMPLETE` — the requirement was satisfied by an accepted artifact.
* `LIMITED` — usable but with explicit limitations (coverage/sample).
* `FAILED` — no accepted product; the reason and unresolved requirement are listed.
* `PENDING` / `IN_PROGRESS` — the run is still working (interactive requests block).

Source provenance is always explicit:

* `POSTGRES` — live PostgreSQL Statcast (2024+).
* `PARQUET` — historical Parquet archive (2015–2023).
* `WEB` — shared knowledge / web evidence.
* `SYNTHETIC` — demo data only, requires `--demo`.

Special cases:

* **no qualifying rows** — the tool returns `EMPTY` and the objective becomes `FAILED`
  (never a false `COMPLETE`). Example: the narrow min-20-BBE compound query yields zero
  qualifying hitters.
* **semantic disagreement** — when the extractor and reviewer materially disagree, or a
  material ambiguity is detected, the run stops at a Clarification instead of executing a
  possibly incorrect interpretation.
* **clarification** — `Clarification required:` lists `[option_id] label -> value`. The
  `*` marks the recommended option.

## 6. Debug / inspection mode

`--trace` on `ask` exposes the structured decisions without model reasoning:

```bash
python3 -m app.cli ask "top 5 by exit velocity on fastballs at least 95 mph with at least 20 BBE in 2025" --trace
```

```
--- semantic trace ---
  semantic parser: dual (hybrid-semantic-v1)
  semantic constraints: RANKING:ranking,PITCH_TYPE:pitch_type,NUMERIC:pitch_velocity,...
  semantic review: outcome=DUAL_REVIEW agreement=AGREE
  semantic models: extractor=deepseek-chat reviewer=deepseek-chat
  semantic call: role=extractor model=deepseek-chat ok=True latency_ms=2018
  semantic call: role=reviewer model=deepseek-chat ok=True latency_ms=2308
  canonical semantics[0]: CATEGORY:date_range, NUMERIC:pitch_velocity, ...
  objective[0] status=COMPLETE
  response[0] accepted_sources=['POSTGRES']
```

For a persisted run you can inspect stored objects and artifacts:

```bash
python3 -m app.cli inspect --run-id <run_id>
python3 -m app.cli show-artifact --artifact-id <artifact_id>
python3 -m app.cli metrics --run-id <run_id>
```

The semantic review record (both candidates, reconciliation outcome, material
differences, models and latency) is stored durably under the `semantic_review` object
kind and reused on restart, so the same query is not reinterpreted after a crash.

## 7. Tests and gates

Developer commands:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q app tests scripts
python3 scripts/secret_scan.py
```

Semantic and adversarial gates:

```bash
python3 docs/reviews/v01-semantic-hybrid-gate.py        # 20-case corpus + 5 compositional SQL cases
python3 docs/reviews/v01-final-hybrid-adversarial.py    # the nine containment cases (must exit 0)
python3 docs/reviews/v01-dual-semantic-gate.py          # 20 dual reconciliation cases (must exit 0)
```

Live end-to-end evidence (requires credentials and running sources):

```bash
python3 docs/reviews/dual-semantic-live-evidence.py
```

## 8. v0.1 limitations

* The semantic vocabulary is closed and narrow: metrics are `pitch_velocity` and
  `exit_velocity`; locations are the three documented upper-zone definitions.
* Generalized temporal NLP, current-period comparisons and RAG/pgvector remain DEFERRED.
* Web Evidence and the separate Operational PostgreSQL control plane remain
  UNVERIFIED_LIVE in this environment.
* The narrow compound query legitimately returns zero qualifying rows at the requested
  minimum; that is reported as `FAILED`, not a data error.
* Report artifacts do not include pitch-grain reconciliation against upstream feeds.

## 9. v0.2 LLM-first conversational runtime

This is the preferred interface. The LLM understands the question, plans tool use,
researches when local data is missing, and writes a natural answer; SQL/filesystem/network
and permissions stay strictly guarded.

```bash
python3 -m app.cli chat                 # multi-turn conversation (recommended)
python3 -m app.cli ask "太鼓达人今年战绩如何？"
python3 -m app.cli ask "最近30天Ohtani和Judge谁打得更好？" --trace
python3 -m app.cli doctor
```

`chat` manages run/request ids internally. Clarifications are answered naturally
(`按个人好球带上缘`, or just `3`). Follow-ups such as `那去年呢？` reuse the conversation
context. `--trace` prints the structured runtime trace (cognition plan, tool calls,
compiled `SQLAnalysisRequest`, evidence, final state) and never hidden model reasoning.
The `--legacy` flag on `ask` still runs the deterministic requirement/ semantic pipeline.

Live tools used by the runtime:

* **Web research** — DuckDuckGo Lite + page reading; unstructured, sourced evidence.
* **Batting / pitching stats** — live season/date-range lines (PA, AVG, OBP, SLG, OPS, HR,
  BB, SO; W-L, ERA, WHIP, SO, IP, SO9).
* **Local analytics** — the strict Statcast `SQLAnalysisRequest` path (Parquet 2015–2023,
  PostgreSQL 2024–2026).
* **Entity lookup** — local dictionary → MLB StatsAPI people search → evidence text.

Candidate knowledge (runtime discoveries such as a community nickname) is **not** written
into Shared Knowledge automatically. Review it as an administrator:

```bash
python3 -m app.cli knowledge candidates
python3 -m app.cli knowledge review <candidate-id> --approve --ingest
python3 -m app.cli knowledge review <candidate-id> --reject
```

Live-verification classification (see the stop report): `LIVE_VERIFIED` for web research,
batting/pitching stats, PostgreSQL and Parquet analytics, and conversational clarification;
`DEMO_ONLY` for synthetic analytics; `UNIT_VERIFIED`/`INTEGRATION_VERIFIED` as labelled in
the test suite. `--demo` success is never reported as real feature success.
