"""Public contracts for encode types."""

from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import enum
import re
import typing
from decimal import Decimal
from typing import Any, TypedDict

import hessian2
import pytest
from assertions import same
from conftest import java_wire as fixture
from hessian2 import Codec, Converter, EncodeError

UTC = dt.UTC

PLUS8 = dt.timezone(dt.timedelta(hours=8))


@pytest.mark.parametrize(
    ("value", "tp", "expected"),
    [
        (5, hessian2.Long, b"\xe5"),
        (5, int, b"\x95"),
        ([1, 2], list[hessian2.Long], b"\x7a\xe1\xe2"),
        ((1, 2), tuple[hessian2.Long, int], b"\x7a\xe1\x92"),
        ((1, 2), tuple[hessian2.Long, ...], b"r\x05[long\xe1\xe2"),  # long[]
        ({1}, set[hessian2.Long], b"q\x11java.util.HashSet\xe1"),
        ({"a": 1}, dict[str, hessian2.Long], b"H\x01a\xe1Z"),
        ({1: "a"}, dict[hessian2.Long, str], b"H\xe1\x01aZ"),
        (None, hessian2.Long | None, b"N"),
        ("x", hessian2.Long | str, b"\x01x"),
        ([1, "x"], list[hessian2.Long | str], b"\x7a\xe1\x01x"),
        ([1], list[hessian2.Long] | dict[str, int], b"\x79\xe1"),
        (True, hessian2.Long, b"T"),
    ],
    ids=repr,
)
def test_top_level_types_choose_wire_forms(
    value: Any, tp: Any, expected: bytes
) -> None:
    assert hessian2.dumps(value, type=tp) == expected


def test_typed_dict_field_annotations_choose_each_values_java_type():
    class Extra(TypedDict):
        count: hessian2.Long
        name: str

    assert (
        hessian2.dumps({"count": 1, "name": "x"}, type=Extra)
        == b"H\x05count\xe1\x04name\x01xZ"
    )


def test_dumps_many_applies_one_annotation_per_stream_value():
    assert (
        hessian2.dumps_many(["m", 1, 2], types=[str, None, hessian2.Long])
        == b"\x01m\x91\xe2"
    )


@pytest.mark.parametrize(
    ("values", "annotations", "message"),
    [([1, 2], [None], "types has 1"), ([1], [None, None], "types has 2")],
    ids=["too-few", "too-many"],
)
def test_dumps_many_requires_the_annotation_count_to_match_values(
    values, annotations, message
):
    with pytest.raises(ValueError, match=re.escape(message)):
        hessian2.dumps_many(values, types=annotations)


def test_field_tags_apply_to_declared_classes() -> None:
    class Extra(TypedDict):
        count: hessian2.Long

    @hessian2.java_class("com.example.Payment")
    @dataclasses.dataclass
    class Payment:
        amount: hessian2.BigInteger
        ids: list[hessian2.Long]
        paid_at: hessian2.Instant
        extra: Extra

    paid_at = dt.datetime(2026, 1, 1, tzinfo=UTC)
    payment = Payment(5, [1], paid_at, {"count": 2})

    data = hessian2.dumps(payment)

    assert b"java.math.BigInteger" in data and b"\x79\xe1C05" in data
    assert b"InstantHandle" in data and data.endswith(b"H\x05count\xe2Z")
    codec = Codec()
    codec.register(Payment)
    assert same(codec.loads(data), payment)


@pytest.mark.parametrize(
    ("fixture_name", "tag"),
    [
        ("extra.time_instant", hessian2.Instant),
        ("extra.time_offset_datetime", hessian2.OffsetDateTime),
        ("extra.time_zoned_datetime", hessian2.ZonedDateTime),
    ],
)
def test_java_time_tags_write_what_hessian_lite_writes(
    fixture_name: str, tag: Any
) -> None:
    java = fixture(fixture_name, "lite")

    assert same(hessian2.dumps(hessian2.loads(java), type=tag), java)


def test_zoned_datetimes_keep_their_fold() -> None:
    # Java's bytes share one LocalDateTime between the two values; Python's
    # values share nothing, so compare values rather than bytes.
    values = hessian2.loads(fixture("extra.review2_time_overlap", "lite"))
    data = hessian2.dumps(values, type=list[hessian2.ZonedDateTime])

    decoded = hessian2.loads(data)

    assert [v.utcoffset() for v in decoded] == [v.utcoffset() for v in values]
    assert [v.fold for v in decoded] == [0, 1]


