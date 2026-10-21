"""Post-composition status/answer consistency (Phase 5B, ADR 0023).

The Composer must not create a new factual authority. Once the Judge has fixed the
proposition coverage and GoalStatus, the rendered answer may not silently retract a
mandatory, verified obligation while the run still reports COMPLETE.

The check is a *structured comparison* between the rendered answer and the accepted,
verified claims attached to each mandatory satisfied proposition. It does not re-read
evidence, does not call a second semantic verifier and does not decide whether the
evidence supports the proposition (that is the Evidence Verifier's job). It answers only:
"does the answer communicate the verified result?"

When a mandatory result is missing, the runtime uses bounded deterministic presentation
repair or governed fallback. This check is telemetry only: it cannot mutate the immediately
frozen Judge result. An upstream inconsistency requires a fresh governed pass.
"""

from __future__ import annotations

import re
from typing import Any
from app.response.accepted_results import COMMUNICABLE_KINDS, claim_factual_text, render_accepted_results, render_qualified_evidence

from app.contracts.models import (
    AnswerProjection,
    AnswerProposition,
    Claim,
    ConsistencyStatus,
    CoverageState,
    Goal,
    GoalStatus,
    PropositionKind,
    StatusConsistency,
)

# Prose obligations the answer is expected to communicate. IDENTITY/SCOPE are preconditions
# the answer expresses implicitly (and often in another language).
_COMMUNICABLE_KINDS = COMMUNICABLE_KINDS


