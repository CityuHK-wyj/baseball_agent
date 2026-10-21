# Freeze-review boundary repairs

This documents the repair contract, not a freeze authorization. The independent
counterexamples are promoted in `tests/test_freeze_repair_regressions.py`; their
original ignored evidence directory is preserved. Production admission remains owned
by the Judge. Composer, Conformance and Orchestrator retain their existing roles.

## 1. Requirement-local derived authority

`app/contracts/operations.py` separates two objects:

* `OperationContract`: interpreted user meaning, attached by RequirementBuilder to
  its owning Requirement **before** planning. It names a finite operation, ordered
  operand obligations, governed parameters and an exact output field.
* `GovernedOperation`: a candidate execution proposal naming that Requirement and
  exact artifact/export/row/field cells. Tool metadata cannot create a contract.

Root obligations contain a canonical measure, analytical role, user subject names,
season/date interval, competition codes and qualification. Typed catalog population
symbols (such as REGULAR_SEASON) are grounded to their existing exact code sets by
`app/semantic/operations.py`; unknown symbols reject. An omitted competition facet
can inherit one unique, independently authoritative required Goal population; explicit
operand codes are unchanged, and absent/ambiguous governing populations are not guessed.
This grounds an unspecified typed slot; it does not parse user wording.
Names must independently resolve through Goal-owned verified identities. Empty subjects means an all-player
population, not an unspecified player. A derived-parent obligation instead names a
unique Goal-local contract key and that parent's exact output field.

Example interpreted contract (artifact identities deliberately absent):

```json
{
  "key": "requested_delta", "kind": "SUBTRACT", "output_field": "difference",
  "operands": [
    {"scope": {"measure": "home_runs", "analytical_role": "BATTER",
               "subjects": ["Mookie Betts"], "season": 2023, "game_types": ["R"]}},
    {"scope": {"measure": "home_runs", "analytical_role": "BATTER",
               "subjects": ["Mookie Betts"], "season": 2024, "game_types": ["R"]}}
  ]
}
```

An interpreter proposal still requires proven Requirement ancestry. Schema validity
is **not** a general proof of natural-language entailment; the semantic interpreter
continues to own interpretation, with live provenance/intent gates. No operation
keyword/regex or program AST can substitute for missing interpreted meaning.

### Evaluation and admission

`app/capabilities/governed_compute.py` binds unambiguous available cells and executes
finite operations. Numeric cell selection is restricted to explicit bindings, while
identity resolution and recursive parent inspection use the full Goal-local evidence
context. If the finite contract binds, it selects trusted execution even when a Planner
also supplies Python: that candidate program is ignored, not executed or blessed. The
artifact records `trusted-finite-ir` and whether a program was ignored. Without a bound
finite contract, Python remains a non-authoritative candidate. This produces candidates,
never Claims. Explicit operation
proposals are also candidates. Arbitrary bounded Python remains available for useful
post-processing but is **non-authoritative** without a separately admitted contract
and exact deterministic output verification.

`app/verification/governed_operations.py:OperationAdmission` independently:

1. Resolves the owner from authoritative Goal Requirements; requires unique keys.
2. Checks requested/executed kind, ordered operand count, parameters and output identity.
3. Resolves each exact export and requires export rows to equal inspected result rows.
4. Reconstructs and validates database execution receipts and independently compares
   **all** requested operand scope facets, including entity, time, competition,
   qualification, measure/column and complete source coverage.
5. Requires a singleton scalar row from reconstructed Safe IR global aggregates.
   Grouping, arbitrary row selection and filtered input populations are unsupported;
   free Python cannot fill that semantic gap. A positive **outer result ceiling** is
   permitted only with the independent no-GROUP-BY global-aggregate cardinality proof
   and an otherwise conforming complete receipt. It cannot restrict aggregate inputs;
   it is recorded in each root proof, not treated as a waived scope check. Native Safe Analytics IR still supports its existing grouped,
   ranked and filtered database operations through their own governed receipts.
6. Recursively admits derived parents through their own authoritative contracts.
   Missing/rejected/unapproved/cyclic ancestry fails closed, independent of collection
   order. A rejected program export cannot be laundered through a child SUM.
7. Recomputes each output with trusted deterministic functions. Exact whole-row and
   export equality reject fabricated siblings, offsets, cancellation and extra rows.