@pytest.mark.parametrize("name", ["extra.bigint_small", "extra.bigint_zero"])
def test_big_integer_tag_writes_what_caucho_writes(name: str) -> None:
    java = fixture(name, "caucho")

    assert same(hessian2.dumps(hessian2.loads(java), type=hessian2.BigInteger), java)


class CustomZone(dt.tzinfo):
    def utcoffset(self, value):
        return dt.timedelta(hours=1)


@pytest.mark.parametrize(
    ("value", "annotation", "message"),
    [
        (2**63, hessian2.Long, "does not fit in a Java long"),
        (dt.datetime(2026, 1, 1), hessian2.Instant, "naive datetime"),
        (
            dt.datetime(2026, 1, 1),
            hessian2.Date,
            "naive datetime has no java.util.Date",
        ),
        (
            dt.datetime(2026, 1, 1, tzinfo=PLUS8),
            hessian2.LocalDateTime,
            "aware datetime has no java.time.Local",
        ),
        (
            dt.datetime(
                2026, 1, 1, tzinfo=dt.timezone(dt.timedelta(seconds=1, microseconds=5))
            ),
            hessian2.OffsetDateTime,
            "whole seconds",
        ),
        (
            dt.datetime(2026, 1, 1, tzinfo=CustomZone()),
            hessian2.ZonedDateTime,
            "ZoneInfo",
        ),
    ],
    ids=[
        "long-range",
        "instant-naive",
        "date-naive",
        "local-aware",
        "fractional-zone",
        "custom-zone",
    ],
)
def test_java_type_tags_reject_values_the_java_type_cannot_hold(
    value, annotation, message
):
    with pytest.raises(EncodeError, match=re.escape(message)):
        hessian2.dumps(value, type=annotation)


def test_zoned_datetime_tags_accept_a_fixed_whole_second_offset():
    value = dt.datetime(2026, 1, 1, tzinfo=PLUS8)

    decoded = hessian2.loads(hessian2.dumps(value, type=hessian2.ZonedDateTime))

    assert decoded.utcoffset() == dt.timedelta(hours=8)
    assert decoded == value


@pytest.mark.parametrize(
    ("annotation", "value", "expected"),
    [
        (tuple[str, ...], ["a"], b"q\x07[string\x01a"),
        (tuple[Any, ...], ["a", 1], b"r\x07[object\x01a\x91"),
        (tuple[int, ...], (1,), b"q\x04[int\x91"),
        (tuple[int | None, ...], (1, None), b"r\x12[java.lang.Integer\x91N"),
        (tuple[hessian2.Long, ...], (1,), b"q\x05[long\xe1"),
        (
            tuple[Decimal, ...],
            (Decimal(1),),
            b"q\x15[java.math.BigDecimalC\x14java.math.BigDecimal\x91\x05value\x60\x011",
        ),
        (
            tuple[hessian2.Date, ...],
            (dt.datetime(2026, 1, 1, tzinfo=dt.UTC),),
            b"q\x05[dateK\x01\xc1\x6d\xc0",
        ),
        (tuple[tuple[int, ...], ...], ((1,),), b"q\x05[[intq\x04[int\x91"),
        (tuple[bytes, ...], (b"x",), b"q\x06[[byte!x"),
        (tuple[list[int], ...], ([1],), b"q\x07[objecty\x91"),
    ],
    ids=[
        "string",
        "object",
        "int",
        "boxed-int",
        "long",
        "decimal",
        "date",
        "nested",
        "binary",
        "list-item",
    ],
)
def test_variadic_tuple_annotations_choose_java_array_types(
    annotation, value, expected
):
    assert hessian2.dumps(value, type=annotation) == expected


def test_int_array_items_must_fit_a_java_int():
    with pytest.raises(EncodeError, match=re.escape("does not fit in a Java int")):
        hessian2.dumps((2**31,), type=tuple[int, ...])


@pytest.mark.parametrize(
    ("value", "annotation", "expected"),
    [(["a"], list[str], b"y\x01a"), (("a", 1), tuple[str, int], b"z\x01a\x91")],
    ids=["list", "fixed-tuple"],
)
def test_non_array_annotations_keep_the_plain_list_form(value, annotation, expected):
    assert hessian2.dumps(value, type=annotation) == expected


def test_a_registered_class_names_its_arrays_component_type():
    @dataclasses.dataclass
    class Third:
        x: int

    codec = Codec()
    codec.register(Third, name="com.example.Third")

    assert (
        codec.dumps((Third(1),), type=tuple[Third, ...])
        == b"q\x12[com.example.ThirdC\x11com.example.Third\x91\x01x\x60\x91"
    )
    assert codec.java_name(tuple[Third, ...]) == "[Lcom.example.Third;"


