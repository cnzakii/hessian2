"""Compare the Python value mappings without bool/int/float equality leaks."""

import dataclasses
import math
from decimal import Decimal
from typing import Any

import attrs
import hessian2


def same(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(map(same, actual, expected))
    if isinstance(expected, dict):
        return same(list(actual), list(expected)) and all(
            same(actual[key], expected[key]) for key in expected
        )
    if isinstance(expected, (set, frozenset)):
        return {(type(item), item) for item in actual} == {
            (type(item), item) for item in expected
        }
    if isinstance(expected, float):
        return (
            math.isnan(actual) and math.isnan(expected)
        ) or actual.hex() == expected.hex()
    if isinstance(expected, Decimal):
        return actual.as_tuple() == expected.as_tuple()
    if isinstance(expected, hessian2.HessianObject):
        return actual.class_name == expected.class_name and same(
            actual.fields, expected.fields
        )
    if dataclasses.is_dataclass(expected):
        return all(
            same(getattr(actual, f.name), getattr(expected, f.name))
            for f in dataclasses.fields(expected)
        )
    if attrs.has(type(expected)):
        return all(
            same(getattr(actual, f.name), getattr(expected, f.name))
            for f in attrs.fields(type(expected))
        )
    return actual == expected
