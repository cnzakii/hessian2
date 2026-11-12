from __future__ import annotations

import collections
import dataclasses
import datetime
import enum
import math
import struct
import typing
import uuid
from decimal import Decimal
from typing import Any

import attrs
import hessian2
import pytest
from hessian2 import Codec, Converter, DecodeError, EncodeError, HessianObject

UTC = datetime.UTC


def roundtrip(value: Any, codec: Codec | None = None) -> Any:
    codec = codec or Codec()
    return codec.loads(codec.dumps(value))


# --- built-in types ----------------------------------------------------------

ROUND_TRIPS = [
    None,
    True,
    False,
    0,
    -16,
    47,
    -(2**31),
    2**31 - 1,
    2**31,
    -(2**63),
    2**63 - 1,
    0.0,
    1.0,
    -128.0,
    32767.0,
    0.001,
    3.14159,
    1e300,
    -1e-300,
    math.inf,
    "",
    "hello",
    "中文",
    "a😀b",
    "x\ud83dy",
    "\udc00",
    "a" * 0x8000 + "😀",
    "😀" * 20000,
    b"",
    b"\x00\xff" * 50000,
    [],
    [1, "two", [3.0, None]],
    {},
    {"a": 1, 2: "b", 3.5: [None]},
    {("a", 1): "tuple key"},
    datetime.datetime(2024, 2, 29, 23, 59, 59, 123000, tzinfo=UTC),
    datetime.datetime(1, 1, 1, tzinfo=UTC),
    datetime.datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
    datetime.datetime(1969, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
    Decimal("-123.4500"),
    Decimal("1E+3"),
    Decimal("0E-10"),
    uuid.UUID("123e4567-e89b-12d3-a456-426614174000"),
    uuid.UUID(int=0),
    uuid.UUID(int=2**128 - 1),
]


@pytest.mark.parametrize("value", ROUND_TRIPS, ids=lambda v: repr(v)[:40])
def test_round_trip(value: Any) -> None:
    assert roundtrip(value) == value


def test_negative_zero_is_written_as_java_writes_it() -> None:
    # Java's integral test (int) -0.0 == -0.0 holds, so it writes 0x5b.
    assert hessian2.dumps(-0.0) == b"\x5b"


def test_nan_is_written_canonically_as_java_writes_it() -> None:
    payload = struct.unpack(">d", bytes.fromhex("7ff8000000000042"))[0]
    assert hessian2.dumps(payload) == b"D" + bytes.fromhex("7ff8000000000000")


def test_nan_round_trips() -> None:
    assert math.isnan(roundtrip(math.nan))


@pytest.mark.parametrize(
    ("value", "converted"),
    [
        ((1, 2), [1, 2]),
        ({3}, [3]),
        (frozenset({4}), [4]),
        (bytearray(b"ab"), b"ab"),
        (memoryview(b"cd"), b"cd"),
        (
            datetime.datetime(
                2024, 1, 1, 8, tzinfo=datetime.timezone(datetime.timedelta(hours=8))
            ),
            datetime.datetime(2024, 1, 1, tzinfo=UTC),
        ),
        (
            datetime.datetime(2024, 1, 1, 0, 0, 0, 999, tzinfo=UTC),
            datetime.datetime(2024, 1, 1, tzinfo=UTC),
        ),
    ],
    ids=[
        "tuple",
        "set",
        "frozenset",
        "bytearray",
        "memoryview",
        "offset",
        "microseconds",
    ],
)
def test_converting_round_trip(value: Any, converted: Any) -> None:
    assert roundtrip(value) == converted


class Flag(int):
    pass


class Text(str):
    pass


class Ratio(float):
    pass


class Bag(dict):  # type: ignore[type-arg]
    pass


class Items(list):  # type: ignore[type-arg]
    pass


@pytest.mark.parametrize(
    "value", [Flag(3), Text("t"), Ratio(0.5), Bag(a=1), Items([1])], ids=type
)
def test_subclasses_encode_as_their_base(value: Any) -> None:
    assert roundtrip(value) == value


@pytest.mark.parametrize(
    ("value", "wire"),
    [
        (None, b"N"),
        (True, b"T"),
        (0, b"\x90"),
        (-16, b"\x80"),
        (2047, b"\xcf\xff"),
        (262143, b"\xd7\xff\xff"),
        (2**31, b"L\x00\x00\x00\x00\x80\x00\x00\x00"),
        (0.0, b"\x5b"),
        (0.001, b"\x5f\x00\x00\x00\x01"),
        ("", b"\x00"),
        ("😀", b"\x02\xed\xa0\xbd\xed\xb8\x80"),
        (b"", b"\x20"),
        ([], b"\x78"),
        ({}, b"HZ"),
        # Specification §4.3.2 gives x4b x4b x92 x0b xa0, which is not this
        # minute count.
        (datetime.datetime(1998, 5, 8, 9, 51, tzinfo=UTC), b"\x4b\x00\xe3\x83\x8f"),
        (
            datetime.datetime(1998, 5, 8, 9, 51, 31, tzinfo=UTC),
            b"\x4a\x00\x00\x00\xd0\x4b\x92\x84\xb8",
        ),
    ],
    ids=repr,
)
def test_wire_form(value: Any, wire: bytes) -> None:
    assert hessian2.dumps(value) == wire


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (Decimal("NaN"), "BigDecimal"),
        (Decimal("-Infinity"), "BigDecimal"),
        (object(), "register"),
        (complex(1, 2), "register"),
    ],
    ids=repr,
)
def test_unencodable(value: Any, message: str) -> None:
    with pytest.raises(EncodeError, match=message):
        hessian2.dumps(value)


