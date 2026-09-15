"""Write-side persistence: record accepted products and take checkpoints.

The recorder keeps artifacts, assessments, states, executions and reports in the
operational store, and payloads in artifact storage. Payloads are written before
any state references them.
"""

from collections.abc import Callable, Iterable
from uuid import uuid4

from app.models.artifacts import Artifact, ArtifactAssessment
from app.models.checkpoint import Checkpoint, RecoveryPosition
from app.models.contracts import ObjectiveState, RequirementState
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
                         attempts: Iterable[TaskAttempt] = (), artifact: Artifact | None = None) -> None:
        payload = {"execution": execution.model_dump(mode="json"),
                   "attempts": [attempt.model_dump(mode="json") for attempt in attempts],
                   "artifact_ref": artifact.artifact_id if artifact else None}
        self._store.save_object("execution", execution.execution_id, run_id, payload)

    def record_completion_report(self, run_id: str, report: CompletionReport) -> None:
        self._store.save_object("completion_report", report.run_id, run_id, report.model_dump(mode="json"))

    def record_response_package(self, run_id: str, package: ResponsePackage) -> None:
        self._store.save_object("response_package", package.run_id, run_id, package.model_dump(mode="json"))

    def record_interaction(self, interaction: InteractionRecord) -> None:
        self._store.save_object("interaction_audit", self._id_factory("interaction-event"),
                                interaction.run_id, interaction.model_dump(mode="json"))
        self._store.save_object("interaction", interaction.run_id, interaction.run_id,
                                interaction.model_dump(mode="json"))

    def load_interaction(self, run_id: str) -> InteractionRecord | None:
        record = self._store.get_object("interaction", run_id)
        return InteractionRecord.model_validate(record.payload) if record else None

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
