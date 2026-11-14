"""Public contracts for as object."""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import re
from decimal import Decimal
from typing import Any, NamedTuple, TypedDict
from uuid import UUID

import attrs
import hessian2
import pytest
from assertions import same
from hessian2 import Codec, Converter, EncodeError, HessianObject, Typed

AWARE = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)


@hessian2.java_class("com.example.Color")
class Color(enum.Enum):
    RED = "r"


@hessian2.java_class("com.example.User")
@dataclasses.dataclass
class User:
    name: str
    user_id: hessian2.Long
    tags: tuple[str, ...]
    ids: list[hessian2.Long]
    at: hessian2.Date | None
    color: Color
    friends: list[User]


@hessian2.java_class("com.example.Pair")
class Pair(NamedTuple):
    left: hessian2.Long
    right: str


@hessian2.java_class("com.example.Box", field_naming=None)
@attrs.define
class Box:
    item_count: int
    sizes: dict[str, hessian2.Long]


@dataclasses.dataclass
class Money:
    cents: int


@dataclasses.dataclass
class Loose:
    value: int


class Plain:
    def __init__(self, value: int) -> None:
        self.value = value


def user(**changes: Any) -> User:
    amy = User("amy", 8, (), [], None, Color.RED, [])
    fields = {
        "name": "bob",
        "user_id": 1221233,
        "tags": ("a", "b"),
        "ids": [1, 2],
        "at": AWARE,
        "color": Color.RED,
        "friends": [amy],
    }
    return User(**{**fields, **changes})


def money_codec() -> Codec:
    codec = Codec()
    codec.register(
        Converter(
            Money,
            to_fields=lambda m: {"cents": m.cents},
            from_fields=lambda f: Money(f["cents"]),
        ),
        name="com.example.Money",
    )
    return codec


@pytest.mark.parametrize(
    ("value", "annotation"),
    [
        (7, hessian2.Long),
        (7, hessian2.Long | None),
        (None, hessian2.Long | None),
        (("a", "b"), tuple[str, ...]),
        ([1, 2], list[hessian2.Long]),
        ({"a": 1}, dict[str, hessian2.Long]),
        (AWARE, hessian2.Instant),
        (7, int),
    ],
)
@pytest.mark.parametrize("position", ["top", "dict", "list"])
def test_typed_writes_its_value_as_its_annotation_anywhere(
    value: Any, annotation: Any, position: str
) -> None:
    typed = Typed(value, annotation)
    actual, expected_value, expected_annotation = {
        "top": (typed, value, annotation),
        "dict": ({"k": typed}, {"k": value}, dict[str, annotation]),
        "list": ([typed], [value], list[annotation]),
    }[position]

    assert hessian2.dumps(actual) == hessian2.dumps(
        expected_value, type=expected_annotation
    )


def test_typed_overrides_the_surrounding_annotation():
    assert hessian2.dumps([Typed(7, hessian2.Long)], type=list[int]) == b"y\xe7"


def test_a_typed_value_decodes_as_its_underlying_python_value():
    assert same(hessian2.loads(hessian2.dumps([Typed(7, hessian2.Long)])), [7])


def test_typed_exposes_its_value_and_annotation():
    value = Typed(7, hessian2.Long)

    assert same(value.value, 7)
    assert value.type is hessian2.Long


@pytest.mark.parametrize(
    ("other", "expected"),
    [(Typed(7, hessian2.Long), True), (Typed(7, int), False)],
    ids=["same", "other-annotation"],
)
def test_typed_equality_includes_the_annotation(other, expected):
    assert (Typed(7, hessian2.Long) == other) is expected


def test_typed_repr_exposes_value_and_annotation():
    assert repr(Typed(1, int)) == "Typed(1, <class 'int'>)"


def test_typed_supports_positional_pattern_matching():
    typed = Typed(7, hessian2.Long)

    match typed:
        case Typed(value, hessian2.Long):
            captured = value
        case _:
            pytest.fail("Typed does not match by position")

    assert same(captured, 7)


def test_typed_is_unhashable():
    with pytest.raises(TypeError, match=re.escape("unhashable")):
        hash(Typed(7, int))


@pytest.mark.parametrize(
    "value",
    [
        user(),
        user(at=None, friends=[]),
        Pair(5, "x"),
        Box(3, {"a": 1}),
        Color.RED,
    ],
    ids=["nested", "flat", "named_tuple", "attrs", "enum"],
)
def test_as_object_encodes_as_the_value_does(value: Any) -> None:
    obj = hessian2.as_object(value)

    assert isinstance(obj, HessianObject)
    assert hessian2.dumps(obj) == hessian2.dumps(value)