def check_status_consistency(
    goal: Goal,
    projection: AnswerProjection,
    answer: str,
    *,
    llm: Any = None,  # accepted for API compatibility; consistency is structured, not an LLM judge
) -> StatusConsistency:
    original = projection.goal_status
    # Every satisfied mandatory prose proposition must be communicated, even when the Goal is
    # only LIMITED: a partial answer must still carry its approved results (M2.6 §7).
    satisfied = [
        proposition
        for proposition in projection.propositions
        if proposition.mandatory
        and proposition.coverage == CoverageState.SATISFIED
        and proposition.kind in _COMMUNICABLE_KINDS
    ]
    if not satisfied:
        return StatusConsistency(
            status=ConsistencyStatus.CONSISTENT,
            original_goal_status=original,
            final_goal_status=original,
            rationale="no mandatory satisfied prose proposition to verify",
        )

    retracted: list[str] = []
    uncertain: list[str] = []
    reasons: list[str] = []
    for proposition in satisfied:
        claims = [claim for claim in projection.claims if proposition.id in claim.proposition_ids]
        if not claims:
            retracted.append(proposition.id)
            reasons.append(f"{proposition.statement}: no verified claim carries this result")
            continue
        verdicts = [_claim_expressed(claim, proposition, answer) for claim in claims]
        if all(verdict is True for verdict in verdicts):
            continue
        if all(verdict is None for verdict in verdicts):
            uncertain.append(proposition.id)
            reasons.append(f"{proposition.statement}: expression could not be determined")
            continue
        retracted.append(proposition.id)
        reasons.append(f"{proposition.statement}: the answer omits or retracts its supported result")

    if retracted:
        return StatusConsistency(
            # A COMPLETE Goal may be downgraded; a LIMITED Goal is already partial, but the
            # omitted approved result still forces a bounded deterministic repair.
            status=ConsistencyStatus.DOWNGRADED if original == GoalStatus.COMPLETE else ConsistencyStatus.UNCERTAIN,
            original_goal_status=original,
            final_goal_status=original,  # presentation never changes Judge authority
            retracted_proposition_ids=retracted,
            method="deterministic_structured",
            rationale="; ".join(reasons),
        )
    if any(claim.comparison_support is not None for claim in projection.claims):
        # Scalar relational Claims have no free-text equivalence validator. An exact
        # approved line plus an additional inverted assertion is not a safe answer.
        # Until a typed expression receipt exists, use the full bounded rendering.
        if (answer or "").strip() != render_accepted_results(projection).strip():
            return StatusConsistency(
                status=ConsistencyStatus.UNCERTAIN,
                original_goal_status=original,
                final_goal_status=original,
                retracted_proposition_ids=[p.id for p in satisfied],
                method="deterministic_structured",
                rationale="comparative answer differs from the Judge-approved canonical result",
            )
    # No arbitrary surrounding prose is a completion receipt. Even a literal result
    # followed by "That assertion is fabricated" needs canonical repair. Optional or
    # unrelated unrecognized prose takes the same safe path; evidence coverage is kept.
    # Composer synthesis is allowed, but it must not assert a number the approved facts do
    # not carry. A numeric invention is the highest-risk unapproved addition.
    approved_number_strings = set(
        re.findall(r"\d*\.\d+|\d+", " ".join(claim_factual_text(claim) for claim in projection.claims))
    )
    approved_number_strings |= {
        str(value.get("value")) for claim in projection.claims for value in claim.answer_values
        if value.get("value") is not None
    }
    # Numbers carried by qualified evidence are provenance-backed, not invented. They are not
    # factual authority, but flagging them as "invented" would wrongly suppress the qualified
    # lane the composer is required to preserve (WSA-03/presentation).
    qualified_text = " ".join(
        str(getattr(item, "span_text", "") or "")
        for item in (getattr(projection, "qualified_evidence", []) or [])
    )
    approved_number_strings |= set(re.findall(r"\d*\.\d+|\d+", qualified_text))
    invented = []
    for number in re.findall(r"\d*\.\d+|\d+", answer or ""):
        if len(number) == 1 and number.isdigit():
            continue  # citation marker / list index
        if len(number) == 4 and number.isdigit() and 1900 <= int(number) <= 2100:
            continue  # a season/scope year
        if any(_number_present(approved, number) for approved in approved_number_strings):
            continue
        invented.append(number)
    if invented:
        uncertain = [p.id for p in satisfied]
        reasons.append(f"answer contains numbers not present in the approved facts: {sorted(set(invented))[:5]}")
    if uncertain:
        return StatusConsistency(
            status=ConsistencyStatus.UNCERTAIN,
            original_goal_status=original,
            final_goal_status=original,
            retracted_proposition_ids=uncertain,
            method="deterministic_structured",
            rationale="; ".join(reasons),
        )
    return StatusConsistency(
        status=ConsistencyStatus.CONSISTENT,
        original_goal_status=original,
        final_goal_status=original,
        method="deterministic_structured",
        rationale="the final answer communicates every mandatory proposition",
    )


def repair_answer(
    projection: AnswerProjection,
    answer: str,
    proposition_ids: list[str] | None = None,
    *,
    language: str = "en",
) -> str:
    """Replace unconfirmed prose with bounded Judge-accepted mandatory results.

    This is a bounded repair derived only from already-accepted claims; it introduces no
    new facts and preserves source references. It never changes the evidence judgment. The
    qualified-evidence lane and structural limitations are retained so a bounded repair does
    not silently drop useful limited evidence (WSA-03/presentation).
    """
    # Repair all mandatory slots: preserving an unconfirmed introduction can keep a
    # denial attached to an otherwise correct result. Optional details may be omitted.
    base = render_accepted_results(projection) or answer
    # A typed comparison requires the exact canonical operand rendering; adding a qualified
    # lane would break that invariant, so it is left unchanged for comparative claims.
    if any(claim.comparison_support is not None for claim in projection.claims):
        return base
    chinese = str(language).startswith("zh")
    blocks = [base] if base else []
    qualified = render_qualified_evidence(projection)
    if qualified:
        blocks.append(("已找到但未能确认：" if chinese else "Found but not fully verified:") + "\n" + qualified)
    if projection.limitations:
        blocks.append(
            ("未能确认：" if chinese else "Not established:")
            + "\n" + "\n".join(f"- {limitation}" for limitation in projection.limitations)
        )
    from app.verification.evidence_origin import origin_presentation
    origins = origin_presentation(projection.evidence_origins, language=language)
    if origins:
        blocks.append(origins)
    return ("\n\n".join(blocks)) if blocks else answer


