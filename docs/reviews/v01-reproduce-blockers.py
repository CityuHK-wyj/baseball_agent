"""Release gate: nonzero while demonstrated analytics intent defects remain.

Run from the repository root: python3 docs/reviews/v01-reproduce-blockers.py
This is intentionally separate from the passing regression suite. It states required
behavior rather than blessing current incorrect output as a passing test.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.semantic.analytics_intent import extract_analytical_constraints
from app.models.contracts import AnalysisObjective
from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer

cases = [
    ('inclusive_velocity', 'top 5 by exit velocity against fastballs >= 95 mph',
     lambda cs: any(c.key == 'pitch_velocity' and c.operator == 'GTE' and c.value == 95 for c in cs)),
    ('velocity_identity', 'top 5 by exit velocity with exit velocity above 95 mph',
     lambda cs: any(c.key == 'exit_velocity' and c.kind == 'NUMERIC' for c in cs)
                and not any(c.key == 'pitch_velocity' for c in cs)),
    ('explicit_maximum', 'top 5 by maximum exit velocity',
     lambda cs: any(c.kind == 'RANKING' and c.aggregation == 'MAX' for c in cs)),
    ('explicit_count', '0-2 fastballs top 5 by exit velocity',
     lambda cs: any(c.kind == 'COUNT' and c.balls == (0,) for c in cs)),
]
failures = []
for name, query, predicate in cases:
    constraints = extract_analytical_constraints(query).constraints
    if not predicate(constraints):
        failures.append(name)
    print(json.dumps({'case': name, 'query': query, 'passed': predicate(constraints),
                      'actual': [c.model_dump(mode='json') for c in constraints]}))
query = 'top 5 by exit velocity minimum 20 batted balls'
constraints = extract_analytical_constraints(query).constraints
requirement = RuleBasedRequirementDecomposer().decompose(AnalysisObjective(
    objective_id='review', raw_query=query, description=query, constraints=constraints))[0]
if requirement.qualification_rule is None:
    failures.append('qualification_not_frozen')
print(json.dumps({'case': 'qualification_not_frozen', 'query': query,
                  'qualification': requirement.qualification_rule,
                  'sample_adequacy': requirement.sample_adequacy_rule}, default=str))
print(json.dumps({'unresolved_blockers': failures}))
raise SystemExit(bool(failures))
