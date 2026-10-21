"""Audit GA-02 regression: LLM ``ignored_spans`` are not authority.

An interpreter may declare task/presentation wording that carries no answer-changing
meaning. The deterministic layer must validate that proposal: after removing the
authoritative accounted terms, the centralized multilingual domain phrases and the closed
task/role vocabulary, no content token may remain. Otherwise an unrecognised modifier
(weather, count state, day/night, platoon, comparison wording, …) could be silently erased
from the conservation residue and from the requested contract.
"""
from __future__ import annotations

import pytest

from app.semantic.analytical_candidate import (
    candidate_proves,
    ground_candidate,
    ignored_span_is_safe,
)
from app.contracts.models import AnalysisRequest, Constraint, EntityMention, SemanticInterpretation
from app.semantic.goals import RequirementBuilder
from app.capabilities.analytics.authority import resolve_authority
from app.capabilities.analytics.measures import MEASURES


def _parents(text, *, mention="Ohtani", year=2023, key="home_runs"):
    label = MEASURES[key].label
    return [
        dict(id="e", kind="ENTITY", source="user", value={"mention": mention}, statement=mention),
        dict(id="s", kind="SEASON", source="user", value=year, statement=str(year)),
        dict(id="m", kind="MEASURE", source="user", value={"key": key, "label": label}, statement=label),
        dict(id="a", kind="ANALYSIS", source="user", value={"objective": text}, statement=text),
    ]


# ---------------------------------------------------------------- validator families
ANSWER_CHANGING = [
    "on a very windy day", "in two-strike counts", "under the lights",
    "when behind in the count", "with the platoon advantage", "change", "trend",
    "difference", "in the eighth inning", "home/away", "主场", "夜场",
]
TASK_WORDING = [
    "how did Ohtani do", "Ohtani's numbers", "stats", "the numbers",
    "in 2023", "2023", "home runs", "的打击数据", "一共", "呢",
]


@pytest.mark.parametrize("span", ANSWER_CHANGING)
def test_answer_changing_span_is_not_safe_to_ignore(span):
    assert ignored_span_is_safe(span, _parents("x")) is False


@pytest.mark.parametrize("span", TASK_WORDING)
def test_task_wording_and_accounted_terms_are_safe_to_ignore(span):
    assert ignored_span_is_safe(span, _parents("x")) is True


# ------------------------------------------------------------- candidate authority
def _candidate(span, *, ignored=None):
    return {
        "analytical_role": "BATTER",
        "restrictions": [{"dimension": "opposing_pitcher_hand", "value": "left", "source_text": span}],
        "ignored_spans": list(ignored or []),
        "source_text": span,
    }


def test_candidate_cannot_ignore_an_unrecognised_modifier():
    text = "Ohtani home runs against lefties on a very windy day in 2023"
    candidate = _candidate("against lefties", ignored=["on a very windy day"])
    grounded, issues = ground_candidate(candidate, source_text=text)
    assert grounded and not issues
    assert candidate_proves(text, grounded, _parents(text)) is False


def test_candidate_can_ignore_a_bound_measure_surface():
    text = "Ohtani home runs against lefties in 2023"
    candidate = _candidate("against lefties", ignored=["home runs"])
    grounded, issues = ground_candidate(candidate, source_text=text)
    assert grounded and not issues
    assert candidate_proves(text, grounded, _parents(text)) is True


def test_unsafe_ignored_span_does_not_become_authoritative():
    text = "Ohtani home runs against lefties in two-strike counts in 2023"
    candidate = _candidate("against lefties", ignored=["in two-strike counts"])
    grounded, _ = ground_candidate(candidate, source_text=text)
    child = dict(id="c", kind="CONSTRAINT", source="inferred", value=grounded,
                 parent_requirement_ids=["e", "s", "m", "a"],
                 derivation_kind="governed_semantic_candidate", derivation_proof="p")
    admitted, unproven, _ = resolve_authority([*_parents(text), child])
    assert "c" not in {r["id"] for r in admitted}
    assert "c" in {r["id"] for r in unproven}


# Metamorphic: the same modifier expressed through fresh surface forms is still rejected,
# while paraphrase task wording is still accepted.
MODIFIER_PARAPHRASES = [
    ("in a howling gale", "against lefties"),
    ("with a full count", "against righties"),
    ("during day games", "against lefties"),
    ("朝左投手", "面对右投手"),
]
TASK_PARAPHRASES = ["show me", "report the results", "list the numbers", "show the numbers"]


@pytest.mark.parametrize("modifier,span", MODIFIER_PARAPHRASES)
def test_fresh_modifier_paraphrases_are_rejected(modifier, span):
    assert ignored_span_is_safe(modifier, _parents("x")) is False


@pytest.mark.parametrize("task", TASK_PARAPHRASES)
def test_fresh_task_paraphrases_are_accepted(task):
    # Closed task/presentation vocabulary (report/show/list/numbers/results/me) is ignored;
    # unknown wording is conservatively rejected and kept unresolved rather than guessed.
    assert ignored_span_is_safe(task, _parents("x")) is True
