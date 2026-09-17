"""Qualification, sample adequacy and league/reference progress.

Qualification decides who may enter a population; sample adequacy decides whether the
available sample supports the conclusion. League progress is authoritative and must not
be inferred from the newest locally ingested date.
"""

from app.models.artifacts import SoftSignal
from app.models.contracts import LeagueStateSnapshot, QualificationRule, SampleAdequacyRule


def sample_adequacy_signal(rule: SampleAdequacyRule | None,
                           sample_size: int | None) -> SoftSignal | None:
    if rule is None or rule.min_sample is None or sample_size is None:
        return None
    if sample_size >= rule.min_sample:
        return None
    severity = "MODERATE" if sample_size * 2 >= rule.min_sample else "MAJOR"
    return SoftSignal(
        code="LOW_SAMPLE", severity=severity,
        detail=f"{sample_size} {rule.sample_unit} below the {rule.min_sample} floor")


def qualification_signal(rule: QualificationRule | None,
                         league_state: LeagueStateSnapshot | None) -> SoftSignal | None:
    if rule is None or rule.kind == "ALL_PLAYERS":
        return None
    if league_state is not None and league_state.local_behind_official:
        return SoftSignal(
            code="QUALIFICATION_PARTIAL", severity="MINOR",
            detail="in-season qualification depends on incomplete local ingestion coverage")
    return None


def league_coverage_limitation(league_state: LeagueStateSnapshot | None) -> str | None:
    """A user-relevant limitation when official progress exceeds local coverage."""
    if league_state is None or not league_state.local_behind_official:
        return None
    lag = league_state.local_lag_days
    if lag is None:
        return "local ingestion coverage end date is unknown; official season progress is unconfirmed"
    return f"local ingestion trails official season progress by {lag} day(s)"