def test_error_types() -> None:
    assert issubclass(DecodeError, ValueError)
    assert issubclass(EncodeError, TypeError)


# --- decoding errors ---------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x05abc",
        b"I\x00\x00",
        b"\x40",
        b"Q\x90",
        b"\x60",
        b"C\x01A\x91\x01f\x61",
        b"\x72\x91",
        b"\x79\x90\x90",
        b"\x01\xff",
        b"\x01\xc0\x80",
        b"\x03\xf0\x9f\x98\x80",
        b"H\x90",
        b"J\x7f\xff\xff\xff\xff\xff\xff\xff",
        b"HHZ\x90Z",
    ],
    ids=repr,
)
def test_malformed_input_raises_decode_error(data: bytes) -> None:
    with pytest.raises(DecodeError):
        hessian2.loads(data)


def test_four_byte_utf8_is_accepted() -> None:
    # Java writes surrogate pairs and rejects this form; the specification
    # says UTF-8, so it is accepted.
    assert hessian2.loads(b"\x02\xf0\x9f\x98\x80") == "😀"


def test_trailing_data() -> None:
    with pytest.raises(DecodeError, match="trailing"):
        hessian2.loads(b"\x90\x90")


@pytest.mark.parametrize("buffer", [bytearray(b"\x91"), memoryview(b"\x91")], ids=type)
def test_loads_accepts_buffers(buffer: Any) -> None:
    assert hessian2.loads(buffer) == 1


def test_loads_rejects_non_buffers() -> None:
    with pytest.raises(TypeError):
        hessian2.loads("\x91")  # type: ignore[arg-type]


# --- references --------------------------------------------------------------


def test_shared_values_stay_shared() -> None:
    inner = [1]
    outer = roundtrip([inner, inner, {"x": inner}])
    assert outer[0] is outer[1] is outer[2]["x"]


def test_cycles() -> None:
    items: list[Any] = [1]
    items.append(items)
    mapping: dict[str, Any] = {}
    mapping["self"] = mapping
    decoded_items, decoded_map = roundtrip([items, mapping])
    assert decoded_items[1] is decoded_items
    assert decoded_map["self"] is decoded_map


def test_many_shares_tables_and_references() -> None:
    shared = {"k": 1}
    data = hessian2.dumps_many([shared, shared, "end"])
    first, second, end = hessian2.loads_many(data)
    assert first is second and end == "end"
    assert hessian2.loads_many(b"") == []


def test_list_changing_size_while_encoding() -> None:
    class Growing(list):  # type: ignore[type-arg]
        def __iter__(self):  # type: ignore[no-untyped-def]
            yield from (1, 2, 3)

    with pytest.raises(EncodeError, match="changed size"):
        hessian2.dumps(Growing([1]))


