"""Canonical entity contracts.

A display name is never an internal identity. ``entity_key`` is the canonical id and
carries its namespace; aliases and nicknames only help resolve mentions.
"""

from typing import Literal

from app.models.contracts import Contract, Name


class CanonicalEntity(Contract):
    entity_key: Name
    entity_type: Literal["PLAYER", "TEAM", "LEAGUE"]
    display_name: Name
    aliases: tuple[Name, ...] = ()
    source_ids: tuple[Name, ...] = ()


class EntityCandidate(Contract):
    entity: CanonicalEntity
    match_kind: Literal["EXACT_KEY", "DISPLAY_NAME", "ALIAS", "CONTAINS"]
    confidence: float


class EntityResolution(Contract):
    mention: Name
    candidates: tuple[EntityCandidate, ...] = ()
    canonical: CanonicalEntity | None = None
    needs_clarification: bool = False
    reason: str = ""
