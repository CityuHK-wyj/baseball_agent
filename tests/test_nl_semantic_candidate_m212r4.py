"""M2.12R4 natural-language semantic-candidate boundary tests.

These tests exercise the intended architecture:

    user text -> semantic interpreter candidate -> schema grounding + span accounting
              -> governed obligation

They do NOT enumerate sentence phrasings in production code: the "interpreter" is a
test-side oracle that proposes a semantic dimension, and the assertions are about the
deterministic grounding. The same semantic intent is expressed through many surface forms
(synonyms, abbreviations, Chinese, typos, word order) and must converge.
"""
import copy
import random

import pytest

from tools.requirement_ancestry_fixture import root_user_requirements
from app.capabilities.analytics.authority import resolve_authority
from app.capabilities.analytics.conformance import requested_contract
from app.contracts.models import (
    AnalysisRequest,
    Constraint,
    EntityMention,
    RequirementKind,
    SemanticInterpretation,
)
from app.semantic.analytical_candidate import candidate_proves, ground_candidate
from app.semantic.goals import RequirementBuilder


# --------------------------------------------------------------------- test oracle
def contract_for(text, candidate, *, entity="Ohtani", year=2025):
    """Run the real RequirementBuilder + requested_contract with one interpreter candidate."""
    constraints = [Constraint(kind="analytical", value=candidate, explicit=True)]
    if year:
        constraints.append(Constraint(kind="season", value=year, explicit=True))
    semantic = SemanticInterpretation(
        raw_query=text,
        understanding=text,
        analysis_requests=[AnalysisRequest(text=text, objective=text)],
        entities=[EntityMention(mention=entity, type_hint="player")],
        constraints=constraints,
    )
    goal = RequirementBuilder().build(semantic)
    requirements = [r.model_dump(mode="json") for r in goal.requirements if r.required]
    return requested_contract({}, dict(analytical_requirements=requirements, bound_user_intent=text), [])


# ------------------------------------------------------ semantic dimension grounding
@pytest.mark.parametrize("value", ["left", "L", "LHP", "lhp", "southpaw", "lefty", "左"])
def test_opposing_pitcher_hand_synonyms_ground_to_one_dimension(value):
    grounded, issues = ground_candidate(
        {"analytical_role": "BATTER",
         "restrictions": [{"dimension": "opposing_pitcher_hand", "value": value, "source_text": "x"}],
         "source_text": "x"},
        source_text="x",
    )
    assert not issues
    assert grounded["filters"] == [{"op": "COMPARE", "field": "p_throws", "comparator": "=", "value": "L"}]


@pytest.mark.parametrize("dimension,value,field,expected", [
    ("batter_hand", "right", "stand", "R"),
    ("pitch_type", "SL", "pitch_type", "SL"),
    ("balls", 3, "balls", 3),
    ("strikes", 2, "strikes", 2),
    ("inning", 7, "inning", 7),
])
def test_governed_dimensions_ground(dimension, value, field, expected):
    grounded, issues = ground_candidate(
        {"restrictions": [{"dimension": dimension, "value": value, "source_text": "x"}], "source_text": "x"},
        source_text="x",
    )
    assert not issues
    assert grounded["filters"][0]["field"] == field
    assert grounded["filters"][0]["value"] == expected


@pytest.mark.parametrize("candidate", [
    {"restrictions": [{"dimension": "stadium", "value": "dome", "source_text": "x"}], "source_text": "x"},
    {"restrictions": [{"dimension": "p_throws", "value": "X", "source_text": "x"}], "source_text": "x"},
    {"restrictions": [{"dimension": "balls", "value": 9, "source_text": "x"}], "source_text": "x"},
    {"analytical_role": "CATCHER"},
    {"limit": 0},
    {"limit": 100000},
])
def test_unsupported_candidates_are_rejected(candidate):
    grounded, issues = ground_candidate(candidate, source_text="x")
    assert not grounded and issues


# ------------------------------------------------------------------ span accounting
def _analysis_parent(text):
    return dict(id="a", kind="ANALYSIS", source="user", value={"objective": text},
                statement=text, original_intent=text)


def test_candidate_from_scope_value_parent_is_rejected():
    season = dict(id="s", kind="SEASON", source="user", value=2022, statement="2022")
    child = dict(id="c", kind="CONSTRAINT", source="inferred",
                 value={"analytical_role": "PITCHER"},
                 parent_requirement_ids=["s"], derivation_kind="governed_semantic_candidate",
                 derivation_proof="p")
    admitted, _, _ = resolve_authority([season, child])
    assert "c" not in {r["id"] for r in admitted}


def test_candidate_cannot_erase_unsupported_context():
    text = "Ohtani facing portsiders under a closed roof in 2025"
    candidate = {"analytical_role": "BATTER",
                 "restrictions": [{"dimension": "opposing_pitcher_hand", "value": "left"}],
                 "source_spans": ["facing portsiders under a closed roof"],
                 "ignored_spans": ["facing portsiders under a closed roof"]}
    assert not candidate_proves(text, candidate, [_analysis_parent(text)])


