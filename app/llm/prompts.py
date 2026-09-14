"""Versioned prompt templates.

Prompts are identifiable and versioned artifacts, not long strings hidden inside
functions, so a change is reviewable and testable.
"""

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name


class PromptTemplate(ArtifactContract):
    prompt_id: Name
    version: int = 1
    template: str
    required_variables: tuple[Name, ...] = ()

    def render(self, **values: object) -> str:
        missing = [name for name in self.required_variables if name not in values]
        if missing:
            raise KeyError(f"Missing prompt variable(s): {sorted(missing)}")
        # Literal replacement, not str.format: prompts contain JSON braces that must
        # not be interpreted as placeholders.
        text = self.template
        for name, value in values.items():
            text = text.replace("{" + name + "}", str(value))
        return text

    def identity(self) -> str:
        return f"{self.prompt_id}@v{self.version}"


PLANNER_PROMPT = PromptTemplate(
    prompt_id="planner.decide", version=1,
    required_variables=("context_json",),
    template=(
        "You are the Baseball Agent Planner. Decide what to do next.\n"
        "Return ONLY JSON with keys: kind (PLAN|REPLAN|STOP_PLANNING), tasks "
        "(array of {requirement_refs:[str], description:str, source_preference:str|null}), "
        "planner_terminal (bool), terminal_reason (str), rationale (str).\n"
        "Rules:\n"
        "- You may only reference requirement ids that appear in the context.\n"
        "- You may not modify or delete an Initial Requirement.\n"
        "- If every core requirement is satisfied, return STOP_PLANNING with "
        "planner_terminal true and a terminal_reason.\n"
        "Context:\n{context_json}\n"))

JUDGE_PROMPT = PromptTemplate(
    prompt_id="judge.assess", version=1,
    required_variables=("artifact_json", "requirement_json", "deterministic_json"),
    template=(
        "You are the Baseball Agent Judge. Assess how usable this Artifact is for this "
        "Requirement, given the deterministic facts.\n"
        "Return ONLY JSON with keys: level (STRONG|ACCEPTABLE|WEAK|REJECT), rationale (str).\n"
        "You may reinterpret soft signals but you must never override a hard failure.\n"
        "Artifact: {artifact_json}\nRequirement: {requirement_json}\n"
        "Deterministic facts: {deterministic_json}\n"))

RESPONSE_PROMPT = PromptTemplate(
    prompt_id="response.compose", version=1,
    required_variables=("objective_ref", "objective_status", "evidence_json", "limitations"),
    template=(
        "Write a concise, sourced answer for the user. Use only the accepted evidence and "
        "knowledge provided; do not invent facts, metrics or numbers.\n"
        "Objective: {objective_ref} (status: {objective_status})\n"
        "Accepted evidence: {evidence_json}\n"
        "Limitations: {limitations}\n"))
