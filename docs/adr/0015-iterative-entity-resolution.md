# ADR 0015 — Entity Resolution is Iterative; Identity has Levels

Status: accepted (2026-09-21)

## Context

The Entity Dictionary (`player_dictionary`, plus curated aliases) is an important source but
not the only entrance to open-world entity understanding. In Phase 3 a dictionary miss
returned `FAILED/UNKNOWN_ENTITY` with no artifact. That conflated two different facts:

- "this dictionary has no canonical id for the mention" (a local data fact), and
- "the agent does not understand who the user means" (almost never true when the model and
  the surrounding context carry a candidate identity).

It also starved web research, because web requests were built only from *resolved* names.

## Decision

Model identity as a level that can advance, not a boolean.

- `CANDIDATE` — a plausible identity from an LLM prior, the user's wording, context or an
  unverified alias. Carries a name/alias, never a canonical database id.
- `RESOLVED` — confirmed by at least one evidence source (local directory, structured
  external API, fetched page, approved knowledge), possibly still without a canonical id.
- `CANONICAL` — a canonical database identity (e.g. an MLBAM id) that satisfies a
  capability's input contract.

Resolution is iterative. A mention may start as `CANDIDATE`; directory lookup, historical
data, structured external APIs (MLB StatsAPI), web research and Shared Knowledge may each
advance it. Different capabilities require different levels:

- `local_analytics` and `dynamic_fact` require `CANONICAL` (a typed id — their input
  contract).
- `web_research`, `shared_knowledge` and general explanation accept `CANDIDATE`/`RESOLVED`
  names and aliases.

`EntityResolutionTool` therefore always emits an identity artifact with
`candidate`/`resolved`/`canonical` levels and an `ENTITY_MAPPING` export. It must not be
necessary to obtain an MLBAM id before research may begin. When a database query is
requested, the Binder still enforces the canonical-id contract and fails closed if it is not
met; that failure is local to the database Need and is fed back to the Planner.

## Consequences

- A dictionary miss no longer terminates the Goal.
- The web lane can search for candidate names, aliases and plain descriptions.
- The database execution guarantee from ADR 0013 is unchanged: no canonical id, no SQL.
