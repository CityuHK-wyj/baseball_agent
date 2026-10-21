"""IR gate: finite operations do not need arbitrary executable Python."""
import pytest
from app.contracts.operations import OperationContract, OperationParameters
from app.verification.governed_operations import evaluate_operation

@pytest.mark.parametrize('kind,params,expected', [
    ('SUBTRACT',{},20), ('SUM',{},58), ('RATIO',{},39/19), ('MEAN',{},29),
    ('RANK',{'direction':'DESC'},[{'operand':0,'value':39,'rank':1},{'operand':1,'value':19,'rank':2}]),
    ('AGGREGATE',{'reducer':'SUM'},58), ('MIN',{},19), ('MAX',{},39), ('MEDIAN',{},29),
])
def test_ir_gate(kind,params,expected):
    contract = OperationContract(key='requested',kind=kind,output_field='result',parameters=params,
        operands=[{'scope':{'measure':'home_runs','season':s}} for s in (2023,2024)])
    assert evaluate_operation(contract.kind,[39,19],contract.parameters) == expected

@pytest.mark.parametrize('params', [{'direction':'DESC'}, {'reducer':'COUNT'}, {'constant':73129}])
def test_parameters_cannot_supply_unrequested_constants(params):
    with pytest.raises(ValueError):
        OperationContract(key='x',kind='SUBTRACT',output_field='result',parameters=params,
                          operands=[{'scope':{'measure':'home_runs'}}]*2)

def test_existing_governed_population_names_ground_to_exact_code_sets():
    from app.semantic.operations import ground_operation_contract
    def ground(symbol):
        return ground_operation_contract({'key':'x','kind':'SUM','output_field':'total',
            'operands':[{'scope':{'measure':'home_runs','game_types':[symbol]}}]}).operands[0].scope
    assert ground('REGULAR_SEASON').game_types == ['R']
    assert ground('POSTSEASON').game_types == ['D','F','L','W']
    with pytest.raises(ValueError):
        ground('MODEL_INVENTED_POPULATION')


def test_zero_denominator_fails_closed():
    with pytest.raises(ZeroDivisionError):
        evaluate_operation('RATIO',[39,0],OperationParameters())