def test_as_object_gives_the_class_name_and_wire_fields_one_level_deep() -> None:
    value = user()

    obj = hessian2.as_object(value)

    assert obj is not None
    assert obj.class_name == "com.example.User"
    assert list(obj.fields) == [
        "name",
        "userId",
        "tags",
        "ids",
        "at",
        "color",
        "friends",
    ]
    assert obj.fields["name"] == "bob"
    assert obj.fields["color"] is Color.RED
    assert obj.fields["friends"] is value.friends


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            Pair(5, "x"),
            HessianObject(
                "com.example.Pair", {"left": Typed(5, hessian2.Long), "right": "x"}
            ),
        ),
        (
            Box(3, {"a": 1}),
            HessianObject(
                "com.example.Box",
                {"item_count": 3, "sizes": Typed({"a": 1}, dict[str, hessian2.Long])},
            ),
        ),
    ],
    ids=["named-tuple", "attrs"],
)
def test_as_object_wraps_tagged_fields_of_named_tuples_and_attrs(value, expected):
    assert hessian2.as_object(value) == expected


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("userId", Typed(1221233, hessian2.Long)),
        ("tags", Typed(("a", "b"), tuple[str, ...])),
        ("ids", Typed([1, 2], list[hessian2.Long])),
        ("at", Typed(AWARE, hessian2.Date | None)),
    ],
)
def test_as_object_wraps_each_field_whose_annotation_changes_encoding(field, expected):
    obj = hessian2.as_object(user())

    assert obj.fields[field] == expected


@pytest.mark.parametrize("field", ["name", "friends"])
def test_as_object_leaves_fields_without_encoding_tags_unwrapped(field):
    obj = hessian2.as_object(user())

    assert not isinstance(obj.fields[field], Typed)


def test_as_object_of_an_enum_is_its_wire_form() -> None:
    assert hessian2.as_object(Color.RED) == HessianObject(
        "com.example.Color", {"name": "RED"}
    )


GeneralizedUser = TypedDict(
    "GeneralizedUser",
    {
        "class": str,
        "name": str,
        "userId": hessian2.Long,
        "tags": tuple[str, ...],
        "ids": list[hessian2.Long],
        "at": hessian2.Date | None,
        "color": Color,
        "friends": list[Any],
    },
)


def test_field_types_survive_moving_the_fields_into_a_dict() -> None:
    obj = hessian2.as_object(user(friends=[]))

    assert obj is not None
    moved = {"class": obj.class_name, **obj.fields}
    plain = {
        key: value.value if isinstance(value, Typed) else value
        for key, value in moved.items()
    }
    expected = hessian2.dumps(plain, type=GeneralizedUser)
    assert hessian2.dumps(moved) == expected


def test_as_object_of_a_converter_type_gives_its_fields() -> None:
    codec = money_codec()

    assert codec.as_object(Money(150)) == HessianObject(
        "com.example.Money", {"cents": 150}
    )
    assert codec.dumps(codec.as_object(Money(150))) == codec.dumps(Money(150))


@pytest.mark.parametrize(
    ("to_fields", "message"),
    [
        (lambda m: [m.cents], "must return a dict"),
        (lambda m: {1: m.cents}, 'field names of "com.example.Money" must be str'),
    ],
)
@pytest.mark.parametrize("operation", ["dumps", "as_object"])
def test_as_object_fails_where_dumps_fails_for_a_converter(
    to_fields: Any, message: str, operation: str
) -> None:
    codec = Codec()
    codec.register(
        Converter(Money, to_fields=to_fields, from_fields=Money),
        name="com.example.Money",
    )

    with pytest.raises(EncodeError, match=re.escape(message)):
        getattr(codec, operation)(Money(1))


def test_as_object_follows_each_codec_s_registrations() -> None:
    codec = Codec()
    codec.register(Loose, name="com.example.Loose")

    assert codec.as_object(Loose(1)) == HessianObject("com.example.Loose", {"value": 1})
    with pytest.raises(EncodeError, match=re.escape("Loose has no Hessian form")):
        hessian2.as_object(Loose(1))


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        7,
        2**70,
        1.5,
        "s",
        b"b",
        bytearray(b"b"),
        [1],
        (1,),
        {1},
        frozenset(),
        {"a": 1},
        Decimal("1.5"),
        UUID(int=1),
        AWARE,
        dt.datetime(2026, 1, 2),
        dt.date(2026, 1, 2),
        dt.time(1, 2),
        dt.timedelta(seconds=1),
        Typed(user(), User),
    ],
)
def test_as_object_is_none_for_values_written_in_another_form(value: Any) -> None:
    assert hessian2.as_object(value) is None
    hessian2.dumps(value)


def test_as_object_returns_a_hessian_object_itself() -> None:
    obj = HessianObject("com.example.Thing", {"a": 1})

    assert hessian2.as_object(obj) is obj


