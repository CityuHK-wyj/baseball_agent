"""Clarification contracts.

Meaning ambiguity belongs to the user. The system may recommend an option, but it must
not silently decide what the user meant.
"""

from typing import Literal

from pydantic import Field

from app.models.contracts import Contract, Name


class ClarificationOption(Contract):
    option_id: Name
    label: Name
    value: Name
    rationale: str = ""


class ClarificationRequest(Contract):
    clarification_id: Name
    kind: Literal["ENTITY", "MEANING", "CONSTRAINT"]
    question: Name
    reason: str = ""
    options: tuple[ClarificationOption, ...] = Field(default=(), max_length=4)
    recommended_option_id: Name | None = None
    affected_ref: str = ""

    def chosen(self, option_id: str) -> ClarificationOption | None:
        for option in self.options:
            if option.option_id == option_id:
                return option
        return None


class ClarificationAnswer(Contract):
    clarification_ref: Name
    chosen_option_id: Name
    note: str = ""