# --- depth -------------------------------------------------------------------


def nested_list(depth: int) -> Any:
    value: Any = None
    for _ in range(depth):
        value = [value]
    return value


def test_default_max_depth() -> None:
    codec = Codec()
    assert codec.max_depth == 256
    assert roundtrip(nested_list(256)) == nested_list(256)
    with pytest.raises(EncodeError, match="max_depth=256"):
        codec.dumps(nested_list(257))
    with pytest.raises(DecodeError, match="max_depth=256"):
        codec.loads(b"\x79" * 257 + b"N")


def test_custom_max_depth() -> None:
    codec = Codec(max_depth=2)
    assert codec.loads(b"\x79\x79N") == [[None]]
    with pytest.raises(DecodeError):
        codec.loads(b"\x79\x79\x79N")
    with pytest.raises(DecodeError):
        codec.loads(b"C\x01A\x91\x01f\x60\x60\x60N")


@pytest.mark.parametrize(
    "kwargs",
    [{"max_depth": 0}, {"unknown_class": "raw"}],
    ids=repr,
)
def test_invalid_codec_options(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Codec(**kwargs)


def test_codec_options_are_readable() -> None:
    codec = Codec(unknown_class="dict", max_depth=9)
    assert (codec.unknown_class, codec.max_depth) == ("dict", 9)


# --- registration ------------------------------------------------------------


def make_codec(**kwargs: Any) -> Codec:
    return Codec(**kwargs)


def test_dataclass_round_trip_and_wire_names() -> None:
    codec = make_codec()

    @dataclasses.dataclass(frozen=True)
    class User:
        user_name: str
        _private_id: int = 0
        tags: list[str] = dataclasses.field(default_factory=list)

    codec.register(User, name="com.example.User")

    user = User("ann", 7, ["a"])
    data = codec.dumps(user)
    assert b"userName" in data and b"_privateId" in data
    assert codec.loads(data) == user
    assert codec.loads(data) is not user


def test_defaults_fill_missing_fields() -> None:
    codec = make_codec()

    @dataclasses.dataclass
    class A:
        a: int
        b: int = 5
        c: list[int] = dataclasses.field(default_factory=lambda: [1])

    codec.register(A, name="A", field_naming=None)

    wire = b"C\x01A\x91\x01a\x60\x91"
    first, second = codec.loads(wire), codec.loads(wire)
    assert first == A(1, 5, [1])
    assert first.c is not second.c


def test_missing_required_field() -> None:
    codec = make_codec()

    @dataclasses.dataclass
    class A:
        a: int
        b: int

    codec.register(A, name="A", field_naming=None)

    with pytest.raises(DecodeError, match='missing field "b"'):
        codec.loads(b"C\x01A\x91\x01a\x60\x91")


def test_unknown_wire_fields_are_ignored() -> None:
    codec = make_codec()

    @dataclasses.dataclass
    class A:
        a: int

    codec.register(A, name="A", field_naming=None)

    # Field "z" holds an object of an unregistered class.
    wire = b"C\x01A\x92\x01z\x01a\x60C\x01B\x90\x61\x91"
    assert codec.loads(wire) == A(1)


def test_post_init_runs_after_decoding() -> None:
    codec = make_codec()
    seen = []

    @dataclasses.dataclass
    class A:
        a: int

        def __post_init__(self) -> None:
            seen.append(self.a)

    codec.register(A, name="A", field_naming=None)

    codec.loads(codec.dumps(A(3)))
    assert seen == [3, 3]


def test_init_is_not_called() -> None:
    codec = make_codec()

    @dataclasses.dataclass(init=False)
    class A:
        a: int

        def __init__(self) -> None:
            raise AssertionError("decoding must not call __init__")

    codec.register(A, name="A", field_naming=None)

    obj = A.__new__(A)
    obj.a = 1
    assert codec.loads(codec.dumps(obj)).a == 1


def test_slots_dataclass() -> None:
    codec = make_codec()

    @dataclasses.dataclass(slots=True, frozen=True)
    class S:
        x: int

    codec.register(S, name="S", field_naming=None)

    assert roundtrip(S(1), codec) == S(1)


def test_attrs_class() -> None:
    codec = make_codec()

    @attrs.frozen
    class P:
        x: int
        y: list[int] = attrs.Factory(list)

        def __attrs_post_init__(self) -> None:
            object.__setattr__(self, "x", self.x * 1)

    codec.register(P, name="P", field_naming=None)

    assert roundtrip(P(1, [2]), codec) == P(1, [2])
    assert codec.loads(b"C\x01P\x91\x01x\x60\x91") == P(1, [])


def test_attrs_takes_self_factory_is_rejected() -> None:
    @attrs.define
    class Q:
        x: int = attrs.Factory(lambda self: 1, takes_self=True)

    with pytest.raises(TypeError, match="takes_self"):
        make_codec().register(Q, name="Q", field_naming=None)


def test_enum() -> None:
    codec = make_codec()

    class Color(enum.Enum):
        RED = 1
        GREEN = 2

    codec.register(Color, name="com.example.Color", field_naming=None)

    data = codec.dumps([Color.RED, Color.GREEN, Color.RED])
    assert data.count(b"RED") == 1
    assert codec.loads(data) == [Color.RED, Color.GREEN, Color.RED]
    with pytest.raises(DecodeError, match="no member"):
        codec.loads(b"C\x11com.example.Color\x91\x04name\x60\x04BLUE")


def test_converter() -> None:
    codec = make_codec()

    class Point:
        def __init__(self, x: int, y: int) -> None:
            self.x, self.y = x, y

    converter = Converter(
        Point, lambda p: {"x": p.x, "y": p.y}, lambda f: Point(f["x"], f["y"])
    )
    assert repr(converter) == f"Converter({Point.__qualname__})"
    codec.register(converter, name="Point")
    point = codec.loads(codec.dumps(Point(1, 2)))
    assert (point.x, point.y) == (1, 2)


@pytest.mark.parametrize("codec", [hessian2, Codec()], ids=["module", "codec"])
def test_public_methods_accept_keywords(codec: Any) -> None:
    assert codec.loads(data=codec.dumps(obj=7)) == 7
    assert codec.loads_many(data=codec.dumps_many(values=[7, None])) == [7, None]


@pytest.mark.parametrize(
    "value", [None, True, 7, 2**100, 1.5, "hello", [1, 2], {"x": 1}]
)
def test_converter_overrides_native_fast_paths(value: Any) -> None:
    codec = Codec()
    calls = []

    def to_fields(obj: Any) -> dict[str, Any]:
        calls.append(obj)
        return {"payload": hessian2.dumps(obj)}

    codec.register(
        Converter(type(value), to_fields, lambda f: hessian2.loads(f["payload"])),
        name="Converted",
        replace=True,
    )
    first, second = codec.loads_many(codec.dumps_many([value, value]))
    assert first == value
    assert first is second
    assert calls == [value]
    assert hessian2.loads(hessian2.dumps(value)) == value


def test_replacing_integer_converter_restores_native_encoding() -> None:
    codec = Codec()
    codec.register(
        Converter(int, lambda n: {"text": str(n)}, lambda f: int(f["text"])),
        name="java.math.BigInteger",
        replace=True,
    )
    for value in (7, 2**100):
        assert codec.loads(codec.dumps(value)) == value
    codec.register(
        Converter(complex, lambda n: {"text": str(n)}, lambda f: complex(f["text"])),
        name="java.math.BigInteger",
        replace=True,
    )
    assert codec.dumps(7) == hessian2.dumps(7)
    assert codec.dumps(2**100) == hessian2.dumps(2**100)


def test_converter_cycle_fails_during_decoding() -> None:
    class Node:
        def __init__(self, next: Any = None) -> None:
            self.next = next

    node = Node()
    node.next = node
    codec = Codec()
    codec.register(Converter(Node, vars, lambda f: Node(f["next"])), name="Node")
    with pytest.raises(DecodeError, match="still being decoded"):
        codec.loads(codec.dumps(node))


def test_converter_must_return_str_keyed_dict() -> None:
    codec = make_codec()
    codec.register(Converter(complex, lambda c: [1], lambda f: 0), name="L")
    with pytest.raises(EncodeError, match="dict"):
        codec.dumps(1j)
    codec = make_codec()
    codec.register(Converter(complex, lambda c: {1: 2}, lambda f: 0), name="K")
    with pytest.raises(EncodeError, match="str"):
        codec.dumps(1j)


def test_builtins_can_be_replaced() -> None:
    codec = make_codec()
    codec.register(
        Converter(uuid.UUID, lambda u: {"text": u.hex}, lambda f: uuid.UUID(f["text"])),
        name="java.util.UUID",
    )
    value = uuid.uuid4()
    data = codec.dumps(value)
    assert b"text" in data and codec.loads(data) == value
    # Other codecs keep the built-in form.
    assert b"value" in Codec().dumps(value)


def test_uuid_decodes_caucho_bean_form() -> None:
    value = uuid.UUID("123e4567-e89b-12d3-a456-426614174000")
    most, least = value.int >> 64, value.int & (2**64 - 1)

    def long(n: int) -> bytes:
        return b"L" + (n - 2**64 if n >= 2**63 else n).to_bytes(8, "big", signed=True)

    wire = (
        b"C\x0ejava.util.UUID\x92\x0bmostSigBits\x0cleastSigBits\x60"
        + long(most)
        + long(least)
    )
    assert hessian2.loads(wire) == value


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("java.math.BigDecimal", "1_000"),
        ("java.math.BigDecimal", "NaN"),
        ("java.math.BigDecimal", " 1"),
        ("java.util.UUID", "123e4567e89b12d3a456426614174000"),
        ("java.util.UUID", "{123e4567-e89b-12d3-a456-426614174000}"),
    ],
)
def test_invalid_builtin_values(name: str, text: str) -> None:
    def s(value: str) -> bytes:
        return bytes([len(value)]) + value.encode()

    with pytest.raises(DecodeError):
        hessian2.loads(b"C" + s(name) + b"\x91" + s("value") + b"\x60" + s(text))


