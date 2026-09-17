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
                   knowledge: KnowledgeBase | None = None,
                   use_llm: bool | None = None) -> AnalysisPipeline:
    """Compose the runtime. Real analytics uses dual semantic review when a provider is
    configured; pass ``use_llm=False`` to force the deterministic high-confidence path."""
    provider = None
    if use_llm is None:
        use_llm = bool(settings.deepseek_api_key) and not (demo or empty)
    if use_llm and settings.deepseek_api_key:
        from app.llm.openai_provider import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(settings)
    return AnalysisPipeline.default(persist=persist, empty=empty, demo=demo or empty,
                                    knowledge=knowledge, llm_provider=provider)


def _open_store() -> SqliteOperationalStore:
    settings.operational_store_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteOperationalStore(settings.operational_store_path)


def _print_result(result, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"needs_clarification": result.needs_clarification,
                          "objective_statuses": list(result.objective_statuses),
                          "responses": list(result.responses),
                          "understanding": (result.understanding.model_dump(mode="json")
                                            if result.understanding is not None else None),
                          "clarifications": [item.model_dump(mode="json") for item in result.clarifications],
                          "permissions": [item.model_dump(mode="json") for item in result.permissions],
                          "constraint_revisions": [item.model_dump(mode="json") for item in result.constraint_revisions],
                          "run_ids": list(result.run_ids)}, indent=2, ensure_ascii=False))
        return 0
    if result.needs_clarification:
        print("I need one clarification before I can continue.")
        for request in result.clarifications:
            print(f"Q: {request.question}")
            print(f"   (why: {request.reason})")
            for option in request.options:
                marker = "*" if option.option_id == request.recommended_option_id else " "
                print(f"   {marker} [{option.option_id}] {option.label} -> {option.value}")
            if request.options:
                print(f"   reply with: python3 -m app.cli answer --run-id {result.run_ids[0]} "
                      f"--request-id {request.clarification_id} --choice <option>")
            else:
                print(f"   run_id={result.run_ids[0]} request_id={request.clarification_id}")
        return 0
    for request in (*result.permissions, *result.constraint_revisions):
        print("I need one decision before I can continue.")
        print(f"  {request.model_dump_json()}")
        print(f"  run_id={result.run_ids[0]}")
    for index, (status, response) in enumerate(zip(result.objective_statuses, result.responses)):
        if index:
            print()
        print(response)
        package = result.response_packages[index] if index < len(result.response_packages) else None
        if package is not None and package.accepted_evidence:
            sources = sorted({item.source_kind for item in package.accepted_evidence})
            print(f"Data coverage: {', '.join(sources)}")
    return 0


def _build_agent(args):
    from app.agent.factory import build_agent
    return build_agent(use_llm=not getattr(args, "no_llm", False))


def _build_runtime(args):
    from app.artifact_runtime.factory import build_runtime
    from app.persistence.store import SqliteOperationalStore

    # Normal product CLI wires durable persistence: the advertised runtime is resumable.
    settings.operational_store_path.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteOperationalStore(settings.operational_store_path)
    return build_runtime(use_llm=not getattr(args, "no_llm", False), store=store)


