"""Audit GA-03 regression: comparison/direction wording is answer-changing semantics.

``NON_SEMANTIC_WORDS`` is a closed task/presentation vocabulary used to decide whether a
clause's residue is irrelevant. Comparison, direction and change wording
(change/trend/difference/increase/…) is not task presentation: it changes what the user is
asking for. If it were waived, a plain value artifact could satisfy a comparison obligation
and the Goal could silently present the wrong answer. Such wording must stay relevant
residue unless a governed obligation (for example an interpreter split) represents it.
"""
from __future__ import annotations

import pytest

from app.capabilities.analytics.conformance import constraint_coverage
from app.semantic.lexicon import NON_SEMANTIC_WORDS

COMPARISON_WORDS = [
    "change", "changes", "difference", "differences", "trend", "increase",
    "decrease", "growth", "decline", "comparison", "compare", "improvement",
    "regression", "rise", "drop", "gain", "loss", "swing",
]


@pytest.mark.parametrize("word", COMPARISON_WORDS)
def test_comparison_wording_is_not_task_vocabulary(word):
    assert word not in NON_SEMANTIC_WORDS


def _accounted(text):
    """The terms an authoritative entity/season/Measure binding would subtract."""
    return ["Ohtani", "2023", "batting average", "Average (AVG)", "avg",
            "season", "seasons", "year", "years"]


# The clause must not be COMPLETE while the comparison meaning is unrepresented.
@pytest.mark.parametrize("text", [
    "Ohtani batting average change in 2023",
    "Ohtani batting average difference in 2023",
    "Ohtani batting average improvement in 2023",
    "Ohtani home run trend in 2023",
    "Ohtani batting average increase in 2023",
    "大谷翔平2023年打击率变化",
    "Ohtani BA change 2023",
])
def test_comparison_clause_is_not_silently_complete(text):
    coverage = constraint_coverage(text, accounted_terms=_accounted(text))
    assert coverage["state"] != "COMPLETE", (text, coverage)


# Control: the same clauses without the comparison wording do resolve.
@pytest.mark.parametrize("text", [
    "Ohtani batting average in 2023",
    "Ohtani home runs in 2023",
])
def test_accounted_clause_without_comparison_completes(text):
    coverage = constraint_coverage(text, accounted_terms=_accounted(text))
    assert coverage["state"] == "COMPLETE", (text, coverage)


# A governed comparison obligation (an interpreter split) still lets the clause resolve:
# the comparison is then represented by structure, not waived by keyword absence.
def test_governed_split_represents_the_comparison():
    coverage = constraint_coverage(
        {"group_by": ["season"], "filters": []},
        accounted_terms=_accounted("Ohtani batting average change in 2023"),
    )
    assert coverage["state"] == "COMPLETE"
    assert coverage["obligations"]["group_by"] == ["season"]
