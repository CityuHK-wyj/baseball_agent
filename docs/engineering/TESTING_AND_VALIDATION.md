# Testing and Validation — Architecture Baseline v1

## Evidence levels

1. **Historical naming-cycle validation:**
   [Naming Refactor Map](../architecture/NAMING_REFACTOR_MAP.md#validation-performed)
   records backend **4184 passed / 63 skipped**, frontend **10 passed**, production
   build passed, focused live smoke **5 passed**, and OpenAPI **5 paths / 35 schemas**
   byte-identical to the pre-refactor baseline. Full live integration was **not**
   rerun after Naming Refactor.
2. **This documentation cycle:** static code/contract review and lightweight checks
   below. Production, tests, dependencies, frontend and configuration are unchanged
   from `1d8cb3ad0ec786b48c82de830c6ad68b229c1511`.
3. **Not evidence from this cycle:** no new full backend/frontend/build/live/browser
   rerun, numerical/accuracy study, UAT or framework integration. Passing fixtures
   do not establish real-provider availability or universal language correctness.

## Current-cycle checks

| Check | Result / scope |
|---|---|
| Git baseline | HEAD `1d8cb3ad0ec786b48c82de830c6ad68b229c1511`, tree `3cca1a0c28a2e377920e5516d23bb898f8e92178`; starting worktree clean |
| Protected tag | Annotated object `3ae5987b6357a65216cb4906b620de62d1fd05ea`, peeled commit `cf0ccc51797eb1bae3cd2de2a8b275aa2f4e0fc0`; unchanged |
| Required context | README, SPEC, naming documents, AGENTS, root architecture and ADRs reviewed; `CONTEXT.md` does not exist in this checkout |
| Semantic identity | Constructor/import/consumer/authority/lifecycle/serialization review recorded in [registry](../architecture/DOMAIN_CONCEPT_REGISTRY.md); no confirmed duplicate authoritative contract |
| AST inventory | No duplicate class names under `app/`; identical annotated shapes only DateRange/DateWindow and API text DTOs, reviewed as intentional boundaries |
| Public schema | `PYTHONPATH=. python tools/export_frontend_openapi.py`: **5 paths / 35 schemas**; SHA-256 `54a6f5bef61e2edf079642e12fee1d503f400c887fb0aada24ab9cc5f5c55fab` |
| Generated client drift | `python -m pytest -q tests/test_frontend_schema_drift.py`: **1 passed**; generated TypeScript byte-equals checked-in client |
| CLI configuration | `python -m app.cli --help` passed; documented commands, dependency/config files and Vite proxy inspected. No live `doctor`/chat/provider call claimed |
| Referenced paths, symbols and links | **16 documentation files, 140 local links/anchors, 56 canonical symbol entries** checked; referenced current source paths and fences pass; terminology review and `git diff --check` pass |
| Mermaid | **6 flowcharts** reviewed for ownership/edges and balanced fences; no `mmdc`/Mermaid renderer installed, so rendered/syntax-tool validation not claimed |
| Changed content safety | Exact staged documentation set/diff reviewed; no new secrets, private environment files, personal absolute paths, datasets/dumps, raw traces, scratch archives or temporary artifacts |
| Existing tracked content | Content scan found personal absolute paths in **47 pre-existing tracked files** and legacy tracked audit artifacts; no candidate real token/private-key signature from the bounded pattern scan. This is **not** full security/history clearance |
| Publication destination | `git remote -v` empty; no verified upstream on `codex/phase5b-final-repair`; `gh repo view` reports no remotes. Local commit only; no repository created, remote changed or push attempted |

The first direct exporter invocation without `PYTHONPATH` failed to import `app`;
the corrected command above passed and is the documented one. Temporary export/check
outputs stayed outside the repository; no audit evidence archive was created.

### Validation scope and interpretation

Local checks cover Markdown links in this cycle's documents, exact registry Python
symbols and referenced source paths. Plain-text historical paths/labels in dated
proposal sections are not assertions that a corresponding module exists now.
Current overview, registry, authority and status explicitly separate target and actual
implementation; historical ADRs/audits are not rewritten to remove past terminology.

The guardrail review introduced no production regex/word lists, no new authority
source, no cross-layer promotion, no Claim/coverage writer and no execution/frontend
semantic behavior. Partial independence, requested/executed matching, clarification
and evidence gates are documented from inspected code, not re-proven by a docs test.
No new claim of `wrong semantic COMPLETE = 0` is made for an unevaluated corpus.

Publication uses a new snapshot commit based on the verified public repository `main`.
The proposed tree, excluded material and reachable history require a separate
publication security scan; documentation-cycle checks above do not establish that result.

## Running checks intentionally

From the repository root with backend dependencies installed:

```bash
python -m pytest tests -q
python -m pytest -q tests/test_frontend_api.py tests/test_frontend_schema_drift.py
PYTHONPATH=. python tools/export_frontend_openapi.py > /tmp/baseball-openapi.json
```

The broad default suite includes offline/unit/contract and fixture-backed tests; live
modules are opt-in and skipped unless their own environment guards are enabled. Check
the target test file's marker/environment before interpreting a skip or fixture result.

```bash
# Real sources/provider required; do not run merely for documentation edits
RUN_INTEGRATION=1 python -m pytest tests/test_integration_live.py -v

# Frontend, from frontend/
npm ci
npm test
npm run build
npm run types:generate  # only for intentional public-schema changes
# Browser tests need Playwright/Chromium and real backend/provider configuration:
npm run e2e
```

Representative mechanism tests (not a complete test index):

- [Requirement-local retention](../../tests/test_partial_retention_m212r4.py)
- [Governed operations](../../tests/test_governed_operation_admission.py) and
  [scalar result ceiling](../../tests/test_scalar_operation_result_ceiling.py)
- [Subject granularity](../../tests/test_subject_granularity.py)
- [Shared Safe Transport](../../tests/test_shared_safe_transport.py)
- [Frontend API](../../tests/test_frontend_api.py) and
  [schema drift](../../tests/test_frontend_schema_drift.py)

For new behavior work, choose mechanism-level regression plus fresh tests appropriate
to the changed semantic boundary. Do not treat these commands as authorization to
restart a large refactor or live acceptance cycle.
