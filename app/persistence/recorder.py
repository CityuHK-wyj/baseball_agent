"""Write-side persistence: record accepted products and take checkpoints.

The recorder keeps artifacts, assessments, states, executions and reports in the
operational store, and payloads in artifact storage. Payloads are written before
any state references them.
"""

from collections.abc import Callable, Iterable
from uuid import uuid4
import hashlib
import json

from app.models.artifacts import Artifact, ArtifactAssessment
from app.models.checkpoint import Checkpoint, RecoveryPosition
from app.models.contracts import AnalysisObjective, ArtifactRequirement, ObjectiveState, RequirementState
from app.models.interaction import InteractionRecord
from app.models.planning import TaskAttempt, TaskExecution
from app.models.reports import CompletionReport, ResponsePackage
from app.persistence.artifacts import ArtifactStorage, StoredArtifact
from app.persistence.store import OperationalStore


class RunRecorder:
    def __init__(self, store: OperationalStore, artifact_storage: ArtifactStorage,
                 id_factory: Callable[[str], str] | None = None) -> None:
        self._store = store
        self._storage = artifact_storage
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")

    def record_metric(self, event) -> None:
        self._store.save_object("run_event", self._id_factory("event"), event.run_id,
                                event.model_dump(mode="json"))

    def read_payload(self, artifact_id: str) -> bytes | None:
        """Read a stored artifact payload back, or None when it is absent."""
        try:
            return self._storage.get(artifact_id)
        except (FileNotFoundError, ValueError):
            return None

    def execute_once(self, run_id, task, routing, executor):
        """Journal a semantic work intent before any external execution.

        An uncompleted intent is uncertain, never an instruction to retry. Payloads
        remain in artifact storage; the durable outcome holds references only.
        """
        from app.agent.executor import ExecutionOutcome
        identity = json.dumps([run_id, task.objective_ref, sorted(task.requirement_refs)])
        key = hashlib.sha256(identity.encode()).hexdigest()
        intent = {"task": task.model_dump(mode="json"), "routing": routing.model_dump(mode="json")}
        if routing.selected_tool is None:
            return executor.run(task, routing)
        if not self._store.create_object("execution_intent", key, run_id, intent):
            original = self._store.get_object("execution_intent", key)
            if original.run_id != run_id or original.payload["routing"]["selected_tool"] != routing.selected_tool:
                raise ValueError("Persisted execution scope differs from current routing")
            record = self._store.get_object("execution_outcome", key)
            payload = record.payload if record else None
            if payload is None:
                completed = [item.payload for item in self._store.list_objects("execution", run_id)
                             if item.payload.get("intent_ref") == key and
                             item.payload["execution"]["status"] in ("SUCCEEDED", "EMPTY", "FAILED", "BLOCKED")]
                if len(completed) != 1:
                    raise RuntimeError("EXECUTION_UNCERTAIN: persisted intent has no durable outcome; automatic retry prohibited")
                payload = dict(completed[0], supporting_refs=[])
                if payload["artifact_ref"]:
                    stored = self._store.get_object("artifact", payload["artifact_ref"])
                    if stored is None or stored.run_id != run_id:
                        raise RuntimeError("Durable execution artifact is missing")
                    payload["supporting_refs"] = stored.payload.get("lineage", [])
            def artifact(reference):
                stored = self._store.get_object("artifact", reference)
                if stored is None or stored.run_id != run_id:
                    raise RuntimeError("Execution references a missing or foreign artifact")
                return Artifact.model_validate(stored.payload)
            return ExecutionOutcome(
                execution=TaskExecution.model_validate(payload["execution"]),
                attempts=tuple(TaskAttempt.model_validate(item) for item in payload["attempts"]),
                artifact=artifact(payload["artifact_ref"]) if payload["artifact_ref"] else None,
                supporting_artifacts=tuple(artifact(reference) for reference in payload["supporting_refs"]))
        outcome = executor.run(task, routing)
        for supporting in outcome.supporting_artifacts:
            self.record_artifact(run_id, supporting)
        artifact = outcome.artifact
        if artifact is not None:
            stored = self.record_artifact(run_id, artifact, outcome.payload, outcome.payload_content_type)
            if stored:
                artifact = artifact.model_copy(update={"payload_ref": stored.location})
        self.record_execution(run_id, outcome.execution, outcome.attempts, artifact, intent_ref=key)
        self._store.save_object("execution_outcome", key, run_id, {
            "execution": outcome.execution.model_dump(mode="json"),
            "attempts": [item.model_dump(mode="json") for item in outcome.attempts],
            "artifact_ref": artifact.artifact_id if artifact else None,
            "supporting_refs": [item.artifact_id for item in outcome.supporting_artifacts]})
        return outcome.model_copy(update={"artifact": artifact, "payload": None})

    def record_artifact(self, run_id: str, artifact: Artifact, payload: bytes | None = None,
                        content_type: str = "application/json") -> StoredArtifact | None:
        """Persist the payload before the metadata that points at it.

        Returns the stored payload record, or ``None`` when no inline payload was
        supplied. The metadata is never written before a successful payload write.
        """
        stored: StoredArtifact | None = None
        record = artifact
        if payload is not None:
            stored = self._storage.put(artifact.artifact_id, payload, content_type)
            record = artifact.model_copy(update={"payload_ref": stored.location})
        self._store.save_object("artifact", artifact.artifact_id, run_id, record.model_dump(mode="json"))
        return stored

    def record_assessment(self, run_id: str, assessment: ArtifactAssessment) -> None:
        self._store.save_object("assessment", assessment.assessment_id, run_id,
                                assessment.model_dump(mode="json"))

    def record_requirement_states(self, run_id: str, states: dict[str, RequirementState]) -> None:
        for reference, state in states.items():
            self._store.save_object("requirement_state", reference, run_id, state.model_dump(mode="json"))

    def record_objective_state(self, run_id: str, state: ObjectiveState) -> None:
        self._store.save_object("objective_state", state.objective_ref, run_id, state.model_dump(mode="json"))

    def record_execution(self, run_id: str, execution: TaskExecution,
                         attempts: Iterable[TaskAttempt] = (), artifact: Artifact | None = None,
                         intent_ref: str | None = None) -> None:
        existing = self._store.get_object("execution", execution.execution_id)
        intent_ref = intent_ref or (existing.payload.get("intent_ref") if existing else None)
        payload = {"execution": execution.model_dump(mode="json"),
                   "attempts": [attempt.model_dump(mode="json") for attempt in attempts],
                   "artifact_ref": artifact.artifact_id if artifact else None,
                   "intent_ref": intent_ref}
        self._store.save_object("execution", execution.execution_id, run_id, payload)

    def record_completion_report(self, run_id: str, report: CompletionReport) -> None:
        self._store.save_object("completion_report", f"{len(run_id)}:{run_id}{report.objective_ref}",
                                run_id, report.model_dump(mode="json"))

    def record_response_package(self, run_id: str, package: ResponsePackage) -> None:
        self._store.save_object("response_package", f"{len(run_id)}:{run_id}{package.objective_ref}",
                                run_id, package.model_dump(mode="json"))

    def record_interaction(self, interaction: InteractionRecord) -> None:
        self._store.save_object("interaction_audit", self._id_factory("interaction-event"),
                                interaction.run_id, interaction.model_dump(mode="json"))
        self._store.save_object("interaction", interaction.run_id, interaction.run_id,
                                interaction.model_dump(mode="json"))

    def load_interaction(self, run_id: str) -> InteractionRecord | None:
        record = self._store.get_object("interaction", run_id)
        if record and record.payload.get("permission") and "expires_at" not in record.payload["permission"]:
            raise ValueError("Legacy permission expired; issue a new scoped request")
        return InteractionRecord.model_validate(record.payload) if record else None

    def has_run(self, run_id: str) -> bool:
        return (self._store.latest_checkpoint(run_id) is not None or
                self._store.get_object("interaction", run_id) is not None or
                self._store.get_object("run_definition", run_id) is not None or
                bool(self._store.list_objects("initial_definition", run_id)))

    def record_run_definition(self, run_id, raw_query, objectives):
        payload = {"raw_query": raw_query, "objectives": [item.model_dump(mode="json") for item in objectives]}
        self._store.create_object("run_definition", run_id, run_id, payload)
        if self._store.get_object("run_definition", run_id).payload != payload:
            raise ValueError("Persisted run definition cannot be changed")

    def run_definition(self, run_id):
        record = self._store.get_object("run_definition", run_id)
        if record is None:
            return None
        return record.payload["raw_query"], tuple(AnalysisObjective.model_validate(item)
                                                  for item in record.payload["objectives"])

    def restore_objective(self, run_id, objective_ref, requirement_refs):
        from app.persistence.resume import ResumeService
        return ResumeService(self._store).rehydrate(run_id, objective_ref=objective_ref,
                                                    requirement_refs=requirement_refs)

    def initial_definitions(self, run_id: str):
        return tuple((AnalysisObjective.model_validate(record.payload["objective"]),
                      tuple(ArtifactRequirement.model_validate(item) for item in record.payload["requirements"]))
                     for record in self._store.list_objects("initial_definition", run_id))

    def record_initial_definition(self, run_id: str, objective: AnalysisObjective,
                                   requirements: tuple[ArtifactRequirement, ...]):
        key = f"{len(run_id)}:{run_id}{objective.objective_id}"
        payload = {"objective": objective.model_dump(mode="json"),
                   "requirements": [item.model_dump(mode="json") for item in requirements]}
        self._store.create_object("initial_definition", key, run_id, payload)
        record = self._store.get_object("initial_definition", key)
        if record.payload["objective"] != payload["objective"]:
            raise ValueError("Persisted initial objective cannot be changed")
        return tuple(ArtifactRequirement.model_validate(item) for item in record.payload["requirements"])

    def consume_interaction(self, pending: InteractionRecord, result: InteractionRecord) -> None:
        record = self._store.get_object("interaction", pending.run_id)
        if (record is None or record.payload != pending.model_dump(mode="json") or
                pending.status != "WAITING_FOR_USER" or result.run_id != pending.run_id or
                result.status == "WAITING_FOR_USER"):
            raise ValueError("Interaction is stale or already consumed")
        if not self._store.replace_object(record, result.model_dump(mode="json")):
            raise ValueError("Interaction is stale or already consumed")
        self._store.save_object("interaction_audit", self._id_factory("interaction-event"),
                                result.run_id, result.model_dump(mode="json"))

    def checkpoint(self, run_id: str, position: RecoveryPosition, *,
                   active_work_refs: Iterable[str] = (),
                   pending_request_refs: Iterable[str] = (),
                   terminal_condition: tuple[tuple[str, ...], tuple[str, ...]] | None = None) -> Checkpoint:
        references = [
            f"{record.kind}:{record.object_id}:v{record.version}"
            for record in self._store.list_objects("requirement_state", run_id)
        ]
        references += [
            f"{record.kind}:{record.object_id}:v{record.version}"
            for record in self._store.list_objects("objective_state", run_id)
        ]
        checkpoint = Checkpoint(
            checkpoint_id=self._id_factory("checkpoint"), run_id=run_id, recovery_position=position,
            state_version_refs=tuple(references), active_work_refs=tuple(active_work_refs),
            pending_request_refs=tuple(pending_request_refs), terminal_condition=terminal_condition)
        self._store.save_checkpoint(checkpoint)
        return checkpoint
