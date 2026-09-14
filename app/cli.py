"""Command-line interface for the Baseball Agent.

Runs the deterministic pipeline offline by default (synthetic data source), or persists a
run to the operational store. Never prints credentials. See docs/usage/.
"""

import argparse
import json
import sys
from uuid import uuid4

from app.agent.planner import RuleBasedPlanner
from app.agent.registry import ArtifactRegistry
from app.agent.routing import Router, ToolCapability
from app.assessment.judge import RuleBasedJudge
from app.assessment.service import AssessmentService
from app.config import settings
from app.models.entities import CanonicalEntity
from app.observability.evaluation import RunSummary, evaluate_runs
from app.persistence.artifacts import LocalFilesystemArtifactStorage
from app.persistence.recorder import RunRecorder
from app.persistence.resume import ResumeService
from app.persistence.store import SqliteOperationalStore
from app.pipeline import AnalysisPipeline, default_tool_factory
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.normalizer import SemanticNormalizer
from app.semantic.objective_extractor import RuleBasedObjectiveExtractor
from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer

DEFAULT_ENTITIES = (
    CanonicalEntity(entity_key="MLBAM:592450", entity_type="PLAYER", display_name="Aaron Judge",
                    aliases=("Judge", "交通指挥员")),
    CanonicalEntity(entity_key="MLBAM:660271", entity_type="PLAYER", display_name="Shohei Ohtani",
                    aliases=("Ohtani",)),
)


def _id_factory():
    def factory(prefix: str) -> str:
        return f"{prefix}-{uuid4().hex[:12]}"
    return factory


def build_pipeline(*, persist: bool = False, empty: bool = False) -> AnalysisPipeline:
    ids = _id_factory()
    dictionary = EntityDictionary(DEFAULT_ENTITIES)
    resolver = EntityResolver(dictionary, id_factory=ids)
    semantic = SemanticNormalizer(RuleBasedObjectiveExtractor(id_factory=ids), resolver,
                                  dictionary, id_factory=ids)
    router = Router((ToolCapability(tool="synthetic", source_kind="SYNTHETIC",
                                    supported_artifact_types=("TABLE", "EVIDENCE", "FEATURE")),),
                    id_factory=ids)
    registry = ArtifactRegistry()
    assessment = AssessmentService(registry, RuleBasedJudge(), id_factory=ids)
    recorder = None
    if persist:
        settings.operational_store_path.parent.mkdir(parents=True, exist_ok=True)
        recorder = RunRecorder(SqliteOperationalStore(settings.operational_store_path),
                               LocalFilesystemArtifactStorage(settings.artifact_storage_path),
                               id_factory=ids)
    return AnalysisPipeline(
        semantic, RuleBasedRequirementDecomposer(id_factory=ids),
        RuleBasedPlanner(id_factory=ids, max_rounds=3), router, assessment, registry,
        tool_factory=default_tool_factory(0 if empty else 1200), recorder=recorder,
        max_rounds=3, budget=10, id_factory=ids)


def _open_store() -> SqliteOperationalStore:
    settings.operational_store_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteOperationalStore(settings.operational_store_path)


def _print_result(result, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"needs_clarification": result.needs_clarification,
                          "objective_statuses": list(result.objective_statuses),
                          "responses": list(result.responses),
                          "run_ids": list(result.run_ids)}, indent=2, ensure_ascii=False))
        return 0
    if result.needs_clarification:
        print("Clarification required:")
        for request in result.clarifications:
            print(f"  Q: {request.question}  (reason: {request.reason})")
            for option in request.options:
                marker = "*" if option.option_id == request.recommended_option_id else " "
                print(f"   {marker} [{option.option_id}] {option.label} -> {option.value}")
        return 0
    for status, response in zip(result.objective_statuses, result.responses):
        print(f"=== {status} ===")
        print(response)
    return 0


def command_ask(args) -> int:
    pipeline = build_pipeline(persist=args.persist, empty=args.empty)
    result = pipeline.analyze(args.query, mentions=tuple(args.mention) if args.mention else None)
    return _print_result(result, args.json)


def command_resume(args) -> int:
    store = _open_store()
    try:
        service = ResumeService(store)
        plan = service.build_plan(args.run_id)
        print(f"status={plan.status} recovery_position={plan.recovery_position or '-'}")
        if plan.status == "RESUMABLE":
            print(f"planner_terminal={plan.planner_terminal}")
            print(f"reusable_artifacts={list(plan.reusable_artifact_refs)}")
            print(f"interrupted_executions={list(plan.interrupted_execution_refs)}")
            print(f"pending_requests={list(plan.pending_request_refs)}")
        return 0
    finally:
        store.close()


def command_inspect(args) -> int:
    store = _open_store()
    try:
        checkpoints = store.list_checkpoints(args.run_id)
        print(f"checkpoints={[item.recovery_position for item in checkpoints]}")
        for kind in ("artifact", "assessment", "requirement_state", "objective_state",
                     "execution", "completion_report", "response_package"):
            print(f"{kind}={len(store.list_objects(kind, args.run_id))}")
        return 0
    finally:
        store.close()


def command_show_artifact(args) -> int:
    store = _open_store()
    try:
        record = store.get_object("artifact", args.artifact_id)
        if record is None:
            print(f"artifact {args.artifact_id} not found", file=sys.stderr)
            return 1
        print(json.dumps(record.payload, indent=2, ensure_ascii=False))
        return 0
    finally:
        store.close()


def command_metrics(args) -> int:
    store = _open_store()
    try:
        summaries = []
        for record in store.list_objects("completion_report", args.run_id):
            payload = record.payload
            execution = payload.get("execution_summary", {})
            summaries.append(RunSummary(
                run_id=payload["run_id"], objective_status=payload["objective_status"],
                rounds=execution.get("rounds", 0), plan_revisions=payload.get("plan_revisions", 0),
                tasks_succeeded=execution.get("tasks_succeeded", 0),
                tasks_empty=execution.get("tasks_empty", 0),
                tasks_failed=execution.get("tasks_failed", 0),
                attempts=execution.get("attempts", 0),
                accepted_artifacts=len(payload.get("final_artifact_refs", []))))
        evaluation = evaluate_runs(summaries)
        print(evaluation.model_dump_json(indent=2))
        return 0
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="baseball-agent", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="run one analysis request (offline by default)")
    ask.add_argument("query")
    ask.add_argument("--mention", action="append", help="explicit entity mention (repeatable)")
    ask.add_argument("--empty", action="store_true", help="simulate a source returning no rows")
    ask.add_argument("--persist", action="store_true", help="persist to the operational store")
    ask.add_argument("--json", action="store_true")
    ask.set_defaults(func=command_ask)

    resume = sub.add_parser("resume", help="inspect resume state for a run")
    resume.add_argument("--run-id", required=True)
    resume.set_defaults(func=command_resume)

    inspect = sub.add_parser("inspect", help="list checkpoints and stored objects for a run")
    inspect.add_argument("--run-id", required=True)
    inspect.set_defaults(func=command_inspect)

    show = sub.add_parser("show-artifact", help="print stored artifact metadata")
    show.add_argument("--artifact-id", required=True)
    show.set_defaults(func=command_show_artifact)

    metrics = sub.add_parser("metrics", help="evaluation metrics for a run")
    metrics.add_argument("--run-id", required=True)
    metrics.set_defaults(func=command_metrics)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
