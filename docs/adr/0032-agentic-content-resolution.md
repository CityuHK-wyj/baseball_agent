# ADR 0032 — Bounded Agentic Content Resolution

Status: accepted for M2.10 implementation; independent acceptance pending.

## Ownership

`WebResearchTool` owns an `AgenticContentResolver` capability. `RetrievedDocument` remains the content artifact and `Representation` remains the view and lineage model. The resolver has no authority to create Evidence or decide support. Safe Acquisition owns every new network request; the resolver can only return a URI candidate.

## Execution choice

A fixed extraction DSL is easier to inspect and replay, but a useful HTML/JSON/XML DSL would need an expanding language of traversal, filtering and string operations. That recreates website adapters in another form. Unrestricted Python is unacceptable. We use a narrow Python AST with a small built-in computation surface, a separate short-lived process, resource limits and no imports or attribute access. The program receives one selected representation; it returns one JSON result. This is more flexible for unfamiliar shapes, while the AST and process boundary remain explicit and testable. The program and policy version are hashed for replay. Exact replay assumes the same Python standard-library version and selected input; the trace records the runtime version.

## Trust boundaries

Acquired text is untrusted data in the prompt and sandbox. The generated program cannot import, inspect the environment, open files, create processes or access the network. A URI output is validated and passed back through the existing fetch ladder. A derived view names its parent representation and program trace; source authority stays with the acquired representation. Evidence can arise only through the existing span, verifier and Judge path. Resolution runs only after a typed deterministic failure and under per-goal, per-resource and depth bounds.