def _claim_expressed(claim: Claim, proposition: AnswerProposition, answer: str) -> bool | None:
    """True if the answer expresses the verified claim; None when it cannot be determined.

    A claim may bundle several approved spans. The answer must express the approved substance
    (tokens, numbers, polarity), not every bundled span verbatim, so a synthesis may state the
    result of any approved sentence. The Composer may paraphrase but not omit or invert it.
    """
    answer_text = answer or ""
    factual = claim_factual_text(claim)
    if proposition.kind == PropositionKind.COMPARISON and claim.comparison_support is not None:
        # No natural-language equivalence heuristic can prove numeric operand order or
        # direction. Accept the Judge's exact canonical comparison; otherwise the
        # bounded repair renders that already-approved Claim verbatim.
        return any(_normalize_unit(line) == _normalize_unit(factual)
                   for line in answer_text.splitlines())
    # A retraction in the answer is never an expression receipt. This is only reached for
    # COMPLETE goals, where no legitimate unresolved-part language should appear.
    if _has_retraction(answer_text) and not _has_retraction(factual):
        return False
    values = [v for v in claim.answer_values if v.get("requirement_id") == proposition.requirement_id]
    if values:
        return all(_value_expressed(v, answer_text) for v in values)
    candidates = [factual, *_sentences(factual)]
    if any(_text_expressed(candidate, answer_text) for candidate in candidates):
        return True
    # Cross-script answers (translation) cannot be structurally compared with literal
    # tokens; report an undetermined result instead of manufacturing a retraction.
    if _cross_script(_tokens(factual), _tokens(answer_text)):
        return None
    return False


def _text_expressed(text: str, answer: str) -> bool:
    if not text.strip():
        return False
    if any(_normalize_unit(text) == _normalize_unit(line) for line in answer.splitlines()):
        return True
    expected = _expression_units(text)
    actual = _expression_units(answer)
    if expected and all(any(_equivalent_unit(unit, candidate) for candidate in actual)
                        for unit in expected):
        return True
    if not re.search(r"\d", text) and _substance_covered(text, answer):
        return True
    if re.search(r"\d", text) and _numeric_substance_covered(text, answer):
        return True
    return False


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", text or "") if part.strip()]


_NEGATION_CUES = (
    "not", "no", "never", "didn't", "doesn't", "wasn't", "isn't", "denied", "without",
    "没有", "未", "并非", "不是", "无法", "未能",
)

# Retraction of an otherwise-present approved fact. Broad for COMPLETE-goal answers.
_RETRACTION_CUES = (
    "false", "untrue", "fabricated", "not true", "isn't true", "wasn't true", "denied",
    "refuted", "incorrect", "wrong", "misleading", "no evidence", "cannot be", "no.",
    "没有", "并非", "不是", "错误", "虚假", "不实", "否定",
)

_SUBSTANCE_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "is", "are",
    "was", "were", "be", "been", "his", "her", "their", "its", "this", "that", "these",
    "those", "with", "about", "into", "from", "as", "by", "over", "under", "between",
}


_METRIC_VALUE_RE = re.compile(
    r"\b(avg|obp|slg|ops|era|whip|hr|rbi|home runs?|strikeouts?|walks?|hits?|runs?)\b[\s:=]*(\d*\.\d+|\d+)",
    re.IGNORECASE,
)


