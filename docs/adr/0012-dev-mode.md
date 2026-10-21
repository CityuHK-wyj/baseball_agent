# ADR 0012 — Developer Mode Shares the Production Runtime

Status: accepted

## Decision
`python3 -m app.cli chat` and `python3 -m app.cli dev --trace-level verbose` compose the
same production runtime. Developer mode only adds observability: semantic message,
requirements, planner proposals (accepted/rejected), need graph, bindings, tool
requests/outcomes, artifacts/exports, scope verification, judge, projection and latency.
No chain-of-thought or secrets are recorded.