8. Retains output-specific proof records with SHA-256 replay digests, ordered cell
   identities, source receipt/result digests, scope and approved parent-proof links.

Supported operation semantics:

| Kind | Deterministic meaning |
| --- | --- |
| SUBTRACT | left minus right; exactly two ordered operands |
| RATIO | left divided by right; zero denominator rejects |
| PERCENT_CHANGE | `(left-right)/right*100`; zero denominator rejects |
| SUM / MEAN / MEDIAN / MIN / MAX | bounded exact operand vector reduction |
| AGGREGATE | explicit SUM/MEAN/MIN/MAX/COUNT reducer over the bounded vector |
| RANK | explicit ASC/DESC stable ordinal ranking; ties retain operand order |

Only finite numeric operands are accepted. Constants are not factual operands.
COUNT and percent scaling are intrinsic governed operation semantics, not model
constants. Unsupported tests/windows/groupings remain candidates or unresolved
obligations; they do not acquire factual authority by replaying Python.

Per-pass bounds: 64 ancestry depth, 1,024 inspected cells, 256 distinct retained root
cells per output, 32 operations per artifact, 256 operands per operation and 1 MB of
serialized proof material. Parent proofs use digest links plus bounded flat root
lineage, avoiding exponential recursive proof serialization.

Judge admission is shared across every lane. Scalar receipts cannot satisfy an
operation-owning Requirement. Derived outputs cannot inherit unrelated MEASURE
Requirements from their operands. Scope support comes from verified root proof scopes,
not tool-declared `source_scopes`. Claim/proposition linking cannot broaden the
operation's eligible Requirement set. Claim text is rendered from the independently
verified operation/value, not arbitrary tool text, objectives or output labels.

## 2. Shared safe acquisition

`app/web/transport.py:request` is the shared evidence-acquisition transport. It is used
by ordinary page acquisition, HTML alternates, RSS article representations, discovery
endpoints, Google News RSS, Wikipedia, Bing/Brave, publisher feeds, structured StatsAPI
records and Tavily search/extraction. LLM provider requests retain their separate SDK
contract and outbound-attempt instrumentation; they are not evidence acquisition.

Each destination and redirect is validated. The socket targets an approved IP literal;
Host and TLS SNI/certificate verification retain the original hostname. Environment
proxies are disabled. Redirects are bounded, loops reject, cross-origin authentication
headers are stripped and provider POST redirects reject rather than forwarding payloads.

Bodies are acquired with `stream=True`/`iter_raw`; HTTPX's automatic decompressor never
sees an unbounded response. Identity/gzip/zlib-deflate/raw-deflate are supported.
Concatenated gzip members are bounded (at most 16). Unknown encodings, malformed or
truncated compressed bodies and trailing junk fail closed. There is no unbounded
`flush()`. Decompression stops at the configured decoded limit plus one overflow
sentinel byte; overflow rejects and retains no article body.

The configured raw cap is an admission/read-stopping policy, **not** a claim that TCP
cannot read ahead. HTTPcore has a fixed socket buffer (64 KiB); actual delivered raw
bytes may exceed the configured cap by one bounded read buffer. Telemetry reports
actual `num_bytes_downloaded`, not a capped or inferred count. Decoder input chunks
are bounded to at most 8 KiB; retained successful bodies never exceed the cap.
Oversized input is rejected, not accepted as a truncated compressed representation.

Request telemetry records HTTP attempts, actual raw bytes, decoded production,
retained bytes, redirects and limit violation. Errors retain the same measurements.
`FetchedPage` carries acquisition telemetry for HTTP/feed paths. Redirect loop and
exhaustion errors retain actual attempt telemetry too. Origin identity includes scheme,
host and port; HTTPS downgrades are rejected. Cross-origin GET redirects retain only
neutral HTTP negotiation headers, not a provider-secret denylist. The fixed StatsAPI
native adapter does not follow redirects: a redirected JSON body cannot be relabeled
as an official StatsAPI observation.

The previous optional active Playwright acquisition route used validate-then-continue
networking and could not enforce this contract. It is now explicitly unavailable
(`JS_RENDER_REQUIRED`) rather than retaining a security escape hatch. Ordinary browser
**UI E2E testing is unchanged**. Restoring active page rendering requires a separately
bounded/pinned network and browser-resource design; do not enable an unsafe legacy path.

