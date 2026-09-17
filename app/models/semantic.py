"""Result of semantic normalization: objectives plus any clarification required."""

from app.models.clarification import ClarificationRequest
from app.models.contracts import AnalysisObjective, Contract, Name
from app.models.understanding import SemanticUnderstanding


class SemanticResult(Contract):
    raw_query: Name
    objectives: tuple[AnalysisObjective, ...] = ()
    clarifications: tuple[ClarificationRequest, ...] = ()
    unresolved_mentions: tuple[Name, ...] = ()
    notes: tuple[str, ...] = ()
    # Open-world interpretation travels with the normalized result. It is not executable
    # semantics; it exists so the Planner is not an information bottleneck.
    understanding: SemanticUnderstanding | None = None

    @property
    def needs_clarification(self) -> bool:
        return bool(self.clarifications)

    @property
    def ready(self) -> bool:
        return bool(self.objectives) and not self.clarifications
