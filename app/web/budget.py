"""Research budget contracts (ADR 0028, SPEC §13.6).

M1 declares the Goal-level and per-call budget **types** and the pure clamping helper.
It deliberately does **not** enforce the quota, reset logic, deadlines or concurrency
reservation; those are M3. This module is a data contract plus one pure function, so the
rest of M1 can record budget counters without changing answer behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass
class GoalResearchBudget:
    """A single budget shared across every ``web_research`` call in one Goal.

    M1 stores this on the Goal state as a contract; enforcement arrives in M3.
    """

    goal_provider_calls: int = 24
    goal_documents_fetched: int = 10
    goal_llm_query_calls: int = 2
    goal_llm_verify_calls: int = 12
    goal_search_rounds: int = 3
    goal_soft_deadline_ms: int = 30_000
    goal_hard_deadline_ms: int = 45_000


@dataclass
class ResearchBudget:
    """Per-``execute()`` budget; its effective values clamp to the Goal remainder."""

    max_search_rounds: int = 2
    max_queries_per_round: int = 4
    max_provider_calls: int = 8
    max_documents_fetched: int = 4
    max_parallel_queries: int = 4
    max_parallel_fetches: int = 4
    max_llm_query_calls: int = 1
    max_llm_verify_calls: int = 6
    provider_timeout_ms: int = 6_000
    fetch_timeout_ms: int = 10_000
    soft_deadline_ms: int = 30_000
    hard_deadline_ms: int = 45_000
    max_bytes_per_doc: int = 2_000_000


@dataclass
class ResearchBudgetUsage:
    """Counters consumed so far at the Goal level (observability in M1)."""

    provider_calls: int = 0
    documents_fetched: int = 0
    llm_query_calls: int = 0
    llm_verify_calls: int = 0
    search_rounds: int = 0


def _remaining(limit: int, used: int) -> int:
    return max(0, int(limit) - int(used))


def effective_budget(
    tool: ResearchBudget,
    goal: GoalResearchBudget,
    used: ResearchBudgetUsage | None = None,
) -> ResearchBudget:
    """Return the per-call budget clamped to the remaining Goal quota.

    ``effective = min(tool_default, goal_remaining)``. Deadline fields clamp too so a late
    Planner round can never extend the shared Goal clock. Pure function, no side effects.
    """
    used = used or ResearchBudgetUsage()
    return replace(
        tool,
        max_search_rounds=min(tool.max_search_rounds, _remaining(goal.goal_search_rounds, used.search_rounds)),
        max_provider_calls=min(tool.max_provider_calls, _remaining(goal.goal_provider_calls, used.provider_calls)),
        max_documents_fetched=min(
            tool.max_documents_fetched, _remaining(goal.goal_documents_fetched, used.documents_fetched)
        ),
        max_llm_query_calls=min(tool.max_llm_query_calls, _remaining(goal.goal_llm_query_calls, used.llm_query_calls)),
        max_llm_verify_calls=min(
            tool.max_llm_verify_calls, _remaining(goal.goal_llm_verify_calls, used.llm_verify_calls)
        ),
        soft_deadline_ms=min(tool.soft_deadline_ms, goal.goal_soft_deadline_ms),
        hard_deadline_ms=min(tool.hard_deadline_ms, goal.goal_hard_deadline_ms),
    )
