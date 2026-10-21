"""Deep, detached snapshots for governed state, preserving JSON/list compatibility.

Execution contracts stay mutable during construction. Only the governor seals a completed
snapshot. This is an application mutation boundary, not a Python sandbox.
"""
from copy import deepcopy
from dataclasses import fields, is_dataclass

from pydantic import BaseModel, PrivateAttr


def _immutable(*args, **kwargs):
    raise TypeError("governed snapshot is immutable")


class FrozenList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = _immutable
    __iadd__ = __imul__ = _immutable

    def __deepcopy__(self, memo):
        return self


class FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __deepcopy__(self, memo):
        return self


class GovernableModel(BaseModel):
    _governed_frozen: bool = PrivateAttr(default=False)

    def __setattr__(self, name, value):
        if self._governed_frozen:
            _immutable()
        super().__setattr__(name, value)

    def __delattr__(self, name):
        if self._governed_frozen:
            _immutable()
        super().__delattr__(name)

    @property
    def is_governed_frozen(self) -> bool:
        return self._governed_frozen

    def frozen_snapshot(self):
        return freeze_value(deepcopy(self))


def freeze_value(value):
    """Seal a private value recursively; callers detach first when sharing mutable inputs."""
    if isinstance(value, GovernableModel):
        for name in type(value).model_fields:
            object.__setattr__(value, name, freeze_value(getattr(value, name)))
        object.__setattr__(value, "_governed_frozen", True)
        return value
    if isinstance(value, BaseModel):
        raise TypeError(f"ungovernable nested model: {type(value).__name__}")
    if is_dataclass(value) and not isinstance(value, type):
        if not type(value).__dataclass_params__.frozen:
            raise TypeError(f"mutable nested dataclass: {type(value).__name__}")
        for item in fields(value):
            object.__setattr__(value, item.name, freeze_value(getattr(value, item.name)))
        return value
    if isinstance(value, list):
        return FrozenList(freeze_value(item) for item in value)
    if isinstance(value, dict):
        return FrozenDict((key, freeze_value(item)) for key, item in value.items())
    if isinstance(value, tuple):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze_value(item) for item in value)
    return value
