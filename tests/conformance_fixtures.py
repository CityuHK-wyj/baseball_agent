"""Explicit complete representation-review proposals for deterministic mechanism tests.

These are scripted reviewer decisions, not production semantic inference. Tests of malformed
or unavailable reviews must call the real parser directly instead of this fixture.
"""
from app.response.composition_contract import draft_identity, parse_conformance_assessment


def complete_review_data(data, result=None, package=None):
    if not data or not data.get("statements"):
        return data
    entries = []
    for proposed in data["statements"]:
        entry = dict(index=0, epistemic="ANALYSIS", approved_claim_backed=False,
                     material_conclusion=False, framed_as_interpretation=False,
                     citations_on_inference=False, limitation_retained=True,
                     origin_claim=False, origin_backed=False, unsupported_addition=False,
                     claim_markers=[], qualified_markers=[], limitation_ids=[],
                     unresolved_requirement_ids=[])
        entry.update(proposed)
        if result is not None and 0 <= entry["index"] < len(result.statements):
            statement = result.statements[entry["index"]]
            if "claim_markers" not in proposed and entry["approved_claim_backed"]:
                entry["claim_markers"] = [m for m in statement.citations if m.startswith("S")]
        if entry["epistemic"] == "QUALIFIED" and "qualified_markers" not in proposed and package:
            if len(package.qualified_evidence) == 1:
                entry["qualified_markers"] = ["Q1"]
        entries.append(entry)
    return {"statements": entries}


def bound_review(data, package, result):
    return parse_conformance_assessment(complete_review_data(data, result, package),
                                        package_id=package.package_id,
                                        draft_id=draft_identity(result))
