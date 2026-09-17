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

EVIDENCE_PROMPT = PromptTemplate(
    prompt_id="evidence.extract", version=1,
    required_variables=("url", "title", "text"),
    template=(
        "Extract factual claims from this baseball document. Record only what the text "
        "states; do not infer, and do not add outside knowledge.\n"
        "Return ONLY JSON with a \"claims\" array of "
        "{claim:str, support:str, claim_time:str}.\n"
        "URL: {url}\nTitle: {title}\nText:\n{text}\n"))

SEMANTIC_PROMPT = PromptTemplate(
    prompt_id="semantic.extract", version=1,
    required_variables=("query", "vocabulary_json"),
    template=(
        "You are the Baseball Agent semantic extractor. Translate ONE analytics request "
        "into a closed, typed JSON semantic candidate. You propose meaning only.\n"
        "You must NOT name database columns, write SQL, choose a data source, decide "
        "policy or invent numbers. Use only the vocabulary below.\n"
        "Rules:\n"
        "- Attach each number to the metric it actually belongs to. A qualification "
        "number (for example '100 BBE') is never a velocity. Never reuse one number for "
        "two clauses.\n"
        "- Record evidence.text as the exact substring of the query that grounds each "
        "constraint.\n"
        "- Explicit counts are exact states: 0-2 -> [{balls:0,strikes:2}]; "
        "'0-2 or 1-1' -> [{balls:0,strikes:2},{balls:1,strikes:1}]; never a cartesian "
        "product and never widened. Generic 'two strikes' -> strikes:2 with all ball "
        "counts.\n"
        "- Keep explicit aggregation (maximum -> MAX, average -> AVG). Do not default an "
        "aggregation the user did not state.\n"
        "- Emit a POPULATION constraint only when the user states one; never emit default "
        "populations.\n"
        "- Do not resolve material ambiguity. Put unresolved choices in ambiguities with "
        "the plausible candidate definitions.\n"
        "- If the request contains contradictory explicit semantics, emit both and let "
        "the validator decide; do not silently drop one.\n"
        "Return ONLY JSON with keys constraints and ambiguities. Each constraint has: "
        "kind, evidence{text,start,end}, origin, metric, operator, value, unit, family, "
        "definition, states[{balls,strikes}], strikes, balls, min_batted_balls, "
        "game_types, event_population, metric_key, aggregation, direction, limit. "
        "Each ambiguity has: kind, evidence{text}, question, candidates.\n"
        "Allowed kinds: NUMERIC, PITCH_TYPE, LOCATION, COUNT, QUALIFICATION, POPULATION, "
        "RANKING.\n"
        "Vocabulary:\n{vocabulary_json}\n"
        "Query:\n{query}\n"))

RESPONSE_PROMPT = PromptTemplate(
    prompt_id="response.compose", version=1,
    required_variables=("objective_ref", "objective_status", "evidence_json", "limitations"),
    template=(
        "Write a concise, sourced answer for the user. Use only the accepted evidence and "
        "knowledge provided; do not invent facts, metrics or numbers.\n"
        "Objective: {objective_ref} (status: {objective_status})\n"
        "Accepted evidence: {evidence_json}\n"
        "Limitations: {limitations}\n"))
