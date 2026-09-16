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
from app.context.knowledge_source import KnowledgeContextSource
from app.context.service import ContextService
from app.knowledge.entities import entity_dictionary_from_knowledge
from app.knowledge.loader import load_packs, load_sources, seed_store
from app.knowledge.refresh import refresh_domain
from app.knowledge.service import KnowledgeBase
from app.knowledge.store import SqliteKnowledgeStore
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

def _id_factory():
    def factory(prefix: str) -> str:
        return f"{prefix}-{uuid4().hex[:12]}"
    return factory


def build_pipeline(*, persist: bool = False, empty: bool = False, demo: bool = False,
                   knowledge: KnowledgeBase | None = None) -> AnalysisPipeline:
    return AnalysisPipeline.default(persist=persist, empty=empty, demo=demo or empty, knowledge=knowledge)


def _open_store() -> SqliteOperationalStore:
    settings.operational_store_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteOperationalStore(settings.operational_store_path)


def _print_result(result, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"needs_clarification": result.needs_clarification,
                          "objective_statuses": list(result.objective_statuses),
                          "responses": list(result.responses),
                          "clarifications": [item.model_dump(mode="json") for item in result.clarifications],
                          "permissions": [item.model_dump(mode="json") for item in result.permissions],
                          "constraint_revisions": [item.model_dump(mode="json") for item in result.constraint_revisions],
                          "run_ids": list(result.run_ids)}, indent=2, ensure_ascii=False))
        return 0
    if result.needs_clarification:
        print(f"run_id={result.run_ids[0]}")
        print("Clarification required:")
        for request in result.clarifications:
            print(f"  request_id={request.clarification_id} Q: {request.question}  (reason: {request.reason})")
            for option in request.options:
                marker = "*" if option.option_id == request.recommended_option_id else " "
                print(f"   {marker} [{option.option_id}] {option.label} -> {option.value}")
        return 0
    for request in (*result.permissions, *result.constraint_revisions):
        print(f"run_id={result.run_ids[0]} WAITING_FOR_USER {request.model_dump_json()}")
    for status, response in zip(result.objective_statuses, result.responses):
        print(f"=== {status} ===")
        print(response)
    return 0


def command_ask(args) -> int:
    pipeline = build_pipeline(persist=args.persist, empty=args.empty, demo=args.demo)
    try:
        result = pipeline.analyze(args.query, mentions=tuple(args.mention) if args.mention else None)
        return _print_result(result, args.json)
    finally:
        pipeline.close()


def command_answer(args) -> int:
    from app.models.clarification import ClarificationAnswer
    from app.models.interaction import PermissionAnswer, ConstraintRevisionAnswer
    pipeline = build_pipeline(persist=True, demo=args.demo)
    try:
        if args.choice is not None:
            result = pipeline.resume_clarification(args.run_id, ClarificationAnswer(
                clarification_ref=args.request_id, chosen_option_id=args.choice))
        elif args.permission is not None:
            result = pipeline.resume_permission(args.run_id, PermissionAnswer(
                permission_ref=args.request_id, approved=args.permission == "approve"))
        else:
            result = pipeline.resume_constraint_revision(args.run_id, ConstraintRevisionAnswer(
                revision_ref=args.request_id, accepted=args.revision == "accept"))
        return _print_result(result, args.json)
    finally:
        pipeline.close()


def command_resume(args) -> int:
    if args.execute:
        pipeline = build_pipeline(persist=True, demo=args.demo)
        try:
            return _print_result(pipeline.resume_run(args.run_id), args.json)
        finally:
            pipeline.close()
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


def _open_knowledge(*, reseed: bool = False) -> KnowledgeBase:
    settings.knowledge_store_path.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteKnowledgeStore(settings.knowledge_store_path)
    base = KnowledgeBase(store)
    if reseed or store.item_count() == 0:
        seed_store(store, settings.knowledge_source_path, settings.knowledge_seed_path)
        base.reload_sources()
    return base


