"""Finite derived-operation IR. Semantic contracts and execution proposals are distinct.

Only RequirementBuilder owns OperationContract. Tools may propose GovernedOperation;
program text and tool-declared scope never authorize an operation or an operand.
"""
from typing import Literal
from pydantic import ConfigDict, Field, model_validator, field_validator
from app.contracts.immutability import GovernableModel as BaseModel

OperationKind = Literal['SUBTRACT', 'SUM', 'RATIO', 'MEAN', 'RANK', 'AGGREGATE',
                        'MIN', 'MAX', 'MEDIAN', 'PERCENT_CHANGE']


class StrictOperationModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class OperationWindow(StrictOperationModel):
    start: str
    end: str


class OperationQualification(StrictOperationModel):
    basis: Literal['PA', 'BBE', 'PITCHES', 'GAMES', 'RESULTS']
    minimum: int = Field(ge=0)


class OperandScope(StrictOperationModel):
    measure: str = Field(min_length=1)
    analytical_role: Literal['BATTER', 'PITCHER'] = 'BATTER'
    subjects: list[str] = Field(default_factory=list)
    season: int | None = None
    date_range: OperationWindow | None = None
    game_types: list[Literal['R','S','F','D','L','W']] | None = None
    qualification: OperationQualification | None = None
    @field_validator('game_types')
    @classmethod
    def canonical_codes(cls, value):
        return sorted(set(value)) if value is not None else None

    # Full-population scalar cells only. Grouped/window/filter operations require a
    # future typed scope extension; neither a row index nor free Python supplies it.


class OperandObligation(StrictOperationModel):
    scope: OperandScope | None = None
    parent_key: str | None = None
    parent_output: str | None = None

    @model_validator(mode='after')
    def exclusive(self):
        if (self.scope is None) == (self.parent_key is None):
            raise ValueError('operand needs exactly one root scope or governed parent')
        if self.parent_key and not self.parent_output:
            raise ValueError('parent output identity required')
        if self.scope is not None and self.parent_output is not None:
            raise ValueError('root cannot declare a parent output')
        return self


class OperationParameters(StrictOperationModel):
    direction: Literal['ASC', 'DESC'] | None = None
    reducer: Literal['SUM', 'MEAN', 'MIN', 'MAX', 'COUNT'] | None = None


class OperationContract(StrictOperationModel):
    key: str = Field(min_length=1)
    kind: OperationKind
    operands: list[OperandObligation] = Field(min_length=1, max_length=256)
    output_field: str = Field(min_length=1, max_length=100)
    parameters: OperationParameters = Field(default_factory=OperationParameters)

    @model_validator(mode='after')
    def bounded(self):
        if self.kind in {'SUBTRACT','RATIO','PERCENT_CHANGE'} and len(self.operands) != 2:
            raise ValueError('ordered binary operation requires exactly two operands')
        if (self.kind == 'RANK') != (self.parameters.direction is not None):
            raise ValueError('only rank requires a governed direction')
        if (self.kind == 'AGGREGATE') != (self.parameters.reducer is not None):
            raise ValueError('only aggregate requires a governed reducer')
        return self


class OperandCell(StrictOperationModel):
    artifact_id: str
    export_id: str
    row_index: int = Field(ge=0)
    field: str


class GovernedOperation(StrictOperationModel):
    requirement_id: str
    kind: OperationKind
    operands: list[OperandCell] = Field(min_length=1, max_length=256)
    output_field: str
    parameters: OperationParameters = Field(default_factory=OperationParameters)
