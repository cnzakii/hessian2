"""Value preservation and total decoding over untrusted bytes."""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import uuid
from typing import Any

import hessian2
import pytest
from assertions import same
from conftest import JAVA_FIXTURES, java_wire
from hypothesis import given, settings
from hypothesis import strategies as st

UTC = datetime.UTC
scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False).map(lambda f: f + 0.0),
    st.text(st.characters(codec=None, exclude_categories=())),
    st.binary(),
    st.datetimes(timezones=st.just(UTC)).map(
        lambda d: d.replace(microsecond=d.microsecond // 1000 * 1000)
    ),
    st.decimals(allow_nan=False, allow_infinity=False),
    st.uuids(),
    st.datetimes(),
    st.dates(),
    st.times(),
    st.timedeltas(),
)
keys = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-(2**63), 2**63 - 1),
    st.text(),
    st.binary(max_size=8),
    st.dates(),
)
values = st.recursive(
    scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=8), st.dictionaries(keys, children, max_size=8)
    ),
    max_leaves=40,
)


@given(values)
def test_untyped_round_trip_preserves_python_types_and_values(value: Any) -> None:
    decoded = hessian2.loads(hessian2.dumps(value))

    assert same(decoded, value)


@given(values)
def test_reencoding_a_decoded_value_is_stable(value: Any) -> None:
    data = hessian2.dumps(value)

    assert same(hessian2.dumps(hessian2.loads(data)), data)


@given(st.lists(values, max_size=5))
def test_stream_round_trip_preserves_python_types_and_values(items: list[Any]) -> None:
    decoded = hessian2.loads_many(hessian2.dumps_many(items))

    assert same(decoded, items)


@hessian2.java_class("properties.Record")
@dataclasses.dataclass
class Record:
    count: int
    names: tuple[str, ...]


integers = st.integers(-(2**63), 2**63 - 1)
text = st.text(st.characters(codec=None, exclude_categories=()))
typed_values = st.one_of(
    integers.map(lambda v: (v, int)),
    st.lists(integers, max_size=8).map(lambda v: (v, list[int])),
    st.lists(text, max_size=8).map(lambda v: (tuple(v), tuple[str, ...])),
    st.sets(integers, max_size=8).map(lambda v: (v, set[int])),
    st.dictionaries(st.uuids(), integers, max_size=8).map(
        lambda v: (v, dict[uuid.UUID, int])
    ),
    st.builds(Record, integers, st.lists(text, max_size=5).map(tuple)).map(
        lambda v: (v, Record)
    ),
)


@given(typed_values)
def test_typed_round_trip_preserves_python_types_and_values(
    case: tuple[Any, Any],
) -> None:
    value, annotation = case
    data = hessian2.dumps(value, type=annotation)

    decoded = hessian2.loads(data, type=annotation)

    assert same(decoded, value)


@pytest.mark.parametrize("mode", ["raise", "hessian_object", "dict"])
@settings(max_examples=500)
@given(st.binary(max_size=64))
def test_any_bytes_decode_or_raise_decode_error(mode: str, data: bytes) -> None:
    codec = hessian2.Codec(unknown_class=mode)

    with contextlib.suppress(hessian2.DecodeError):
        codec.loads_many(data)


@pytest.mark.parametrize(
    "annotation",
    [int, float, list[int], tuple[str, ...], dict[str, int], Record],
    ids=["int", "float", "list", "tuple", "dict", "record"],
)
@settings(max_examples=500)
@given(st.binary(max_size=64))
def test_any_bytes_with_a_type_decode_or_raise_decode_error(
    annotation: Any, data: bytes
) -> None:
    with contextlib.suppress(hessian2.DecodeError):
        hessian2.loads(data, type=annotation)


SMALL = [
    java_wire(name, "caucho" if case["caucho"] else "lite")
    for name, case in JAVA_FIXTURES.items()
    if 0 < len(case["caucho"] or case["lite"]) < 2000
]


@pytest.mark.parametrize("mode", ["raise", "hessian_object", "dict"])
@settings(max_examples=1000)
@given(st.sampled_from(SMALL), st.data())
def test_mutated_peer_bytes_decode_or_raise_decode_error(
    mode: str, data: bytes, draw: st.DataObject
) -> None:
    mutated = bytearray(data)
    edits = draw.draw(
        st.lists(
            st.tuples(st.integers(0, len(data) - 1), st.integers(0, 255)),
            min_size=1,
            max_size=4,
        )
    )
    for index, byte in edits:
        mutated[index] = byte
    codec = hessian2.Codec(unknown_class=mode)

    with contextlib.suppress(hessian2.DecodeError):
        codec.loads_many(mutated)


@given(st.uuids())
def test_uuid_encoding_uses_the_canonical_uuid_text(value: uuid.UUID) -> None:
    assert hessian2.dumps(value).endswith(str(value).encode())