def command_knowledge_status(args) -> int:
    base = _open_knowledge(reseed=args.seed)
    try:
        status = base.status()
        print(f"total_items={status['total_items']}  sources={status['sources']}  "
              f"verified={status['verified_items']}  stale={status['stale_items']}")
        for knowledge_type, count in sorted(status["by_type"].items()):
            print(f"  {knowledge_type:<20} {count:>5}")
        print("by_status:", dict(sorted(status["by_status"].items())))
        snapshot = status["latest_snapshot"]
        if snapshot is not None:
            print(f"latest_snapshot={snapshot.snapshot_id} label={snapshot.label} "
                  f"activated={snapshot.activated}")
        print("freshness_max_age_days:", status["freshness_policies"])
        return 0
    finally:
        base.store.close()


def command_knowledge_sources(args) -> int:
    base = _open_knowledge(reseed=args.seed)
    try:
        sources = base.sources.sources()
        if args.community:
            sources = tuple(s for s in sources if s.authority_level == "COMMUNITY")
        if args.authority:
            sources = tuple(s for s in sources if s.authority_level == args.authority)
        for source in sources:
            checked = source.last_checked.isoformat() if source.last_checked else "never"
            print(f"{source.source_id:<28} {source.authority_level:<22} {source.refresh_policy:<14} "
                  f"checked={checked:<12} {source.name}")
        print(f"total={len(sources)}")
        return 0
    finally:
        base.store.close()


def command_knowledge_search(args) -> int:
    from datetime import date

    base = _open_knowledge(reseed=args.seed)
    try:
        changes = {"max_items": args.max}
        if args.knowledge_type:
            changes["knowledge_types"] = tuple(args.knowledge_type)
        if args.as_of:
            changes["as_of"] = date.fromisoformat(args.as_of)
        matches = base.search(args.query, **changes)
        if not matches:
            print("no matches")
            return 1
        for match in matches:
            item = match.item
            print(f"[{item.knowledge_type}] {item.knowledge_id}  ({item.source_authority}, "
                  f"score={match.score}, {','.join(match.reasons)})")
            print(f"    {item.title}: {item.summary[:160]}")
        print(f"total={len(matches)} (store has {base.store.item_count()} items)")
        return 0
    finally:
        base.store.close()


def command_knowledge_show(args) -> int:
    base = _open_knowledge(reseed=args.seed)
    try:
        item = base.get(args.identifier)
        if item is None:
            print(f"knowledge item {args.identifier!r} not found", file=sys.stderr)
            return 1
        print(json.dumps(item.model_dump(mode="json"), indent=2, ensure_ascii=False))
        history = base.store.history(item.knowledge_id)
        print(f"versions={[entry.version for entry in history]}")
        relations = base.store.relations(from_key=item.knowledge_id)
        for relation in relations:
            print(f"  {relation.relation_type} -> {relation.to_key}")
        return 0
    finally:
        base.store.close()


def command_knowledge_refresh(args) -> int:
    base = _open_knowledge(reseed=args.seed)
    try:
        try:
            diff = refresh_domain(base.store, args.domain, seed_dir=settings.knowledge_seed_path,
                                  source_dir=settings.knowledge_source_path)
        except Exception as error:  # noqa: BLE001 - a refresh failure must be reported, not crash
            print(f"refresh failed: {type(error).__name__}: {error}", file=sys.stderr)
            return 1
        print(f"domain={diff.domain} added={len(diff.added)} updated={len(diff.updated)} "
              f"unchanged={len(diff.unchanged)} rejected={len(diff.rejected)}")
        if diff.message:
            print(diff.message)
        return 0
    finally:
        base.store.close()


