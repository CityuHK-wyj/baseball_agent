"""Extract AnalysisObjectives from a normalized query.

The Planner must not re-interpret the user's intent (D023), so objective extraction
happens here. The deterministic implementation is keyword-cue based and is the tested
default; an LLM implementation can replace it behind the same Protocol.
"""

from collections.abc import Callable
from typing import Protocol

from app.models.contracts import AnalysisObjective, Constraint, Entity

# Ordered so the first matching cue family wins for the default objective.
_TYPE_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("INJURY", ("injur", "伤病", "傷病", "受伤", "受傷", "伤停", "傷停",
                 "injured list", " il ")),
    ("VALUE", ("salary", "薪资", "薪資", "薪水", "合同", "性价比", "性價比",
                "value", "payroll", "cost")),
    ("STRATEGY", ("strategy", "策略", "战术", "戰術", "lineup", "打线", "打線", "shift", "bunt")),
    ("CONTEXT", ("news", "新闻", "新聞", "舆论", "輿論", "background", "背景", "rumor")),
)

_BASE_PRIORITY = {"PERFORMANCE": "MEDIUM", "INJURY": "HIGH", "VALUE": "MEDIUM",
                  "STRATEGY": "LOW", "CONTEXT": "LOW"}


class ObjectiveExtractor(Protocol):
    def extract(self, raw_query: str, entities: tuple[Entity, ...] = (),
                constraints: tuple[Constraint, ...] = ()) -> tuple[AnalysisObjective, ...]: ...


class RuleBasedObjectiveExtractor:
    def __init__(self, id_factory: Callable[[str], str] | None = None) -> None:
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{abs(hash(prefix))}")

    def extract(self, raw_query: str, entities: tuple[Entity, ...] = (),
                constraints: tuple[Constraint, ...] = ()) -> tuple[AnalysisObjective, ...]:
        lowered = raw_query.casefold()
        knowledge_question = any(cue in lowered for cue in (
            "是什么意思", "是什么", "哪个分区", "规则", "what is ", "what does ", "which division"))
        matched = [objective_type for objective_type, cues in _TYPE_CUES
                   if any(cue in lowered for cue in cues)]
        if not matched:
            matched = ["PERFORMANCE"]
        if knowledge_question:
            matched = ["CONTEXT"]
        return tuple(
            AnalysisObjective(
                objective_id=self._id_factory("objective"), raw_query=raw_query,
                description=raw_query, objective_type=objective_type,
                subtype="knowledge" if knowledge_question else objective_type.casefold(), entities=entities, constraints=constraints,
                base_priority=_BASE_PRIORITY[objective_type])
            for objective_type in matched
        )
