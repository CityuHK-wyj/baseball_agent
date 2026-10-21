from app.contracts.models import AnalysisRequest, Constraint, SemanticInterpretation
from app.contracts.operations import OperationContract
from app.semantic.goals import RequirementBuilder
from tests.governed_operation_fixture import compute, goal


def test_typed_contract_not_model_program_selects_finite_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('candidate Python must not run when the contract binds')
    monkeypatch.setattr('app.capabilities.python_compute.run_program',forbidden)
    g,artifact,source=compute(inputs={'program':'output=[{"difference":73129}]'})
    assert artifact.structured_data['rows']==[{'difference':20}]
    assert artifact.structured_data['governed_operations']


def test_omitted_competition_grounds_from_the_authoritative_goal_population():
    contract=next(r.operation_contract for r in goal().requirements if r.operation_contract).model_dump()
    for operand in contract['operands']:operand['scope']['game_types']=None
    text='2023 home runs minus 2024 home runs'
    g=RequirementBuilder().build(SemanticInterpretation(raw_query=text,language='en',population_hints=['REGULAR_SEASON'],
        constraints=[Constraint(kind='season',value=2023),Constraint(kind='season',value=2024)],
        analysis_requests=[AnalysisRequest(text=text,user_span=text,operation_contract=OperationContract.model_validate(contract))]))
    assert next(r.operation_contract for r in g.requirements if r.operation_contract).operands[0].scope.game_types==['R']


def test_missing_population_is_not_guessed_without_governed_context():
    g=goal()
    assert all(o.scope.game_types is None for r in g.requirements if r.operation_contract
               for o in r.operation_contract.operands)


def test_distinct_goal_populations_do_not_license_inheritance():
    contract=next(r.operation_contract for r in goal().requirements if r.operation_contract)
    text='Compare regular season and postseason home runs in 2023 and 2024.'
    g=RequirementBuilder().build(SemanticInterpretation(raw_query=text,language='en',
        population_hints=['REGULAR_SEASON','POSTSEASON'],
        analysis_requests=[AnalysisRequest(text=text,user_span=text,operation_contract=contract)]))
    assert all(o.scope.game_types is None for r in g.requirements if r.operation_contract
               for o in r.operation_contract.operands)
