#!/usr/bin/env python3
"""Independent development attacker for M2.12R3 (Stage A adversarial review).

This attacker is authored separately from the implementation tests. It tries to find
missing authority paths, semantic clause deletion, opaque classification swallowing,
residue waiver, incomplete conjunction handling and unsupported-dimension broadening.
It records every probe, including probes that pass. It never changes production.
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.capabilities.analytics.conformance import constraint_coverage, requested_contract
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.contracts.models import ToolRequest
from tools.evaluate_m212r3 import production_hashes
from tools.m212r3_cases import case

OUT = Path("docs/audit/m212r3")


# Independent modifier probes: (text, catalog_field_or_None). A modifier whose field
# exists must become a structured obligation; one whose field is absent must block.
RESIDUE_PROBES = (
    ("in the second inning", "inning"),
    ("from the ninth inning onward", "inning"),
    ("during extra innings", "inning"),
    ("versus left-handed pitchers", "p_throws"),
    ("against right-handed pitchers", "p_throws"),
    ("with two strikes", "strikes"),
    ("with three balls", "balls"),
    ("ballpark altitude adjustment", None),
    ("during a lunar eclipse", None),
    ("on a neutral-site game", None),
    ("while the designated hitter rule applies", None),
    ("in innings shortened by rain", None),
)

AUTHORITY_PROBES = (
    ("no_parent", dict(source="inferred"), False),
    ("dangling_parent", dict(source="inferred", parent_requirement_ids=["missing"],
                              derivation_kind="schema_field_binding", derivation_proof="x"), False),
    ("parent_not_authoritative", dict(source="inferred", parent_requirement_ids=["probe_parent"],
                                      derivation_kind="schema_field_binding", derivation_proof="x"), False),
    ("ungoverned_kind", dict(source="inferred", parent_requirement_ids=["anchor"],
                             derivation_kind="model_guess", derivation_proof="x"), False),
    ("empty_proof", dict(source="inferred", parent_requirement_ids=["anchor"],
                         derivation_kind="schema_field_binding", derivation_proof=""), False),
    ("tool_source", dict(source="tool", parent_requirement_ids=["anchor"],
                         derivation_kind="schema_field_binding", derivation_proof="x"), False),
)


def contract(inputs):
    tool = LocalAnalyticsTool(None, None)
    request = ToolRequest(need_id="attacker", capability="local_analytics", objective="probe", inputs=inputs)
    return requested_contract(tool._intent(request), inputs, [])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    before = production_hashes()
    checks = []
    for seed in range(2128700000, 2128700012):
        c = case(seed)
        # --- residue classification -------------------------------------------------
        for modifier, field in RESIDUE_PROBES:
            coverage = constraint_coverage(modifier, c["role"], c["metric"])
            if field:
                structured = field in {
                    f.get("field") for f in coverage["obligations"].get("filters", [])
                } or field in coverage["obligations"].get("group_by", [])
                # A modifier for an existing field is safe when it is structured; if the
                # first pass cannot structure it, it must be preserved as blocking
                # (temporarily unresolved/unsupported), never silently COMPLETE.
                ok = structured or coverage["state"] != "COMPLETE"
            else:
                ok = coverage["state"] != "COMPLETE" or bool(coverage["unsupported"])
            checks.append(dict(family="residue", seed=seed, probe=modifier, expected="structured" if field else "blocked",
                               ok=ok, state=coverage["state"], unsupported=coverage.get("unsupported"),
                               unresolved=coverage["unresolved"], incorrect_COMPLETE=not ok))
        # --- opaque classification --------------------------------------------------
        analysis = [r for r in c["inputs"]["analytical_requirements"] if r["kind"] == "ANALYSIS"]
        if analysis:
            text = analysis[0]["value"]["objective"] + " AND during a lunar eclipse"
            inputs = copy.deepcopy(c["inputs"])
            inputs["bound_user_intent"] = text
            observed = contract(inputs)
            ok = bool(observed.issues) and bool(observed.unresolved_obligations)
            checks.append(dict(family="opaque_classification", seed=seed, probe="unresolved_inside_analysis_objective",
                               ok=ok, issues=observed.issues, incorrect_COMPLETE=not ok))
        # --- authority laundering ----------------------------------------------------
        for name, patch, expected in AUTHORITY_PROBES:
            inputs = copy.deepcopy(c["inputs"])
            addition = dict(id=f"probe_{name}", kind="CONSTRAINT",
                            value={"filters": [dict(op="COMPARE", field="strikes", comparator="=", value=0)]},
                            statement="attacker", required=True)
            addition.update(patch)
            # The parent id must resolve to the anchor requirement for the derived cases.
            addition["parent_requirement_ids"] = [
                inputs["analytical_requirements"][0]["id"] if p == "anchor" else p
                for p in addition.get("parent_requirement_ids", [])
            ]
            if name == "parent_not_authoritative":
                addition["parent_requirement_ids"] = ["probe_parent"]
                inputs["analytical_requirements"].append(dict(
                    id="probe_parent", kind="CONSTRAINT", value={"limit": 2}, statement="planner",
                    required=True, source="planner"))
            inputs["analytical_requirements"].append(addition)
            observed = contract(inputs)
            if expected:
                ok = not observed.issues
            else:
                ok = bool(observed.issues) and addition["id"] in observed.unproven_requirements
            checks.append(dict(family="authority", seed=seed, probe=name, ok=ok,
                               incorrect_COMPLETE=(not ok)))
        # --- conjunction integrity ---------------------------------------------------
        compound = "minimum 9 PITCHES AND stand IN ('L', 'R') AND notional eligibility clause"
        coverage = constraint_coverage(compound, c["role"], c["metric"])
        ok = coverage["state"] != "COMPLETE" and bool(coverage["unresolved"] or coverage["unsupported"])
        checks.append(dict(family="conjunction", seed=seed, probe="unknown_third_clause", ok=ok,
                           state=coverage["state"], incorrect_COMPLETE=not ok))
    after = production_hashes()
    assert before == after, "production changed during adversarial review"
    summary = {}
    for check in checks:
        item = summary.setdefault(check["family"], dict(total=0, contained=0, incorrect_COMPLETE=0))
        item["total"] += 1
        item["contained"] += int(check["ok"])
        item["incorrect_COMPLETE"] += int(not check["ok"])
    payload = dict(generated_at=datetime.now(timezone.utc).isoformat(), production_hashes=before,
                   summary=summary, checks=checks)
    (OUT / "attacker.json").write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(json.dumps(dict(total=len(checks), incorrect_COMPLETE=sum(not c["ok"] for c in checks),
                          families=summary), indent=1))
    return 0 if sum(not c["ok"] for c in checks) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
