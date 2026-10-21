#!/usr/bin/env python3
"""M2.12R3 independent PostgreSQL + Parquet + engine-parity reference runner.

Uses the generated rows as an independent Python reduction, a read-only mirrored
PostgreSQL fixture, an identical historical-format Parquet fixture and the production
compiler/SQL path. Production Artifacts are never used as ground truth.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.executor import PostgresExecutor
from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
from app.capabilities.analytics.tool import LocalAnalyticsTool
from app.config import get_config
from app.contracts.models import ToolRequest
from tools.evaluate_m212r import FixedIR, mirror
from tools.evaluate_m212r3 import production_hashes
from tools.m212r3_cases import case, reference

OUT = Path("docs/audit/m212r3")


def equal_rows(left, right):
    if left is None or right is None or len(left) != len(right):
        return False
    keys = set(right[0]) if right else set()
    for a, b in zip(left, right):
        for key in keys:
            av, bv = a.get(key), b.get(key)
            if isinstance(bv, float):
                if av is None or abs(float(av) - bv) > 1e-9:
                    return False
            elif av != bv:
                return False
    return True


def main():
    frozen = json.loads((OUT / "frozen.json").read_text())
    seeds = frozen["seeds"]["numerical"]
    pg = PostgresExecutor(get_config().postgres)
    directory = Path(tempfile.mkdtemp(prefix="m212r3-reference-"))
    checks = []
    for seed in seeds:
        c = case(seed)
        catalog, executor, file = mirror(c, directory, pg)
        compiler = SQLCompiler(catalog)
        query = validate_query(AnalyticalQuery.model_validate(c["raw"]))
        expected = reference(c)
        observed = {}
        for source in ("POSTGRES", "PARQUET"):
            compiled = compiler.compile(query, source)
            result = executor.run(compiled.sql, source)
            observed[source] = result.rows
            checks.append(dict(seed=seed, channel="reference_" + source,
                               ok=not result.error and equal_rows(result.rows, expected),
                               error=result.error, expected=expected, actual=result.rows))
        checks.append(dict(seed=seed, channel="engine_parity",
                           ok=equal_rows(observed.get("POSTGRES"), observed.get("PARQUET"))))
        tool = LocalAnalyticsTool(executor, FixedIR(c["raw"]))
        tool.catalog = catalog
        tool.compiler = compiler
        request = ToolRequest(need_id="ref", capability="local_analytics", objective="probe", inputs=c["inputs"])
        tool_result = tool.execute(request)
        rows = tool_result.artifact.structured_data["rows"] if tool_result.artifact else None
        checks.append(dict(seed=seed, channel="tool_execution",
                           ok=rows is not None and equal_rows(rows, expected), error=tool_result.outcome.message))
    summary = {}
    for check in checks:
        item = summary.setdefault(check["channel"], dict(total=0, passed=0))
        item["total"] += 1
        item["passed"] += int(check["ok"])
    payload = dict(generated_at=datetime.now(timezone.utc).isoformat(),
                   production_hashes=production_hashes(), summary=summary, checks=checks)
    (OUT / "reference.json").write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(json.dumps(summary, indent=1))
    return 0 if all(item["total"] == item["passed"] for item in summary.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