def _print_runtime_trace(trace) -> None:
    """Structured artifact-runtime trace. Never hidden model chain-of-thought."""
    if trace is None:
        print("--- trace --- (none)")
        return
    print("--- trace ---")
    print(f"Raw Query: {trace.raw_query}")
    print("Goal")
    if trace.goal is not None:
        print(f"  statement: {trace.goal.statement}")
        print(f"  constraints: {'; '.join(trace.goal.constraints) or '(none)'}")
        print(f"  scope: {trace.goal.scope.model_dump() if trace.goal.scope else '(none)'}")
    print("Needs")
    for need in trace.needs:
        print(f"  [{need.criticality}] {need.need_id} -> {need.proposed_capability} "
              f"({need.status})")
        print(f"      objective: {need.objective}")
    print("Planner iterations")
    for decision in trace.decisions:
        request = decision.request
        capability = request.capability if request else "-"
        print(f"  #{decision.iteration} need={decision.need_id} tool={capability} "
              f"input_refs={list(request.input_refs) if request else []}")
    print("Tool outcomes")
    for attempt in getattr(trace, "attempts", ()):
        print(f"  {attempt.capability} need={attempt.need_id or '-'} "
              f"status={attempt.status} code={attempt.outcome_code} "
              f"retryable={attempt.retryable} detail={attempt.detail[:120]}")
    print("Recovery / notes")
    for step in trace.steps:
        print(f"  {step}")
    print("Artifacts")
    for summary in trace.artifact_summaries:
        print(f"  - {summary}")
    if trace.sql_statements:
        print("SQL boundary (compiled, read-only)")
        for sql in trace.sql_statements:
            print(f"  {sql[:400]}")
    print("Coverage assessment")
    for assessment in trace.assessments:
        print(f"  need={assessment.need_id} verdict={assessment.verdict} "
              f"entity={assessment.entity_coverage} temporal={assessment.temporal_coverage} "
              f"measure={assessment.measure_coverage} gaps={list(assessment.gaps)}")
    print(f"  core_goal_supported={trace.coverage.get('core_goal_supported')}")
    if trace.obligation_coverage:
        print("User obligations")
        for obligation in (trace.goal.obligations if trace.goal else ()):
            state = trace.obligation_coverage.get(obligation.obligation_id, "MISSING")
            print(f"  [{state}] {obligation.kind}: {obligation.description}")
    if getattr(trace, "events", ()):
        print("Event journal (projection)")
        for event in trace.events[-40:]:
            print(f"  {event.event_type} need={event.need_id or '-'} "
                  f"attempt={event.attempt_id or '-'} :: {event.detail[:160]}")
    if trace.claims:
        print("Claims")
        for claim in trace.claims:
            print(f"  - {claim.text[:160]}")
            print(f"      support: {list(claim.support_refs)}")
    print(f"Final state: {trace.status}")


def _print_agent_trace(trace) -> None:
    """Structured LLM-first runtime trace. Never hidden model chain-of-thought."""
    if trace is None:
        print("--- trace --- (none)")
        return
    print("--- trace ---")
    print(f"Raw Query: {trace.raw_query}")
    plan = trace.plan
    print("Cognition")
    if plan is not None:
        print(f"  user_goal: {plan.user_goal}")
        print(f"  understanding: {plan.understanding}")
        print(f"  analysis_strategy: {plan.analysis_strategy}")
        if plan.assumptions:
            print(f"  assumptions: {'; '.join(plan.assumptions)}")
        if plan.unresolved:
            print(f"  unresolved: {'; '.join(plan.unresolved)}")
        print(f"  planner_source: {plan.source}")
        if plan.clarification:
            print(f"  clarification: {plan.clarification.question}")
    if trace.tool_calls:
        print("Tools")
        for call in trace.tool_calls:
            print(f"  {call}")
    if trace.sql_requests:
        print("SQL Boundary (compiled SQLAnalysisRequest)")
        for request in trace.sql_requests:
            print(f"  {request[:400]}")
    if trace.steps:
        print("Recovery / notes")
        for step in trace.steps:
            print(f"  {step}")
    if trace.evidence:
        print("Evidence")
        for summary in trace.evidence:
            print(f"  - {summary[:160]}")
    print(f"Final state: {trace.status}")


def command_ask(args) -> int:
    if getattr(args, "legacy", False) or getattr(args, "demo", False) \
            or getattr(args, "empty", False):
        # Synthetic/demo analytics and the persisted requirement pipeline stay on the
        # legacy deterministic path; they never masquerade as live capability.
        return _command_ask_legacy(args)
    agent = _build_runtime(args)
    try:
        conversation_id = agent.start_conversation()
        result = agent.send_message(conversation_id, args.query)
        if args.trace:
            _print_runtime_trace(result.trace)
        if getattr(args, "json", False):
            print(json.dumps({"status": result.status, "answer": result.answer,
                              "pending_clarification": (
                                  result.pending_clarification.model_dump(mode="json")
                                  if result.pending_clarification else None),
                              "claims": [item.model_dump(mode="json")
                                         for item in result.claims]},
                             ensure_ascii=False, indent=2))
            return 0
        print(result.answer)
        if result.pending_clarification is not None:
            for index, option in enumerate(result.pending_clarification.options, 1):
                print(f"  {index}. {option}")
            print("  (answer naturally with: python3 -m app.cli chat)")
        return 0
    finally:
        agent.close()