def test_registration_rules() -> None:
    codec = make_codec()

    @dataclasses.dataclass
    class A:
        x: int

    codec.register(A, name="first", field_naming=None)
    codec.register(
        A, name="first", field_naming=None
    )  # the same mapping again is allowed
    with pytest.raises(ValueError, match="already registered as 'first'"):
        codec.register(A, name="second", field_naming=None)

    @dataclasses.dataclass
    class B:
        y: int

    with pytest.raises(ValueError, match="'first' is already registered to .*A"):
        codec.register(B, name="first", field_naming=None)
    codec.register(B, name="first", field_naming=None, replace=True)
    codec.register(A, name="second", field_naming=None)  # A is free again
    assert codec.loads(codec.dumps(B(1))) == B(1)
    assert codec.loads(codec.dumps(A(1))) == A(1)

    # replace=True also moves a class to a new name; the old name still decodes.
    codec.register(A, name="third", field_naming=None, replace=True)
    assert b"third" in codec.dumps(A(1))
    assert codec.loads(hessian2.dumps(HessianObject("second", {"x": 2}))) == A(2)


def test_built_in_mappings_need_replace() -> None:
    codec = make_codec()

    @dataclasses.dataclass
    class Money:
        value: str

    with pytest.raises(ValueError, match="built-in mapping"):
        codec.register(Money, name="java.math.BigDecimal", field_naming=None)
    codec.register(Money, name="java.math.BigDecimal", field_naming=None, replace=True)
    assert codec.loads(codec.dumps(Money("1.5"))) == Money("1.5")
    # Decimal no longer has a mapping of its own.
    with pytest.raises(EncodeError):
        codec.dumps(Decimal("1.5"))


