"""Proposition-aware passage selection (M2-1, ADR 0027).

The M1 selector (`evidence_spans`) is lexical and front-loaded: it returns the first six
sentences that share any query token. For a long document (an encyclopedia article, a
transcript, a mailbox thread) that strategy floods the verifier with lead/infobox text and
never reaches the sentence that actually states the requested relationship.

This module scores sentences by what the *mandatory proposition* needs — subject, required
information, time scope, population — and by whether the sentence is real prose rather than a
citation list, a navigation block or an infobox fragment. It never changes the verifier: it
only chooses better candidate passages, and it preserves exactly

    body_text[start:end] == span.text

and the Phase 5B lineage/version invariants.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.contracts.models import RelationKind, RelationSemantics
from app.semantic.relation_attribution import directed_support
from app.semantic.relations import (
    CAUSE_QUERY_CUES,
    CAUSE_SUPPORT_CUES,
    EFFECT_QUERY_CUES,
    EFFECT_SUPPORT_CUES,
    SECTION_CAUSE_CUES,
    SECTION_EFFECT_CUES,
    relation_cue_tokens,
)

# Generic question words carry no proposition content and must not by themselves make a
# front-matter sentence look relevant.
_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "is", "are",
    "was", "were", "be", "been", "his", "her", "their", "its", "this", "that", "these",
    "those", "what", "which", "who", "whom", "how", "why", "when", "where", "did", "does",
    "do", "with", "about", "into", "from", "as", "by", "over", "under", "between",
    "find", "explain", "recent", "report", "reports", "information", "question", "answer",
    "结果", "表现", "如何", "什么", "为什么", "怎么", "是否", "关于", "相关", "报道",
    "解释", "介绍", "分析", "情况", "方面", "总体", "以及", "和", "与", "的", "了",
}

# Citation / navigation / metadata markers. These are strong negative signals: a passage
# that is mostly link lists or bibliographic metadata must not be preferred over prose.
_CITATION_MARKERS = (
    "link to", "retrieved", "archived from the original", "isbn", "doi:", "oclc",
    "pmid", "bibcode", "arxiv", "cs1", "citation", "cite journal", "cite web",
    "references", "external links", "further reading", "see also", "bibliography",
    "permalink", "edit this", "categories:",
)
_NAVIGATION_MARKERS = (
    "jump to", "main menu", "sign in", "log in", "subscribe", "cookie", "privacy policy",
    "terms of use", "advertisement", "share", "facebook", "twitter", "instagram",
    "©", "all rights reserved", "newsletter", "podcast", "listen",
)
_METADATA_RE = re.compile(r"^[\w\s\-']*\s*[|=:]\s*$", re.MULTILINE)
_CITATION_RE = re.compile(r"\[\d+\]|https?://|www\.|v · t · e|navbox", re.IGNORECASE)

_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")

# The relation vocabularies live in ``app.semantic.relations``; these aliases keep the
# selection code readable without re-declaring a second meaning.
_CAUSE_MARKERS = CAUSE_SUPPORT_CUES
_EFFECT_MARKERS = EFFECT_SUPPORT_CUES
_CAUSE_WORDS = CAUSE_QUERY_CUES
_EFFECT_WORDS = EFFECT_QUERY_CUES
_SECTION_CAUSE = SECTION_CAUSE_CUES
_SECTION_EFFECT = SECTION_EFFECT_CUES


@dataclass(frozen=True)
class SpanSelection:
    """One selected passage plus why it was selected (never persisted as evidence)."""

    text: str
    start: int
    end: int
    score: float
    quality: str  # prose | citation | navigation | metadata | fallback
    subject_matched: bool
    required_matched: tuple[str, ...] = ()
    scope_matched: bool = False
    windowed: bool = False
    location: str = ""


@dataclass
class _Sentence:
    start: int
    end: int
    text: str
    stripped_start: int


def _content_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.finditer(r"[0-9]+(?:\.[0-9]+)?|[a-z][a-z0-9'\-]+|[\u4e00-\u9fff]+", (text or "").lower()):
        token = match.group(0)
        if token in _STOPWORDS:
            continue
        if all("\u4e00" <= ch <= "\u9fff" for ch in token):
            if len(token) >= 2:
                tokens.add(token)
        elif len(token) >= 3:
            tokens.add(token)
    return tokens


def _mentions(haystack: str, token: str) -> bool:
    if not token:
        return False
    if all(ord(ch) < 128 for ch in token):
        pattern = rf"(?<![a-z0-9]){re.escape(token)}(?:s|es|d|ed|ing)?(?![a-z0-9])"
        return re.search(pattern, haystack) is not None
    return token in haystack


def _sentences(text: str) -> list[_Sentence]:
    sentences: list[_Sentence] = []
    for match in re.finditer(r"[^.!?。！？\n]+[.!?。！？]?", text):
        raw = match.group(0)
        stripped_start = match.start() + (len(raw) - len(raw.lstrip()))
        body = raw.strip()
        if not body:
            continue
        sentences.append(_Sentence(start=match.start(), end=match.start() + len(raw), text=body, stripped_start=stripped_start))
    return sentences


def _quality(text: str) -> str:
    low = text.lower()
    tokens = re.findall(r"[a-z0-9']+", low)
    if not tokens:
        return "metadata"
    citation_hits = sum(1 for marker in _CITATION_MARKERS if marker in low) + len(_CITATION_RE.findall(text))
    navigation_hits = sum(1 for marker in _NAVIGATION_MARKERS if marker in low)
    link_ratio = low.count("link to") / max(1, len(tokens) / 10)
    if citation_hits >= 2 and citation_hits / max(1, len(tokens)) > 0.02:
        return "citation"
    if link_ratio >= 0.5 or (navigation_hits >= 2 and len(tokens) < 25):
        return "navigation"
    if _METADATA_RE.search(text) and len(tokens) < 12:
        return "metadata"
    if len(tokens) < 5:
        return "metadata"
    return "prose"


def _acronyms(subject: str) -> set[str]:
    # Only capitalized words contribute: "Major League Baseball strike" -> "mlb" (not "mlbs").
    words = re.findall(r"\b[A-Z][A-Za-z]+\b", subject or "")
    acronym = "".join(word[0].lower() for word in words if word)
    return {acronym} if len(acronym) >= 2 else set()


def _proposition_terms(proposition: Any) -> tuple[str, set[str], int | None, str | None]:
    """Return (subject, required tokens, season, population) for a proposition-like value."""
    if proposition is None:
        return "", set(), None, None
    if isinstance(proposition, dict):
        subject = str(proposition.get("subject") or "")
        required = str(proposition.get("required_information") or "")
        operation = str(proposition.get("operation") or "")
        time_scope = proposition.get("time_scope") if isinstance(proposition.get("time_scope"), dict) else {}
        season = time_scope.get("season")
        population = proposition.get("population")
        statement = str(proposition.get("statement") or "")
    else:
        subject = str(getattr(proposition, "subject", "") or "")
        required = str(getattr(proposition, "required_information", "") or "")
        operation = str(getattr(proposition, "operation", "") or "")
        time_scope = getattr(proposition, "time_scope", {}) or {}
        season = time_scope.get("season") if isinstance(time_scope, dict) else None
        population = getattr(proposition, "population", None)
        statement = str(getattr(proposition, "statement", "") or "")
    tokens = _content_tokens(required) | _content_tokens(operation)
    # Drop scope-only tokens (a year is scope, not evidence) and subject tokens (the subject
    # belongs to the entity condition, not the requested relationship). The subject's acronym
    # (e.g. "MLB" for "Major League Baseball") is a subject token too, not a relationship.
    subject_tokens = _content_tokens(subject) | _acronyms(subject)
    tokens = {token for token in tokens if not token.isdigit()} - subject_tokens
    return subject, tokens, season, population




def _prop_relation_kinds(proposition: Any) -> set[RelationKind]:
    """All canonical relation labels carried by a proposition (multi-label aware)."""
    relation = proposition.get("relation_semantics") if isinstance(proposition, dict) else getattr(proposition, "relation_semantics", None)
    if relation is None:
        return set()
    raw = relation.get("relation_kinds") if isinstance(relation, dict) else getattr(relation, "relation_kinds", None)
    if not raw:
        single = relation.get("relation_kind") if isinstance(relation, dict) else getattr(relation, "relation_kind", None)
        raw = [single] if single is not None else []
    kinds: set[RelationKind] = set()
    for item in raw:
        try:
            kinds.add(RelationKind(getattr(item, "value", str(item)).upper()))
        except ValueError:
            continue
    return kinds


def _relation_kind(propositions: list[Any]) -> str:
    """Classify the explanation relation the propositions ask for.

    Prefers the canonical ``RelationSemantics`` labels carried by the Requirement and falls
    back to the shared cue vocabulary so a raw proposition dict (tests, legacy callers) keeps
    the previous behaviour.
    """
    cause = False
    effect = False
    parts: list[str] = []
    for proposition in propositions:
        kinds = _prop_relation_kinds(proposition)
        if RelationKind.CAUSE in kinds:
            cause = True
        if RelationKind.EFFECT in kinds:
            effect = True
        if isinstance(proposition, dict):
            parts.append(str(proposition.get("statement", "")).lower())
            parts.append(str(proposition.get("required_information", "")).lower())
        else:
            parts.append(str(getattr(proposition, "statement", "")).lower())
            parts.append(str(getattr(proposition, "required_information", "")).lower())
    text = " ".join(parts)
    if not cause:
        cause = any(word in text for word in _CAUSE_WORDS)
    if not effect:
        effect = any(word in text for word in _EFFECT_WORDS)
    if cause and effect:
        return "BOTH"
    if cause:
        return "CAUSE"
    if effect:
        return "EFFECT"
    return "GENERAL"


def _marker_bonus(sentence: str, relation: str) -> float:
    low = sentence.lower()
    bonus = 0.0
    if relation in {"CAUSE", "BOTH"}:
        if any(marker in low for marker in _CAUSE_MARKERS):
            bonus += 1.5
    if relation in {"EFFECT", "BOTH"}:
        if any(marker in low for marker in _EFFECT_MARKERS):
            bonus += 1.5
    return bonus


def _section_headers(sentences: list[_Sentence]) -> list[tuple[int, str]]:
    headers: list[tuple[int, str]] = []
    for index, sentence in enumerate(sentences):
        text = sentence.text.strip().rstrip(":")
        words = text.split()
        if not words or len(words) > 5:
            continue
        if sentence.text.rstrip().endswith((".", "!", "?", "。", "！", "？")):
            continue
        headers.append((index, text.lower()))
    return headers


def _section_bonus(index: int, headers: list[tuple[int, str]], relation: str) -> float:
    if relation == "GENERAL" or not headers:
        return 0.0
    current = None
    for header_index, header in headers:
        if header_index <= index:
            current = header
        else:
            break
    if not current:
        return 0.0
    if relation in {"CAUSE", "BOTH"} and any(word in current for word in _SECTION_CAUSE):
        return 1.5
    if relation in {"EFFECT", "BOTH"} and any(word in current for word in _SECTION_EFFECT):
        return 1.5
    return 0.0


def _normalize_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _is_title_line(sentence: str, document_title: str) -> bool:
    if not document_title:
        return False
    low = _normalize_title(sentence)
    title = _normalize_title(document_title)
    if not low or not title:
        return False
    title_no_suffix = title.replace(" wikipedia", "").strip()
    if low in {title, title_no_suffix}:
        return True
    # A short line that is contained in (or contains) the document title is a heading/title
    # fragment, not a passage.
    if len(low.split()) <= 10 and (low in title or title in low or low in title_no_suffix):
        return True
    return False


def _score(
    sentence: str,
    subject_tokens: set[str],
    required_tokens: set[str],
    season: int | None,
    population: str | None,
    query_tokens: set[str],
    *,
    relation: str = "GENERAL",
    section_relation: str = "GENERAL",
    is_title: bool = False,
) -> tuple[float, bool, tuple[str, ...], bool]:
    low = sentence.lower()
    subject_matched = bool(subject_tokens) and any(_mentions(low, token) for token in subject_tokens)
    matched_required = tuple(sorted(token for token in required_tokens if _mentions(low, token)))
    required_coverage = len(matched_required) / len(required_tokens) if required_tokens else 0.0
    query_hits = sum(1 for token in query_tokens if _mentions(low, token))
    scope_matched = False
    if season is not None and re.search(rf"(?<!\d){season}(?!\d)", low):
        scope_matched = True
    if population and population.lower() in low:
        scope_matched = True

    score = 0.0
    score += 3.0 if subject_matched else 0.0
    score += 4.0 * required_coverage
    score += 0.5 * min(3, query_hits)
    score += 1.5 if scope_matched else 0.0
    score += _marker_bonus(sentence, relation)
    if section_relation != "GENERAL":
        score += section_relation  # pre-computed section bonus (0.0 or 1.5)
    # Prefer information-rich prose slightly; a title line is never a passage.
    score += min(1.0, len(sentence) / 240.0)
    if is_title:
        score -= 8.0

    quality = _quality(sentence)
    if quality == "citation":
        score -= 4.0
    elif quality == "navigation":
        score -= 3.0
    elif quality == "metadata":
        score -= 2.0
    if len(sentence) < 25:
        score -= 1.0
    # A sentence must not be rewarded merely for being first: no positional prior is added.
    return score, subject_matched, matched_required, scope_matched


# How far the slot-driven context window may look for a missing subject/relation/scope slot.
# The character cap (``max_window_chars``) still bounds the selected passage.
_MAX_SLOT_DISTANCE = 8


def _slots_present(
    text_lower: str,
    subject_tokens: set[str],
    required_tokens: set[str],
    seasons: list[int],
    populations: list[str],
) -> set[str]:
    """Which proposition slots (subject / relation / scope) a passage already satisfies."""
    present: set[str] = set()
    if subject_tokens and any(_mentions(text_lower, token) for token in subject_tokens):
        present.add("subject")
    if required_tokens and any(_mentions(text_lower, token) for token in required_tokens):
        present.add("relation")
    if seasons and any(re.search(rf"(?<!\d){season}(?!\d)", text_lower) for season in seasons):
        present.add("scope")
    if populations and any(
        str(population).lower().replace("_", " ") in text_lower for population in populations
    ):
        present.add("scope")
    return present


def select_spans(
    text: str,
    *,
    query: str = "",
    propositions: Iterable[Any] = (),
    max_spans: int = 6,
    max_window_chars: int = 1200,
    document_title: str = "",
) -> list[SpanSelection]:
    """Select proposition-aware passages from ``text`` with exact offsets.

    Adjacent-sentence windows are used only to recover the subject/relationship context
    (coreference): when a high-scoring sentence carries the relation but not the subject, or
    the subject but not the relation, the nearest adjacent sentence supplying the missing
    half is merged into one span. Offsets are always ``body_text[start:end] == text``.
    """
    if not text:
        return []
    query_tokens = _content_tokens(query)
    propositions = list(propositions)
    term_sets = [_proposition_terms(proposition) for proposition in propositions]
    if not term_sets:
        term_sets = [("", set(), None, None)]

    sentences = _sentences(text)
    if not sentences:
        stored = text[:600]
        return [SpanSelection(stored, 0, len(stored), 0.0, "fallback", False, (), False, False, "offset 0")]

    subject_tokens: set[str] = set()
    required_tokens: set[str] = set()
    seasons: list[int] = []
    populations: list[str] = []
    for subject, required, season, population in term_sets:
        subject_tokens |= _content_tokens(subject) | _acronyms(subject)
        required_tokens |= required
        if isinstance(season, int):
            seasons.append(season)
        if population:
            populations.append(str(population))

    relation = _relation_kind(propositions)
    headers = _section_headers(sentences)
    scored: list[tuple[float, int, _Sentence, bool, tuple[str, ...], bool]] = []
    for index, sentence in enumerate(sentences):
        best: tuple[float, bool, tuple[str, ...], bool] | None = None
        section_bonus = _section_bonus(index, headers, relation)
        title_line = _is_title_line(sentence.text, document_title)
        for proposition, (_subject, required, season, population) in zip(propositions or [None], term_sets):
            value = _score(
                sentence.text,
                subject_tokens,
                required,
                season,
                population,
                query_tokens,
                relation=relation,
                section_relation=section_bonus,
                is_title=title_line,
            )
            if directed_support(proposition, sentence.text):
                semantics = proposition.get("relation_semantics") if isinstance(proposition, dict) else getattr(proposition, "relation_semantics", None)
                if isinstance(semantics, dict):
                    semantics = RelationSemantics.model_validate(semantics)
                if semantics is not None and (semantics.relation_source or semantics.relation_target):
                    value = (value[0] + 3.0, *value[1:])
            if best is None or value[0] > best[0]:
                best = value
        if best is None:
            best = (0.0, False, (), False)
        # M2-4 candidate admission: a citation/navigation/metadata fragment that does not
        # itself carry a requested relationship token is never offered as a candidate. It may
        # still appear in the artifact body, but it cannot become selected support evidence.
        quality = _quality(sentence.text)
        if quality != "prose" and not best[2]:
            continue
        # A document title/heading is never a passage candidate, even when it contains the
        # subject: it cannot establish a relationship.
        if title_line:
            continue
        if best[0] > 0:
            scored.append((best[0], index, sentence, best[1], best[2], best[3]))

    if not scored:
        stored = text[:600]
        return [SpanSelection(stored, 0, len(stored), 0.0, "fallback", False, (), False, False, "offset 0")]

    # Prefer real prose: when at least one prose sentence is a candidate, citation/navigation/
    # metadata fragments are not selected at all, even if they happen to contain a token. Only
    # when no prose candidate exists (e.g. a short table or a citation-only page) are
    # low-quality fragments retained, and then only when they carry the requested relation.
    prose_scored = [item for item in scored if _quality(item[2].text) == "prose"]
    if prose_scored:
        scored = prose_scored

    scored.sort(key=lambda item: (-item[0], item[2].stripped_start))

    selected: list[SpanSelection] = []
    used: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < other_end and end > other_start for other_start, other_end in used)

    for score, index, sentence, subject_matched, matched_required, scope_matched in scored:
        if len(selected) >= max_spans:
            break
        quality = _quality(sentence.text)
        start, end = sentence.stripped_start, sentence.stripped_start + len(sentence.text)
        windowed = False
        # Slot-driven context window: expand outward only while a proposition slot is missing
        # (subject identity, requested relation, time/population scope). This replaces a fixed
        # sentence-count window: the smallest sufficient contiguous context is selected, and
        # exact offsets are preserved because the range is a contiguous slice of ``text``.
        if quality == "prose":
            needed: set[str] = set()
            if subject_tokens:
                needed.add("subject")
            if required_tokens:
                needed.add("relation")
            if seasons or populations:
                needed.add("scope")
            satisfied = _slots_present(text[start:end].lower(), subject_tokens, required_tokens, seasons, populations)
            for distance in range(1, _MAX_SLOT_DISTANCE + 1):
                if needed <= satisfied:
                    break
                for neighbour_index in (index - distance, index + distance):
                    if not 0 <= neighbour_index < len(sentences):
                        continue
                    neighbour = sentences[neighbour_index]
                    gained = _slots_present(
                        neighbour.text.lower(), subject_tokens, required_tokens, seasons, populations
                    ) - satisfied
                    if not gained:
                        continue
                    new_start = min(start, neighbour.stripped_start)
                    new_end = max(end, neighbour.stripped_start + len(neighbour.text))
                    if new_end - new_start > max_window_chars:
                        continue
                    start, end = new_start, new_end
                    windowed = True
                    satisfied |= gained
        start = max(0, start)
        end = min(len(text), end)
        if end - start > max_window_chars:
            end = start + max_window_chars
        if overlaps(start, end):
            continue
        stored = text[start:end]
        if not stored.strip():
            continue
        used.append((start, end))
        selected.append(
            SpanSelection(
                text=stored,
                start=start,
                end=end,
                score=round(score, 4),
                quality=quality,
                subject_matched=subject_matched,
                required_matched=matched_required,
                scope_matched=scope_matched,
                windowed=windowed,
                location=f"offset {start}",
            )
        )
    return selected


def select_span_dicts(
    text: str,
    *,
    query: str = "",
    propositions: Iterable[Any] = (),
    max_spans: int = 6,
    max_window_chars: int = 1200,
    document_title: str = "",
) -> list[dict[str, Any]]:
    """``select_spans`` rendered in the persisted span-dictionary shape.

    ``quality``/``required_matched`` are internal candidate-admission metadata; the persisted
    ``EvidenceSpan`` model ignores them, and the verifier never sees them as evidence fields.
    """
    result: list[dict[str, Any]] = []
    for selection in select_spans(
        text, query=query, propositions=propositions, max_spans=max_spans,
        max_window_chars=max_window_chars, document_title=document_title,
    ):
        result.append(
            {
                "text": selection.text,
                "start": selection.start,
                "end": selection.end,
                "location": selection.location,
                "source_type": "DOCUMENT_BODY",
                "retrieval_status": "OK",
                "quality": selection.quality,
                "score": selection.score,
                "subject_matched": selection.subject_matched,
                "required_matched": list(selection.required_matched),
                "scope_matched": selection.scope_matched,
                "windowed": selection.windowed,
            }
        )
    return result


_ORG_STARTERS = {"major", "new", "los", "san", "st", "saint", "the", "national", "american", "world"}


def _looks_like_person_name(subject: str) -> bool:
    """A two-token capitalized name with no organizational starter (e.g. "Aaron Judge")."""
    parts = re.findall(r"[A-Za-z][A-Za-z'\-]+", subject or "")
    if len(parts) != 2:
        return False
    if not all(part[0].isupper() for part in parts):
        return False
    return parts[0].lower() not in _ORG_STARTERS


def subject_signature(subject: str) -> str:
    """The distinguishing token of a subject (family name / single CJK name).

    Used for candidate admission only: a passage must contain the subject's distinguishing
    token before it is offered to the verifier, so a shared given name ("Aaron") cannot let a
    different person ("Aaron Hicks") become support. It applies only to person-shaped names,
    so league/team/event subjects keep their normal recall. The Evidence Verifier is unchanged.
    """
    parts = re.findall(r"[A-Za-z][A-Za-z'\-]+|[\u4e00-\u9fff]+", subject or "")
    for part in reversed(parts):
        if all("\u4e00" <= ch <= "\u9fff" for ch in part) or len(part) >= 3:
            return part.lower()
    return ""


def subject_signature_present(text: str, subject: str) -> bool:
    if not _looks_like_person_name(subject):
        return True
    signature = subject_signature(subject)
    if not signature:
        return True
    return _mentions((text or "").lower(), signature)


def relationship_tokens(proposition: Any) -> set[str]:
    """The requested-relationship tokens (subject/scope tokens removed) for candidate admission.

    Relational propositions also carry their canonical cue tokens so the admission gate can
    recognise a fragment that expresses the relationship without the generic summary word.
    """
    _subject, required, _season, _population = _proposition_terms(proposition)
    relation = (
        proposition.get("relation_semantics") if isinstance(proposition, dict)
        else getattr(proposition, "relation_semantics", None)
    )
    if relation is None:
        return required
    if isinstance(relation, dict):
        try:
            relation = RelationSemantics.model_validate(relation)
        except Exception:  # noqa: BLE001 - a malformed semantics must not change admission
            return required
    return required | relation_cue_tokens(relation)


def support_eligible(span: dict[str, Any], required_tokens: set[str]) -> bool:
    """Candidate-admission gate (M2-4): a low-quality fragment may only be admitted when it
    actually carries a requested relationship token.

    ``required_tokens`` must be the *relationship* tokens (subject tokens removed); otherwise a
    subject-only citation or metadata fragment would pass. This is applied **before** the
    unchanged verifier. It never turns a low-quality fragment into support; it only stops
    obvious citation/navigation/metadata fragments from being presented to the verifier as if
    they were candidate evidence.
    """
    quality = str(span.get("quality") or "prose")
    if quality == "prose":
        return True
    text = str(span.get("text") or "").lower()
    if not required_tokens:
        return False
    return any(_mentions(text, token) for token in required_tokens)
