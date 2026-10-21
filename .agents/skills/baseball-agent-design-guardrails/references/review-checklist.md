# Architecture Review Checklist

Use this before finishing a semantic or architectural change.

## Language boundary

- [ ] Does this change add a new regex, keyword list or phrase list?
- [ ] If yes, is it genuinely closed-world or stable domain terminology?
- [ ] Could equivalent unseen wording work without another code change?
- [ ] Is a lexical cue gaining semantic authority?
- [ ] Could a typo incorrectly change the governed meaning?

## Semantic conservation

- [ ] Are all answer-changing clauses represented?
- [ ] Can partial matching incorrectly mark a full clause UNDERSTOOD?
- [ ] Can short tokens, numbers or CJK fragments disappear?
- [ ] Is ignored text explicitly safe to ignore?
- [ ] Is unresolved meaning preserved?

## Authority

- [ ] Does every inferred Requirement have authoritative ancestry?
- [ ] Is the actual semantic transformation validated?
- [ ] Could Planner/Need metadata launder an unasked constraint into authority?
- [ ] Could model-generated proof prose be mistaken for semantic proof?

## Execution

- [ ] Does requested scope match executed scope?
- [ ] Are role, entity, filters, temporal scope, population, qualification,
      grouping, ordering and limit checked where applicable?
- [ ] Could an extra execution restriction still be admitted?
- [ ] Could an omitted restriction still be admitted?
- [ ] Could wrong role or population become COMPLETE?

## Evidence/Judge

- [ ] Is the Artifact linked to the Requirement it supports?
- [ ] Is unsupported collected data prevented from becoming approved evidence?
- [ ] Could the Composer self-certify?
- [ ] Could frontend state self-certify?
- [ ] Is `wrong semantic COMPLETE` still zero?

## Partial results

- [ ] Are independent supported results retained?
- [ ] Are dependent results correctly blocked?
- [ ] Does unresolved sibling state incorrectly delete valid evidence?
- [ ] Can clarification invalidate only affected dependent results?

## Clarification

- [ ] Did the system try bounded internal resolution first?
- [ ] Is ambiguity material enough to ask the user?
- [ ] Does clarification target the smallest unresolved scope?
- [ ] Does the clarification response become authoritative on the backend?
- [ ] Can the same resolved ambiguity be asked repeatedly?

## Shared Knowledge

- [ ] Is stable terminology centralized?
- [ ] Is Shared Knowledge being used as semantic help rather than factual proof?
- [ ] Could mutable facts bypass Artifact/Judge governance?

## Frontend

- [ ] Is the frontend only collecting intent and presenting approved state?
- [ ] Did any semantic parsing leak into React/TypeScript?
- [ ] Are clarification options backend-defined?
- [ ] Are only approved Artifacts rendered as factual results?
- [ ] Are SQL, IR, receipts and credentials still private?

## Testing

- [ ] Does the test verify the mechanism rather than one phrase?
- [ ] Are fresh paraphrases tested?
- [ ] Are typos/noisy input tested when language behavior changed?
- [ ] Are adversarial compound clauses tested?
- [ ] Are both safety and utility measured?
- [ ] Were existing regression suites preserved?

## Development

- [ ] Were known in-scope failures repaired before stopping?
- [ ] Did the change solve an abstraction rather than one benchmark?
- [ ] Are remaining limitations explicitly documented?
- [ ] Has production avoided patching genuinely frozen evaluation cases?
