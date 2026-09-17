# Web results become evidence through an extractor, not directly

Status: accepted

## Context

D016 and P004 require that a raw web result is not itself approved evidence: the Router
only decides *where* to look, and a separate extraction step turns unstructured text into
structured evidence with two layers (raw source + structured extraction). No such stage
existed.

## Decision

- `app/models/evidence.py`: `RawWebResult` (url, title, text, retrieved_at) and
  `Evidence` (raw_ref, url, title, `EvidenceClaim[]`, provenance). A claim carries the
  claim, its supporting context and any claim time.
- `app/semantic/evidence.py`: `EvidenceExtractor` Protocol,
  `RuleBasedEvidenceExtractor` (deterministic, sentence-level, keeps the source sentence as
  support), and `evidence_to_artifact(evidence, raw_artifact_id)` which wraps structured
  evidence as a unified `EVIDENCE` artifact with `lineage=(raw_artifact_id,)`.
- `app/llm/evidence.py`: `LLMEvidenceExtractor` behind the same Protocol, with a versioned
  `EVIDENCE_PROMPT`, JSON validation, and a deterministic fallback on provider or parse
  failure. It must not invent claims; empty claims and malformed output are rejected.
- Evidence enters the normal Artifact → validation → Judge → assessment path, so a web
  claim is assessed contextually like any other product.

## Alternatives

- Feed raw web text into the model context as "evidence": rejected by D016/P004; it makes
  unsourced text look approved.
- A separate Retrieval/Evidence agent: rejected; it is a transformation stage, not a
  business sub-agent.

## Consequences

- Evidence extraction is deterministic and testable with no network; the LLM path is a
  Protocol swap with a fallback.
- The web tool (`app/tools/web_api.py`) still returns raw JSON and is not wired to the
  extractor or the Orchestrator; wiring a live web source remains a follow-up.
- Claim extraction is heuristic; richer extraction belongs behind the same Protocol.
