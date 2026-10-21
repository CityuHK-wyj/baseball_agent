"""Response composer: grounded, cited answers in the user's language (M2.6 / WSA-06).

The composer receives an :class:`~app.response.answer_contract.AnswerPackage`: the Judge-approved
facts (with citation markers), the useful-but-unapproved qualified evidence (each with its
structural limitation) and what remains unresolved.

**Inference belongs only here.** The composer is the only layer allowed to synthesise, explain,
interpret or judge. It must distinguish, in the answer it produces, between:

* sourced fact (an approved Claim),
* qualified/limited information (QualifiedEvidence, limitation retained),
* its own analysis/inference/hypothesis/judgment.

It proposes that distinction through a response-local typed contract (``ComposedStatement``),
and a bounded LLM response-conformance assessment classifies the representation. Deterministic
code then validates the typed state and enforces the response policy (``composition_conformance``).
None of this becomes a Claim, Requirement, EvidenceFinding or Shared Knowledge.
"""

from __future__ import annotations

import json
from app.verification.evidence_origin import origin_presentation

from app.contracts.models import AnswerProjection, EpistemicStatus, Goal, GoalStatus, SemanticInterpretation
from app.response.accepted_results import render_accepted_results, render_qualified_evidence
from app.response.answer_contract import AnswerPackage, build_answer_package
from app.response.composition_contract import (
    ComposedStatement,
    ComposerDraft,
    StatementKind,
    parse_composer_draft,
)
from app.verification.composition_conformance import (
    conform_composition,
    request_conformance_assessment,
)

SYSTEM_PROMPT = """You are the response composer of a Baseball Agent.

You receive APPROVED FACTS (the only statements you may assert), read-only CONTEXT, the
user's question and the coverage of each mandatory requirement. Write the final answer in the
SAME LANGUAGE as the user's question.

AUTHORITY
- Use ONLY the APPROVED FACTS for factual content.
- CONTEXT is for understanding only (pronouns, what an event refers to, chronology). NEVER
  introduce a fact, cause, number, date, quote, comparison or attribution that is not in an
  APPROVED FACT, even if it appears in the context.
- Preserve every approved number, entity, season/population scope and polarity exactly.
  A negative approved fact must stay negative.
- QUALIFIED EVIDENCE is relevant material the Judge could NOT approve. You may mention it
  for the user's benefit ONLY together with its stated limitation (unverified / speculative /
  stale / lower authority). Never present a qualified item as an established fact, never use
  it to conclude a comparison, and never drop its limitation.

STYLE
- Answer the question directly and naturally; synthesise and paraphrase rather than dumping.
- You may merge compatible approved facts into one sentence or paragraph.
- Cite each factual statement inline as [S1], [S2] matching the approved-fact markers.
- Consolidate citations when several approved facts share a source; keep each citation next
  to the statement it supports.
- Do not print source URLs, document/evidence/finding IDs, provider names, raw queues, scores,
  or a "Verified results" heading.

COVERAGE
- A requirement is either SATISFIED (state its result) or unresolved. For every unresolved
  mandatory requirement, clearly say the result was not established; never assert it.
- If some requirements are satisfied and others are not, give the supported partial answer
  first, then the unresolved parts. Do not suppress approved information.
- BACKGROUND, if allowed, must be clearly labelled as unverified and must not contain numbers,
  dates, injuries, transactions or other contested specifics.
- If a claim includes a markdown table, you may reformat it but keep every value.
- Be concise. No chain-of-thought, no speculation about internal components.
"""

SYSTEM_PROMPT_TYPED = """You are the response composer of a Baseball Agent.

You turn GOVERNED INPUTS into a final answer. Upstream layers only govern evidence; YOU are the
only layer allowed to interpret, synthesise, explain, predict or judge. Return JSON:
{"statements":[{"kind":"FACTUAL|QUALIFIED|ANALYSIS|LIMITATION","text":"...",
  "citations":["S1"],"supports":[]}]}

STATEMENT KINDS
- FACTUAL: a sourced assertion stated directly by an APPROVED FACT. Include the fact's inline
  marker (e.g. [S1]) in the text and list it in citations. Invent nothing.
- QUALIFIED: information from QUALIFIED EVIDENCE. Keep the stated limitation next to the claim.
  Never present it as an established fact and never use it to conclude a comparison.
- ANALYSIS: your own synthesis, interpretation, causal hypothesis, prediction or judgment.
  Keep analysis and sourced fact clearly distinct. Do not attach [S1]/[Q1] markers to analysis;
  put the premises it draws on in "citations" only (they are receipts, not printed support).
  When you make a material conclusion not directly stated by an approved fact (causality,
  explanation, prediction, ranking/judgment, a latent relationship), frame it as your
  interpretation (e.g. "Taken together, this suggests...", "My reading is...").
- LIMITATION: what remains unresolved or missing.

RULES
- Preserve every approved number, entity, season/population scope and polarity exactly.
- Never invent a number, date, quote, comparison or attribution. Never turn correlation into
  causation unless an approved fact itself states the causal relation.
- Do not collapse a LIMITED answer into a generic failure: communicate the supported facts, the
  qualified items with limitations, and the unresolved parts.
- EVIDENCE ORIGINS are proposition-local governance metadata, not approval of facts. Explain
  shared bases or unknown dependency where useful. Never invent separate sourcing, count outlets
  as confirmation, resolve conflicts by majority, or strengthen assertion/temporal/authority state.
  Typed origin states govern; basis-description prose cannot override those states.
- BACKGROUND, if allowed, is clearly-labelled unverified material.
- Order statements as the final answer should read. Write in the SAME LANGUAGE as the user.
- No chain-of-thought, no source URLs, IDs or internal details.
"""

