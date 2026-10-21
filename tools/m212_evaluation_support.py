"""Serialization and numerical comparison for audit scripts, never production."""
import dataclasses
import math


def encode(value):
    if hasattr(value, "model_dump"):
        return encode(value.model_dump(mode="python"))
    if dataclasses.is_dataclass(value):
        return {
            field.name: encode(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if field.name != "trace"
        }
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    return value


def equal(actual, expected):
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            equal(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected
