# Running

## Entry point

```bash
python3 -m app.cli <command> [options]
```

All commands are offline by default and never print credentials.

## `ask` — one analysis request

```bash
python3 -m app.cli ask "How did Aaron Judge perform at the plate?"
```

| Option | Effect |
| --- | --- |
| `--mention NAME` | Force an entity mention (repeatable). Useful when the query does not contain a known name. |
| `--empty` | Simulate a source that returns no rows, to see the failure/limitation path. |
| `--persist` | Write the run to the operational store and artifact storage. |
| `--json` | Machine-readable output. |

If the objective is ambiguous, the command prints a clarification question with a few
options and a recommended one; it does **not** guess.

## `resume` — inspect how a run can continue

```bash
python3 -m app.cli resume --run-id run-1
```

Prints the resume status (`RESUMABLE` or `NO_CHECKPOINT`), the recovery position, whether
planning is terminal, which artifacts can be reused, and which executions were
interrupted.

## `inspect` — what a run stored

```bash
python3 -m app.cli inspect --run-id run-1
```

Lists the checkpoint positions and counts artifacts, assessments, requirement/objective
states, executions, and reports.

## `show-artifact` — artifact metadata

```bash
python3 -m app.cli show-artifact --artifact-id synthetic-requirement-...
```

Prints the stored artifact record (descriptor, provenance, payload reference, lineage).

## `metrics` — evaluation for a run

```bash
python3 -m app.cli metrics --run-id run-1
```

Prints completion/replan/retry/source-failure rates and average steps for the run.

## Programmatic use

```python
from app.agent.registry import ArtifactRegistry
from app.agent.planner import RuleBasedPlanner
from app.agent.routing import Router, ToolCapability
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.pipeline import AnalysisPipeline, default_tool_factory
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.normalizer import SemanticNormalizer
from app.semantic.objective_extractor import RuleBasedObjectiveExtractor
from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer

ids = lambda prefix: f"{prefix}-1"
dictionary = EntityDictionary(())
semantic = SemanticNormalizer(RuleBasedObjectiveExtractor(id_factory=ids),
                              EntityResolver(dictionary, id_factory=ids), dictionary, id_factory=ids)
registry = ArtifactRegistry()
pipeline = AnalysisPipeline(
    semantic, RuleBasedRequirementDecomposer(id_factory=ids),
    RuleBasedPlanner(id_factory=ids), Router((ToolCapability(
        tool="synthetic", source_kind="SYNTHETIC",
        supported_artifact_types=("TABLE", "EVIDENCE", "FEATURE")),), id_factory=ids),
    AssessmentService(registry, RuleBasedJudge(), id_factory=ids), registry,
    tool_factory=default_tool_factory(), id_factory=ids)
result = pipeline.analyze("Who led the league in home runs?")
print(result.objective_statuses, result.responses)
```

## Stopping safely

- Delete `.runtime/` to clear local persisted runs and artifacts.
- A run is bounded by `max_rounds` and `budget`; there is no background process to stop.
- Interrupted executions are reclassified as `INTERRUPTED` on resume; nothing is retried
  blindly.
