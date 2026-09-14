"""Read-side persistence: build a resume plan from a checkpoint.

An interrupted run must not assume its ``RUNNING`` executions are still running.
They are reclassified as ``INTERRUPTED``; already-persisted artifacts are reused
rather than re-fetched, and a terminal Planner decision stays terminal.
"""

from typing import Literal

from app.models.artifacts import Artifact, ArtifactAssessment, ArtifactContract
from app.models.checkpoint import TERMINAL_POSITIONS
from app.models.contracts import Name, ObjectiveState, RequirementState
from app.models.planning import TaskExecution
from app.persistence.store import OperationalStore

_INTERRUPTED_FROM = ("PENDING", "RUNNING")


class ResumePlan(ArtifactContract):
    run_id: Name
    status: Literal["RESUMABLE", "NO_CHECKPOINT"]
    recovery_position: str = ""
    reusable_artifact_refs: tuple[Name, ...] = ()
    interrupted_execution_refs: tuple[Name, ...] = ()
    pending_request_refs: tuple[Name, ...] = ()
    state_version_refs: tuple[Name, ...] = ()
    planner_terminal: bool = False


class RestoredRun(ArtifactContract):
    """Independently persisted domains rehydrated for a resumed run.

    Each domain stays in its own store; this is a convenience view, not a merged
    AgentState blob.
    """

    run_id: Name
    recovery_position: str = ""
    planner_terminal: bool = False
    artifacts: tuple[Artifact, ...] = ()
    assessments: tuple[ArtifactAssessment, ...] = ()
    requirement_states: tuple[RequirementState, ...] = ()
    objective_state: ObjectiveState | None = None


class ResumeService:
    def __init__(self, store: OperationalStore) -> None:
        self._store = store

    def build_plan(self, run_id: str) -> ResumePlan:
        checkpoint = self._store.latest_checkpoint(run_id)
        if checkpoint is None:
            return ResumePlan(run_id=run_id, status="NO_CHECKPOINT")

        interrupted: list[str] = []
        for record in self._store.list_objects("execution", run_id):
            execution = TaskExecution.model_validate(record.payload["execution"])
            if execution.status not in _INTERRUPTED_FROM:
                continue
            interrupted.append(execution.execution_id)
            resumed = execution.model_copy(update={"status": "INTERRUPTED"})
            payload = dict(record.payload)
            payload["execution"] = resumed.model_dump(mode="json")
            self._store.save_object("execution", execution.execution_id, run_id, payload)

        reusable = tuple(record.object_id for record in self._store.list_objects("artifact", run_id))
        return ResumePlan(
            run_id=run_id, status="RESUMABLE", recovery_position=checkpoint.recovery_position,
            reusable_artifact_refs=reusable, interrupted_execution_refs=tuple(interrupted),
            pending_request_refs=checkpoint.pending_request_refs,
            state_version_refs=checkpoint.state_version_refs,
            planner_terminal=checkpoint.recovery_position in TERMINAL_POSITIONS)

    def rehydrate(self, run_id: str) -> RestoredRun | None:
        """Load persisted domains for a resumed run, without merging them into one blob."""
        plan = self.build_plan(run_id)
        if plan.status == "NO_CHECKPOINT":
            return None
        artifacts = tuple(
            Artifact.model_validate(record.payload)
            for record in self._store.list_objects("artifact", run_id))
        assessments = tuple(
            ArtifactAssessment.model_validate(record.payload)
            for record in self._store.list_objects("assessment", run_id))
        requirement_states = tuple(
            RequirementState.model_validate(record.payload)
            for record in self._store.list_objects("requirement_state", run_id))
        objective_records = self._store.list_objects("objective_state", run_id)
        objective_state = (ObjectiveState.model_validate(objective_records[0].payload)
                           if objective_records else None)
        return RestoredRun(
            run_id=run_id, recovery_position=plan.recovery_position,
            planner_terminal=plan.planner_terminal, artifacts=artifacts,
            assessments=assessments, requirement_states=requirement_states,
            objective_state=objective_state)
