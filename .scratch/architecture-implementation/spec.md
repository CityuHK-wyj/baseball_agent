# Frozen architecture implementation

Status: IN_PROGRESS

Source: user development request; blog content/knowledge/decisions.md D034–D066 as of 2026-09-14. Public testing seams below are selected under the user's explicit authorization to design the testing strategy autonomously.

## Milestone 1: safe domain foundation

1. Settings read environment at construction time and never reveal credentials in repr. No credential defaults exist in source or example configuration.
2. Secret scanning produces only file/line/rule plus SECRET_REDACTED. Test synthetic provider keys, URL passwords, literal keyword credentials and staged content. Existing history is audited separately and never silently published.
3. AnalysisObjective and ArtifactRequirement are frozen definitions; ObjectiveState and RequirementState are distinct initial projections. ArtifactDescriptor shares canonical entity, semantic data keys, date range and typed constraints between requirements and artifacts.
4. The public requirement catalog preserves initial requirements. Additions must have PLANNER_ADDED origin, unique identities, a known objective and valid same-objective parent. Replaying the same addition is idempotent; identity reuse with changed content fails. Initial criticality cannot change.
5. Legacy unsafe SQL/agent entry points fail closed until a tested read-only executor exists. No analytics writes are executed during development. This temporary restriction must be explicit in documentation.

## Later milestones

Matcher + contextual assessment (hard failure veto; soft facts; Judge summary); RequirementState and ObjectiveState transitions; semantic Planner PLAN/REPLAN/STOP_PLANNING with terminal latch; Router policy and fallback; deterministic Executor retry; accepted-products ResponsePackage; Shared Context projection; independent Operational PostgreSQL and filesystem payload storage; checkpoint consistency and idempotent resume.

Integration/adversarial cases: wrong entity/schema, corrupt artifact, hard time mismatch, 0 rows, partial coverage, low sample, contextual Judge adjustment, supporting gaps, source failure, policy/budget/max rounds, terminal reinvocation, duplicates, checkpoint interruption, malformed tools, SQL mutation/function/file escape, secret output. Each later ticket must specify its seam and red/green evidence before implementation.
