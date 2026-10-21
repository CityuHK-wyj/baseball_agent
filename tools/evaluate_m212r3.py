#!/usr/bin/env python3
"""M2.12R3 evaluator: authority, semantic coverage, deletion and resolution.

Two stages:

* ``development`` — runs the Stage A attack families over the development seeds and
  records metrics.
* ``frozen`` — generates fresh OS-entropy seeds after implementation freeze and runs
  the same families plus additional independent probes. Production is never changed.

The evaluator is read-only with respect to ``app/``. It uses the offline mirrored
source so contract/coverage attacks are executable without a live PostgreSQL server.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.conformance import (
    TEMPORARILY_UNRESOLVED,
    UNSUPPORTED,
    UNDERSTOOD,
    attest,
    compare,
    constraint_coverage,
    requested_contract,
    residue_is_semantically_relevant,
)
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.contracts.models import ToolRequest
from app.verification.judge import Judge
from tools.evaluate_m212r import FixedIR
from tools.m212r3_cases import (
    DEV_AUTHORITY,
    DEV_COVERAGE,
    DEV_DELETION,
    DEV_NATURAL,
    DEV_RESOLUTION,
    ANALYSIS_PHRASES,
    authority_mutations,
    case,
    coverage_mutations,
    deletion_mutations,
    goal,
    reference,
)
from tools.m212r3_support import offline_mirror

OUT = Path("docs/audit/m212r3")


def sha256_tree(root: Path) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*.py"))}


def production_hashes() -> dict[str, str]:
    return sha256_tree(Path("app"))


def contract(inputs):
    tool = LocalAnalyticsTool(None, None)
    request = ToolRequest(need_id="eval", capability="local_analytics", objective="probe", inputs=inputs)
    return requested_contract(tool._intent(request), inputs, [])


def executed_attestation(raw):
    query = validate_query(AnalyticalQuery.model_validate(raw))
    return attest(query, SQLCompiler().compile(query, "POSTGRES"))


class Recorder:
    def __init__(self):
        self.checks: list[dict] = []

    def add(self, family, name, seed, ok, **detail):
        self.checks.append(dict(family=family, name=name, seed=seed, ok=bool(ok),
                                incorrect_COMPLETE=bool(detail.pop("incorrect_COMPLETE", False)), **detail))

    def summary(self):
        families: dict[str, dict] = {}
        for check in self.checks:
            item = families.setdefault(check["family"], dict(total=0, contained=0, incorrect_COMPLETE=0))
            item["total"] += 1
            item["contained"] += int(check["ok"])
            item["incorrect_COMPLETE"] += int(check["incorrect_COMPLETE"])
        return dict(total=len(self.checks), incorrect_COMPLETE=sum(c["incorrect_COMPLETE"] for c in self.checks),
                    families=families)


# ------------------------------------------------------------------ families


def family_authority(seeds, rec: Recorder):
    for seed in seeds:
        c = case(seed)
        base = contract(c["inputs"])
        if base.issues:
            rec.add("authority", "base_incomplete", seed, False, issues=base.issues)
            continue
        for name, addition, authoritative, dimension in authority_mutations(c):
            inputs = copy.deepcopy(c["inputs"])
            inputs["analytical_requirements"].append(addition)
            observed = contract(inputs)
            if authoritative:
                ok = not observed.issues
                rec.add("authority", name, seed, ok, incorrect_COMPLETE=False)
            else:
                # Attack passes only if the addition is typed unproven and cannot alter truth.
                ok = bool(observed.issues) and addition["id"] in observed.unproven_requirements
                rec.add("authority", name, seed, ok, incorrect_COMPLETE=(not ok))


def family_coverage(seeds, rec: Recorder):
    for seed in seeds:
        c = case(seed)
        rng = random.Random(seed)
        clause, expectation, dimension = coverage_mutations(c, rng)
        inputs = copy.deepcopy(c["inputs"])
        inputs["bound_user_intent"] = inputs["raw_query"] + " AND " + clause
        observed = contract(inputs)
        if expectation == "supported_filter":
            ok = (not observed.issues) and observed.unresolved_obligations == ()
            rec.add("coverage_supported", f"structured::{clause}", seed, ok, incorrect_COMPLETE=(not ok))
        else:
            ok = bool(observed.issues) and bool(observed.unresolved_obligations)
            rec.add("coverage_unsupported", f"blocked::{clause}", seed, ok, incorrect_COMPLETE=(not ok))


def family_opaque_analysis(seeds, rec: Recorder):
    for seed in seeds:
        c = case(seed)
        analysis = [r for r in c["inputs"]["analytical_requirements"] if r["kind"] == "ANALYSIS"]
        if not analysis:
            continue
        text = analysis[0]["value"]["objective"] + " AND during extra innings"
        analysis[0]["value"]["objective"] = text
        analysis[0]["statement"] = text
        inputs = copy.deepcopy(c["inputs"])
        inputs["bound_user_intent"] = text
        observed = contract(inputs)
        # The restriction must become a structured inning obligation, not an
        # opaque analysis objective, and must not be silently lost.
        structured = any(
            p.get("value") and any(isinstance(f, dict) and f.get("field") == "inning"
                                   for f in (p["value"] if isinstance(p["value"], list) else [p["value"]]))
            for p in observed.obligation_provenance.get("filters", [])
        )
        ok = not observed.issues and structured
        rec.add("opaque_analysis", "inning_restriction_structured", seed, ok, incorrect_COMPLETE=(not ok))


def family_deletion(seeds, rec: Recorder, monkeypatch=None):
    import app.capabilities.analytics.conformance as module

    for seed in seeds:
        c = case(seed)
        rng = random.Random(seed)
        clauses = []
        if not c["raw"].get("qualification"):
            clauses.append(f"at least {rng.randint(7, 19)} PITCHES")
        clauses.append(rng.choice(["pitch_type IN ('CU', 'FC')", "stand IN ('L', 'R')", "balls >= 2"]))
        clauses.append("group by stand")
        text = " AND ".join(clauses)
        c["inputs"]["analytical_requirements"].append(dict(id=f"compound_{seed}", kind="CONSTRAINT",
                                                           value=text, statement="compound", required=True, source="user"))
        assert not contract(c["inputs"]).issues
        original = module.constraint_coverage
        for position in range(len(clauses)):
            class Patch:
                def __enter__(self):
                    module.constraint_coverage = self._dropped
                    return self

                def __exit__(self, *args):
                    module.constraint_coverage = original

                def _dropped(self, value, *args, **kwargs):
                    result = copy.deepcopy(original(value, *args, **kwargs))
                    if value == text:
                        result["recognized"] = [e for e in result["recognized"] if e.get("clause_index") != position]
                    return result

            with Patch():
                observed = contract(c["inputs"])
            ok = bool(observed.issues)
            rec.add("adapter_clause_deletion", f"position_{position}", seed, ok, incorrect_COMPLETE=(not ok))


def family_resolution(seeds, rec: Recorder):
    for seed in seeds:
        # Unresolved -> understood via Shared Knowledge.
        understood = constraint_coverage("versus southpaws", "BATTER", "walks")
        rec.add("resolution", "unresolved_to_understood", seed,
                understood["state"] == "COMPLETE" and understood["obligations"].get("filters"),
                incorrect_COMPLETE=False)
        # Unresolved -> unsupported.
        unsupported = constraint_coverage("in day games", "BATTER", "walks")
        rec.add("resolution", "unresolved_to_unsupported", seed,
                unsupported["state"] != "COMPLETE" and bool(unsupported["unsupported"]),
                incorrect_COMPLETE=False)
        # Still unresolved -> bounded + recorded.
        c = case(seed)
        c["inputs"]["analytical_requirements"][-1]["value"] = "unresolved eligibility rule"
        observed = contract(c["inputs"])
        attempts = [a for item in observed.unresolved_obligations for a in item.get("attempts") or []]
        keys = [(a["resolver"], a["input"]) for a in attempts]
        rec.add("resolution", "bounded_attempts_recorded", seed,
                bool(observed.issues) and bool(attempts) and len(keys) == len(set(keys)),
                incorrect_COMPLETE=False)


def family_numerical(seeds, rec: Recorder, directory: Path):
    for seed in seeds:
        c = case(seed)
        catalog, executor, file = offline_mirror(c, directory / str(seed))
        tool = LocalAnalyticsTool(executor, FixedIR(c["raw"]))
        tool.catalog = catalog
        tool.compiler = SQLCompiler(catalog)
        request = ToolRequest(need_id="num", capability="local_analytics", objective="probe", inputs=c["inputs"])
        result = tool.execute(request)
        expected = reference(c)
        actual = result.artifact.structured_data["rows"] if result.artifact else None
        correct = actual is not None and len(actual) == len(expected) and all(
            all((row.get(k) == exp.get(k)) or (isinstance(exp.get(k), float) and abs(row.get(k, 0) - exp.get(k)) < 1e-9)
                for k in exp)
            for row, exp in zip(actual, expected)
        )
        rec.add("numerical_offline", "reference_reduction", seed, correct, incorrect_COMPLETE=False)
        if result.artifact:
            projection = Judge().evaluate(goal(c), [result.artifact], [])
            # A valid request may be LIMITED by response consistency, but it must not
            # be FAILED because of an introduced coverage rejection.
            rec.add("numerical_offline", "valid_artifact_admitted", seed,
                    projection.goal_status.value != "FAILED", incorrect_COMPLETE=False)


def family_natural(rec: Recorder):
    from app.semantic.interpreter import SemanticInterpreter
    from app.semantic.goals import RequirementBuilder

    questions = list(ANALYSIS_PHRASES)
    extra = [
        "How many strikeouts did Tarik Skubal record in 2024?",
        "Rank the top five pitchers by walks during the 2023 regular season",
        "Which batters had at least 40 plate appearances against left-handed pitchers in 2024?",
        "Compare Shohei Ohtani's home runs in 2021 and 2024",
        "Average release speed for Yoshinobu Yamamoto in the 2024 postseason",
        "Rank batters by strikeouts from the seventh inning onward in 2024",
        "Nolan Arenado singles during extra innings in 2019",
        "Which pitchers threw the most pitches in the 2022 World Series?",
    ]
    for index, question in enumerate(extra):
        message = SemanticInterpreter(llm=None).interpret(question)
        built = RequirementBuilder().build(message)
        analysis = [r for r in built.requirements if r.kind.value == "ANALYSIS"]
        if not analysis:
            rec.add("natural_language", f"q{index}", question, False, incorrect_COMPLETE=False)
            continue
        objective = analysis[0].value.get("objective") if isinstance(analysis[0].value, dict) else analysis[0].statement
        coverage = constraint_coverage(objective, "BATTER", "")
        # No silent loss: every clause is either understood or explicitly blocking.
        ok = coverage["state"] in {"COMPLETE", "PARTIAL", "UNRESOLVED"} and (
            coverage["state"] == "COMPLETE" or coverage["unresolved"] or coverage["unsupported"]
        )
        rec.add("natural_language", f"q{index}", question, ok, incorrect_COMPLETE=(not ok),
                state=coverage["state"])


UNSEEN_MODIFIERS = (
    "after a pitching change", "with the bases empty", "when the count is full",
    "in the top half of the inning", "on a 3-2 count", "during a save situation",
    "in a dome stadium", "against a division rival", "with runners in scoring position",
    "during a replay review",
)


def unseen_probes(rec: Recorder, seeds):
    for seed in seeds:
        c = case(seed)
        for text in UNSEEN_MODIFIERS:
            coverage = constraint_coverage(text, c["role"], c["metric"])
            # Unseen factual modifiers must never silently vanish: either understood and
            # structured, or explicitly blocking (temporarily unresolved/unsupported).
            ok = coverage["state"] != "COMPLETE" or bool(coverage["obligations"])
            rec.add("unseen_residue", "blocked_or_structured", seed, ok,
                    incorrect_COMPLETE=(not ok), probe=text, state=coverage["state"])


# ------------------------------------------------------------------ driver


def run(stage: str, seeds: dict, rec: Recorder, directory: Path):
    family_authority(seeds["authority"], rec)
    family_coverage(seeds["coverage"], rec)
    family_opaque_analysis(seeds["coverage"], rec)
    family_deletion(seeds["deletion"], rec)
    family_resolution(seeds["resolution"], rec)
    family_numerical(seeds["numerical"], rec, directory)
    family_natural(rec)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["development", "frozen"])
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=f"m212r3-{args.stage}-"))
    rec = Recorder()
    if args.stage == "development":
        seeds = dict(
            authority=list(DEV_AUTHORITY)[:16],
            coverage=list(DEV_COVERAGE)[:16],
            deletion=list(DEV_DELETION)[:16],
            resolution=list(DEV_RESOLUTION)[:16],
            numerical=list(DEV_AUTHORITY)[:16],
        )
        before = production_hashes()
        run("development", seeds, rec, directory)
        after = production_hashes()
        assert before == after, "production changed during development evaluation"
    else:
        # Fresh OS-entropy seeds are generated only after freeze.
        import secrets as _secrets
        manifest = json.loads((OUT / "IMPLEMENTATION_FROZEN.json").read_text())
        assert manifest["production_hashes"] == production_hashes(), "production differs from the freeze manifest"
        seeds = dict(
            authority=[2128600000 + _secrets.randbelow(1000) for _ in range(16)],
            coverage=[2128610000 + _secrets.randbelow(1000) for _ in range(16)],
            deletion=[2128620000 + _secrets.randbelow(1000) for _ in range(16)],
            resolution=[2128630000 + _secrets.randbelow(1000) for _ in range(16)],
            numerical=[2128640000 + _secrets.randbelow(1000) for _ in range(16)],
        )
        before = production_hashes()
        run("frozen", seeds, rec, directory)
        unseen_probes(rec, seeds["coverage"])
        after = production_hashes()
        assert before == after, "production changed during frozen evaluation"
    payload = dict(
        stage=args.stage,
        generated_at=datetime.now(timezone.utc).isoformat(),
        production_hashes=before,
        seeds=seeds,
        summary=rec.summary(),
        checks=rec.checks,
    )
    out = Path(args.out) if args.out else OUT / (f"{args.stage}.json")
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(json.dumps(rec.summary(), indent=1))
    return 0 if rec.summary()["incorrect_COMPLETE"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
