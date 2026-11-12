"""Declared classes, recursive registration, Java type tags, and java_name."""

from __future__ import annotations

import base64
import collections
import dataclasses
import datetime as dt
import enum
import gc
import json
import sys
import typing
import weakref
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple, TypedDict
from uuid import UUID

import attrs
import hessian2
import pytest
from hessian2 import Codec, Converter, DecodeError, EncodeError, HessianObject

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "java.json").read_text(encoding="utf-8")
)
UTC = dt.UTC
PLUS8 = dt.timezone(dt.timedelta(hours=8))


def fixture(name: str, writer: str) -> bytes:
    case = FIXTURES[name]
    return base64.b64decode(case[writer] or case["caucho"])


@hessian2.java_class("com.example.Address")
@dataclasses.dataclass
class Address:
    city: str
    postal_code: str = ""


@hessian2.java_class("com.example.Customer")
@dataclasses.dataclass
class Customer:
    user_name: str
    home: Address | None = None
    others: list[Address] = dataclasses.field(default_factory=list)
    friends: list[Customer] = dataclasses.field(default_factory=list)


# --- declarations ------------------------------------------------------------


def test_declared_classes_encode_anywhere_and_decode_only_when_registered() -> None:
    value = Customer("ann", Address("Hangzhou", "310000"))
    data = hessian2.dumps(value)
    assert b"com.example.Customer" in data and b"userName" in data
    assert b"postalCode" in data
    with pytest.raises(DecodeError, match="not registered"):
        hessian2.loads(data)
    codec = Codec()
    codec.dumps(value)
    # Encoding never makes a class decodable.
    with pytest.raises(DecodeError, match="not registered"):
        codec.loads(data)
    codec.register(Customer)
    assert codec.loads(data) == value