## 3. Whole governed-state consistency

`app/verification/coverage.py` owns canonical Requirement coverage/status and Goal
aggregation. Judge finalization and fresh integrity recovery share that policy.
`ExpectedObligations` captures a detached, controller-owned source Goal graph before
Judge evaluation. Engine supplies it through Orchestrator, preserving it across the
one bounded new-Judge retry and replans; direct Judge finalization also checks its
source graph. A self-consistent candidate cannot omit a Requirement/mandatory child,
change its meaning (including kind, subject, scope and evidence expectations),
owner/mandatory/dependency graph, or substitute another Goal. Only evidence-state
fields such as coverage, support, gaps and notes may change. This source
context is not a candidate-supplied self-attestation. The standalone validator without
source context checks internal consistency only; production uses the source-bound path.
`app/verification/governed_state.py` checks the whole projection before publication:

* Unique Requirement/proposition/Claim/artifact assessment identities.
* Requirement status, coverage, support, gaps and conflicts equal child aggregation.
* No missing Requirement assessment, unproved satisfied owner, dangling Claim/artifact
  references, or missing/cyclic proposition dependency.
* SUPPORTED Claims have evidence references and authoritative obligation ownership.
* Satisfied communicable mandatory obligations have approved Claims; every satisfied
  proposition has inspectable support.
* COMPLETE agrees with all mandatory coverage, FAILED does not discard approved
  support, and clarification agrees with WAITING_FOR_USER.

This is a structural integrity guard, not a second evidence Judge. Conformance and
presentation still cannot modify or re-Judge authority. Recovery constructs a detached
new snapshot, removes invalid authority, reaggregates every Requirement and preserves
intact approved partial Claims under LIMITED. It never mutates the original snapshot
or promotes missing evidence. A finalization error carries a sealed invalid candidate
to Orchestrator so the same one-new-Judge-pass bound and fresh fallback still apply;
it never becomes public authority. Canonical aggregation rebuilds mutable private assessment
copies before sealing; it never mutates a copied frozen model. Missing/changed source
obligations are restored from the controller's detached graph as UNSUPPORTED, with no
support carried over. Ambiguous duplicate owners are not selected as authority. Intact
approved Claims remain; Claims attached to reset obligations are removed.

Claim ownership is not assigned merely because a partial Requirement lists a knowledge
artifact. Each semantic knowledge item must independently pass the existing Requirement
relevance gate before receiving that owner's proposition IDs. The view excludes sibling
items and the artifact's aggregate prose, so a relevant definition cannot launder an
unrelated entry. This reuses existing relevance semantics; it is not a proof of arbitrary
language entailment and introduces no new query-wording rule.

## 4. Actual retry telemetry

`LLMClient` distinguishes application attempts, SDK/provider calls, actual outbound
HTTP sends and provider retry sends. Actual sends are measured by the SDK HTTP
client's request hook, including connection/time-out attempts and SDK retries. SDK
retry headers identify retry sends; retry counts are not guessed from configured
maxima. A mocked SDK call with no HTTP transport correctly records zero HTTP sends.
Owned HTTP provider fixtures verify 500, 400, retry-success, malformed JSON and success;
an owned timeout transport verifies no-response attempt accounting.

## 5. Small native-state temporal correction

A current TEAM_MEMBERSHIP record establishes the observed state on its provider date.
For a typed CONFIRMED_STATE Requirement, an **inferred, unconfirmed** relative acquisition
lookback is treated as freshness policy only when it exactly matches the owning
Requirement's resolved window; the observation must equal its anchor date. Explicit
historical intervals, seasons, caller as-of fabrication, reporting/prediction requests
and predicate/subject/record-fragment conflicts remain rejected. The change neither
reinterprets arbitrary user words nor weakens event/history safeguards.

## Review limits

These repairs are not a semantic-parser redesign and do not claim universal model
entailment, unrestricted Python authority, arbitrary grouped/window post-processing,
active browser acquisition, or a freeze decision. Independent adversarial review and
human freeze authorization remain separate gates. Validation logs, live failures,
reverse-provenance examples and final Git identifiers belong in the repair evidence
report, not in a self-issued freeze tag.
