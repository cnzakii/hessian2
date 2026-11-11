"""Property tests: encoding round trips, and decoding arbitrary bytes fails only
with DecodeError."""

from __future__ import annotations

import base64
import contextlib
import datetime
import json
import uuid
from pathlib import Path
from typing import Any

import hessian2
from hypothesis import given, settings
from hypothesis import strategies as st

UTC = datetime.UTC

scalars = st.one_of(
    st.none(),
    st.booleans(),
    # Beyond 64 bits as java.math.BigInteger.
    st.integers(),
    # Java, and so this library, writes -0.0 as 0.0.
    st.floats(allow_nan=False).map(lambda f: f + 0.0),
    # Includes unpaired surrogates, which Java and Python strings allow.
    st.text(st.characters(codec=None, exclude_categories=())),
    st.binary(),
    st.datetimes(
        min_value=datetime.datetime(1, 1, 1),
        max_value=datetime.datetime(9999, 12, 31),
        timezones=st.just(UTC),
    ).map(lambda d: d.replace(microsecond=d.microsecond // 1000 * 1000)),
    st.decimals(allow_nan=False, allow_infinity=False),
    st.uuids(),
    # java.time.LocalDateTime, LocalDate, LocalTime, and Duration.
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
    st.uuids(),
)
values = st.recursive(
    scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=8),
        st.dictionaries(keys, children, max_size=8),
    ),
    max_leaves=40,
)


def same(a: Any, b: Any) -> bool:
    """Equality that also compares the exponent of Decimals and the sign of zero."""
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(map(same, a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(same(a[k], b[k]) for k in a)
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return a.hex() == b.hex()
    return str(a) == str(b) if hasattr(a, "as_tuple") else a == b


@given(values)
def test_round_trip(value: Any) -> None:
    assert same(hessian2.loads(hessian2.dumps(value)), value)


@given(values)
def test_encoding_is_stable(value: Any) -> None:
    data = hessian2.dumps(value)
    assert hessian2.dumps(hessian2.loads(data)) == data


@given(st.lists(values, max_size=5))
def test_many_round_trip(items: list[Any]) -> None:
    assert same(hessian2.loads_many(hessian2.dumps_many(items)), items)


CODECS = [
    hessian2.Codec(),
    hessian2.Codec(unknown_class="hessian_object"),
    hessian2.Codec(unknown_class="dict"),
]


def decodes_or_rejects(data: bytes) -> None:
    for codec in CODECS:
        with contextlib.suppress(hessian2.DecodeError):
            codec.loads_many(data)


@settings(max_examples=500)
@given(st.binary(max_size=64))
def test_arbitrary_bytes(data: bytes) -> None:
    decodes_or_rejects(data)


FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "java.json").read_text())
SMALL = [
    base64.b64decode(data)
    for case in FIXTURES.values()
    if len(data := case["caucho"] or case["lite"]) < 2000
]


def test_every_truncation_of_java_bytes() -> None:
    for data in SMALL:
        for end in range(len(data)):
            decodes_or_rejects(data[:end])


@settings(max_examples=1000)
@given(st.sampled_from(SMALL), st.data())
def test_mutated_java_bytes(data: bytes, draw: st.DataObject) -> None:
    mutated = bytearray(data)
    for _ in range(draw.draw(st.integers(1, 4))):
        index = draw.draw(st.integers(0, len(mutated) - 1))
        mutated[index] = draw.draw(st.integers(0, 255))
    decodes_or_rejects(bytes(mutated))


@given(st.uuids())
def test_uuid_text(value: uuid.UUID) -> None:
    assert hessian2.dumps(value).endswith(str(value).encode())
