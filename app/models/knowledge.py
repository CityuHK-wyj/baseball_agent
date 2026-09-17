"""Shared Knowledge contracts: a persistent, sourced, versioned MLB knowledge base.

Knowledge is not runtime state and not an agent. Each item is a stable statement with
provenance, authority, temporal validity and a verification status. Dynamic data (today's
stats, current IL status) never lives here; it belongs to the data plane.
"""

from datetime import date, datetime, timezone
from typing import Annotated, Literal

from pydantic import Field

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name


KnowledgeType = Literal[
    "RULE", "TERM", "METRIC", "TEAM", "DIVISION", "LEAGUE", "BALLPARK",
    "PLAYER_PROFILE", "AWARD", "LEAGUE_STRUCTURE", "TRANSACTION_RULE",
    "SCOUTING_CONCEPT", "SOURCE", "COMMUNITY_CREATOR", "HISTORICAL_CONTEXT",
    "ALIAS",
]

# COLLECTED is raw ingested material; ACTIVE is approved and visible to retrieval.
# HISTORICAL was true for a closed period; SUPERSEDED was replaced by a newer version;
# UNVERIFIED has no authoritative source and must not silently override anything.
KnowledgeStatus = Literal["COLLECTED", "ACTIVE", "HISTORICAL", "SUPERSEDED", "UNVERIFIED"]

AuthorityLevel = Literal[
    "OFFICIAL", "AUTHORITATIVE_REFERENCE", "TRUSTED_ANALYTICS",
    "TRUSTED_MEDIA", "COMMUNITY", "UNVERIFIED",
]

AUTHORITY_RANK: dict[str, int] = {
    "OFFICIAL": 5, "AUTHORITATIVE_REFERENCE": 4, "TRUSTED_ANALYTICS": 3,
    "TRUSTED_MEDIA": 2, "COMMUNITY": 1, "UNVERIFIED": 0,
}

# How often a category must be rechecked; the number is a maximum age in days.
FreshnessPolicy = Literal[
    "OFFICIAL_RULES", "CBA", "SEASONAL", "ANNUAL", "PERIODIC",
    "FREQUENT", "LOW", "COMMUNITY", "STATIC",
]

KnowledgeLanguage = Literal["en", "zh", "ja"]

SourceType = Literal[
    "OFFICIAL", "RULEBOOK", "STATS_API", "REFERENCE", "ANALYTICS", "MEDIA",
    "GLOSSARY", "NEWSLETTER", "PODCAST", "VIDEO", "FORUM", "SOCIAL",
]

RelationType = Literal[
    "PLAYS_FOR", "MEMBER_OF", "HOME_BALLPARK", "DEFINED_BY", "PROVIDED_BY",
    "SUPERSEDES", "COVERS", "REFERS_TO", "FORMERLY_KNOWN_AS", "PART_OF",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeSource(ArtifactContract):
    """A registry entry describing where knowledge comes from and how much to trust it."""

    source_id: Name
    name: Name
    root_url: str = ""
    publisher: str = ""
    source_type: SourceType = "REFERENCE"
    authority_level: AuthorityLevel = "UNVERIFIED"
    language: KnowledgeLanguage = "en"
    refresh_policy: FreshnessPolicy = "PERIODIC"
    last_checked: date | None = None
    active: bool = True
    best_for: tuple[Name, ...] = ()
    limitations: tuple[Name, ...] = ()
    notes: str = ""


class KnowledgeItem(ArtifactContract):
    """One stable knowledge statement. ``canonical_key`` is the stable identity."""

    knowledge_id: Name
    knowledge_type: KnowledgeType
    canonical_key: Name
    title: Name
    aliases: tuple[Name, ...] = ()
    language: KnowledgeLanguage = "en"
    summary: str = ""
    structured_payload: dict = {}
    entity_refs: tuple[Name, ...] = ()
    tags: tuple[Name, ...] = ()
    source_refs: tuple[Name, ...] = ()
    source_authority: AuthorityLevel = "UNVERIFIED"
    effective_from: date | None = None
    effective_to: date | None = None
    as_of: date | None = None
    last_verified_at: datetime | None = None
    freshness_policy: FreshnessPolicy = "PERIODIC"
    verification_status: Literal["COLLECTED", "VERIFIED", "UNVERIFIED"] = "COLLECTED"
    version: int = 0
    status: KnowledgeStatus = "ACTIVE"

    def is_current_on(self, day: date) -> bool:
        if self.effective_from is not None and day < self.effective_from:
            return False
        if self.effective_to is not None and day > self.effective_to:
            return False
        return True

    def names(self) -> tuple[str, ...]:
        """Every surface form used for alias/term lookup."""
        return (self.canonical_key, self.title, *self.aliases)


class KnowledgeRelation(ArtifactContract):
    relation_id: Name
    from_key: Name
    relation_type: RelationType
    to_key: Name
    source_refs: tuple[Name, ...] = ()
    as_of: date | None = None
    notes: str = ""


class KnowledgeSnapshot(ArtifactContract):
    snapshot_id: Name
    created_at: datetime = Field(default_factory=utcnow)
    label: str = ""
    counts: dict[str, int] = {}
    source_ids: tuple[Name, ...] = ()
    activated: bool = False


class KnowledgeQuery(ArtifactContract):
    """Narrow retrieval request over the knowledge store."""

    query: str = ""
    knowledge_types: tuple[KnowledgeType, ...] = ()
    entity_refs: tuple[Name, ...] = ()
    tags: tuple[Name, ...] = ()
    language: KnowledgeLanguage | None = None
    community: Name | None = None
    as_of: date | None = None
    authority_floor: AuthorityLevel | None = None
    # Normal authoritative retrieval is ACTIVE-only. HISTORICAL/contextual material must
    # be requested explicitly (with as_of where relevant), never as unconditional truth.
    statuses: tuple[KnowledgeStatus, ...] = ("ACTIVE",)
    max_items: int = Field(default=10, ge=1)


class KnowledgeMatch(ArtifactContract):
    item: KnowledgeItem
    score: float
    reasons: tuple[Name, ...] = ()


class KnowledgeDiff(ArtifactContract):
    """Report of an ingestion/refresh run. Never mutates on its own."""

    domain: Name
    added: tuple[Name, ...] = ()
    updated: tuple[Name, ...] = ()
    unchanged: tuple[Name, ...] = ()
    removed: tuple[Name, ...] = ()
    rejected: tuple[Name, ...] = ()
    ok: bool = True
    message: str = ""


KnowledgeItemRef = Annotated[str, Field(min_length=1)]
