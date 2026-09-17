# Domain docs

This repository uses a single domain context.

## Before exploring or changing code

- Read the root `CONTEXT.md` and use its terms in specifications, tickets, tests, code, and reviews.
- Read the ADRs under `docs/adr/` that affect the area being changed.
- If a required concept is missing or ambiguous, resolve it through `domain-modeling` or `grill-with-docs` and update `CONTEXT.md`.
- If proposed work conflicts with an ADR, surface the conflict explicitly before changing the recorded decision.

## Layout

```text
/
├── CONTEXT.md
└── docs/
    └── adr/
```

Keep vocabulary in `CONTEXT.md`. Keep durable architecture decisions and their rationale in numbered ADRs. Do not copy skill instructions into either location.
