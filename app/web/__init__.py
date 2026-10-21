"""Web Research v2 — internal source, budget and evidence-version contracts (M1).

Nothing in this package is Planner-visible. M1 declares the contracts and observability;
budget/repeated-call enforcement, ranking, parallelism and caching remain M2/M3.
"""

from __future__ import annotations

from app.web.budget import (
    GoalResearchBudget,
    ResearchBudget,
    ResearchBudgetUsage,
    effective_budget,
)

__all__ = [
    "GoalResearchBudget",
    "ResearchBudget",
    "ResearchBudgetUsage",
    "effective_budget",
]