def command_chat(args) -> int:
    agent = _build_runtime(args)
    try:
        conversation_id = agent.start_conversation()
        print("Baseball Agent — ask a baseball question in Chinese or English (Ctrl-D to exit).")
        while True:
            try:
                text = input("你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not text:
                continue
            if text.casefold() in ("exit", "quit", ":q"):
                break
            conversation = agent.get_conversation(conversation_id)
            if conversation.status == "WAITING_FOR_USER":
                mapped = _map_choice(conversation, text)
                result = agent.respond_to_clarification(conversation_id, mapped)
            else:
                result = agent.send_message(conversation_id, text)
            if getattr(args, "trace", False):
                _print_runtime_trace(result.trace)
            print(f"Agent: {result.answer}")
            if result.pending_clarification is not None:
                for index, option in enumerate(result.pending_clarification.options, 1):
                    print(f"  {index}. {option}")
        return 0
    finally:
        agent.close()


def _map_choice(conversation, text: str) -> str:
    """Let a user answer a clarification with a number instead of pasting option text."""
    pending = conversation.pending_clarification
    if pending is None or not pending.options:
        return text
    token = text.strip().rstrip(".、)）")
    if token.isdigit():
        index = int(token) - 1
        if 0 <= index < len(pending.options):
            return pending.options[index]
    return text


def _command_ask_legacy(args) -> int:
    pipeline = build_pipeline(persist=args.persist, empty=args.empty, demo=args.demo,
                              use_llm=False if args.no_llm else None)
    try:
        try:
            result = pipeline.analyze(args.query, mentions=tuple(args.mention) if args.mention else None)
        except (ValueError, RuntimeError) as error:
            # User language must never escape as an uncaught parser/semantic exception.
            print("I could not safely interpret that request, so I stopped instead of "
                  "guessing.")
            print(f"Reason: {type(error).__name__}: {error}")
            print("Try naming the player, the season/date range and the metric explicitly.")
            return 1
        if args.trace:
            _print_semantic_trace(pipeline, result)
        return _print_result(result, args.json)
    finally:
        pipeline.close()


def _print_semantic_trace(pipeline, result) -> None:
    """Print the open-world architecture trace. Never hidden model reasoning."""
    print("--- trace ---")
    print(f"Raw Query: {result.raw_query}")
    print("Semantic Understanding")
    printed: set[str] = set()
    if result.understanding is None:
        print("  (none)")
    else:
        for line in result.understanding.summary():
            print(f"  {line}")
            printed.add(line)
        if result.understanding.known_constraints:
            constraints = ", ".join(f"{item.kind}:{item.key}"
                                     for item in result.understanding.known_constraints)
            print(f"  structured_facts: {constraints}")
    for note in result.semantic_trace:
        if note in printed:
            continue
        print(f"  note: {note}")
    for index, objective in enumerate(result.objectives):
        constraints = ", ".join(f"{item.kind}:{item.key}" for item in objective.constraints)
        print(f"  canonical semantics[{index}]: {constraints or 'none'}")
    for run in result.runs:
        print("Planner")
        for decision in run.planning_decisions:
            print(f"  {decision.kind} round={decision.round} tasks={len(decision.tasks)} "
                  f"reason={decision.terminal_reason or '-'} :: {decision.rationale}")
            for task in decision.tasks:
                print(f"    task[{task.task_type}] {task.description}")
                if task.objective:
                    print(f"      objective: {task.objective}")
        print("Tool calls")
        for routing in run.routing_decisions:
            print(f"  task={routing.task_ref} tool={routing.selected_tool or 'BLOCKED'} "
                  f":: {routing.rationale}")
        print("SQL Boundary")
        import json as _json
        for outcome in run.executions:
            data = None
            if outcome.payload:
                try:
                    data = _json.loads(outcome.payload.decode())
                except (ValueError, UnicodeDecodeError):
                    data = None
            request = (data or {}).get("compiled_sql_request")
            if request:
                filters = ", ".join(item.get("kind", "") for item in request.get("filters", []))
                print(f"  compiled SQLAnalysisRequest: metric={request.get('metric')} "
                      f"agg={request.get('aggregation')} limit={request.get('limit')} "
                      f"source={request.get('source_kind')} filters=[{filters}]")
                print(f"  validation: {outcome.execution.status} "
                      f"({len(outcome.attempts)} attempt(s))")
            for attempt in outcome.attempts:
                if attempt.error_code:
                    print(f"  attempt={attempt.attempt_id} status={attempt.status} "
                          f"code={attempt.error_code} :: {attempt.safe_error_summary}")
        print("Artifacts")
        for assessment in run.assessments:
            print(f"  artifact={assessment.artifact_ref} level={assessment.final_level} "
                  f":: {assessment.assessment_summary}")
        print(f"Final state: {run.objective_state.status} "
              f"({run.completion_report.stop_reason})")


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


def command_doctor(args) -> int:
    from app.diagnostics import run_doctor
    report = run_doctor(check_llm=not args.no_llm)
    print(report.render())
    return 1 if report.failed else 0


def _open_candidates():
    from app.knowledge.candidates import CandidateKnowledgeStore
    path = settings.operational_store_path.parent / "candidate_knowledge.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return CandidateKnowledgeStore(path)


def _open_governance():
    from app.knowledge.governance import KnowledgeGovernance
    candidates = _open_candidates()
    base = _open_knowledge()
    return KnowledgeGovernance(candidates, base), candidates, base


def command_knowledge_inspect(args) -> int:
    gov, candidates, base = _open_governance()
    try:
        report = gov.inspect(args.candidate_id)
        candidate = report["candidate"]
        print(json.dumps(candidate.model_dump(mode="json"), indent=2, ensure_ascii=False))
        conflicts = list(report["conflicts"])
        print("conflicts:")
        for conflict in conflicts or ["(none)"]:
            print(f"  - {conflict}")
        return 0
    except KeyError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        candidates.close()
        base.store.close()


def command_knowledge_approve(args) -> int:
    from app.knowledge.governance import GovernanceError
    gov, candidates, base = _open_governance()
    try:
        try:
            item = gov.approve(args.candidate_id, admin=args.admin,
                               supersede=args.supersede, note=args.note or "")
        except (KeyError, GovernanceError) as error:
            print(f"cannot approve: {error}", file=sys.stderr)
            return 1
        print(f"{args.candidate_id} -> ACTIVE knowledge {item.knowledge_id}")
        return 0
    finally:
        candidates.close()
        base.store.close()


def command_knowledge_edit_approve(args) -> int:
    from app.knowledge.candidates import CandidateScope
    from app.knowledge.governance import GovernanceError
    gov, candidates, base = _open_governance()
    try:
        candidate = candidates.get(args.candidate_id)
        if candidate is None:
            print(f"Unknown candidate {args.candidate_id!r}", file=sys.stderr)
            return 1
        edits: dict = {}
        if args.meaning:
            edits["meaning"] = args.meaning
        if args.proposed_type:
            edits["proposed_type"] = args.proposed_type
        if args.language:
            edits["language"] = args.language
        scope = candidate.scope or CandidateScope()
        scope_changes = {}
        for field in ("domain", "community", "authority"):
            value = getattr(args, field, None)
            if value:
                scope_changes[field] = value
        if scope_changes:
            edits["scope"] = scope.model_copy(update=scope_changes)
        try:
            item = gov.approve(args.candidate_id, admin=args.admin, edits=edits,
                               supersede=args.supersede, note=args.note or "")
        except (KeyError, GovernanceError) as error:
            print(f"cannot approve: {error}", file=sys.stderr)
            return 1
        print(f"{args.candidate_id} -> ACTIVE knowledge {item.knowledge_id}")
        return 0
    finally:
        candidates.close()
        base.store.close()


def command_knowledge_reject(args) -> int:
    gov, candidates, base = _open_governance()
    try:
        try:
            updated = gov.reject(args.candidate_id, reason=args.reason or "",
                                 admin=args.admin)
        except KeyError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f"{updated.candidate_id} -> {updated.status}")
        return 0
    finally:
        candidates.close()
        base.store.close()