def _numeric_substance_covered(expected: str, answer: str) -> bool:
    """A paraphrase of a numeric claim is accepted when numbers and metric binding survive."""
    expected_numbers = _claim_numbers(expected)
    if not expected_numbers:
        return False
    if not _ordered_numbers_present(expected_numbers, _claim_numbers(answer)):
        return False
    if not _metric_binding_ok(expected, answer):
        return False
    expected_tokens = {token for token in _tokens(expected) if token not in _SUBSTANCE_STOPWORDS and len(token) > 1}
    if not expected_tokens:
        return True
    answer_tokens = _tokens(answer)
    covered = sum(1 for token in expected_tokens if token in answer_tokens) / len(expected_tokens)
    return covered >= 0.5


def _claim_numbers(text: str) -> list[str]:
    cleaned = re.sub(r"\[[SIBHU]\d+\]", " ", text or "")
    return re.findall(r"\d*\.\d+|\d+", cleaned)


def _ordered_numbers_present(expected: list[str], answer: list[str]) -> bool:
    """The approved numbers must occur in order; a swapped direction is a retraction."""
    index = 0
    for number in expected:
        matched = False
        while index < len(answer):
            if _number_present(number, answer[index]):
                matched = True
                index += 1
                break
            index += 1
        if not matched:
            return False
    return True


def _metric_binding_ok(expected: str, answer: str) -> bool:
    """Each metric label keeps its own value (a swap between metrics is a retraction)."""
    expected_pairs = {m.group(1).lower(): m.group(2) for m in _METRIC_VALUE_RE.finditer(expected)}
    if not expected_pairs:
        return True
    answer_pairs = {m.group(1).lower(): m.group(2) for m in _METRIC_VALUE_RE.finditer(answer)}
    for metric, value in expected_pairs.items():
        if metric not in answer_pairs:
            return False
        if not _number_present(value, answer_pairs[metric]):
            return False
    return True


def _substance_covered(expected: str, answer: str) -> bool:
    """A synthesised answer expresses the claim when it preserves tokens, numbers, polarity."""
    expected_tokens = {token for token in _tokens(expected) if token not in _SUBSTANCE_STOPWORDS and len(token) > 1}
    if not expected_tokens:
        return False
    answer_tokens = _tokens(answer)
    covered = sum(1 for token in expected_tokens if token in answer_tokens) / len(expected_tokens)
    if covered < 0.5:
        return False
    for number in re.findall(r"\d*\.\d+|\d+", expected):
        if len(number) == 1 and number.isdigit():
            continue
        if not _number_present(number, answer):
            return False
    if _has_negation(expected) and not _has_negation(answer):
        return False
    return True


def _has_negation(text: str) -> bool:
    low = (text or "").lower()
    return any(cue in low for cue in _NEGATION_CUES)


def _has_retraction(text: str) -> bool:
    low = (text or "").lower()
    if any(cue in low for cue in _RETRACTION_CUES):
        return True
    # A bare "No." answer to the approved statement is a retraction.
    return bool(re.search(r"[?.!]\s*no\b", low))


def _normalize_unit(text):
    text = re.sub(r"^\s*(?:-\s+)?(?:\[[SIBHU]\d+\]\s*)?", "", text)
    text = re.sub(r"\s*\[[SIBHU]\d+\]\s*$", "", text)
    return ' '.join(text.lower().split()).rstrip('.')


def _expression_units(text):
    # Bounded formatting/metric equivalence only. Questions and quoted propositions
    # retain their punctuation, so they cannot equal an asserted accepted fact.
    text = re.sub(r"\band\s+(?:his\s+)?(?=(?:AVG|OBP|SLG|OPS|ERA|WHIP|HR|RBI)\b)",
                  '; ', text, flags=re.I)
    text = re.sub(r"(?<=[a-z0-9])\.(?=\s+[A-Z])", ';', text)
    return [_normalize_unit(unit) for unit in _value_clauses(text) if unit.strip()]


