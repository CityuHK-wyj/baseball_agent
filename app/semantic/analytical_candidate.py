"""Semantic-candidate grounding: open-world interpretation -> governed obligations.

M2.12R4 architecture review (see ``docs/audit/NATURAL_LANGUAGE_HARDCODING_AUDIT.md``)
found that analytical meaning was reconstructed by enumerating sentence-level regexes in
``conformance.py``. That makes recall depend on a growing vocabulary and puts factual
decisions (BATTER/PITCHER, filters, grouping, limits) behind brittle wording.

This module is the deterministic half of the intended boundary:

    user text -> semantic interpreter -> *semantic candidate* -> grounding/validation
              -> governed obligation -> Planner / Analytics

The interpreter (an LLM, or any producer) may propose a *semantic candidate* using stable
semantic dimension names such as ``opposing_pitcher_hand = left``. It is untrusted. This
module:

1. maps the semantic dimension to an existing :data:`CATALOG` field;
2. validates the value against the governed enum/bounds;
3. records the exact user span the interpretation came from;
4. assesses the cited wording independently of candidate admission. Provenance alone
   cannot erase extra answer-changing content: unresolved residue stays with the source
   review even when the candidate's governed fragment is admitted.

If grounding or provenance fails the candidate is rejected. Partial coverage is not
full understanding and remains visible downstream. The function
never emits SQL, never chooses a source and never trusts an LLM identity or Measure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.capabilities.analytics.catalog import CATALOG
from app.semantic.lexicon import OPPOSITION_CONNECTIVES, PITCH_TYPE_NAMES

# --------------------------------------------------------------------- semantic lexicon
# Semantic dimension -> governed catalog field. Only stable, canonical concepts belong
# here. This is deliberately a small closed map, not an enumeration of sentence wording.
_HAND_SIDE = {
    "left": "L", "right": "R", "lhp": "L", "rhp": "R", "southpaw": "L",
    "lefty": "L", "righty": "R", "左": "L", "右": "R", "l": "L", "r": "R",
}

_PITCH_TYPE_MAP = dict(PITCH_TYPE_NAMES)
for _code in ("FF", "SI", "SL", "CH", "FC", "ST", "CU", "FS", "KC", "SV", "EP", "FA", "KN", "FO", "CS"):
    _PITCH_TYPE_MAP.setdefault(_code.lower(), _code)

FILTER_DIMENSIONS: dict[str, tuple[str, dict[str, str] | None]] = {
    # semantic dimension              governed field       value map (None => enum/int)
    "opposing_pitcher_hand": ("p_throws", _HAND_SIDE),
    "pitcher_hand": ("p_throws", _HAND_SIDE),
    "pitcher_handedness": ("p_throws", _HAND_SIDE),
    "p_throws": ("p_throws", _HAND_SIDE),
    "batter_hand": ("stand", _HAND_SIDE),
    "batter_handedness": ("stand", _HAND_SIDE),
    "stand": ("stand", _HAND_SIDE),
    "pitch_type": ("pitch_type", _PITCH_TYPE_MAP),
    "balls": ("balls", None),
    "strikes": ("strikes", None),
    "inning": ("inning", None),
    "zone": ("zone", None),
    "game_type": ("game_type", None),
}

GROUP_DIMENSIONS: dict[str, str] = {
    "opposing_pitcher_hand": "p_throws",
    "pitcher_hand": "p_throws",
    "pitcher_handedness": "p_throws",
    "p_throws": "p_throws",
    "batter_hand": "stand",
    "batter_handedness": "stand",
    "stand": "stand",
    "pitch_type": "pitch_type",
    "balls": "balls",
    "strikes": "strikes",
    "inning": "inning",
    "zone": "zone",
    "game_type": "game_type",
    "pitcher": "pitcher_id",
    "batter": "batter_id",
}

_RANKING_WORDS = {"top": "DESC", "most": "DESC", "highest": "DESC", "best": "DESC",
                  "lowest": "ASC", "fewest": "ASC", "worst": "ASC", "least": "ASC"}


def _coerce_side(value: Any, mapping: dict[str, str] | None) -> Any:
    if mapping is None:
        return value
    token = str(value or "").strip().lower()
    return mapping.get(token)


def _validate_filter(field: str, value: Any) -> tuple[dict[str, Any] | None, str]:
    try:
        spec = CATALOG.field("pitch", field)
    except Exception:  # noqa: BLE001 - unknown field is a grounding failure
        return None, f"unknown field '{field}'"
    if "FILTER" not in spec.ops:
        return None, f"field '{field}' is not filterable"
    if spec.enum_values:
        if str(value) not in spec.enum_values:
            return None, f"value '{value}' is not a governed value of {field}"
    elif spec.type in {"int", "float"}:
        try:
            value = int(value) if spec.type == "int" else float(value)
        except (TypeError, ValueError):
            return None, f"value '{value}' is not numeric for {field}"
        if spec.minimum is not None and value < spec.minimum:
            return None, f"value '{value}' is below the governed minimum for {field}"
        if spec.maximum is not None and value > spec.maximum:
            return None, f"value '{value}' is above the governed maximum for {field}"
    return {"op": "COMPARE", "field": field, "comparator": "=", "value": value}, ""


def _validate_group(field: str) -> str:
    try:
        spec = CATALOG.field("pitch", field)
    except Exception:  # noqa: BLE001
        return ""
    return field if "GROUP_BY" in spec.ops else ""


def ground_candidate(candidate: dict[str, Any], *, source_text: str = "") -> tuple[dict[str, Any], list[str]]:
    """Ground one interpreter candidate into a governed obligation dict.

    ``candidate`` is the untrusted interpreter proposal. Returns ``(obligations, issues)``.
    ``obligations`` carries the catalog-form keys consumed by ``constraint_coverage`` plus
    ``source_spans`` recording the exact user wording. Any issue makes the candidate
    unusable; the caller must leave the text unresolved rather than guess.
    """
    issues: list[str] = []
    obligations: dict[str, Any] = {}
    spans: list[str] = []
    attributions: list[dict[str, str]] = []

    role = candidate.get("analytical_role") or candidate.get("role")
    if role:
        role = str(role).upper()
        if role in {"BATTER", "PITCHER"}:
            obligations["analytical_role"] = role
        else:
            issues.append(f"unsupported analytical role '{role}'")

    def _span(item: dict[str, Any]) -> str:
        span = str(item.get("source_text") or item.get("source_span") or "").strip()
        if span and span not in spans:
            spans.append(span)
        attributed = str(item.get("attributed_span") or "").strip()
        if attributed:
            attributions.append({"source_span": span, "attributed_span": attributed})
        return span

    for item in candidate.get("restrictions") or []:
        if not isinstance(item, dict):
            issues.append("malformed restriction")
            continue
        dimension = str(item.get("dimension") or item.get("field") or "")
        mapping = FILTER_DIMENSIONS.get(dimension)
        if mapping is None:
            issues.append(f"unsupported restriction dimension '{dimension}'")
            continue
        field, value_map = mapping
        value = _coerce_side(item.get("value"), value_map)
        predicate, error = _validate_filter(field, value)
        if predicate is None:
            issues.append(error)
            continue
        _span(item)
        obligations.setdefault("filters", []).append(predicate)

    # A producer may emit catalog-form predicates directly. They are not trusted: the
    # deterministic probe below validates every field, operator and value against the
    # governed catalog, exactly as the text adapter does.
    for item in candidate.get("filters") or []:
        if not isinstance(item, dict) or not item.get("field"):
            issues.append("malformed filter")
            continue
        _span(item)
        obligations.setdefault("filters", []).append({k: v for k, v in item.items() if k not in {"source_text", "source_span", "attributed_span"}})

    for item in candidate.get("splits") or []:
        if not isinstance(item, dict):
            issues.append("malformed split")
            continue
        dimension = str(item.get("dimension") or item.get("field") or "")
        field = GROUP_DIMENSIONS.get(dimension)
        if not field or not _validate_group(field):
            issues.append(f"unsupported split dimension '{dimension}'")
            continue
        _span(item)
        obligations.setdefault("group_by", []).append(field)

    for field in candidate.get("group_by") or []:
        field = str(field or "")
        if not _validate_group(field):
            issues.append(f"unsupported split dimension '{field}'")
            continue
        obligations.setdefault("group_by", []).append(field)

    if candidate.get("limit") is not None:
        limit = candidate.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            issues.append("unsupported ranking limit")
        else:
            obligations["limit"] = limit
    if candidate.get("source_text") and not spans:
        spans.append(str(candidate["source_text"]).strip())

    for item in candidate.get("ordering") or []:
        if not isinstance(item, dict):
            issues.append("malformed ordering")
            continue
        metric = str(item.get("metric") or "")
        direction = str(item.get("direction") or _RANKING_WORDS.get(str(item.get("kind") or "").lower(), "")).upper()
        if direction not in {"ASC", "DESC"}:
            issues.append("unsupported ordering direction")
            continue
        if item.get("field"):
            field = str(item["field"])
            spec_ok = CATALOG.is_valid("pitch", field)
            spec = CATALOG.field("pitch", field) if spec_ok else None
            if not spec_ok or "GROUP_BY" not in spec.ops:
                issues.append(f"unsupported ordering field '{field}'")
                continue
            obligations.setdefault("ordering", []).append({"field": field, "direction": direction})
            _span(item)
            continue
        from app.capabilities.analytics.measures import resolve_texts

        resolutions = resolve_texts([metric]) if metric else []
        if len(resolutions) != 1 or not resolutions[0].key:
            issues.append(f"unresolved ordering Measure '{metric}'")
            continue
        obligations.setdefault("ordering", []).append({"metric": resolutions[0].key, "direction": direction})
        _span(item)

    if candidate.get("qualification") is not None:
        from app.capabilities.analytics.conformance import qualification

        try:
            obligations["qualification"] = qualification(candidate["qualification"])
            if candidate.get("source_text"):
                _span(candidate)
        except (ValueError, TypeError) as error:
            issues.append(str(error))

    if source_text and not spans:
        # A whole-clause candidate is only usable when the interpreter cited the clause.
        issues.append("candidate has no authoritative source span")
    # The top-level cited clause is provenance when it encloses a single item's
    # narrower citation. Preserve the original attributed substring (if supplied),
    # so material modifiers outside it are not lost before coverage/clarification.
    clause = str(candidate.get('source_text') or '').strip()
    if clause and len(spans) == 1 and spans[0] != clause and spans[0] in clause:
        if attributions:
            attributions = [{**entry, 'source_span': clause} for entry in attributions]
        spans = [clause]

    if issues:
        return {}, issues

    # Closed-world consistency: mutually exclusive equality/IN restrictions on one field
    # cannot both hold. This is catalog arithmetic, not sentence understanding; a producer
    # that needs a split must emit a grouping, not contradictory filters.
    contradiction = _contradictory_filters(obligations.get("filters") or [])
    if contradiction:
        return {}, [contradiction]

    # Deterministic schema/completeness validation, reusing the governed adapter.
    from app.capabilities.analytics.conformance import constraint_coverage

    probe = {k: v for k, v in obligations.items()}
    coverage = constraint_coverage(probe)
    if coverage["state"] != "COMPLETE":
        return {}, [f"candidate is not fully grounded: {coverage['unresolved']}"]
    grounded = dict(coverage["obligations"])
    if spans:
        grounded["source_spans"] = spans
    if attributions:
        grounded["attributions"] = attributions
    ignored = [str(s).strip() for s in (candidate.get("ignored_spans") or []) if str(s).strip()]
    if ignored:
        grounded["ignored_spans"] = ignored
    return grounded, issues


def _contradictory_filters(filters: list[dict[str, Any]]) -> str:
    """One field may not require two different scalar values or disjoint value sets."""
    equality: dict[str, Any] = {}
    in_sets: dict[str, set] = {}
    for predicate in filters:
        if not isinstance(predicate, dict):
            continue
        field = str(predicate.get("field") or "")
        if predicate.get("op") == "COMPARE" and predicate.get("comparator") == "=":
            value = predicate.get("value")
            if field in equality and equality[field] != value:
                return f"contradictory equality restrictions on {field}"
            equality[field] = value
        if predicate.get("op") == "IN":
            values = {str(v) for v in predicate.get("values") or []}
            if field in in_sets and not (in_sets[field] & values):
                return f"disjoint value sets on {field}"
            in_sets.setdefault(field, set()).update(values)
    for field, value in equality.items():
        if field in in_sets and str(value) not in in_sets[field]:
            return f"contradictory value restrictions on {field}"
    return ""


def _covered(wanted: dict[str, Any], available: dict[str, Any]) -> bool:
    """Whether every wanted obligation is already present in ``available``."""
    if not wanted:
        return True
    for key, value in wanted.items():
        if key in {"filters", "group_by", "ordering", "ordering_tail", "tie_break_ordering"}:
            if any(item not in (available.get(key) or []) for item in value):
                return False
        elif available.get(key) != value:
            return False
    return True


def _accounted_terms(parents) -> list[str]:
    """Surface terms already represented by authoritative sibling/parent Requirements.

    These are the same terms the source review subtracts. They may be removed from the
    candidate's remainder without hiding a modifier, because each is independently bound
    to an authoritative Requirement.
    """
    from app.capabilities.analytics.measures import MEASURES

    terms: list[str] = []
    for parent in parents or ():
        kind, value = parent.get("kind"), parent.get("value")
        if kind == "ENTITY" and isinstance(value, dict):
            for key in ("mention", "canonical_name"):
                if value.get(key):
                    terms.append(str(value[key]))
        elif kind in {"GAME_POPULATION", "SEASON"} and value not in (None, ""):
            terms.append(str(value))
            if kind == "SEASON":
                terms.extend(["season", "seasons", "year", "years"])
        elif kind == "MEASURE" and isinstance(value, dict):
            spec = MEASURES.get(value.get("key"))
            if spec:
                terms.extend([spec.key, spec.label, *spec.aliases])
        elif kind == "TIME" and isinstance(value, dict):
            for key in ("start", "end"):
                if value.get(key):
                    terms.append(str(value[key]))
    return terms


def _remove_terms(text: str, terms) -> str:
    import re as _re

    result = text
    for term in sorted((str(t or "").strip() for t in terms), key=len, reverse=True):
        if not term:
            continue
        if all(ord(ch) < 128 for ch in term):
            # Flexible whitespace and an optional possessive so an interpreter span that
            # contains an entity name still matches after earlier removals blank it.
            parts = [_re.escape(part) for part in term.split()]
            body = r"\s+".join(parts)
            result = _re.sub(rf"(?<![A-Za-z0-9]){body}(?:'s|’s)?(?![A-Za-z0-9])", " ", result, flags=_re.I)
        else:
            result = result.replace(term, " ")
    result = _re.sub(r"[\s]+", " ", result)
    return result.strip(" ,.;:，。'\"")


def ignored_span_is_safe(span: str, parents=()) -> bool:
    """Deterministically validate an interpreter-declared non-answer span.

    ``ignored_spans`` are an *LLM proposal*, never authority (design principle: “LLM
    ignored spans are not authority”). A span may be subtracted from the conservation
    residue only when it carries no answer-changing content: after removing the
    authoritative sibling/parent terms (entity/season/Measure/population/date endpoints),
    the centralized multilingual domain-accounted phrases and the closed
    task/presentation/role vocabulary, no content token may remain. Unknown residue stays
    semantically relevant, exactly as in the finite adapter, so an unrecognised modifier
    (weather, count state, day/night, platoon, comparison/direction wording, …) cannot be
    silently ignored.
    """
    text = str(span or "").strip()
    if not text:
        return True
    import re as _re

    from app.capabilities.analytics.conformance import (
        _DOMAIN_ACCOUNTED_PHRASES,
        residue_is_semantically_relevant,
    )

    remainder = _remove_terms(text, _accounted_terms(parents))
    for pattern in _DOMAIN_ACCOUNTED_PHRASES:
        remainder = _re.sub(pattern, " ", remainder)
    remainder = _re.sub(r"\s{2,}", " ", remainder).strip(" ,.;:，。'\"")
    return not residue_is_semantically_relevant(remainder)


@dataclass(frozen=True)
class CandidateAssessment:
    """Admission is separate from text the source review may account for."""

    admitted: bool = False
    coverage: str = "NONE"  # FULL / PARTIAL / NONE, relative to cited spans
    accounted_spans: tuple[str, ...] = ()
    uncovered_spans: tuple[str, ...] = ()


def _conflicts(observed: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Reject only contradictory *observed* governed meaning, not independent modifiers."""
    for predicate in observed.get("filters") or []:
        for proposed in candidate.get("filters") or []:
            if predicate.get("field") == proposed.get("field") and predicate != proposed:
                return True
    for key in ("season", "date_range", "population", "game_types", "qualification", "limit"):
        if key in observed and key in candidate and observed[key] != candidate[key]:
            return True
    return False


