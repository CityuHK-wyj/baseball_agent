# ADR 0011 — Sandboxed Python Compute at the Artifact Layer

Status: accepted

## Decision
Source-level filtering/grouping/aggregation belongs to Safe IR/SQL. Bounded Artifact
post-processing (alignment, bootstrap, statistical tests, ad-hoc ranking) runs in an
isolated Python process that receives only explicit Artifact bindings and has no network,
database, host environment, arbitrary filesystem, or subprocess access.