def _equivalent_unit(expected, actual):
    def form(unit):
        # Supported before/after formatting synonyms, not general semantic inference.
        unit = re.sub(r"\bhis\s+(?=(?:avg|obp|slg|ops|era|whip|hr|rbi)\b)", '', unit)
        unit = re.sub(r"\b(?:went|moved|changed|increased)\s+from\b", 'from', unit)
        return unit
    expected, actual = form(expected), form(actual)
    numbers = r"(?<![\w])(?:\d*\.\d+|\d+)(?![\w])"
    # Every nonnumeric word/role must agree; unknown polarity prose cannot be ignored.
    if re.sub(numbers, '#', expected) != re.sub(numbers, '#', actual):
        return False
    left, right = re.findall(numbers, expected), re.findall(numbers, actual)
    return len(left) == len(right) and all(_number_present(n,[a]) for n,a in zip(left,right))


def _value_expressed(value, answer):
    # Each row's identity/scope and metric must occur with its value in a local unit.
    for clause in re.split(r"[;\n]|(?<=[a-z])\.\s+", answer.lower()):
        metric = str(value["metric"]).lower().replace("_", " ")
        if metric not in clause:
            continue
        if value.get("entity") and str(value["entity"]).lower() not in clause:
            continue
        if value.get("season") and str(value["season"]) not in clause:
            continue
        if value.get("population") and str(value["population"]).lower().replace("_", " ") not in clause:
            continue
        if value.get("unit") and str(value["unit"]).lower() not in clause:
            continue
        tail = clause.split(metric, 1)[1].split(",", 1)[0]
        numbers = re.findall(r"\d*\.\d+|\d+", tail)
        residual = clause
        for field in ('entity', 'season', 'population', 'unit'):
            if value.get(field):
                residual = residual.replace(str(value[field]).lower().replace('_',' '), '')
        residual = residual.replace(metric, '')
        residual = re.sub(r"\d*\.\d+|\d+", '', residual)
        residual = re.sub(r"\b(?:was|is|at|of|the)\b|[. :|=\-]", '', residual)
        if residual.strip():
            continue  # unrecognized prose is repaired, never guessed from token overlap
        if len(numbers) == 1 and _number_present(str(value["value"]), numbers):
            return True
    return False


def _value_clauses(text):
    clauses = []
    for clause in re.split(r"[,;\n]|(?<=[a-z])\.\s+|\b(?:while|whereas|watched)\b", text.lower()):
        # Metric labels introduce independent value roles even without punctuation.
        labels = list(re.finditer(r"\b(?:avg|obp|slg|ops|era|whip|hr|rbi)\b", clause))
        if len(labels) <= 1:
            clauses.append(clause)
            continue
        prefix = clause[:labels[0].start()]
        for index, label in enumerate(labels):
            end = labels[index + 1].start() if index + 1 < len(labels) else len(clause)
            clauses.append(prefix + clause[label.start():end])
    return clauses


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.finditer(r"\d*\.\d+|\d+|[a-z][a-z0-9'\-]{2,}|[\u4e00-\u9fff]{2,}", (text or "").lower()):
        tokens.add(match.group(0))
    return tokens


def _number_present(expected: str, answer: Any) -> bool:
    """Allow rounding to the answer's precision; counts and years remain exact."""
    try:
        target = float(expected)
    except ValueError:
        return False
    if isinstance(answer, str):
        candidates = re.findall(r"\d*\.\d+|\d+", answer)
    else:
        candidates = [str(value) for value in answer]
    for value in candidates:
        try:
            actual = float(value)
        except (TypeError, ValueError):
            continue
        if target.is_integer():
            if actual == target:
                return True
            continue
        decimals = len(value.split('.')[1]) if '.' in value else 0
        # A fractional statistic cannot be rounded all the way to an integer receipt.
        if decimals and abs(actual - target) <= 0.5 * (10 ** -decimals) + 1e-9:
            return True
    return False


def _cross_script(claim_tokens: set[str], answer_tokens: set[str]) -> bool:
    def is_cjk(token: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in token)

    claim_cjk = any(is_cjk(token) for token in claim_tokens)
    answer_cjk = any(is_cjk(token) for token in answer_tokens)
    return claim_cjk != answer_cjk