@pytest.mark.parametrize("kind", ["plain", "tagged", "retagged"])
def test_a_converter_registration_names_array_components_even_through_tags(kind):
    codec = Codec()
    codec.register(
        Converter(int, lambda v: {"v": str(v)}, lambda f: int(f["v"])),
        name="com.example.Count",
    )
    count = typing.NewType("count", int)
    retagged = typing.Annotated[count, *typing.get_args(hessian2.Long)[1:]]
    annotation = {
        "plain": tuple[int, ...],
        "tagged": tuple[hessian2.BigInteger | None, ...],
        "retagged": tuple[retagged, ...],
    }[kind]

    assert (
        codec.dumps((1,), type=annotation)
        == b"q\x12[com.example.CountC\x11com.example.Count\x91\x01v\x60\x011"
    )
    assert codec.java_name(annotation) == "[Lcom.example.Count;"


@pytest.mark.parametrize(
    ("value", "annotation"),
    [
        (dt.datetime(2026, 1, 1, tzinfo=PLUS8), hessian2.Date),
        (dt.datetime(2026, 1, 1), hessian2.LocalDateTime),
    ],
    ids=["date", "local-datetime"],
)
def test_explicit_default_datetime_tags_preserve_the_default_mapping(value, annotation):
    assert hessian2.dumps(value, type=annotation) == hessian2.dumps(value)


def test_a_tag_does_not_override_a_registered_values_mapping():
    class Level(enum.IntEnum):
        HIGH = 3

    codec = Codec()
    codec.register(Level, name="Level")

    assert (
        codec.dumps(Level.HIGH, type=hessian2.Long)
        == b"C\x05Level\x91\x04name\x60\x04HIGH"
    )


def test_a_tag_does_not_apply_to_values_outside_its_python_type():
    assert hessian2.dumps("text", type=hessian2.Instant) == b"\x04text"


@pytest.mark.parametrize(
    ("value", "tp", "expected"),
    [
        (
            collections.defaultdict(int, {"a": 1}),
            collections.defaultdict[str, hessian2.Long],
            b"H\x01a\xe1Z",
        ),
        (
            collections.OrderedDict(a=1),
            collections.OrderedDict[str, hessian2.Long],
            b"H\x01a\xe1Z",
        ),
    ],
    ids=repr,
)
def test_tags_apply_to_container_subclasses(
    value: Any, tp: Any, expected: bytes
) -> None:
    assert hessian2.dumps(value, type=tp) == expected


@pytest.mark.parametrize(
    ("annotations", "expected"),
    [
        ([list[int], list[hessian2.Long]], b"y\x91y\xe1"),
        ([list[hessian2.Long], list[int]], b"y\xe1Q\x90"),
        ([list[hessian2.Long], list[hessian2.Long]], b"y\xe1Q\x90"),
    ],
    ids=["new-tags", "untagged", "same-tags"],
)
def test_list_references_reuse_only_a_version_that_has_the_requested_tags(
    annotations, expected
):
    shared = [1]

    assert hessian2.dumps_many([shared, shared], types=annotations) == expected


def test_each_tagged_list_version_keeps_its_own_reference_identity():
    shared = [1]
    annotations = [list[int], list[hessian2.Long], list[hessian2.Long], None]
    stream = hessian2.dumps_many([shared] * 4, types=annotations)

    first, copy, again, plain = hessian2.loads_many(stream)

    assert stream == b"y\x91y\xe1Q\x91Q\x90"
    assert plain is first and again is copy
    assert copy is not first


def test_a_map_seen_under_other_value_tags_is_written_again():
    shared = {"a": 1}

    assert (
        hessian2.dumps_many(
            [shared, shared], types=[dict[str, int], dict[str, hessian2.Long]]
        )
        == b"H\x01a\x91ZH\x01a\xe1Z"
    )


def test_a_tagged_copy_of_a_cyclic_list_keeps_the_outer_reference():
    value = []
    value.append(value)

    data = hessian2.dumps(value, type=list[list[hessian2.Long]])

    decoded = hessian2.loads(data)

    assert data == b"yyQ\x90"
    assert decoded[0] is not decoded
    assert decoded[0][0] is decoded


def test_a_union_member_that_stops_applying_does_not_panic() -> None:
    class Shifty(tuple):  # type: ignore[type-arg]
        calls = 0

        def __len__(self) -> int:
            Shifty.calls += 1
            return 1 if Shifty.calls == 1 else 2

    value = Shifty((1, 2))

    data = hessian2.dumps(value, type=tuple[hessian2.Long] | dict[str, hessian2.Long])

    assert same(hessian2.loads(data), [1, 2])
