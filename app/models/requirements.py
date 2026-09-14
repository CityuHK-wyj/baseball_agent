"""Requirement baseline ownership. Planning can append, never replace definitions."""

from app.models.contracts import AnalysisObjective, ArtifactRequirement, ObjectiveState, RequirementState


class RequirementCatalog:
    def __init__(self, objectives: tuple[AnalysisObjective, ...], initial: tuple[ArtifactRequirement, ...]):
        self._objectives = tuple(objectives)
        self._initial = tuple(initial)
        self._supporting: tuple[ArtifactRequirement, ...] = ()
        objective_ids = {o.objective_id for o in self._objectives}
        if not objective_ids or len(objective_ids) != len(self._objectives):
            raise ValueError("Objectives must be nonempty and unique")
        if len({r.requirement_id for r in self._initial}) != len(self._initial):
            raise ValueError("Initial requirement identities must be unique")
        for requirement in self._initial:
            if requirement.origin != "INITIAL" or requirement.parent_ref is not None:
                raise ValueError("Baseline must contain original atomic requirements")
            if requirement.objective_ref not in objective_ids:
                raise ValueError("Unknown requirement objective")
        if any(not self.completion_requirements(oid) for oid in objective_ids):
            raise ValueError("Every objective needs at least one core initial requirement")
        self._objective_states = tuple(ObjectiveState(
            objective_ref=o.objective_id,
            requirement_refs=tuple(r.requirement_id for r in self._initial if r.objective_ref == o.objective_id)
        ) for o in self._objectives)
        self._requirement_states = tuple(RequirementState(requirement_ref=r.requirement_id) for r in self._initial)

    @property
    def initial_requirements(self) -> tuple[ArtifactRequirement, ...]:
        return self._initial

    @property
    def objective_states(self) -> tuple[ObjectiveState, ...]:
        return self._objective_states

    @property
    def requirement_states(self) -> tuple[RequirementState, ...]:
        return self._requirement_states

    @property
    def supporting_requirements(self) -> tuple[ArtifactRequirement, ...]:
        return self._supporting

    def completion_requirements(self, objective_ref: str) -> tuple[ArtifactRequirement, ...]:
        if objective_ref not in {o.objective_id for o in self._objectives}:
            raise ValueError("Unknown objective")
        return tuple(r for r in self._initial if r.objective_ref == objective_ref and r.base_criticality == "CORE")

    def add_supporting(self, requirement: ArtifactRequirement) -> None:
        if requirement.origin != "PLANNER_ADDED":
            raise ValueError("Planner additions require PLANNER_ADDED origin")
        if requirement.objective_ref not in {o.objective_id for o in self._objectives}:
            raise ValueError("Unknown requirement objective")
        existing = {r.requirement_id: r for r in self._initial + self._supporting}
        if requirement.requirement_id in existing:
            if requirement in self._supporting:
                return
            raise ValueError("Requirement identity cannot be reused or changed")
        if requirement.parent_ref is not None:
            parent = existing.get(requirement.parent_ref)
            if parent is None or parent.objective_ref != requirement.objective_ref:
                raise ValueError("Parent must already exist in the same objective")
        self._supporting += (requirement,)
        self._requirement_states += (RequirementState(requirement_ref=requirement.requirement_id),)