def test_register_decorator_passes_replace() -> None:
    codec = make_codec()

    @dataclasses.dataclass
    class P:
        x: int

    codec.register(P, name="P", field_naming=None)

    @dataclasses.dataclass
    class Q:
        x: int

    codec.register(Q, name="P", field_naming=None, replace=True)

    assert codec.loads(codec.dumps(Q(1))) == Q(1)


@pytest.mark.parametrize("cls", [int, object(), dict], ids=repr)
def test_register_rejects_unsupported(cls: Any) -> None:
    with pytest.raises(TypeError):
        make_codec().register(cls, name="X", field_naming=None)


def test_registration_is_per_codec() -> None:
    codec = make_codec()

    @dataclasses.dataclass
    class A:
        x: int

    codec.register(A, name="A", field_naming=None)

    with pytest.raises(EncodeError):
        hessian2.dumps(A(1))


# --- unknown classes ---------------------------------------------------------

UNKNOWN = b"C\x0bcom.x.Thing\x92\x01a\x01b\x60\x91\x02hi"


def test_unknown_class_error() -> None:
    with pytest.raises(DecodeError, match="'com.x.Thing' is not registered"):
        hessian2.loads(UNKNOWN)


def test_unknown_class_object() -> None:
    value = Codec(unknown_class="hessian_object").loads(UNKNOWN)
    assert value == HessianObject("com.x.Thing", {"a": 1, "b": "hi"})
    match value:
        case HessianObject("com.x.Thing", {"a": a}):
            assert a == 1
        case _:
            pytest.fail("pattern did not match")
    assert hessian2.dumps(value) == UNKNOWN