def command_knowledge_validate(args) -> int:
    from app.knowledge.ingestion import KnowledgeValidator

    sources = load_sources(settings.knowledge_source_path)
    packs = load_packs(settings.knowledge_seed_path)
    known_keys = [key for pack in packs for item in pack.items
                  for key in (item.canonical_key, item.knowledge_id)]
    manifest_source_ids = {source.source_id for source in sources}
    failed = False
    for pack in packs:
        source_ids = manifest_source_ids | {source.source_id for source in pack.sources}
        report = KnowledgeValidator(source_ids, known_keys).validate(pack)
        status = "OK" if report.ok and not report.rejected else "PROBLEMS"
        print(f"{pack.domain:<12} items={len(pack.items):<5} fatals={len(report.fatals)} "
              f"rejected={len(report.rejected)}  {status}")
        for fatal in report.fatals:
            print("   FATAL:", fatal)
        for key, reason in list(report.rejected.items())[:10]:
            print("   REJECT:", key, "->", reason)
        failed = failed or not report.ok or bool(report.rejected)
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="baseball-agent", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="run one analysis request (offline by default)")
    ask.add_argument("query")
    ask.add_argument("--mention", action="append", help="explicit entity mention (repeatable)")
    ask.add_argument("--empty", action="store_true", help="simulate a source returning no rows")
    ask.add_argument("--demo", action="store_true", help="explicitly enable synthetic analytics")
    ask.add_argument("--persist", action="store_true", help="persist to the operational store")
    ask.add_argument("--json", action="store_true")
    ask.set_defaults(func=command_ask)

    answer = sub.add_parser("answer", help="answer a persisted request and resume the same run")
    answer.add_argument("--run-id", required=True)
    answer.add_argument("--request-id", required=True)
    choices = answer.add_mutually_exclusive_group(required=True)
    choices.add_argument("--choice")
    choices.add_argument("--permission", choices=("approve", "reject"))
    choices.add_argument("--revision", choices=("accept", "reject"))
    answer.add_argument("--demo", action="store_true")
    answer.add_argument("--json", action="store_true")
    answer.set_defaults(func=command_answer)

    resume = sub.add_parser("resume", help="inspect resume state for a run")
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--execute", action="store_true", help="recover durable run intent")
    resume.add_argument("--demo", action="store_true")
    resume.add_argument("--json", action="store_true")
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

    knowledge = sub.add_parser("knowledge", help="inspect and refresh the Shared Knowledge base")
    knowledge.add_argument("--seed", action="store_true",
                           help="rebuild from committed manifests + seed packs first")
    ksub = knowledge.add_subparsers(dest="knowledge_command", required=True)

    kstatus = ksub.add_parser("status", help="counts, freshness and latest snapshot")
    kstatus.set_defaults(func=command_knowledge_status)

    ksources = ksub.add_parser("sources", help="list the source registry")
    ksources.add_argument("--authority", choices=("OFFICIAL", "AUTHORITATIVE_REFERENCE",
                                                   "TRUSTED_ANALYTICS", "TRUSTED_MEDIA",
                                                   "COMMUNITY", "UNVERIFIED"))
    ksources.add_argument("--community", action="store_true", help="only COMMUNITY sources")
    ksources.set_defaults(func=command_knowledge_sources)

    ksearch = ksub.add_parser("search", help="search knowledge by term, alias or key")
    ksearch.add_argument("query")
    ksearch.add_argument("--type", dest="knowledge_type", action="append")
    ksearch.add_argument("--max", type=int, default=8)
    ksearch.add_argument("--as-of", help="ISO date for temporal validity")
    ksearch.set_defaults(func=command_knowledge_search)

    kshow = ksub.add_parser("show", help="show one item with provenance and history")
    kshow.add_argument("identifier")
    kshow.set_defaults(func=command_knowledge_show)

    krefresh = ksub.add_parser("refresh", help="refresh a knowledge domain")
    krefresh.add_argument("domain", choices=("reference", "teams", "ballparks", "rules",
                                              "glossary", "players", "community", "context", "all"))
    krefresh.set_defaults(func=command_knowledge_refresh)

    kvalidate = ksub.add_parser("validate", help="validate committed seed packs")
    kvalidate.set_defaults(func=command_knowledge_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
