"""Requested / Declared / Verified scope separation.

Three concepts, deliberately not interchangeable:

``RequestedScope``
    what the user / Need requires (``Need.required_scope``, ``Goal.scope``).

``DeclaredScope``
    what the Tool/provider *claims* it returned (``RuntimeArtifact.actual_scope``). A
    Tool declaring success is never proof of coverage: an echoed request value can only
    ever reach ``PARTIAL``, never ``VERIFIED``.

``ScopeVerification``
    what deterministic/provider/execution evidence actually establishes, per dimension.

The verifier is the only producer of verified scope. It reads a truthful execution/provider
*receipt* and, for derived products, the verification status of the exact upstream
artifacts that were bound. ``UNKNOWN`` stays UNKNOWN and never becomes coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.models.artifact_runtime import RuntimeArtifact, Scope, ScopeVerification
from app.models.contracts import TimeRange

from app.artifact_runtime.scope import measure_families, population_category

_DIMENSION_FIELDS: dict[str, str] = {
    "entity": "entities",
    "population": "population",
    "membership": "membership_basis",
    "time": "time_range",
    "season": "seasons",
    "game_type": "game_types",
    "event_population": "event_population",
    "measure": "metric",
    "aggregation": "aggregation",
    "qualification": "qualification",
    "source_coverage": "source_coverage",
}

# Dimensions that are inherited from bound upstream products rather than established by
# this Artifact's own execution.
_INHERITED_DIMENSIONS = ("entity", "membership")

_STATUS_SCORE = {"VERIFIED": 1.0, "PARTIAL": 0.5, "UNKNOWN": 0.0, "MISMATCH": 0.0}
_STATUS_ORDER = {"MISMATCH": 0, "UNKNOWN": 1, "PARTIAL": 2, "VERIFIED": 3}


@dataclass(frozen=True)
class ScopeVerdict:
    """Dimension-aware comparison result used by the judge and state projection."""

    dimensions: dict[str, str] = field(default_factory=dict)  # dimension -> status
    gaps: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    per_artifact: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def hard_mismatch(self) -> bool:
        return any(status == "MISMATCH" for status in self.dimensions.values())

    @property
    def blocking(self) -> bool:
        return self.hard_mismatch

    @property
    def unknown(self) -> bool:
        return any(status == "UNKNOWN" for status in self.dimensions.values())

    @property
    def fully_verified(self) -> bool:
        return all(status == "VERIFIED" for status in self.dimensions.values())

    @property
    def aggregate(self) -> float:
        if not self.dimensions:
            return 1.0
        return round(sum(_STATUS_SCORE.get(value, 0.0)
                         for value in self.dimensions.values())
                     / len(self.dimensions), 4)


def requested_dimensions(scope: Scope | None) -> tuple[str, ...]:
    if scope is None:
        return ()
    return tuple(dimension for dimension, attribute in _DIMENSION_FIELDS.items()
                 if getattr(scope, attribute, None))


def _requested_value(scope: Scope, dimension: str):
    return getattr(scope, _DIMENSION_FIELDS[dimension], None)


def _norm(value) -> str:
    return str(value or "").strip().casefold()


def _render(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _window(value) -> TimeRange | None:
    if value is None:
        return None
    if isinstance(value, TimeRange):
        return value
    try:
        return TimeRange(start=date.fromisoformat(str(value[0])),
                         end=date.fromisoformat(str(value[1])))
    except (ValueError, TypeError, IndexError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Per-kind independent observations
# ---------------------------------------------------------------------------


def _analytics_observation(dimension: str, receipt: dict):
    """Independent observations from a compiled/executed analytical query."""
    if dimension == "time":
        return _window(receipt.get("applied_window")), True
    if dimension == "season":
        values = receipt.get("seasons")
        return (tuple(str(item) for item in values), True) if values else (None, True)
    if dimension == "game_type":
        if "game_types" in receipt:
            return tuple(str(item) for item in receipt["game_types"]), True
        return None, True
    if dimension == "measure":
        value = receipt.get("measure") or receipt.get("metric")
        return (str(value), True) if value else (None, True)
    if dimension == "event_population":
        value = receipt.get("event_population")
        return (str(value), True) if value else (None, True)
    if dimension == "aggregation":
        value = receipt.get("aggregation")
        return (str(value), True) if value else (None, True)
    if dimension == "population":
        value = receipt.get("population")
        return (str(value), True) if value else (None, False)
    if dimension == "qualification":
        value = receipt.get("qualification")
        return (str(value), True) if value else (None, True)
    if dimension == "source_coverage":
        values = receipt.get("source_coverage")
        return (tuple(str(item) for item in values), True) if values else (None, True)
    # entity / membership are inherited, never self-declared by the query.
    return None, False


def _roster_observation(dimension: str, receipt: dict):
    if dimension == "entity":
        team = receipt.get("team")
        return ((str(team),), True) if team else (None, False)
    if dimension == "membership":
        roster_type = receipt.get("roster_type")
        return (str(roster_type), True) if roster_type else (None, False)
    if dimension == "population":
        return (str(receipt.get("population") or "players"), True)
    return None, False


def _evidence_observation(dimension: str, receipt: dict):
    if dimension == "entity":
        values = receipt.get("canonical_entities") or receipt.get("entities")
        return (tuple(str(item) for item in values), True) if values else (None, True)
    return None, False


def _composite_observation(dimension: str, receipt: dict):
    if dimension in ("aggregation", "qualification"):
        value = receipt.get(dimension)
        return (str(value), True) if value else (None, False)
    return None, False


def _observation(artifact: RuntimeArtifact, receipt: dict, dimension: str):
    """Return ``(observed_value, independently_established)``."""
    kind = artifact.kind
    declared = artifact.declared_scope
    if kind == "analytics":
        return _analytics_observation(dimension, receipt)
    if kind == "team_roster":
        return _roster_observation(dimension, receipt)
    if kind in ("knowledge", "web_evidence", "entity_mapping"):
        return _evidence_observation(dimension, receipt)
    if kind in ("derived", "ranked", "compute"):
        return _composite_observation(dimension, receipt)
    if kind == "batting_stats":
        if dimension == "time":
            if receipt.get("mode") == "range" and receipt.get("start") and receipt.get("end"):
                return _window((receipt["start"], receipt["end"])), True
            return None, True
        if dimension == "season":
            season = receipt.get("season")
            return (str(season), True) if season else (None, True)
        if dimension == "measure":
            metric = receipt.get("metric")
            return (str(metric), True) if metric else (None, True)
        if dimension == "entity":
            names = receipt.get("entities")
            return (tuple(str(item) for item in names), True) if names else (None, True)
        return None, False
    # Unknown producer kinds: declared scope is a claim, never independent evidence.
    if dimension in ("time",):
        return None, False
    value = getattr(declared, _DIMENSION_FIELDS[dimension], None) if declared else None
    return value, False


# ---------------------------------------------------------------------------
# Status computation
# ---------------------------------------------------------------------------


def _set_status(requested, observed, independent: bool) -> tuple[str, str]:
    if not observed:
        return ("PARTIAL" if independent else "UNKNOWN",
                "evidence does not state the requested values")
    requested_norm = {_norm(item) for item in requested}
    observed_norm = {_norm(item) for item in observed}
    if requested_norm <= observed_norm:
        if independent:
            return "VERIFIED", ""
        return "PARTIAL", "declared by the producer, not independently verified"
    if requested_norm & observed_norm:
        return "PARTIAL", "only some requested values are present"
    return "MISMATCH", "evidence values do not match the request"


def _time_status(requested_range: TimeRange, observed, independent: bool
                 ) -> tuple[str, str]:
    if observed is None:
        if independent:
            return "MISMATCH", "no executed/provider time predicate matches the request"
        return "UNKNOWN", "no executed/provider time predicate is recorded"
    req_days = (requested_range.end - requested_range.start).days + 1
    obs_days = (observed.end - observed.start).days + 1
    if observed.start <= requested_range.start and observed.end >= requested_range.end:
        if obs_days > req_days:
            return "MISMATCH", "evidence aggregates a broader window than requested"
        return ("VERIFIED" if independent else "PARTIAL"), (
            "" if independent else "declared window, not independently verified")
    if observed.end < requested_range.start or observed.start > requested_range.end:
        return "MISMATCH", "evidence window does not overlap the request"
    return "PARTIAL", "evidence window only partially covers the request"


def _measure_status(requested_value, observed, independent: bool) -> tuple[str, str]:
    if not observed:
        return ("PARTIAL" if independent else "UNKNOWN",
                "evidence does not state the executed measure")
    if _norm(requested_value) == _norm(observed):
        return ("VERIFIED" if independent else "PARTIAL"), (
            "" if independent else "declared measure, not independently verified")
    if measure_families(str(requested_value)) & measure_families(str(observed)):
        return "PARTIAL", ("requested and executed measures are in the same family but "
                           "are not identical")
    return "MISMATCH", (f"executed measure {observed!r} is not the requested "
                        f"{requested_value!r}")


def _exact_status(requested_value, observed, independent: bool) -> tuple[str, str]:
    if not observed:
        return ("PARTIAL" if independent else "UNKNOWN",
                "evidence does not state the requested value")
    if _norm(requested_value) == _norm(observed):
        return ("VERIFIED" if independent else "PARTIAL"), (
            "" if independent else "declared value, not independently verified")
    return "MISMATCH", f"observed {observed!r} differs from requested {requested_value!r}"


def _population_status(requested_value, observed, independent: bool) -> tuple[str, str]:
    if not observed:
        return ("PARTIAL" if independent else "UNKNOWN",
                "evidence does not state its population")
    if _norm(requested_value) == _norm(observed):
        return ("VERIFIED" if independent else "PARTIAL"), (
            "" if independent else "declared population, not independently verified")
    if population_category(str(requested_value)) == population_category(str(observed)):
        return "PARTIAL", "requested and observed population share only a coarse category"
    return "MISMATCH", f"observed population {observed!r} differs from requested"


def _membership_status(requested_value, observed, independent: bool) -> tuple[str, str]:
    if not observed:
        return ("PARTIAL" if independent else "UNKNOWN",
                "evidence does not state a membership basis")
    requested_norm = _norm(requested_value)
    observed_norm = _norm(observed)
    synonyms = {
        "official_roster": {"official_roster", "official", "postseason_roster"},
        "postseason_roster": {"official_roster", "postseason_roster", "postseason"},
        "active_roster": {"active_roster", "active"},
        "observed_participants": {"observed_participants", "participants"},
    }
    if requested_norm == observed_norm:
        return ("VERIFIED" if independent else "PARTIAL"), ""
    allowed = synonyms.get(requested_norm, {requested_norm})
    if observed_norm in allowed:
        return ("VERIFIED" if independent else "PARTIAL"), ""
    if observed_norm in ("active", "active_roster") and requested_norm in (
            "official_roster", "postseason_roster"):
        return "MISMATCH", ("the provider returns the current active roster, which cannot "
                            "establish the requested membership basis")
    return "MISMATCH", (f"observed membership basis {observed!r} differs from requested "
                        f"{requested_value!r}")


def _status_for(dimension: str, requested_value, observed, independent: bool
                ) -> tuple[str, str]:
    if dimension == "time":
        return _time_status(requested_value, observed, independent)
    if dimension == "measure":
        return _measure_status(requested_value, observed, independent)
    if dimension == "population":
        return _population_status(requested_value, observed, independent)
    if dimension == "membership":
        return _membership_status(requested_value, observed, independent)
    if dimension == "entity":
        return _set_status(requested_value, observed, independent)
    if dimension in ("game_type", "event_population", "aggregation", "qualification"):
        # An accepted query that does not actually restrict this dimension cannot cover
        # an explicit request for it.
        if not observed:
            return (("MISMATCH" if independent else "UNKNOWN"),
                    f"the executed evidence does not establish {dimension}")
    return _exact_status(requested_value, observed, independent)


def _inherited(dimension: str, upstream: tuple[RuntimeArtifact, ...]
               ) -> tuple[str, str] | None:
    """Combine upstream verification for an inherited dimension.

    A downstream product may *inherit* a VERIFIED upstream dimension, but it can never
    upgrade a MISMATCH or an UNKNOWN upstream.
    """
    best: tuple[str, str] | None = None
    for parent in upstream:
        status = parent.dimension_status(dimension)
        if status == "MISMATCH":
            return ("MISMATCH", f"upstream {parent.artifact_id} has a scope mismatch")
        if status in ("UNKNOWN", "PARTIAL", "VERIFIED"):
            candidate = (status, f"inherited from upstream {parent.artifact_id}")
            if best is None or _STATUS_ORDER[status] > _STATUS_ORDER[best[0]]:
                best = candidate
    return best


def _kind_override(kind: str, dimension: str, requested_value, receipt: dict
                   ) -> tuple[str, str] | None:
    """Truthful capability restrictions: a provider that cannot select the requested
    dimension must not be allowed to fall back to UNKNOWN/declared scope."""
    if kind == "team_roster":
        if dimension in ("time", "season"):
            if requested_value:
                return ("MISMATCH", "this provider returns the current active roster only "
                        "and cannot select a historical date, season or roster type")
        if dimension == "population":
            return ("PARTIAL", "the roster provider returns every listed position; a "
                    "position-filtered population is not established")
    if kind == "batting_stats" and dimension == "time":
        if requested_value and receipt.get("mode") == "season":
            return ("MISMATCH", "a season aggregate is not evidence for the requested "
                    "date window")
    return None


def verify_artifact_scope(requested: Scope | None, artifact: RuntimeArtifact, *,
                          receipt: dict | None = None,
                          upstream: tuple[RuntimeArtifact, ...] = ()
                          ) -> tuple[ScopeVerification, ...]:
    """Compute per-dimension verification for one Artifact against a request."""
    if requested is None:
        return ()
    receipt = dict(receipt if receipt is not None else
                   (artifact.metadata.get("execution_receipt") or {}))
    verifier = f"deterministic:{artifact.kind}"
    verifications: list[ScopeVerification] = []
    for dimension in requested_dimensions(requested):
        requested_value = _requested_value(requested, dimension)
        observed, independent = _observation(artifact, receipt, dimension)
        status, limitation = _status_for(dimension, requested_value, observed, independent)
        override = _kind_override(artifact.kind, dimension, requested_value, receipt)
        if override is not None:
            status, limitation = override

        if dimension in _INHERITED_DIMENSIONS:
            inherited = _inherited(dimension, upstream)
            if inherited is not None and status != "MISMATCH" \
                    and _STATUS_ORDER[inherited[0]] > _STATUS_ORDER[status]:
                status, limitation = inherited
        verifications.append(ScopeVerification(
            dimension=dimension, status=status,
            requested=_render(requested_value), observed=_render(observed),
            evidence_refs=tuple(_evidence_refs(artifact, receipt, upstream)),
            verifier=verifier, source_snapshot=str(receipt.get("source_snapshot", "")),
            limitations=(limitation,) if limitation else ()))
    return tuple(verifications)


def _evidence_refs(artifact: RuntimeArtifact, receipt: dict,
                   upstream: tuple[RuntimeArtifact, ...]) -> list[str]:
    refs = list(artifact.references)
    refs.extend(receipt.get("evidence_refs", ()) or ())
    refs.extend(parent.artifact_id for parent in upstream)
    refs.append(artifact.artifact_id)
    return list(dict.fromkeys(refs))


def best_dimension_status(artifacts: tuple[RuntimeArtifact, ...]) -> dict[str, str]:
    """Best status per dimension across a set of artifacts (joint support)."""
    result: dict[str, str] = {}
    for artifact in artifacts:
        for verification in artifact.scope_verifications:
            current = result.get(verification.dimension)
            if current is None or _STATUS_ORDER[verification.status] > _STATUS_ORDER[current]:
                result[verification.dimension] = verification.status
    return result


def verified_verdict(requested: Scope | None, artifacts: tuple[RuntimeArtifact, ...]
                     ) -> ScopeVerdict:
    """Judge-facing comparison built only from scope verifications (joint support)."""
    if requested is None:
        return ScopeVerdict(dimensions={}, gaps=(), reasons=())
    best = best_dimension_status(artifacts)
    dims: dict[str, str] = {}
    gaps: list[str] = []
    reasons: list[str] = []
    for dimension in requested_dimensions(requested):
        status = best.get(dimension, "UNKNOWN")
        dims[dimension] = status
        requested_value = _render(_requested_value(requested, dimension))
        if status == "MISMATCH":
            observed = ""
            for artifact in artifacts:
                verification = artifact.verification(dimension)
                if verification is not None and verification.status == "MISMATCH":
                    observed = verification.observed
                    break
            gaps.append(f"{dimension} mismatch: requested {requested_value!r}, "
                        f"observed {observed!r}")
        elif status == "UNKNOWN":
            gaps.append(f"{dimension} could not be verified for the request")
        elif status == "PARTIAL":
            reasons.append(f"{dimension} only partially verified")
    return ScopeVerdict(dimensions=dims, gaps=tuple(dict.fromkeys(gaps)),
                        reasons=tuple(dict.fromkeys(reasons)),
                        per_artifact={artifact.artifact_id: {
                            v.dimension: v.status for v in artifact.scope_verifications}
                            for artifact in artifacts})