def test_unknown_class_dict() -> None:
    assert Codec(unknown_class="dict").loads(UNKNOWN) == {"a": 1, "b": "hi"}


def test_hessian_object() -> None:
    obj = HessianObject("T", {"x": [1]})
    assert repr(obj) == "HessianObject('T', {'x': [1]})"
    assert obj == HessianObject("T", {"x": [1]})
    assert obj != HessianObject("U", {"x": [1]})
    assert obj != ("T", {"x": [1]})
    assert HessianObject("E").fields == {}
    with pytest.raises(TypeError):
        hash(obj)
    with pytest.raises(AttributeError):
        obj.class_name = "U"  # type: ignore[misc]


def test_self_referencing_unknown_object() -> None:
    wire = b"C\x01N\x91\x04next\x60Q\x90"
    value = Codec(unknown_class="hessian_object").loads(wire)
    assert value.fields["next"] is value


def test_unhashable_key_is_a_decode_error() -> None:
    with pytest.raises(DecodeError, match="unhashable"):
        hessian2.loads(b"HHZ\x90Z")


class Point(typing.NamedTuple):
    x_pos: int
    label: str = "origin"


def test_named_tuple_round_trip() -> None:
    codec = hessian2.Codec()
    codec.register(Point, name="com.example.Point")
    data = codec.dumps(Point(1, "a"))
    assert data.startswith(b"C\x11com.example.Point\x92\x04xPos\x05label")
    decoded = codec.loads(data)
    assert decoded == Point(1, "a")
    assert type(decoded) is Point


def test_named_tuple_defaults_and_identity() -> None:
    codec = hessian2.Codec()
    codec.register(Point, name="com.example.Point", field_naming=None)
    sparse = hessian2.dumps(HessianObject("com.example.Point", {"x_pos": 3}))
    assert codec.loads(sparse) == Point(3)
    with pytest.raises(hessian2.DecodeError, match="missing field"):
        codec.loads(hessian2.dumps(HessianObject("com.example.Point", {"label": "b"})))
    shared = Point(2)
    first, second = codec.loads(codec.dumps([shared, shared]))
    assert first is second


def test_collections_namedtuple() -> None:
    # The factory form, which some code still uses, on purpose.
    Pair = collections.namedtuple("Pair", ["left", "right"], defaults=[None])  # noqa: PYI024
    codec = hessian2.Codec()
    codec.register(Pair, name="com.example.Pair", field_naming=None)
    assert codec.loads(codec.dumps(Pair(1))) == Pair(1, None)
    # An unregistered named tuple is a plain list, as for any tuple.
    assert hessian2.loads(hessian2.dumps(Pair(1, 2))) == [1, 2]


def test_dataclass_on_a_builtin_base_decodes_as_object_new_allows() -> None:
    # object.__new__ cannot lay out a dict subclass, and says so.
    codec = hessian2.Codec()

    @dataclasses.dataclass(eq=False)
    class Bag(dict):
        size: int

    codec.register(Bag, name="com.example.Bag", field_naming=None)

    with pytest.raises(TypeError, match=r"object\.__new__"):
        codec.loads(codec.dumps(Bag(1)))


