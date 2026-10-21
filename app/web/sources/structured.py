"""Structured-record envelope contract (M1-T6, ADR 0030, SPEC §13.5).

Declared in M1 as a contract only: no structured network adapter is implemented until M4.
A rendered structured body is an explicitly **labeled** rendering, never a news article and
never the unconverted official original.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StructuredRecord:
    """Provenance envelope for one official structured record."""

    record_id: str
    source_id: str
    source_url: str
    as_of: str
    conversion_version: str
    raw_fields: dict[str, Any] = field(default_factory=dict)

    def provenance_header(self) -> str:
        """The deterministic header every rendered body must begin with."""
        return "\n".join(
            [
                f"source_id: {self.source_id}",
                f"record_id: {self.record_id}",
                f"as_of: {self.as_of}",
                f"source_url: {self.source_url}",
                f"conversion_version: {self.conversion_version}",
            ]
        )

    def render_body(self) -> str:
        """Canonical, deterministic body: provenance header + verbatim raw fields."""
        return self.provenance_header() + "\n\n" + json.dumps(self.raw_fields, ensure_ascii=False, sort_keys=True)


def structured_source_quality(source_id: str) -> str:
    return f"official_structured_record:{source_id}"


def structured_location(record_id: str) -> str:
    return f"structured:{record_id}"