def test_candidate_cannot_cover_already_parsed_meaning():
    text = "Ohtani against lefties in 2025"
    # The deterministic adapter already consumes "against lefties"; a candidate that
    # re-labels that span with another dimension must be rejected.
    candidate = {"restrictions": [{"dimension": "pitch_type", "value": "SL"}],
                 "source_spans": ["against lefties"]}
    assert not candidate_proves(text, candidate, [_analysis_parent(text)])


def test_unknown_residue_keeps_candidate_unproven():
    text = "Ohtani facing portsiders while batting on a neutral field in 2025"
    candidate = {"analytical_role": "BATTER",
                 "restrictions": [{"dimension": "opposing_pitcher_hand", "value": "left"}],
                 "source_spans": ["facing portsiders"]}
    assert not candidate_proves(text, candidate, [_analysis_parent(text)])


# -------------------------------------------------------------- end-to-end convergence
_PARAPHRASES = [
    ("how did Ohtani do against southpaws in 2025", "against southpaws", ["how did Ohtani do", "in 2025"]),
    ("Ohtani's numbers versus left-handed pitchers in 2025", "versus left-handed pitchers", ["Ohtani's numbers", "in 2025"]),
    ("Ohtani vs LHP in 2025", "vs LHP", ["in 2025"]),
    ("Ohtani aginst lefties 2025 stats", "aginst lefties", ["stats"]),
    ("2025年Ohtani对左投手的打击数据", "对左投手", ["的打击数据"]),
    ("a pitcher throwing from the left side, how did Ohtani do in 2025", "a pitcher throwing from the left side", ["how did Ohtani do", "in 2025"]),
]


@pytest.mark.parametrize("text,span,ignored", _PARAPHRASES)
def test_paraphrases_converge_to_one_governed_contract(text, span, ignored):
    candidate = {
        "analytical_role": "BATTER",
        "restrictions": [{"dimension": "opposing_pitcher_hand", "value": "left", "source_text": span,
                          "attributed_span": span}],
        "ignored_spans": ignored,
        "source_text": span,
    }
    contract = contract_for(text, candidate)
    assert contract.filters == ({"op": "COMPARE", "field": "p_throws", "comparator": "=", "value": "L"},), (text, contract.issues)
    assert contract.analytical_role == "BATTER"
    # Neither short nor long wholly opaque attribution proves atomic meaning.
    assert bool(contract.issues) == (span in {"vs LHP", "aginst lefties", "a pitcher throwing from the left side"})


def test_typo_candidate_does_not_change_meaning():
    candidate = {"analytical_role": "BATTER",
                 "restrictions": [{"dimension": "opposing_pitcher_hand", "value": "left",
                                   "source_text": "aginst leftes"}],
                 "ignored_spans": ["2025"],
                 "source_text": "aginst leftes"}
    contract = contract_for("Ohtani aginst leftes in 2025", candidate)
    assert contract.filters == ({"op": "COMPARE", "field": "p_throws", "comparator": "=", "value": "L"},)
    assert contract.analytical_role == "BATTER"


def test_unfounded_role_candidate_does_not_become_authoritative():
    # The interpreter proposes a role but cites no span and the clause is a bare scope
    # question. The governed role must stay unresolved rather than guess.
    candidate = {"analytical_role": "PITCHER"}
    season = dict(id="s", kind="SEASON", source="user", value=2022, statement="2022")
    child = dict(id="c", kind="CONSTRAINT", source="inferred", value=candidate,
                 parent_requirement_ids=["s"], derivation_kind="governed_semantic_candidate",
                 derivation_proof="p")
    admitted, _, _ = resolve_authority([season, child])
    assert "c" not in {r["id"] for r in admitted}


def test_deterministic_path_is_unchanged_without_a_candidate():
    season = dict(id="s", kind="SEASON", source="user", value=2025, statement="2025")
    analysis = _analysis_parent("Report batter walks against lefties")
    root_user_requirements([analysis, season], "Report batter walks against lefties")
    contract = requested_contract({}, dict(analytical_requirements=[analysis, season], bound_user_intent="Report batter walks against lefties"), [])
    assert any(f.get("field") == "p_throws" and f.get("value") == "L" for f in contract.filters)


# ------------------------------------------------------------- metamorphic generation
def test_metamorphic_language_holds_contract_constant():
    """Synonym / order / abbreviation / typo changes keep the governed meaning invariant."""
    rng = random.Random(20221204)
    positive = ["left", "L", "LHP", "southpaw", "lefty"]
    connectors = ["vs ", "facing ", "against "]
    prefixes = ["Ohtani ", "2025 Ohtani ", "Ohtani "]
    suffixes = [" in 2025", " 2025", ""]
    for _ in range(40):
        form = rng.choice(positive)
        span = rng.choice(connectors) + form
        text = rng.choice(prefixes) + span + rng.choice(suffixes)
        candidate = {"analytical_role": "BATTER",
                     "restrictions": [{"dimension": "opposing_pitcher_hand", "value": form, "source_text": span}],
                     "source_text": span}
        contract = contract_for(text, candidate)
        assert contract.filters == ({"op": "COMPARE", "field": "p_throws", "comparator": "=", "value": "L"},), (text, contract.issues)
        assert contract.analytical_role == "BATTER"