def test_dataclass_on_an_extension_base_decodes_as_object_new_allows() -> None:
    # struct.Struct is a heap type with its own constructor, which
    # object.__new__ refuses to skip.
    codec = hessian2.Codec()

    @dataclasses.dataclass(eq=False)
    class Packed(struct.Struct):
        size: int

    codec.register(Packed, name="com.example.Packed", field_naming=None)

    wire = hessian2.dumps(HessianObject("com.example.Packed", {"size": 1}))
    with pytest.raises(TypeError, match=r"object\.__new__"):
        codec.loads(wire)


def test_abstract_dataclass_cannot_be_decoded() -> None:
    import abc

    codec = hessian2.Codec()

    @dataclasses.dataclass
    class Shape(abc.ABC):
        sides: int

        @abc.abstractmethod
        def area(self) -> float: ...

    codec.register(Shape, name="com.example.Shape", field_naming=None)

    wire = hessian2.dumps(HessianObject("com.example.Shape", {"sides": 3}))
    with pytest.raises(TypeError, match="abstract"):
        codec.loads(wire)


def test_decoding_runs_data_descriptors() -> None:
    codec = hessian2.Codec()
    seen = []

    @dataclasses.dataclass
    class Temp:
        celsius: float

    codec.register(Temp, name="com.example.Temp", field_naming=None)

    def store(self: Any, value: float) -> None:
        seen.append(value)
        self.__dict__["celsius"] = value

    Temp.celsius = property(lambda self: self.__dict__["celsius"], store)  # type: ignore[assignment]
    decoded = codec.loads(
        hessian2.dumps(HessianObject("com.example.Temp", {"celsius": 1.5}))
    )
    assert decoded.celsius == 1.5
    assert seen == [1.5]


def test_converter_runs_once_per_object_in_a_stream() -> None:
    calls = []

    class Point:
        pass

    codec = Codec()
    codec.register(
        Converter(Point, lambda p: calls.append(p) or {"x": 1}, lambda f: Point()),
        name="Point",
    )
    point = Point()
    first, second = codec.loads(codec.dumps([point, point]))
    assert first is second
    assert calls == [point]


def test_hessian_object_cycles_are_collected() -> None:
    import gc
    import weakref

    class Sentinel:
        pass

    sentinel = Sentinel()
    ref = weakref.ref(sentinel)
    obj = HessianObject("Node", {"sentinel": sentinel})
    obj.fields["self"] = obj
    del obj, sentinel
    gc.collect()
    assert ref() is None


def test_replacing_an_old_decode_alias_preserves_the_current_encoder() -> None:
    codec = Codec()

    @dataclasses.dataclass
    class A:
        value: int

    @dataclasses.dataclass
    class B:
        value: int

    codec.register(A, name="Old", field_naming=None)
    codec.register(A, name="New", field_naming=None, replace=True)
    codec.register(B, name="Old", field_naming=None, replace=True)
    assert codec.loads(codec.dumps(A(1))) == A(1)
    assert codec.loads(codec.dumps(B(2))) == B(2)
    assert b"New" in codec.dumps(A(1))
    assert type(codec.loads(hessian2.dumps(HessianObject("Old", {"value": 2})))) is B


@pytest.mark.parametrize("width", [4, 1024])
def test_field_plan_handles_reordering_missing_and_extra_fields(width: int) -> None:
    codec = Codec()
    record = dataclasses.make_dataclass(
        "Record", [(f"f{i}", int, dataclasses.field(default=-1)) for i in range(width)]
    )
    codec.register(record, name="Record", field_naming=None)
    fields = {f"f{i}": i for i in reversed(range(1, width))}
    fields["ignored"] = [1, 2, 3]
    value = codec.loads(hessian2.dumps(HessianObject("Record", fields)))
    assert value.f0 == -1
    assert [getattr(value, f"f{i}") for i in range(1, width)] == list(range(1, width))


def test_colliding_wire_field_names_fail_at_registration() -> None:
    @dataclasses.dataclass
    class Collision:
        user_name: str
        userName: str

    with pytest.raises(ValueError, match="multiple Python fields map to 'userName'"):
        Codec().register(Collision, name="Collision")
    codec = Codec()
    codec.register(Collision, name="Collision", field_naming=None)
    value = Collision("first", "second")
    assert codec.loads(codec.dumps(value)) == value