_STATUS_LABEL_ZH = {
    GoalStatus.COMPLETE: "已获得可支持的证据",
    GoalStatus.LIMITED: "部分证据",
    GoalStatus.FAILED: "证据不足",
    GoalStatus.WAITING_FOR_USER: "需要澄清",
}

_MARKER = {
    EpistemicStatus.SUPPORTED: "S",
    EpistemicStatus.INFERRED: "I",
    EpistemicStatus.BACKGROUND: "B",
    EpistemicStatus.HYPOTHESIS: "H",
    EpistemicStatus.UNKNOWN: "U",
}


class ResponseComposer:
    def __init__(self, llm):
        self.llm = llm

    @property
    def available(self) -> bool:
        return bool(self.llm and self.llm.configured)

    def compose(
        self,
        goal: Goal,
        semantic: SemanticInterpretation,
        projection: AnswerProjection,
        *,
        artifacts: list | None = None,
        trace=None,
        package: AnswerPackage | None = None,
        orchestrator=None,
    ) -> str:
        package = package or build_answer_package(goal, projection, artifacts, language=semantic.language)
        if orchestrator is not None:
            generate = None
            if self.available and package.clarification is None and (
                package.facts or package.qualified_evidence or package.background_ok
            ):
                generate = lambda supplied, feedback: self.compose_draft(goal, semantic, supplied, feedback=feedback)
            return orchestrator.compose(
                package, generate,
                lambda supplied, draft: request_conformance_assessment(self.llm, supplied, draft, language=semantic.language),
                language=semantic.language,
            )
        # Compatibility facade for direct callers; production lifecycle belongs to Engine's
        # explicit Orchestrator. All paths retain the fail-closed representation boundary.
        if projection.clarification is not None:
            return self._empty(goal, semantic, projection)
        if not projection.claims and not projection.background_ok and not projection.qualified_evidence:
            return self._empty(goal, semantic, projection)
        if not self.available:
            return self._fallback(goal, semantic, projection)

        user = self._payload(goal, semantic, projection, package)
        draft, model, latency_ms = self._request_composition(user)
        if draft is None:
            if trace:
                trace.record("response", "composer produced no usable output; using bounded rendering")
            return self._fallback(goal, semantic, projection)

        assessment = request_conformance_assessment(self.llm, package, draft, language=semantic.language)
        if assessment is None and any(statement.kind == StatementKind.UNTYPED for statement in draft.statements):
            # Free prose with no open-world representation assessment: do not trust it for
            # inference. Fall back to the bounded deterministic rendering.
            if trace:
                trace.record("response", "no conformance assessment for untyped answer; bounded rendering")
            return self._fallback(goal, semantic, projection)

        conformance = conform_composition(draft, package, assessment, language=semantic.language)
        text = conformance.text or self._fallback(goal, semantic, projection)
        if trace:
            trace.record(
                "response", "composed",
                model=model, latency_ms=latency_ms, source=draft.source,
                conformance=[violation.kind for violation in conformance.violations],
            )
        return text

    # ------------------------------------------------------------------ model calls

    def compose_draft(self, goal, semantic, package: AnswerPackage, *, feedback=()) -> ComposerDraft | None:
        user = self._payload(goal, semantic, None, package)
        if feedback:
            user += "\nREGENERATION FEEDBACK (representation violations only):\n" + json.dumps(list(feedback)[:16])
            user += "\nRegenerate against the SAME governed package. Feedback is not new evidence."
        draft, _, _ = self._request_composition(user)
        return draft

    def _request_composition(self, user: str) -> tuple[ComposerDraft | None, str, int]:
        complete_json = getattr(self.llm, "complete_json", None)
        if callable(complete_json):
            try:
                response = complete_json(
                    role="response", system=SYSTEM_PROMPT_TYPED, user=user, max_tokens=1400,
                )
                parsed = parse_composer_draft(getattr(response, "data", None))
                return parsed, getattr(response, "model", ""), int(getattr(response, "latency_ms", 0) or 0)
            except Exception:  # noqa: BLE001 - one provider attempt per composition attempt
                return None, "", 0
        try:
            text, latency_ms, model = self.llm.complete_text(
                role="response", system=SYSTEM_PROMPT, user=user, max_tokens=900,
            )
        except Exception:  # noqa: BLE001
            return None, "", 0
        text = (text or "").strip()
        if not text:
            return None, model, latency_ms
        return (
            ComposerDraft(
                answer_text=text,
                statements=[ComposedStatement(kind=StatementKind.UNTYPED, text=text)],
                source="text",
            ),
            model,
            latency_ms,
        )

    def _payload(self, goal: Goal, semantic: SemanticInterpretation, projection: AnswerProjection, package: AnswerPackage) -> str:
        facts_text: list[str] = []
        for fact in package.facts:
            facts_text.append(f"[{fact.marker}] {fact.text}")
            for context in fact.context[:1]:
                if context.context_text:
                    facts_text.append(f"    CONTEXT (read-only, not authority): {context.context_text}")
        requirement_lines = [
            f"- [{entry.coverage}] {entry.statement}" for entry in package.requirements if entry.required
        ]
        background_line = (
            f"BACKGROUND: ALLOWED for topic '{goal.statement}'. If no approved fact answers it, "
            "you may add one clearly-labelled unverified background statement."
            if package.background_ok
            else "BACKGROUND: NOT ALLOWED. Do not use outside knowledge."
        )
        qualified_lines = [
            f"[Q{index}] {item.span_text}"
            + (f" (source: {item.source_url or item.source_title})" if (item.source_url or item.source_title) else "")
            + f" — not established: {item.limitation}"
            for index, item in enumerate(package.qualified_evidence, 1)
        ]
        return (
            f"USER QUESTION: {goal.raw_query}\n"
            f"UNDERSTANDING: {semantic.understanding}\n"
            f"LANGUAGE: {semantic.language}\n"
            f"STATUS: {package.goal_status}\n"
            f"LIMITATIONS: {'; '.join(package.limitations) or 'none'}\n"
            f"{background_line}\n"
            + "MANDATORY REQUIREMENTS:\n"
            + ("\n".join(requirement_lines) if requirement_lines else "(none)")
            + "\nREQUESTED EVIDENCE ROLES (intent only, not evidence or approval):\n"
            + json.dumps([{'requirement_id': r.id, 'evidence_intent': r.evidence_intent.model_dump(mode='json')}
                          for r in package.governed_requirements if r.evidence_intent is not None], ensure_ascii=False)
            + "\nAPPROVED FACTS (the only sourced assertions):\n"
            + ("\n".join(facts_text) if facts_text else "(none)")
            + "\nQUALIFIED EVIDENCE (NOT approved; keep each item's limitation):\n"
            + ("\n".join(qualified_lines) if qualified_lines else "(none)")
            + "\nEVIDENCE ORIGINS (not factual satisfaction or confidence):\n"
            + json.dumps([o.model_dump(mode="json", exclude={"llm_proposal", "deterministic_signals", "validation_notes"})
                          for o in package.evidence_origins], ensure_ascii=False)
        )

    # ------------------------------------------------------------------ helpers

    def _empty(self, goal: Goal, semantic: SemanticInterpretation, projection: AnswerProjection) -> str:
        if projection.clarification is not None:
            options = " / ".join(projection.clarification.options)
            return f"{projection.clarification.question}" + (f"\n可选：{options}" if options else "")
        if semantic.language.startswith("zh"):
            return "抱歉，我没有获得足以回答这个问题的可靠证据。" + (
                f"\n未覆盖：" + "；".join(projection.limitations) if projection.limitations else ""
            )
        return "I could not obtain reliable evidence to answer this question." + (
            f"\nGaps: " + "; ".join(projection.limitations) if projection.limitations else ""
        )

    def _fallback(self, goal: Goal, semantic: SemanticInterpretation, projection: AnswerProjection) -> str:
        chinese = semantic.language.startswith("zh")
        if projection.clarification is not None:
            return self._empty(goal, semantic, projection)
        lines: list[str] = []
        approved = render_accepted_results(projection)
        if approved:
            lines.append(approved)
        elif projection.claims:
            header = "结论：" if chinese else "Result:"
            lines.append(header)
            for index, claim in enumerate(projection.claims, start=1):
                marker = _MARKER.get(claim.epistemic, "S")
                lines.append(f"[{marker}{index}] {claim.text}")
        qualified = render_qualified_evidence(projection)
        if qualified:
            lines.append("")
            lines.append("已找到但未能确认：" if chinese else "Found but not fully verified:")
            lines.append(qualified)
        if projection.limitations:
            lines.append("")
            lines.append("未能确认：" if chinese else "Not established:")
            for limitation in projection.limitations:
                lines.append(f"- {limitation}")
        if projection.background_ok and not projection.claims:
            lines.append("")
            lines.append(
                "以下为模型背景知识（未经核实，仅供参考）：" if chinese
                else "General background knowledge (unverified, for context only):"
            )
            lines.append(projection.background_topic)
        if projection.goal_status == GoalStatus.FAILED:
            lines.append("未能获得可支持的结论。" if chinese else "No supported conclusion could be produced.")
        origins = origin_presentation(projection.evidence_origins, language=semantic.language)
        if origins:
            lines.append(origins)
        return "\n".join(lines) if lines else self._empty(goal, semantic, projection)
