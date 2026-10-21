# ADR 0007 — Open Web Semantics with Security and Evidence Governance

Status: accepted

## Decision
A Web request is an open `objective` + `query` + hints + references. It is not forced
through a Web DSL. The web lane enforces SSRF protection, an allow/deny source policy, a
fetch budget, correct error classification, provenance and evidence spans. A search hit
(snippet) is never treated as grounded support; only a retrieved page body can ground a
claim.

## Consequences
- Web failure is classified and never automatically fails the Goal.
- Claims carry references to retrieved evidence spans.
