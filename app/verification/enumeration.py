"""Operation-aware enumeration compatibility (F5, ADR 0020).

Individual ``COMPLETE`` inputs are not sufficient for a global result. Whether two
analytical artifacts may be combined depends on the *operation*:

- ``EXTREMUM`` / ``RANK`` / ``UNION`` pool partitions of one population, so the
  partitions must be provably disjoint and share population, qualification and identity.
- ``DELTA`` compares the same entities across distinct partitions, so different seasons
  (and different sources covering disjoint windows) are expected and legal.
- ``JOIN`` aligns different measures for the same entities in the same population, so
  overlapping partitions are expected.
- ``UNKNOWN`` fails closed when more than one input would be merged.

Different seasons or different sources are never automatically a mismatch; they are
judged against the declared operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.contracts.models import Artifact, EnumerationContract, GlobalOperation

_POPULATION_NONE = "__NONE__"


@dataclass
class EnumerationCompatibilityResult:
    ok: bool
    code: str = "OK"
    detail: str = ""
    shared: dict[str, Any] = field(default_factory=dict)


def check_compatibility(operation: GlobalOperation, artifacts: list[Artifact]) -> EnumerationCompatibilityResult:
    contracts = [artifact.enumeration for artifact in artifacts]
    if not artifacts:
        return EnumerationCompatibilityResult(True, "OK", "no inputs")

    # 1. Every input that contributes to a global/non-local claim must be COMPLETE.
    if operation.kind != "LOCAL":
        incomplete = [
            (artifact, artifact.enumeration)
            for artifact in artifacts
            if artifact.enumeration is None or not artifact.enumeration.is_complete()
        ]
        if incomplete:
            detail = "; ".join(
                f"{artifact.id}:"
                + (enumerate_detail(enum) if enum is not None else "UNKNOWN(no enumeration contract)")
                for artifact, enum in incomplete
            )
            return EnumerationCompatibilityResult(False, "INCOMPLETE_INPUT", f"truncated or unmeasured input population: {detail}")

    if len(artifacts) <= 1:
        return EnumerationCompatibilityResult(True, "OK", "single complete input")

    if operation.kind == "UNKNOWN":
        return EnumerationCompatibilityResult(
            False,
            "UNKNOWN_OPERATION",
            "multiple inputs are combined without a declared global operation; refusing a global claim",
        )
    if operation.kind == "LOCAL":
        return EnumerationCompatibilityResult(
            False,
            "LOCAL_OPERATION_WITH_MULTIPLE_INPUTS",
            "a LOCAL operation cannot combine more than one input into a global claim",
        )

    # 2. Shared universe: identity domain, population and qualification must agree.
    kinds = {_kind(contract) for contract in contracts}
    if len(kinds) != 1 or "" in kinds:
        return EnumerationCompatibilityResult(
            False,
            "IDENTITY_KIND_UNVERIFIED",
            f"inputs do not share one verified identity domain: {sorted(kinds)}",
        )
    shared_kind = next(iter(kinds))
    if operation.identity and operation.identity.upper() != shared_kind:
        return EnumerationCompatibilityResult(
            False,
            "IDENTITY_MISMATCH",
            f"declared identity '{operation.identity}' != input identity '{shared_kind}'",
        )

    populations = {_population(contract) for contract in contracts}
    if len(populations) != 1:
        return EnumerationCompatibilityResult(
            False,
            "POPULATION_MISMATCH",
            f"inputs cover different populations: {sorted(populations)}",
        )
    qualifications = {contract.qualification for contract in contracts}
    if len(qualifications) != 1:
        return EnumerationCompatibilityResult(
            False,
            "QUALIFICATION_MISMATCH",
            f"inputs use different qualification rules: {sorted(str(q) for q in qualifications)}",
        )

    if operation.kind == "JOIN":
        return _check_join(operation, artifacts, contracts)

    # 3. Partitioned combination (RANK / UNION / EXTREMUM / DELTA).
    if _same_source_same_window(contracts):
        return EnumerationCompatibilityResult(
            False,
            "SOURCE_OVERLAP",
            "two inputs read the same source over the same window; a union would double count",
        )
    partition = operation.partition_by or _infer_partition(contracts)
    if not partition:
        if operation.dedup_key and all(set(operation.dedup_key) <= set(c.identity_columns) for c in contracts):
            partition = "DEDUP"
        else:
            return EnumerationCompatibilityResult(
                False,
                "PARTITION_PROOF_MISSING",
                "cannot prove the inputs are disjoint partitions of one population",
            )
    if partition != "DEDUP":
        values = [_partition_value(contract, partition) for contract in contracts]
        if len(set(values)) != len(values):
            return EnumerationCompatibilityResult(
                False,
                "PARTITION_NOT_DISTINCT",
                f"inputs are not disjoint on {partition}: {values}",
            )
        if _source_partition_collision(contracts, partition):
            return EnumerationCompatibilityResult(
                False,
                "SOURCE_OVERLAP",
                f"two inputs share a source within the same {partition} partition; double counting is possible",
            )
    if _ranges_overlap(contracts) and partition != "DEDUP":
        return EnumerationCompatibilityResult(
            False,
            "DATE_RANGE_OVERLAP",
            "input date ranges overlap; a union would double count",
        )

    if operation.kind == "DELTA":
        identity = _delta_identity_ok(artifacts)
        if not identity.ok:
            return identity

    return EnumerationCompatibilityResult(
        True,
        "OK",
        f"compatible {operation.kind} over {len(contracts)} complete partitions",
        {"identity_kind": shared_kind, "partition_by": partition, "population": next(iter(populations))},
    )


def enumerate_detail(contract: EnumerationContract) -> str:
    count = f", count={contract.population_count}" if contract.population_count is not None else ""
    return f"{contract.completeness}(limit={contract.limit}, rows={contract.returned_rows}{count})"


# ------------------------------------------------------------------ helpers
def _kind(contract: EnumerationContract | None) -> str:
    if contract is None or not contract.entity_kind:
        return ""
    return str(contract.entity_kind).upper()


def _population(contract: EnumerationContract) -> str:
    return (contract.population or _POPULATION_NONE).upper()


def _infer_partition(contracts: list[EnumerationContract]) -> str:
    seasons = [contract.season for contract in contracts]
    if all(season is not None for season in seasons) and len(set(seasons)) == len(seasons) and len(seasons) > 1:
        return "SEASON"
    ranges = [
        (contract.date_range.start, contract.date_range.end) if contract.date_range else None
        for contract in contracts
    ]
    if all(rng is not None for rng in ranges) and len(set(ranges)) == len(ranges) and len(ranges) > 1:
        return "DATE_RANGE"
    sources = [contract.source for contract in contracts]
    if all(sources) and len(set(sources)) == len(sources) and len(sources) > 1:
        return "SOURCE"
    return ""


def _partition_value(contract: EnumerationContract, partition: str) -> Any:
    if partition == "SEASON":
        return contract.season if contract.season is not None else ("date", _range_key(contract))
    if partition == "DATE_RANGE":
        return _range_key(contract)
    if partition == "SOURCE":
        return contract.source
    return None


def _range_key(contract: EnumerationContract) -> Any:
    if contract.date_range is None:
        return None
    return (str(contract.date_range.start), str(contract.date_range.end))


def _ranges_overlap(contracts: list[EnumerationContract]) -> bool:
    ranges = [contract.date_range for contract in contracts if contract.date_range is not None]
    for index, first in enumerate(ranges):
        for second in ranges[index + 1:]:
            if _overlaps(first, second):
                return True
    return False


def _overlaps(first, second) -> bool:
    from datetime import date

    first_start = first.start or date.min
    first_end = first.end or date.max
    second_start = second.start or date.min
    second_end = second.end or date.max
    return not (first_end < second_start or second_end < first_start)


def _source_partition_collision(contracts: list[EnumerationContract], partition: str) -> bool:
    seen: set[tuple] = set()
    for contract in contracts:
        key = (contract.source, _partition_value(contract, partition))
        if key in seen:
            return True
        seen.add(key)
    return False


def _same_source_same_window(contracts: list[EnumerationContract]) -> bool:
    for index, first in enumerate(contracts):
        for second in contracts[index + 1:]:
            if first.source and first.source == second.source:
                if first.season is not None and first.season == second.season:
                    return True
                first_range = _range_key(first)
                second_range = _range_key(second)
                if first_range is not None and first_range == second_range:
                    return True
    return False


def _delta_identity_ok(artifacts: list[Artifact]) -> EnumerationCompatibilityResult:
    """A DELTA must compare the same entities across its partitions."""
    entity_sets = [
        {str(e) for e in (artifact.actual_scope.entities if artifact.actual_scope else []) if e}
        for artifact in artifacts
    ]
    non_empty = [entities for entities in entity_sets if entities]
    if non_empty and all(entities == non_empty[0] for entities in non_empty) and len(non_empty) == len(artifacts):
        return EnumerationCompatibilityResult(True, "OK", "delta over identical entity set")
    # A grouped per-entity delta must carry a stable identity column in every input.
    contracts = [artifact.enumeration for artifact in artifacts if artifact.enumeration is not None]
    if len(contracts) == len(artifacts) and all(contract.identity_columns for contract in contracts):
        return EnumerationCompatibilityResult(True, "OK", "delta over a shared stable identity column")
    return EnumerationCompatibilityResult(
        False,
        "DELTA_IDENTITY_UNPROVEN",
        "a per-entity DELTA needs the same entity set or a shared stable identity column in every input",
    )


def _check_join(operation: GlobalOperation, artifacts: list[Artifact], contracts: list[EnumerationContract]) -> EnumerationCompatibilityResult:
    if not all(contract.identity_columns or (artifact.actual_scope and artifact.actual_scope.entities) for artifact, contract in zip(artifacts, contracts)):
        return EnumerationCompatibilityResult(
            False,
            "JOIN_IDENTITY_MISSING",
            "a JOIN needs a stable identity in every input",
        )
    return EnumerationCompatibilityResult(
        True,
        "OK",
        f"compatible JOIN over {len(contracts)} inputs",
        {"identity_kind": _kind(contracts[0]), "partition_by": "JOIN"},
    )