def command_knowledge_candidates(args) -> int:
    store = _open_candidates()
    try:
        items = store.list(status=args.status)
        for item in items:
            print(f"{item.candidate_id}  [{item.status}] {item.surface} = {item.meaning[:120]}")
            if item.evidence:
                print(f"    evidence: {item.evidence[0][:160]}")
        print(f"total={len(items)}")
        return 0
    finally:
        store.close()


def command_knowledge_review(args) -> int:
    store = _open_candidates()
    try:
        candidate = store.review(args.candidate_id, approve=args.approve)
        print(f"{candidate.candidate_id} -> {candidate.status}")
        if args.approve and args.ingest:
            base = _open_knowledge()
            try:
                from app.models.knowledge import KnowledgeItem
                base.store.upsert_item(KnowledgeItem(
                    knowledge_id=f"CANDIDATE:{candidate.candidate_id}",
                    canonical_key=candidate.surface, knowledge_type="ALIAS",
                    title=candidate.surface, aliases=(candidate.surface,),
                    summary=candidate.meaning or candidate.context,
                    source_authority="COMMUNITY"))
                print("ingested into Shared Knowledge")
            finally:
                base.store.close()
        return 0
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="baseball-agent", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="ask one baseball question (LLM-first conversational runtime)")
    ask.add_argument("query")
    ask.add_argument("--mention", action="append", help="explicit entity mention (repeatable)")
    ask.add_argument("--empty", action="store_true", help="simulate a source returning no rows")
    ask.add_argument("--demo", action="store_true", help="explicitly enable synthetic analytics")
    ask.add_argument("--persist", action="store_true", help="persist to the operational store")
    ask.add_argument("--no-llm", action="store_true",
                     help="use the deterministic fallback planner instead of the LLM")
    ask.add_argument("--legacy", action="store_true",
                     help="use the legacy requirement/semantic pipeline")
    ask.add_argument("--trace", action="store_true",
                     help="print structured runtime decisions (no model reasoning)")
    ask.add_argument("--json", action="store_true")
    ask.set_defaults(func=command_ask)

    chat = sub.add_parser("chat", help="interactive multi-turn conversation")
    chat.add_argument("--no-llm", action="store_true", help="deterministic fallback planner")
    chat.add_argument("--trace", action="store_true")
    chat.set_defaults(func=command_chat)

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

    doctor = sub.add_parser("doctor", help="non-destructive environment/source preflight")
    doctor.add_argument("--no-llm", action="store_true", help="skip the live model probe")
    doctor.set_defaults(func=command_doctor)

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

    kcandidates = ksub.add_parser("candidates", help="list runtime-proposed candidate knowledge")
    kcandidates.add_argument("--status", choices=("CANDIDATE", "UNDER_REVIEW", "APPROVED",
                                                   "ACTIVE", "REJECTED", "RETIRED", "SUPERSEDED"))
    kcandidates.set_defaults(func=command_knowledge_candidates)

    kinspect = ksub.add_parser("inspect", help="show one candidate with evidence and conflicts")
    kinspect.add_argument("candidate_id")
    kinspect.set_defaults(func=command_knowledge_inspect)

    kapprove = ksub.add_parser("approve", help="administrator: promote a candidate to ACTIVE")
    kapprove.add_argument("candidate_id")
    kapprove.add_argument("--admin", default="administrator")
    kapprove.add_argument("--supersede", action="store_true",
                          help="explicitly supersede conflicting existing knowledge")
    kapprove.add_argument("--note", default="")
    kapprove.set_defaults(func=command_knowledge_approve)

    kedit = ksub.add_parser("edit-approve", help="administrator: edit then promote to ACTIVE")
    kedit.add_argument("candidate_id")
    kedit.add_argument("--meaning")
    kedit.add_argument("--type", dest="proposed_type")
    kedit.add_argument("--language")
    kedit.add_argument("--domain")
    kedit.add_argument("--community")
    kedit.add_argument("--authority", choices=("OFFICIAL", "AUTHORITATIVE_REFERENCE",
                                                 "TRUSTED_ANALYTICS", "TRUSTED_MEDIA",
                                                 "COMMUNITY", "UNVERIFIED"))
    kedit.add_argument("--admin", default="administrator")
    kedit.add_argument("--supersede", action="store_true")
    kedit.add_argument("--note", default="")
    kedit.set_defaults(func=command_knowledge_edit_approve)

    kreject = ksub.add_parser("reject", help="administrator: reject a candidate")
    kreject.add_argument("candidate_id")
    kreject.add_argument("--reason", default="")
    kreject.add_argument("--admin", default="administrator")
    kreject.set_defaults(func=command_knowledge_reject)

    kreview = ksub.add_parser("review", help="approve/reject a candidate (admin action)")
    kreview.add_argument("candidate_id")
    group = kreview.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", dest="approve", action="store_false")
    kreview.add_argument("--ingest", action="store_true",
                         help="with --approve, also write it into Shared Knowledge")
    kreview.set_defaults(func=command_knowledge_review)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
