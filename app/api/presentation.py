"""One-way, Judge-gated projection of internal results into inert UI data."""
from __future__ import annotations

from app.api.contracts import (AnswerIssue, AnswerView, ClarificationOption, ClarificationRequest,
                               ColumnSpec, PresentationArtifact, RequirementView, TablePayload)
from app.response.answer_contract import build_answer_package


def clarification_view(clarification, conversation_id: str, run_id: str) -> ClarificationRequest:
    return ClarificationRequest(
        id=f"clar:{clarification.goal_id}", conversation_id=conversation_id, run_id=run_id,
        goal_id=clarification.goal_id, question=clarification.question,
        reason="The backend needs your input to continue.",
        blocking_requirement_ids=[clarification.requirement_id] if clarification.requirement_id else [],
        options=[ClarificationOption(option_id=f"choice:{i}", label=label)
                 for i, label in enumerate(clarification.options)],
    )


def present(result) -> AnswerView:
    projection = result.projection
    package = build_answer_package(result.goal, projection, result.artifacts, language=result.semantic.language)
    # A supported Artifact ID alone is insufficient: only an actual Judge-admitted Claim
    # referencing this exact Artifact can authorize display of its raw tabular rows.
    claimed = {ref.ref_id for claim in projection.claims for ref in claim.support_refs
               if ref.ref_type.value == "ARTIFACT"}
    reporting_proofs = {assessment.artifact_id: assessment.reporting_proofs
                        for assessment in projection.artifact_assessments if assessment.reporting_proofs}
    subject_proofs = {assessment.artifact_id: assessment.subject_proofs
                      for assessment in projection.artifact_assessments if assessment.subject_proofs}
    artifacts = []
    for artifact in result.artifacts:
        if artifact.id not in claimed or artifact.status == "REJECTED" or artifact.kind != "DATABASE_RESULT":
            continue
        data = artifact.structured_data
        rows = data.get("rows") or []
        if not isinstance(rows, list) or not rows:
            continue
        refs = [ref for claim in projection.claims for ref in claim.support_refs
                if ref.ref_type.value == 'ARTIFACT' and ref.ref_id == artifact.id]
        subject_only = bool(refs) and all(ref.source == 'subject_cells' for ref in refs)
        if subject_only:
            claimed_slots = {pid for claim in projection.claims for pid in claim.proposition_ids
                             if any(ref.ref_id == artifact.id and ref.source == 'subject_cells'
                                    for ref in claim.support_refs)}
            approved = [p for p in subject_proofs.get(artifact.id, []) if p['proposition_id'] in claimed_slots]
            # Row/column navigation is Judge-owned. A supported A row must not
            # expose raw B rows or unapproved auxiliary values from the same table.
            selected = {}
            for proof in approved:
                for index in proof['row_indices']:
                    if not 0 <= index < len(rows):
                        continue
                    target = selected.setdefault(index, {})
                    for name in proof['fields']:
                        target[name] = rows[index].get(name)
                    if proof['identity_column']:
                        target[proof['identity_column']] = rows[index].get(proof['identity_column'])
                    target['subject'] = proof['subject']
            rows = [selected[index] for index in sorted(selected)]
            if not rows:
                continue
        cell_only = bool(refs) and all(ref.source == 'reporting_cell' for ref in refs)
        proofs = reporting_proofs.get(artifact.id, [])
        if cell_only and not proofs:
            continue
        approved_fields = {proof['cell']['field'] for proof in proofs}
        names = (sorted(approved_fields) if cell_only else [] if subject_only
                 else list(data.get("output_columns") or []))
        for row in rows:
            if not isinstance(row, dict):
                continue
            for name in row:
                if name not in names and not cell_only:
                    names.append(name)
        # No execution receipts, IR, SQL or arbitrary nested payloads on the public wire.
        safe_rows = [{name: value if type(value) in (str, int, float, bool, type(None)) else str(value)
                      for name, value in row.items() if name in names}
                     for row in rows if isinstance(row, dict)][:500]
        schema = {c.get("name"): str(c.get("type", "")) for c in data.get("schema", []) if isinstance(c, dict)}
        columns = []
        for name in names:
            kind = "number" if any(token in schema.get(name, "").upper() for token in
                                   ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC")) else "string"
            if kind == "string" and any(type(row.get(name)) in (int, float) for row in safe_rows):
                kind = "number"
            columns.append(ColumnSpec(id=name, label=name.replace("_", " "), kind=kind))
        artifacts.append(PresentationArtifact(
            id=artifact.id, type="table", title=("Verified scalar observations" if cell_only
                                                 else data.get("intent_summary") or "Analytical result"),
            description=(f"Judge-approved local analysis; showing first 500 of {len(rows)} rows"
                         if len(rows) > 500 else "Judge-approved local analysis"), status="partial" if artifact.status == "PARTIAL" else "accepted",
            evidence_refs=[artifact.id, str(data.get("source") or "")],
            scope={"source": artifact.actual_scope.source, "season": artifact.actual_scope.season,
                   "population": artifact.actual_scope.game_population} if artifact.actual_scope else {},
            calculation=(sorted({p['requested']['measure'] for p in proofs}) if cell_only else
                         [str(key) for key in (data.get("measure_keys") or []) if isinstance(key, str)]),
            payload=TablePayload(columns=columns, rows=safe_rows, total_rows=len(rows)),

        ))
    # Only operational outcomes from the actual backend, not browser heuristics. Do not
    # publish arbitrary provider exception text, SQL or ToolRequest internals.
    issue_types = {
        "UNSUPPORTED_MEASURE": ("unsupported", "Requested measure is not available from this source."),
        "SOURCE_UNAVAILABLE": ("provider", "The analytical source is temporarily unavailable."),
        "UNRESOLVED_REQUIRED_INPUT": ("semantic", "A required input was not resolved."),
        "IDENTITY_AMBIGUOUS": ("semantic", "Identity needs clarification."),
        "INVALID_IR": ("validation", "Analytical request could not be validated."),
    }
    seen_issues = set()
    issues = []
    for outcome in getattr(result, "outcomes", []):
        if outcome.status == "SUCCESS" or outcome.code in seen_issues:
            continue
        category, message = issue_types.get(outcome.code, ("run", "A requested step was not completed."))
        issues.append(AnswerIssue(code=outcome.code, category=category, message=message))
        seen_issues.add(outcome.code)
    return AnswerView(
        goal_id=result.goal.id, status=projection.goal_status.value, text=result.answer,
        limitations=package.limitations, issues=issues,
        requirements=[RequirementView(id=r.requirement_id, statement=r.statement,
                                      coverage=r.coverage, required=r.required) for r in package.requirements],
        artifacts=artifacts,
    )