def _other_governed_field_mentioned(span: str, candidate: dict[str, Any]) -> bool:
    """Detect *field identities*, not natural-language filters, the candidate lacks.

    This is a conservative guard for unresolved source wording such as a hyphenated
    count-state: the finite clause resolver may not structure the phrase, but the
    stable catalog field still makes the extra semantic dimension inspectable.
    """
    import re

    tokens = set(re.findall(r"[a-z_]+", span.lower().replace("-", " ")))
    fields = {p.get("field") for p in candidate.get("filters") or []}
    fields.update(candidate.get("group_by") or [])
    for name, spec in CATALOG.table("pitch").fields.items():
        if name in fields or not ({"FILTER", "GROUP_BY"} & set(spec.ops)):
            continue
        if name in tokens or name.rstrip("s") in tokens:
            return True
    return False


def assess_candidate(parent_text: str, candidate: dict[str, Any], parents=()) -> CandidateAssessment:
    """Validate provenance and governed meaning without treating a cited span as consumed.

    The existing clause resolver supplies observations and remaining spans. Already
    recognised candidate-compatible meaning needs no subtraction. Provenance is
    inspected for admission, but only a validated attribution can account novel
    wording. Without attribution, FULL requires independently complete finite
    coverage of the same governed footprint. Every non-accounted fragment is
    reviewed by normal Requirement-local conformance.
    """
    from app.capabilities.analytics.conformance import (
        constraint_coverage, residue_is_semantically_relevant, role_from_text,
    )
    from app.contracts.models import RelationKind
    from app.semantic.relations import relation_kinds_from_text
    import re

    rejected = CandidateAssessment()
    text = parent_text or ""
    spans = [str(s).strip() for s in candidate.get("source_spans") or [] if str(s).strip()]
    ignored = [str(s).strip() for s in candidate.get("ignored_spans") or [] if str(s).strip()]
    attributions = candidate.get("attributions") or []
    if not text or not any(p.get("kind") in {"ANALYSIS", "EVIDENCE"} for p in parents):
        return rejected
    if any(span not in text for span in [*spans, *ignored]):
        return rejected
    if any(not ignored_span_is_safe(span, parents) for span in ignored):
        return rejected
    wanted = {k: v for k, v in candidate.items() if k not in {"source_spans", "ignored_spans", "attributions"}}
    grounded = constraint_coverage(wanted)
    if grounded["state"] != "COMPLETE" or not grounded["obligations"]:
        return rejected
    wanted = grounded["obligations"]
    if candidate.get("analytical_role"):
        outside = text
        for span in spans:
            outside = outside.replace(span, " ")
        detected = role_from_text(outside)
        if detected and detected != candidate["analytical_role"]:
            return rejected
    if not spans:
        # Only an independently visible finite subject-role cue can justify a role-only
        # candidate without a source span. Ignored text cannot prove a novel filter.
        if set(wanted) == {"analytical_role"} and role_from_text(text) == wanted["analytical_role"]:
            return CandidateAssessment(True, "FULL", ())
        return rejected

    # Each proposal must refer to exactly one cited span, with unique literal offsets.
    # No implicit fallback to a broad provenance when any explicit attribution exists.
    attribution_by_source: dict[str, str] = {}
    if attributions:
        if (not isinstance(attributions, list) or len(attributions) != len(spans)
                or any(not isinstance(a, dict) or set(a) != {"source_span", "attributed_span"}
                       for a in attributions)):
            return rejected
        for entry in attributions:
            source, region = entry["source_span"], entry["attributed_span"]
            if (source not in spans or source in attribution_by_source or not isinstance(region, str)
                    or not region.strip() or source.count(region) != 1 or text.count(source) != 1):
                return rejected
            start = source.index(region)
            end = start + len(region)
            if ((start and source[start - 1].isalnum() and region[0].isalnum())
                    or (end < len(source) and source[end].isalnum() and region[-1].isalnum())):
                return rejected
            attribution_by_source[source] = region
        # Independently owned regions cannot overlap. An ignored region cannot
        # silently absorb part of the candidate's proposed semantic ownership.
        locations = []
        for source, region in attribution_by_source.items():
            base = text.index(source)
            start = base + source.index(region)
            locations.append((start, start + len(region)))
        for i, (start, end) in enumerate(locations):
            if any(start < other_end and other_start < end
                   for other_start, other_end in locations[i + 1:]):
                return rejected
            if any(text.count(ignored_span) == 1
                   and start < text.index(ignored_span) + len(ignored_span)
                   and text.index(ignored_span) < end for ignored_span in ignored):
                return rejected
    accounted: list[str] = []
    uncovered: list[str] = []
    partial = False
    contribution = False
    for provenance in spans:
        # The provenance remains inspectable for admission even without attribution.
        # It is never, by default, an accounted semantic region.
        span = attribution_by_source.get(provenance, provenance)
        if span != provenance:
            start = provenance.index(span)
            for outside in (provenance[:start], provenance[start + len(span):]):
                outside = outside.strip(" ,.;:，。'\"")
                if outside and residue_is_semantically_relevant(_remove_terms(outside, _accounted_terms(parents))):
                    uncovered.append(outside)
                    partial = True
        observed = constraint_coverage(span, accounted_terms=_accounted_terms(parents))
        footprint = dict(observed["obligations"])
        # "pitcher" inside an opposing-pitcher description does not assign the subject
        # role. Keep the independent, outside-the-span conflict check above.
        if "analytical_role" in wanted:
            footprint.pop("analytical_role", None)
        if _conflicts(footprint, wanted):
            return rejected
        matching = any(
            any(member in (wanted.get(key) or []) for member in item)
            if key in {"filters", "group_by", "ordering", "tie_break_ordering"}
            else key in wanted and wanted[key] == item
            for key, item in footprint.items()
        )
        extra = not _covered(footprint, wanted)
        # Finite proof must represent the candidate's own footprint as well as
        # exclude independently observed extra semantics. Subject role is checked
        # separately against the parent/entity and outside-span role cues.
        finite_wanted = {key: value for key, value in wanted.items() if key != "analytical_role"}
        independently_covered = (observed["state"] == "COMPLETE" and matching and not extra
                                 and _covered(finite_wanted, footprint))
        if observed["state"] == "COMPLETE" and matching and not _covered(finite_wanted, footprint):
            # A wholly finite reading cannot authorize an additional candidate
            # restriction missing from that reading. Partial coverage must not
            # silently become a COMPLETE contract via the structured child alone.
            return rejected
        novel = residue_is_semantically_relevant(_remove_terms(span, _accounted_terms(parents)))
        if not matching and not novel and not ("analytical_role" in wanted and role_from_text(span)):
            # Entity, Measure, season and task wording alone cannot authorize a filter.
            return rejected
        contribution = True
        if extra or observed["unsupported"]:
            partial = True
        residuals = [str(r).strip() for entry in observed["clause_states"]
                     for r in entry.get("remaining_spans") or [] if str(r).strip()]
        if matching:
            # The finite adapter owns this meaning, even when it overlaps the citation.
            # Its other residue, including unsupported dimensions, must remain visible.
            if observed["unresolved"]:
                # Even a seemingly grammatical unparsed token could be another
                # novel condition. A model attribution or one-edit typo hypothesis
                # does not authorize consumption of that token.
                partial = True
                uncovered.extend(residuals)
            if not attribution_by_source and not independently_covered:
                partial = True
                uncovered.append(provenance)
            continue
        # A new open-world reading is needed. A fully parsed *different* dimension
        # with no novel source content cannot authorize an invented candidate value.
        safe = [r for r in residuals if r in span and residue_is_semantically_relevant(r)]
        if footprint or observed["unsupported"]:
            if (not safe and not observed["unresolved"]
                    and not (set(wanted) == {"analytical_role"} and
                             role_from_text(span) == wanted["analytical_role"])):
                return rejected
            # Finite parsing may cut the remaining words into noncontiguous
            # fragments. That cannot prove ownership, but it must not discard a
            # valid open-world candidate; keep all such residue unresolved.
            uncovered.extend(safe or residuals or [span])
            partial = True
        elif observed["unresolved"]:
            # A known relation is an independently recognised semantic dimension too.
            # In opposing-hand descriptions "vs/versus" is a stable opposition
            # connective, not by itself a numeric comparison. The canonical relation
            # vocabulary remains the sole detector; no new user-phrase rules live here.
            without_opposition = re.sub(
                r"\b(?:" + "|".join(sorted(OPPOSITION_CONNECTIVES, key=len, reverse=True)) + r")\b",
                " ", span, flags=re.I)
            other_relation = bool({RelationKind.COMPARISON, RelationKind.CAUSE, RelationKind.EFFECT}
                                  & set(relation_kinds_from_text(without_opposition)))
            if _other_governed_field_mentioned(span, wanted) or other_relation:
                # An extra dimension is visible even if its full grammar is not.
                # Keep the source for the normal resolver / unresolved ledger.
                partial = True
            else:
                # No length or model-attribution claim proves that wholly novel
                # wording carries only the candidate's footprint. Admission survives;
                # only independent finite proof or authoritative user confirmation
                # can account the opaque region.
                uncovered.extend(residuals or [span])
                partial = True
        # A fully parsed role cue or task wording needs no source subtraction.
        if not attribution_by_source and not independently_covered:
            # Never promote an uncited attribution by treating provenance as its
            # substitute. Prefer the unresolved fragment for the user-facing
            # question; provenance remains the immutable mask target.
            partial = True
            if not uncovered:
                uncovered.append(provenance)
    return CandidateAssessment(contribution, "PARTIAL" if partial else "FULL",
                               tuple(dict.fromkeys(accounted)), tuple(dict.fromkeys(uncovered)))


def candidate_proves(parent_text: str, candidate: dict[str, Any], parents=()) -> bool:
    """Compatibility entry point for Requirement authority: candidate admission only."""
    return assess_candidate(parent_text, candidate, parents).admitted
