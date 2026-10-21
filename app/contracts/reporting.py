"""User-requested original observations, distinct from internal operation operands.

The scope vocabulary is shared with governed scalar roots; this is not an operation,
execution proposal or factual approval. Only the owning Requirement requests these cells.
"""
from pydantic import Field
from app.contracts.operations import OperandScope, StrictOperationModel


class ReportingContract(StrictOperationModel):
    cells: list[OperandScope] = Field(min_length=1, max_length=64)
