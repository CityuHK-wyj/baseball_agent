#!/usr/bin/env python3
"""Paired generated-resource evaluation of deterministic and agentic content resolution.

The synthesizer here is a scripted stand-in, not a live LLM quality measurement. The
unseen seeds are fixed only at evaluation time and every result is replayable.
"""

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

from app.capabilities.web_search import FetchedPage, SearchHit
from app.capabilities.web_research import WebResearchTool
from app.config import WebConfig
from app.contracts.models import AnswerProposition, EvidenceSpan, RetrievedDocument
from app.verification.lineage import span_resolves
from app.web.agentic_resolution import AgenticContentResolver


class ScriptedSynthesizer:
    configured = True
    def __init__(self):
        self.calls = 0
    def complete_json(self, *, user, **kwargs):
        self.calls += 1
        request = json.loads(user)
        if "payload" not in request:
            reps = request["representations"]
            selected = next((rep for rep in reps if rep["kind"] in {"HTML", "DOM"}), reps[0])
            data = {"representation_id": selected["representation_id"]}
        elif "nextHop" in request["payload"]:
            data = {"program": 'record = json_parse(find_all(text, "<script>(.*?)</script>")[0])\nresult = {"kind":"URI","value":get(record,"nextHop")}' }
        else:
            data = {"program": 'record = json_parse(find_all(text, "<script>(.*?)</script>")[0])\nresult = {"kind":"TEXT","value":get(get(record,"payload"),"body")}' }
        return type("Reply", (), {"data": data})()


def case(seed):
    rng = random.Random(seed)
    subject = f"Entity{rng.randrange(10000, 99999)}"
    metric = f"metric{rng.randrange(10000, 99999)}"
    value = rng.randrange(10, 90)
    sentence = f"{subject} recorded {metric} {value} in the generated source."
    uri = f"https://source{seed}.example.org/start"
    target = f"https://publisher{seed}.example.org/article"
    mode = "URI" if seed % 2 else "TEXT"
    if mode == "URI":
        raw = f'<html><script>{{"decoy":"https://other.example/decoy","nextHop":"{target}"}}</script></html>'.encode()
        first = FetchedPage(uri, uri, subject, "", "2026-10-02T00:00:00Z", False,
                            error_code="NO_BODY", content_hash=hashlib.sha256(raw).hexdigest(),
                            raw_content=raw, content_type="text/html")
        body = sentence + " Additional context about the same recorded result."
        second = FetchedPage(target, target, subject, body, "2026-10-02T00:00:00Z", True,
                             content_hash=hashlib.sha256(body.encode()).hexdigest(),
                             raw_content=body.encode(), content_type="text/plain")
    else:
        record = {"payload": {"body": sentence, "unknown": rng.randrange(10000)}, "decoy": "No matching metric here"}
        raw = f"<html><script>{json.dumps(record)}</script><p>{subject} navigation only.</p></html>".encode()
        first = FetchedPage(uri, uri, subject, f"{subject} navigation only.", "2026-10-02T00:00:00Z", True,
                            content_hash=hashlib.sha256(raw).hexdigest(), raw_content=raw,
                            content_type="text/html")
        second = None
    return subject, metric, sentence, first, second


def run(seeds):
    counts = {mode: {"resources": 0, "triggers": 0, "programs": 0, "executions_succeeded": 0,
                     "derived": 0, "resolved_uri": 0, "new_resources": 0,
                     "replay_ok": 0, "replay_total": 0, "verified_support": 0,
                     "false_support": 0, "admitted_support": 0, "mandatory_covered": 0,
                     "cycle_or_limit": 0, "llm_calls": 0}
              for mode in ("deterministic", "agentic")}
    for seed in seeds:
        subject, metric, sentence, first, second = case(seed)
        true = AnswerProposition(id=f"true_{seed}", statement=f"{subject} recorded {metric}",
                                 subject=subject, required_information=metric, mandatory=True)
        decoy = AnswerProposition(id=f"decoy_{seed}", statement=f"{subject} recorded absent{seed}",
                                  subject=subject, required_information=f"absent{seed}", mandatory=True)
        for mode in counts:
            row = counts[mode]
            row["resources"] += 1
            tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
            synth = ScriptedSynthesizer()
            if mode == "agentic":
                tool._content_resolver = AgenticContentResolver(synth)
            import app.capabilities.web_research as wr
            original = wr.fetch_with_fallback
            fetches = []
            def fetch(uri, *args, **kwargs):
                fetches.append(uri)
                return first if uri == first.url else second
            wr.fetch_with_fallback = fetch
            try:
                findings, _, _, _, documents = tool._retrieve(
                    [SearchHit(subject, first.url, engine="publisher_feeds")],
                    f"{subject} {metric}", [], {subject.lower()}, 2, [true, decoy],
                )
                evidence = tool._verify([true, decoy], findings, documents)
            finally:
                wr.fetch_with_fallback = original
            row["new_resources"] += max(0, len(fetches) - 1)
            row["llm_calls"] += synth.calls
            row["programs"] += tool._content_resolver.program_count
            for payload in documents:
                document = RetrievedDocument.model_validate(payload)
                spans = {span["id"]: span for finding in findings for span in finding.get("evidence_spans", [])}
                for trace in document.resolution_traces:
                    row["triggers"] += 1
                    row["executions_succeeded"] += int(trace.get("status") in {"DERIVED_REPRESENTATION", "RESOLVED_URI"})
                    row["derived"] += int(trace.get("status") == "DERIVED_REPRESENTATION")
                    row["resolved_uri"] += int(trace.get("status") == "RESOLVED_URI")
                    row["cycle_or_limit"] += int(trace.get("status") in {"CYCLE_DETECTED", "LIMIT_REACHED"})
                for decision in evidence:
                    if decision.document_id != document.id:
                        continue
                    span_data = spans.get(decision.span_id)
                    if span_data:
                        row["replay_total"] += 1
                        row["replay_ok"] += int(span_resolves(EvidenceSpan.model_validate(span_data), document))
            supported = [item for item in evidence if item.status.value == "SUPPORTED"]
            row["admitted_support"] += len(supported)
            row["verified_support"] += sum(item.proposition_id == true.id for item in supported)
            row["false_support"] += sum(item.proposition_id == decoy.id for item in supported)
            row["mandatory_covered"] += int(any(item.proposition_id == true.id for item in supported))
    report = {"seeds": seeds, "scripted_synthesizer": True, "paired": counts}
    for row in counts.values():
        row["evidence_precision"] = row["verified_support"] / row["admitted_support"] if row["admitted_support"] else None
        row["false_support_rate"] = row["false_support"] / row["admitted_support"] if row["admitted_support"] else None
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=9000)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = run(list(range(args.start, args.start + args.count)))
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