def test_declaration_options_and_validation() -> None:
    @hessian2.java_class("Snake", field_naming=None)
    @dataclasses.dataclass
    class Snake:
        user_name: str

    assert b"user_name" in hessian2.dumps(Snake("a"))
    with pytest.raises(TypeError):
        hessian2.java_class(1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        hessian2.java_class("")
    with pytest.raises(ValueError):
        hessian2.java_class("X", field_naming="snake")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        hessian2.java_class("X")(object())  # type: ignore[type-var]


def test_subclasses_are_not_declared_by_inheritance() -> None:
    @dataclasses.dataclass
    class Special(Address):
        floor: int = 0

    with pytest.raises(EncodeError, match="Special has no Hessian form"):
        hessian2.dumps(Special("x"))
    with pytest.raises(TypeError, match="no Java class name"):
        Codec().register(Special)


def test_declaration_survives_class_rebuilding_decorators() -> None:
    @attrs.define
    @hessian2.java_class("Inner")
    class Inner:
        x: int

    @hessian2.java_class("Slotted")
    @dataclasses.dataclass(slots=True, frozen=True)
    class Slotted:
        inner: Inner

    codec = Codec()
    codec.register(Slotted)
    assert codec.loads(codec.dumps(Slotted(Inner(1)))) == Slotted(Inner(1))


def test_enums_and_named_tuples_can_be_declared() -> None:
    @hessian2.java_class("com.example.Color")
    class Color(enum.Enum):
        RED = 1

    @hessian2.java_class("com.example.Point")
    class Point(NamedTuple):
        x_pos: int
        color: Color

    codec = Codec()
    codec.register(Point)
    data = hessian2.dumps(Point(1, Color.RED))
    assert b"xPos" in data
    assert codec.loads(data) == Point(1, Color.RED)


# --- registration ------------------------------------------------------------


def test_register_follows_field_annotations_and_type_expressions() -> None:
    @hessian2.java_class("Leaf")
    @dataclasses.dataclass
    class Leaf:
        x: int

    @hessian2.java_class("Keyed")
    @dataclasses.dataclass
    class Keyed:
        x: int

    class Holder(TypedDict):
        leaf: Leaf

    @hessian2.java_class("Root")
    @dataclasses.dataclass
    class Root:
        maybe: Leaf | None
        by_key: dict[str, Keyed]
        holder: Holder
        values: typing.Annotated[list[tuple[Leaf, ...]], "meta"]

    codec = Codec()
    codec.register(Root)
    for cls in (Root, Leaf, Keyed):
        assert codec.java_name(cls) == cls.__name__
    value = Root(Leaf(1), {"k": Keyed(2)}, {"leaf": Leaf(3)}, [(Leaf(4),)])
    decoded = codec.loads(codec.dumps(value))
    assert decoded.by_key["k"] == Keyed(2) and decoded.holder["leaf"] == Leaf(3)

    other = Codec()
    other.register(list[Leaf] | Keyed | int)
    assert other.loads(hessian2.dumps([Leaf(1), Keyed(2)])) == [Leaf(1), Keyed(2)]
    other.register(int)
    other.register(Root)
    other.register(Root)


def test_register_reports_classes_without_a_java_name() -> None:
    @dataclasses.dataclass
    class Plain:
        x: int

    @hessian2.java_class("Uses")
    @dataclasses.dataclass
    class Uses:
        plain: list[Plain]

    with pytest.raises(TypeError, match=r"Uses.plain refers to .*Plain"):
        Codec().register(Uses)
    with pytest.raises(TypeError, match="pass name="):
        Codec().register(Plain, field_naming=None)
    codec = Codec()
    codec.register(Plain, name="com.example.Plain")
    codec.register(Uses)
    assert codec.loads(codec.dumps(Uses([Plain(1)]))) == Uses([Plain(1)])
    assert codec.java_name(Plain) == "com.example.Plain"
    with pytest.raises(TypeError, match="no Java class name"):
        hessian2.java_name(Plain)


def test_register_arguments_must_agree_with_declarations() -> None:
    codec = Codec()
    with pytest.raises(ValueError, match="declared as 'com.example.Address'"):
        codec.register(Address, name="Other")
    with pytest.raises(TypeError, match="field naming from @java_class"):
        codec.register(Address, field_naming=None)
    codec.register(Address, name="com.example.Address")
    with pytest.raises(TypeError, match="need a class"):
        codec.register(list[Address], name="X")
    with pytest.raises(TypeError, match="name="):
        codec.register(Converter(Address, vars, lambda f: Address(**f)))  # type: ignore[call-overload]
    with pytest.raises(TypeError, match="to_fields"):
        codec.register(
            Converter(Address, vars, lambda f: Address(**f)),
            name="A",
            field_naming=None,  # type: ignore[call-overload]
        )


def test_local_classes_resolve_names_defined_before_them() -> None:
    # This module uses string annotations, as `from __future__` makes them.
    @hessian2.java_class("Early")
    @dataclasses.dataclass
    class Early:
        x: int

    @hessian2.java_class("Late")
    @dataclasses.dataclass
    class Late:
        early: Early
        later: Later | None = None

    @hessian2.java_class("Later")
    @dataclasses.dataclass
    class Later:
        x: int

    @hessian2.java_class("Self")
    @dataclasses.dataclass
    class Self:
        early: Early
        children: list[Self]

    codec = Codec()
    codec.register(Self)
    assert codec.loads(codec.dumps(Self(Early(1), []))) == Self(Early(1), [])
    with pytest.raises(TypeError, match="name 'Later' is not defined"):
        codec.register(Late)
    with pytest.raises(EncodeError, match="name 'Later' is not defined"):
        hessian2.dumps(Late(Early(1)))
    assert Later(1).x == 1


def test_generic_dataclasses_resolve_their_type_parameters() -> None:
    T = typing.TypeVar("T")

    @hessian2.java_class("Box")
    @dataclasses.dataclass
    class Box(typing.Generic[T]):
        item: T

    codec = Codec()
    codec.register(Box)
    assert codec.loads(codec.dumps(Box(1))) == Box(1)


# --- tags ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "tp", "expected"),
    [
        (5, hessian2.Long, b"\xe5"),
        (5, int, b"\x95"),
        ([1, 2], list[hessian2.Long], b"\x7a\xe1\xe2"),
        ((1, 2), tuple[hessian2.Long, int], b"\x7a\xe1\x92"),
        ((1, 2), tuple[hessian2.Long, ...], b"\x7a\xe1\xe2"),
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


def test_typed_dict_keys_and_dumps_many_types() -> None:
    class Extra(TypedDict):
        count: hessian2.Long
        name: str

    extra: Extra = {"count": 1, "name": "x"}
    assert hessian2.dumps(extra, type=Extra) == b"H\x05count\xe1\x04name\x01xZ"
    stream = hessian2.dumps_many(["m", 1, 2], types=[str, None, hessian2.Long])
    assert stream == b"\x01m\x91\xe2"
    assert hessian2.loads_many(stream) == ["m", 1, 2]
    with pytest.raises(ValueError, match="types has 1"):
        hessian2.dumps_many([1, 2], types=[None])
    with pytest.raises(ValueError, match="types has 2"):
        hessian2.dumps_many([1], types=[None, None])


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
    assert codec.loads(data) == payment


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
    assert hessian2.dumps(hessian2.loads(java), type=tag) == java


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
    assert hessian2.dumps(hessian2.loads(java), type=hessian2.BigInteger) == java


def test_tags_reject_values_the_java_type_cannot_hold() -> None:
    with pytest.raises(EncodeError, match="does not fit in a Java long"):
        hessian2.dumps(2**63, type=hessian2.Long)
    with pytest.raises(EncodeError, match="naive datetime"):
        hessian2.dumps(dt.datetime(2026, 1, 1), type=hessian2.Instant)
    fractional = dt.timezone(dt.timedelta(seconds=1, microseconds=5))
    with pytest.raises(EncodeError, match="whole seconds"):
        hessian2.dumps(
            dt.datetime(2026, 1, 1, tzinfo=fractional), type=hessian2.OffsetDateTime
        )

    class Custom(dt.tzinfo):
        def utcoffset(self, value: dt.datetime | None) -> dt.timedelta:
            return dt.timedelta(hours=1)

    with pytest.raises(EncodeError, match="ZoneInfo"):
        hessian2.dumps(
            dt.datetime(2026, 1, 1, tzinfo=Custom()), type=hessian2.ZonedDateTime
        )
    zoned = hessian2.dumps(
        dt.datetime(2026, 1, 1, tzinfo=PLUS8), type=hessian2.ZonedDateTime
    )
    assert zoned.endswith(b"\x06+08:00")


def test_tags_leave_values_of_registered_types_alone() -> None:
    class Level(enum.IntEnum):
        HIGH = 3

    codec = Codec()
    codec.register(Level, name="Level")
    assert codec.dumps(Level.HIGH, type=hessian2.Long) == codec.dumps(Level.HIGH)
    assert hessian2.dumps("text", type=hessian2.Instant) == b"\x04text"


# --- java_name -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tp", "expected"),
    [
        (Customer, "com.example.Customer"),
        (Customer | None, "com.example.Customer"),
        (typing.Annotated[Customer, "meta"], "com.example.Customer"),
        (str, "java.lang.String"),
        (bytes, "[B"),
        (Decimal, "java.math.BigDecimal"),
        (UUID, "java.util.UUID"),
        (dt.date, "java.time.LocalDate"),
        (dt.time, "java.time.LocalTime"),
        (dt.timedelta, "java.time.Duration"),
        (hessian2.BigInteger, "java.math.BigInteger"),
        (hessian2.Instant, "java.time.Instant"),
        (hessian2.OffsetDateTime, "java.time.OffsetDateTime"),
        (hessian2.ZonedDateTime, "java.time.ZonedDateTime"),
        (hessian2.Long, None),
        (int, None),
        (bool, None),
        (float, None),
        (dt.datetime, None),
        (list[Customer], None),
        (dict, None),
        (Customer | Address, None),
        (Any, None),
        (object, None),
        (type(None), None),
        (HessianObject, None),
    ],
    ids=repr,
)
def test_java_name(tp: Any, expected: str | None) -> None:
    assert hessian2.java_name(tp) == expected


def test_java_name_sees_this_codecs_registrations() -> None:
    codec = Codec()
    codec.register(
        Converter(Decimal, lambda d: {"v": str(d)}, lambda f: Decimal(f["v"])),
        name="com.example.Money",
        replace=True,
    )
    assert codec.java_name(Decimal) == "com.example.Money"
    assert hessian2.java_name(Decimal) == "java.math.BigDecimal"
    with pytest.raises(TypeError, match="not a type"):
        codec.java_name("Customer")


# --- per-call options and user errors -----------------------------------------


def test_unknown_class_can_be_overridden_per_call() -> None:
    data = hessian2.dumps(HessianObject("com.example.Unknown", {"x": 1}))
    codec = Codec()
    with pytest.raises(DecodeError):
        codec.loads(data)
    assert codec.loads(data, unknown_class="dict") == {"x": 1}
    assert codec.loads_many(data, unknown_class="hessian_object")[0].fields == {"x": 1}
    with pytest.raises(ValueError, match="unknown_class"):
        codec.loads(data, unknown_class="raw")  # type: ignore[arg-type]


def test_user_value_and_type_errors_become_codec_errors() -> None:
    @hessian2.java_class("Checked")
    @dataclasses.dataclass
    class Checked:
        x: int

        def __post_init__(self) -> None:
            if self.x < 0:
                raise ValueError("x must not be negative")

    codec = Codec()
    codec.register(Checked)
    with pytest.raises(DecodeError, match="x must not be negative") as error:
        codec.loads(hessian2.dumps(HessianObject("Checked", {"x": -1})))
    assert type(error.value.__cause__) is ValueError

    class Money:
        pass

    def broken(money: Money) -> dict[str, Any]:
        raise TypeError("cannot convert")

    codec.register(Converter(Money, broken, Money), name="Money")
    with pytest.raises(EncodeError, match="cannot convert") as encode_error:
        codec.dumps(Money())
    assert type(encode_error.value.__cause__) is TypeError

    @hessian2.java_class("Buggy")
    @dataclasses.dataclass
    class Buggy:
        x: int

        def __post_init__(self) -> None:
            raise AttributeError("bug")

    codec.register(Buggy)
    with pytest.raises(AttributeError, match="bug"):
        codec.loads(hessian2.dumps(HessianObject("Buggy", {"x": 1})))


# --- audit regressions ---------------------------------------------------------


def test_register_follows_subscripted_generic_classes() -> None:
    T = typing.TypeVar("T")

    @hessian2.java_class("Box")
    @dataclasses.dataclass
    class Box(typing.Generic[T]):
        item: T

    codec = Codec()
    codec.register(list[Box[Address]])
    value = [Box(Address("Hangzhou"))]
    assert codec.loads(codec.dumps(value)) == value
    assert codec.java_name(Box[Address]) == "Box"
    assert codec.java_name(Box[Address] | None) == "Box"
    with pytest.raises(TypeError, match="do not apply"):
        hessian2.dumps(Box(1), type=Box[hessian2.Long])


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


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
def test_subscripted_type_aliases_substitute_their_arguments() -> None:
    T = typing.TypeVar("T")
    pair = typing.TypeAliasType("Pair", list[T], type_params=(T,))
    same = typing.TypeAliasType("Same", T, type_params=(T,))
    assert hessian2.dumps([1], type=pair[hessian2.Long]) == b"\x79\xe1"
    assert hessian2.dumps(1, type=same[hessian2.Long]) == b"\xe1"


@pytest.mark.parametrize(
    "tp",
    [
        hessian2.Long | hessian2.BigInteger,
        hessian2.Instant | hessian2.ZonedDateTime,
        list[hessian2.Long] | tuple[hessian2.BigInteger, ...],
        dict[str, hessian2.Long] | dict[hessian2.Long, str],
    ],
    ids=repr,
)
def test_unions_reject_tags_that_compete_for_a_value(tp: Any) -> None:
    with pytest.raises(TypeError, match="same values"):
        hessian2.dumps(None, type=tp)


def test_encoding_a_declared_class_registers_nothing() -> None:
    @hessian2.java_class("Temp")
    @dataclasses.dataclass
    class Temp:
        count: hessian2.Long

    codec = Codec()
    assert codec.dumps(Temp(1)) == codec.dumps(Temp(1))
    assert b"\xe1" in codec.dumps(Temp(1))
    assert codec.java_name(Temp) == "Temp"
    with pytest.raises(DecodeError, match="not registered"):
        codec.loads(codec.dumps(Temp(1)))
    ref = weakref.ref(Temp)
    del Temp
    gc.collect()
    assert ref() is None


def test_failed_registration_registers_nothing() -> None:
    @dataclasses.dataclass
    class Child:
        x: int = 0

    @hessian2.java_class("Parent")
    @dataclasses.dataclass
    class Parent:
        child: Child | None = None

    codec = Codec()
    with pytest.raises(TypeError, match=r"refers to .*Child, which"):
        codec.register(Parent)
    with pytest.raises(DecodeError, match="not registered"):
        codec.loads(hessian2.dumps(HessianObject("Parent", {"child": None})))

    @hessian2.java_class("Taken")
    @dataclasses.dataclass
    class Other:
        pass

    @hessian2.java_class("Holder")
    @dataclasses.dataclass
    class Holder:
        other: Other | None = None

    @dataclasses.dataclass
    class Squatter:
        pass

    codec.register(Squatter, name="Taken")
    with pytest.raises(ValueError, match="already registered"):
        codec.register(Holder)
    assert codec.java_name(Holder) == "Holder"
    with pytest.raises(DecodeError, match="not registered"):
        codec.loads(hessian2.dumps(HessianObject("Holder", {"other": None})))


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
def test_type_aliases_substitute_parameters_by_identity() -> None:
    A, B, C = typing.TypeVar("A"), typing.TypeVar("B"), typing.TypeVar("C")
    swapped = typing.TypeAliasType("Swapped", tuple[B, A], type_params=(A, B))
    unused = typing.TypeAliasType("Unused", list[A], type_params=(A, C))
    assert hessian2.dumps((1, 2), type=swapped[int, hessian2.Long]) == b"\x7a\xe1\x92"
    assert hessian2.dumps([1], type=unused[hessian2.Long, str]) == b"\x79\xe1"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
def test_recursive_annotations_terminate() -> None:
    ns: dict[str, Any] = {"hessian2": hessian2}
    exec(
        "type Json = dict[str, Json] | list[Json] | int\n"
        "type Tree = dict[str, Tree] | hessian2.Long\n",
        ns,
    )
    assert hessian2.dumps({"x": [1]}, type=ns["Json"]) == hessian2.dumps({"x": [1]})
    Codec().register(ns["Json"])
    # Tags below the first level would be lost, so they are refused.
    with pytest.raises(TypeError, match="contains itself"):
        hessian2.dumps(1, type=ns["Tree"])


def test_recursive_typed_dicts_refuse_tags_they_cannot_apply() -> None:
    class Node(TypedDict):
        value: hessian2.Long
        children: list[Node]

    with pytest.raises(TypeError, match="contains itself"):
        hessian2.dumps({"value": 1, "children": []}, type=Node)


def test_callback_errors_take_the_running_operations_class() -> None:
    @dataclasses.dataclass
    class P:
        a: int

    decoding = Codec()
    decoding.register(
        Converter(
            P,
            to_fields=lambda p: {"a": p.a},
            from_fields=lambda f: hessian2.dumps(object()),
        ),
        name="P",
    )
    with pytest.raises(DecodeError) as caught:
        decoding.loads(decoding.dumps(P(1)))
    assert isinstance(caught.value.__cause__, EncodeError)

    encoding = Codec()
    encoding.register(
        Converter(
            P,
            to_fields=lambda p: hessian2.loads(b"\xff"),
            from_fields=lambda f: P(**f),
        ),
        name="P",
    )
    with pytest.raises(EncodeError) as caught2:
        encoding.dumps(P(1))
    assert isinstance(caught2.value.__cause__, DecodeError)


def test_a_container_seen_under_other_tags_is_written_again() -> None:
    xs = [1]
    long_list = list[hessian2.Long]
    # Different tags: a second copy; same tags or none: a reference.
    assert hessian2.dumps_many([xs, xs], types=[list[int], long_list]) == (
        b"\x79\x91\x79\xe1"
    )
    assert hessian2.dumps_many([xs, xs], types=[long_list, list[int]]) == (
        b"\x79\xe1\x51\x90"
    )
    assert hessian2.dumps_many([xs, xs], types=[long_list, long_list]) == (
        b"\x79\xe1\x51\x90"
    )
    # Each version is a reference target of its own, numbered as Java numbers it.
    stream = hessian2.dumps_many(
        [xs, xs, xs, xs], types=[list[int], long_list, long_list, None]
    )
    assert stream == b"\x79\x91\x79\xe1\x51\x91\x51\x90"
    first, copy, again, plain = hessian2.loads_many(stream)
    assert plain is first and again is copy and copy is not first

    shared: dict[str, int] = {"a": 1}
    stream = hessian2.dumps_many(
        [shared, shared], types=[dict[str, int], dict[str, hessian2.Long]]
    )
    assert stream == b"H\x01a\x91ZH\x01a\xe1Z"

    # Copying a container that contains itself ends where the tags do.
    cycle: list[Any] = []
    cycle.append(cycle)
    data = hessian2.dumps(cycle, type=list[list[hessian2.Long]])
    assert data == b"\x79\x79\x51\x90"
    outer = hessian2.loads(data)
    assert outer[0] is not outer and outer[0][0] is outer


def test_replace_needs_a_class() -> None:
    with pytest.raises(TypeError, match="replace= need a class"):
        Codec().register(list[Address], replace=True)


def test_a_union_member_that_stops_applying_does_not_panic() -> None:
    class Shifty(tuple):  # type: ignore[type-arg]
        calls = 0

        def __len__(self) -> int:
            Shifty.calls += 1
            return 1 if Shifty.calls == 1 else 2

    value = Shifty((1, 2))
    data = hessian2.dumps(value, type=tuple[hessian2.Long] | dict[str, hessian2.Long])
    assert hessian2.loads(data) == [1, 2]


@pytest.mark.parametrize(
    "tp",
    [
        list[int] | tuple[hessian2.Long, ...],
        int | hessian2.Long,
        dict[str, int] | dict[str, hessian2.Long],
    ],
    ids=repr,
)
def test_unions_reject_tags_an_untagged_member_competes_with(tp: Any) -> None:
    with pytest.raises(TypeError, match="same values as another member"):
        hessian2.dumps([1], type=tp)


def test_registration_resolves_each_class_where_it_was_defined() -> None:
    def factory() -> tuple[type, Any]:
        @hessian2.java_class("Leaf")
        @dataclasses.dataclass
        class Leaf:
            n: int

        class Fields(TypedDict):
            leaf: Leaf

        @hessian2.java_class("Root")
        @dataclasses.dataclass
        class Root:
            fields: Fields

        return Root, Root({"leaf": Leaf(1)})

    root, value = factory()
    codec = Codec()
    codec.register(root)
    assert codec.loads(codec.dumps(value)) == value


def test_java_name_agrees_with_the_encoder() -> None:
    codec = Codec()
    codec.register(
        Converter(int, to_fields=lambda v: {"v": v}, from_fields=lambda f: f["v"]),
        name="custom.Int",
        replace=True,
    )
    assert b"custom.Int" in codec.dumps(5, type=hessian2.BigInteger)
    assert codec.java_name(hessian2.BigInteger) == "custom.Int"

    class Money(Decimal):
        pass

    with pytest.raises(EncodeError):
        hessian2.dumps(Money("1"))
    with pytest.raises(TypeError, match="no Java class name"):
        hessian2.java_name(Money)
    assert hessian2.java_name(memoryview) == "[B"


def test_tags_reach_through_qualifiers_and_annotated_unions() -> None:
    class Extra(TypedDict):
        count: typing.NotRequired[hessian2.Long]

    assert hessian2.dumps({"count": 1}, type=Extra) == b"H\x05count\xe1Z"
    # Annotated keeps a union from merging into the outer one.
    nested = typing.Annotated[hessian2.Long | list[hessian2.Long], "meta"] | str
    assert hessian2.dumps([1], type=nested) == b"\x79\xe1"


class Plain(TypedDict):
    count: int


@pytest.mark.parametrize(
    "tp",
    [
        typing.Annotated[int, "meta"] | hessian2.Long,
        Plain | dict[str, hessian2.Long],
        dt.datetime | hessian2.Instant,
    ],
    ids=repr,
)
def test_unions_reject_tags_competing_with_any_untagged_member(tp: Any) -> None:
    with pytest.raises(TypeError, match="same values as another member"):
        hessian2.dumps(None, type=tp)


def test_register_follows_annotated_and_typing_unions() -> None:
    codec = Codec()
    codec.register(typing.Annotated[typing.Optional[Address], "meta"])  # noqa: UP045
    assert codec.loads(codec.dumps(Address("x"))) == Address("x")
