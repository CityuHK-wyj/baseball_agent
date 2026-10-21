#!/usr/bin/env python3
"""Live LLM synthesis on generated acquired resources; no web fetches or known sites."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import LLMConfig
from app.contracts.models import Representation, RepresentationKind, RetrievedDocument
from app.llm.client import LLMClient
from app.web.agentic_resolution import AgenticContentResolver, ResolutionReason


def run(seeds):
    llm = LLMClient(LLMConfig())
    rows = []
    for seed in seeds:
        rng = random.Random(seed)
        subject = f"Entity{rng.randrange(10000, 99999)}"
        fact = f"{subject} recorded metric{rng.randrange(10000, 99999)} {rng.randrange(10, 90)}."
        uri = f"https://publisher{seed}.example.org/story"
        mode = "URI" if seed % 2 else "TEXT"
        raw = (f'<html><script>{{"noise":"wrong", "next":"{uri}"}}</script></html>' if mode == "URI"
               else f'<html><script>{{"state":{{"article":{{"body":"{fact}"}}}},"noise":"wrong"}}</script></html>')
        parent = Representation(kind=RepresentationKind.HTML, content=raw,
                                content_hash=hashlib.sha256(raw.encode()).hexdigest())
        document = RetrievedDocument(url=f"https://source{seed}.example.org/start", source_uri=f"https://source{seed}.example.org/start",
                                     content_hash=parent.content_hash, representations=[parent])
        resolver = AgenticContentResolver(llm)
        result = resolver.resolve(document, objective=f"Find {subject}'s recorded metric or the publisher URL",
                                  reason=ResolutionReason.INTERMEDIATE_RESOURCE if mode == "URI" else ResolutionReason.TEXT_DERIVATION_INSUFFICIENT)
        correct = result.uri == uri if mode == "URI" else bool(result.representation and result.representation.content == fact)
        rows.append({"seed": seed, "mode": mode, "status": result.status, "correct": correct,
                     "programs": resolver.program_count, "trace": result.trace})
    return {"seeds": seeds, "cases": rows, "correct": sum(row["correct"] for row in rows),
            "programs": sum(row["programs"] for row in rows), "llm_calls": len(llm.usage_records),
            "usage": llm.usage_records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=11000)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = run(list(range(args.start, args.start + args.count)))
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"correct": report["correct"], "programs": report["programs"], "llm_calls": report["llm_calls"]}))


if __name__ == "__main__":
    main()