def test_as_object_of_a_value_without_a_hessian_form_raises() -> None:
    with pytest.raises(EncodeError, match=re.escape("Plain has no Hessian form")):
        hessian2.as_object(Plain(1))


@pytest.mark.parametrize(
    "construct",
    [lambda: Typed(Typed(7, hessian2.Long), int), lambda: Typed(7, Typed(7, int))],
    ids=["nested-value", "value-as-annotation"],
)
def test_typed_rejects_nesting_itself_as_a_value_or_annotation(construct):
    with pytest.raises(TypeError):
        construct()


def test_fields_annotated_with_local_types_encode_from_as_object() -> None:
    local_long = hessian2.Long

    class Fields(TypedDict):
        id: local_long  # type: ignore[valid-type]

    @hessian2.java_class("com.example.Local")
    @dataclasses.dataclass
    class Local:
        payload: Fields

    value = Local({"id": 7})

    obj = hessian2.as_object(value)

    assert obj is not None
    assert hessian2.dumps(obj) == hessian2.dumps(value)
    moved = {"payload": obj.fields["payload"]}
    assert hessian2.dumps(moved) == hessian2.dumps(
        {"payload": {"id": 7}}, type=dict[str, dict[str, hessian2.Long]]
    )


@hessian2.java_class("com.example.Node")
@dataclasses.dataclass
class Node:
    value: Any


def test_as_object_keeps_nested_objects_of_the_same_class_name_encodable():
    value = Node(HessianObject("com.example.Node", {"value": 7}))

    assert hessian2.dumps(hessian2.as_object(value)) == hessian2.dumps(value)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (
            [HessianObject("com.example.Node", {"value": 1}), Node(1)],
            b"C\x10com.example.Node\x91\x05value\x60\x91\x60\x91",
        ),
        (
            [Node(1), HessianObject("com.example.Node", {"value": 1})],
            b"C\x10com.example.Node\x91\x05value\x60\x91\x60\x91",
        ),
        (
            [HessianObject("java.math.BigDecimal", {"value": "1"}), Decimal(1)],
            b"C\x14java.math.BigDecimal\x91\x05value\x60\x011\x60\x011",
        ),
        (
            [Decimal(1), HessianObject("java.math.BigDecimal", {"value": "1"})],
            b"C\x14java.math.BigDecimal\x91\x05value\x60\x011\x60\x011",
        ),
    ],
    ids=["raw-first", "declared-first", "raw-builtin-first", "builtin-first"],
)
def test_equal_class_shapes_share_one_definition_across_python_representations(
    values, expected
):
    assert hessian2.dumps_many(values) == expected


@pytest.mark.parametrize("cls", [Typed, HessianObject])
def test_typed_and_hessian_object_take_no_converter(cls: type) -> None:
    with pytest.raises(TypeError, match=re.escape("cannot have a converter")):
        Codec().register(
            Converter(cls, to_fields=lambda v: {}, from_fields=lambda f: f), name="X"
        )


@pytest.mark.parametrize("operation", ["dumps", "as_object"])
def test_as_object_rejects_converter_field_names_dumps_rejects(operation) -> None:
    codec = Codec()
    codec.register(
        Converter(Money, to_fields=lambda m: {"\ud800": 1}, from_fields=Money),
        name="com.example.Money",
    )

    with pytest.raises(UnicodeEncodeError):
        getattr(codec, operation)(Money(1))


@pytest.mark.parametrize(
    "make",
    [lambda v: Typed(v, int), lambda v: HessianObject("X", {"v": v})],
    ids=["typed", "hessian_object"],
)
def test_ordering_compares_no_contents(make: Any) -> None:
    class EqualRaises:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("equality called")

    value = make(EqualRaises())

    with pytest.raises(TypeError, match=re.escape("not supported")):
        value < value  # noqa: B015


@hessian2.java_class("com.example.Counter")
@dataclasses.dataclass
class Counter:
    count: hessian2.Long


def test_a_typed_field_value_is_kept_not_typed_again() -> None:
    first = hessian2.as_object(Counter(7))

    assert first is not None
    typed = first.fields["count"]
    again = hessian2.as_object(Counter(typed))
    assert again is not None
    assert again.fields["count"] is typed
    assert hessian2.dumps(again) == hessian2.dumps(Counter(typed))


def test_a_locally_declared_field_shows_its_own_annotation() -> None:
    local_long = hessian2.Long

    @hessian2.java_class("com.example.LocalCounter")
    @dataclasses.dataclass
    class LocalCounter:
        count: local_long  # type: ignore[valid-type]

    obj = hessian2.as_object(LocalCounter(7))

    assert obj is not None
    assert obj.fields["count"] == Typed(7, hessian2.Long)
    assert obj.fields["count"].type is hessian2.Long
    assert hessian2.dumps(obj) == hessian2.dumps(LocalCounter(7))
