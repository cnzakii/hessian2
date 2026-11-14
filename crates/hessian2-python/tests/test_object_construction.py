"""Public contracts for object construction."""

from __future__ import annotations

import abc
import collections
import dataclasses
import datetime as dt
import re
import struct
import typing
from dataclasses import InitVar, dataclass, field
from typing import Any, ClassVar, Generic, NamedTuple, TypeVar

import attrs
import hessian2
import pytest
from assertions import same
from hessian2 import Codec, DecodeError, EncodeError, HessianObject


def roundtrip(value: Any, codec: Codec | None = None) -> Any:
    codec = codec or Codec()
    return codec.loads(codec.dumps(value))


class Bag(dict):  # type: ignore[type-arg]
    pass


def test_camel_case_naming_preserves_private_field_prefixes():
    @dataclasses.dataclass(frozen=True)
    class User:
        user_name: str
        _private_id: int = 0
        tags: list[str] = dataclasses.field(default_factory=list)

    codec = Codec()
    codec.register(User, name="com.example.User")

    raw = hessian2.loads(codec.dumps(User("ann", 7, ["a"])), type=HessianObject)

    assert raw == HessianObject(
        "com.example.User", {"userName": "ann", "_privateId": 7, "tags": ["a"]}
    )


def test_frozen_dataclasses_restore_their_registered_type_and_values():
    @dataclasses.dataclass(frozen=True)
    class User:
        name: str

    codec = Codec()
    codec.register(User, name="User")
    original = User("ann")

    decoded = codec.loads(codec.dumps(original))

    assert type(decoded) is User
    assert decoded == original
    assert decoded is not original


def test_defaults_fill_missing_fields() -> None:
    codec = Codec()

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
    codec = Codec()

    @dataclasses.dataclass
    class A:
        a: int
        b: int

    codec.register(A, name="A", field_naming=None)

    with pytest.raises(DecodeError, match=re.escape('missing field "b"')):
        codec.loads(b"C\x01A\x91\x01a\x60\x91")


def test_unknown_wire_fields_are_ignored() -> None:
    codec = Codec()

    @dataclasses.dataclass
    class A:
        a: int

    codec.register(A, name="A", field_naming=None)

    # Field "z" holds an object of an unregistered class.
    wire = b"C\x01A\x92\x01z\x01a\x60C\x01B\x90\x61\x91"

    assert same(codec.loads(wire), A(1))


def test_post_init_runs_after_decoding() -> None:
    codec = Codec()
    seen = []

    @dataclasses.dataclass
    class A:
        a: int

        def __post_init__(self) -> None:
            seen.append(self.a)

    codec.register(A, name="A", field_naming=None)

    codec.loads(codec.dumps(A(3)))

    assert seen == [3, 3]


def test_dataclass_decoding_bypasses_init() -> None:
    codec = Codec()

    @dataclasses.dataclass(init=False)
    class A:
        a: int

        def __init__(self) -> None:
            raise AssertionError("decoding must not call __init__")

    codec.register(A, name="A", field_naming=None)

    obj = A.__new__(A)
    obj.a = 1

    assert same(codec.loads(codec.dumps(obj)).a, 1)


def test_frozen_slotted_dataclasses_restore_their_fields() -> None:
    codec = Codec()

    @dataclasses.dataclass(slots=True, frozen=True)
    class S:
        x: int

    codec.register(S, name="S", field_naming=None)

    assert same(roundtrip(S(1), codec), S(1))


@attrs.frozen
class AttrsPoint:
    x: int
    y: list[int] = attrs.Factory(list)


def test_attrs_classes_restore_their_registered_type_and_values():
    codec = Codec()
    codec.register(AttrsPoint, name="P", field_naming=None)

    decoded = roundtrip(AttrsPoint(1, [2]), codec)

    assert type(decoded) is AttrsPoint
    assert decoded == AttrsPoint(1, [2])


def test_attrs_missing_fields_use_their_factory_default():
    codec = Codec()
    codec.register(AttrsPoint, name="P", field_naming=None)

    assert same(
        codec.loads(hessian2.dumps(HessianObject("P", {"x": 1}))), AttrsPoint(1, [])
    )


class Point(typing.NamedTuple):
    x_pos: int
    label: str = "origin"


def test_named_tuples_use_registered_names_and_restore_their_type() -> None:
    codec = hessian2.Codec()
    codec.register(Point, name="com.example.Point")

    data = codec.dumps(Point(1, "a"))

    assert data.startswith(b"C\x11com.example.Point\x92\x04xPos\x05label")
    decoded = codec.loads(data)
    assert decoded == Point(1, "a")
    assert type(decoded) is Point


def test_named_tuples_fill_missing_fields_from_defaults():
    codec = Codec()
    codec.register(Point, name="com.example.Point", field_naming=None)

    data = hessian2.dumps(HessianObject("com.example.Point", {"x_pos": 3}))

    assert same(codec.loads(data), Point(3))


def test_named_tuples_reject_missing_required_fields():
    codec = Codec()
    codec.register(Point, name="com.example.Point", field_naming=None)
    data = hessian2.dumps(HessianObject("com.example.Point", {"label": "b"}))

    with pytest.raises(DecodeError, match=re.escape("missing field")):
        codec.loads(data)


def test_named_tuples_keep_shared_reference_identity():
    codec = Codec()
    codec.register(Point, name="com.example.Point", field_naming=None)
    shared = Point(2)

    first, second = codec.loads(codec.dumps([shared, shared]))

    assert first is second


def test_collections_namedtuples_restore_their_registered_type_and_defaults():
    pair = collections.namedtuple("Pair", ["left", "right"], defaults=[None])  # noqa: PYI024
    codec = Codec()
    codec.register(pair, name="com.example.Pair", field_naming=None)

    decoded = codec.loads(codec.dumps(pair(1)))

    assert type(decoded) is pair
    assert decoded == pair(1, None)


def test_unregistered_namedtuples_encode_as_plain_lists():
    pair = collections.namedtuple("Pair", ["left", "right"])  # noqa: PYI024

    assert same(hessian2.loads(hessian2.dumps(pair(1, 2))), [1, 2])


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

    with pytest.raises(TypeError, match=re.escape("abstract")):
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


@pytest.mark.parametrize("width", [4, 1024])
def test_wire_fields_fill_attributes_by_name_even_in_wide_sparse_records(
    width: int,
) -> None:
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


@pytest.mark.parametrize("present", [False, True], ids=["null", "object"])
def test_optional_registered_fields_restore_null_or_the_declared_class(present):
    @dataclass
    class Item:
        value: int

    @dataclass
    class Optional:
        child: Item | None = None

    codec = Codec()
    codec.register(Item, name="Item", field_naming=None)
    codec.register(Optional, name="Optional", field_naming=None)
    value = Optional({False: None, True: Item(3)}[present])
    expected_type = {False: type(None), True: Item}[present]

    decoded = codec.loads(codec.dumps([value]))[0]

    assert type(decoded) is Optional
    assert type(decoded.child) is expected_type
    assert decoded == value


@pytest.mark.parametrize(
    ("factory", "items_field"),
    [
        (dataclass, lambda: field(default_factory=list)),
        (attrs.define, lambda: attrs.field(factory=list)),
        (attrs.frozen, lambda: attrs.field(factory=list)),
    ],
    ids=["dataclass", "attrs", "frozen-attrs"],
)
def test_missing_factory_fields_are_independent_between_nested_instances(
    factory, items_field
):
    @factory
    class Defaults:
        value: int = 7
        items: list = items_field()

    @dataclass
    class Pair:
        left: Defaults
        right: Defaults

    codec = Codec()
    codec.register(Defaults, name="Defaults", field_naming=None)
    codec.register(Pair, name="Pair", field_naming=None)
    data = hessian2.dumps(
        HessianObject(
            "Pair",
            {"left": HessianObject("Defaults"), "right": HessianObject("Defaults")},
        )
    )

    decoded = codec.loads(data)

    assert type(decoded.left) is type(decoded.right) is Defaults
    assert decoded.left is not decoded.right
    assert decoded.left.items is not decoded.right.items
    assert same(decoded.left.items, [])
    assert same(decoded.right.items, [])


@pytest.mark.parametrize(
    "factory",
    [dataclass, attrs.define, attrs.frozen],
    ids=["dataclass", "attrs", "frozen-attrs"],
)
def test_present_fields_override_class_defaults(factory):
    @factory
    class Defaults:
        value: int = 7

    codec = Codec()
    codec.register(Defaults, name="Defaults", field_naming=None)

    assert same(
        codec.loads(hessian2.dumps(HessianObject("Defaults", {"value": 9}))),
        Defaults(9),
    )


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("slots", [False, True])
def test_dataclass_kw_only_init_false_and_classvar(frozen, slots):
    codec = Codec()

    @dataclass(frozen=frozen, slots=slots, kw_only=True)
    class Options:
        value: int
        cached: int = field(init=False, default=0)
        category: ClassVar[str] = "static"

    codec.register(Options, name="Options", field_naming=None)

    original = Options(value=1)
    object.__setattr__(original, "cached", 42)
    wire = codec.dumps({"options": original})

    decoded = codec.loads(wire)["options"]

    assert decoded == original and type(decoded) is Options
    assert decoded.cached == 42
    assert b"category" not in wire and b"cached" in wire


def test_initvar_without_post_init_is_not_a_wire_field():
    codec = Codec()

    @dataclass
    class Input:
        value: int
        transient: InitVar[int]

    codec.register(Input, name="Input", field_naming=None)

    original = Input(1, 99)
    wire = codec.dumps([original])

    decoded = codec.loads(wire)[0]

    assert decoded == original and type(decoded) is Input
    assert b"transient" not in wire


def test_initvar_default_is_passed_to_post_init():
    codec = Codec()
    seen = []

    @dataclass
    class Input:
        value: int
        transient: InitVar[int] = 9

        def __post_init__(self, transient):
            seen.append(transient)

    codec.register(Input, name="Input", field_naming=None)

    original = Input(1)

    decoded = codec.loads(codec.dumps([original]))[0]

    assert decoded == original and type(decoded) is Input
    assert seen == [9, 9]


@pytest.mark.parametrize(
    "decorator", [attrs.define, attrs.frozen], ids=["mutable", "frozen"]
)
@pytest.mark.parametrize("slots", [False, True], ids=["dict", "slots"])
@pytest.mark.parametrize("value", [3, -1], ids=["valid", "outside-validator"])
def test_attrs_decoding_restores_fields_and_runs_only_post_init(
    decorator, slots, value
):
    events = []

    def convert(item):
        events.append("convert")
        return int(item)

    def validate(instance, attribute, item):
        events.append("validate")
        if item < 0:
            raise ValueError("negative")

    @decorator(slots=slots)
    class Attrs:
        value: int = attrs.field(converter=convert, validator=validate)

        def __attrs_post_init__(self):
            events.append("post_init")

    codec = Codec()
    codec.register(Attrs, name="Attrs", field_naming=None)
    data = hessian2.dumps(HessianObject("Attrs", {"value": value}))

    decoded = codec.loads(data)

    assert type(decoded) is Attrs
    assert same(decoded.value, value)
    assert events == ["post_init"]


@pytest.mark.parametrize("base", [dict, list, int, dt.datetime])
def test_unsupported_extension_layouts_raise_type_error_on_decode(base):
    cls = dataclasses.make_dataclass("Extension", [("value", int)], bases=(base,))
    codec = Codec()
    codec.register(cls, name="Extension", field_naming=None)
    wire = hessian2.dumps(HessianObject("Extension", {"value": 1}))

    with pytest.raises(TypeError):
        codec.loads(wire)


@pytest.mark.parametrize("slots", [False, True])
def test_python_new_metaclass_and_init_subclass_are_bypassed(slots):
    events = []

    class Meta(type):
        def __call__(cls, *args, **kwargs):
            events.append("call")
            return super().__call__(*args, **kwargs)

    class Base(metaclass=Meta):
        __slots__ = ("__dict__",)

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__(**kwargs)

            def new(cls, *args, **kwargs):
                events.append("new")
                return object.__new__(cls)

            cls.__new__ = staticmethod(new)

    @dataclasses.dataclass(slots=slots)
    class Item(Base):
        value: int

    codec = Codec()
    codec.register(Item, name="Item", field_naming=None)

    value = codec.loads(hessian2.dumps(HessianObject("Item", {"value": 3})))

    assert type(value) is Item and value.value == 3
    assert events == []


def test_encoding_uses_the_current_class_after_instance_reassignment():
    T = TypeVar("T")

    @dataclasses.dataclass
    class Item(abc.ABC, Generic[T]):
        value: T

    @dataclasses.dataclass
    class Other(Item):
        pass

    codec = Codec()
    codec.register(Item, name="Item", field_naming=None)
    codec.register(Other, name="Other", field_naming=None)
    item = Item(3)
    item.__class__ = Other

    decoded = codec.loads(codec.dumps(item))

    assert type(decoded) is Other
    assert same(decoded.value, 3)


def test_decoding_rechecks_a_registered_class_that_becomes_abstract():
    @dataclasses.dataclass
    class Item(abc.ABC):
        value: int

    codec = Codec()
    codec.register(Item, name="Item", field_naming=None)
    Item.__abstractmethods__ = frozenset({"missing"})

    with pytest.raises(TypeError, match=re.escape("abstract")):
        codec.loads(hessian2.dumps(HessianObject("Item", {"value": 3})))


def test_descriptor_can_change_instance_class_during_decode():
    @dataclasses.dataclass
    class Item:
        first: int
        second: int

    class Other(Item):
        pass

    def store(instance, value):
        instance.__dict__["first"] = value
        instance.__class__ = Other

    Item.first = property(lambda self: self.__dict__["first"], store)
    codec = Codec()
    codec.register(Item, name="Item", field_naming=None)

    decoded = codec.loads(
        hessian2.dumps(HessianObject("Item", {"first": 1, "second": 2}))
    )

    assert type(decoded) is Other and (decoded.first, decoded.second) == (1, 2)


def test_named_tuple_cycles_are_rejected_during_encoding():
    class Node(NamedTuple):
        children: list

    codec = Codec()
    codec.register(Node, name="Node", field_naming=None)
    children = []
    value = Node(children)
    children.append(value)

    with pytest.raises(EncodeError, match=re.escape("contains itself")):
        codec.dumps(value)


def test_named_tuple_construction_bypasses_custom_new(custom_named_tuple):
    codec, cls = custom_named_tuple
    data = hessian2.dumps(HessianObject("Pair", {"left": [1], "ignored": 3}))

    decoded = codec.loads(data)

    assert type(decoded) is cls
    assert same(tuple(decoded), ([1], 9))


def test_named_tuple_fields_keep_shared_reference_identity(custom_named_tuple):
    codec, _ = custom_named_tuple
    value = codec.loads(hessian2.dumps(HessianObject("Pair", {"left": [1]})))

    first, second, child = codec.loads(codec.dumps([value, value, value.left]))

    assert first is second
    assert first.left is child


@pytest.mark.parametrize(
    "items", [(), (1,), (1, 2, 3)], ids=["empty", "too-few", "too-many"]
)
def test_named_tuple_encoding_rejects_incorrect_arity(custom_named_tuple, items):
    codec, cls = custom_named_tuple
    value = tuple.__new__(cls, items)

    with pytest.raises(EncodeError, match=re.escape("items")):
        codec.dumps(value)


def test_initvar_inheritance_kw_only_and_override():
    seen = []

    @dataclasses.dataclass
    class Base:
        seed: dataclasses.InitVar[int] = 2

    @dataclasses.dataclass
    class Item(Base):
        seed: dataclasses.InitVar[int] = 4
        extra: dataclasses.InitVar[int] = dataclasses.field(default=7, kw_only=True)
        value: int = 3

        def __post_init__(self, seed, extra):
            seen.append((seed, extra, self.value))

    codec = Codec()
    codec.register(Item, name="Item", field_naming=None)

    wire = codec.dumps(Item())

    assert b"seed" not in wire and b"extra" not in wire
    assert same(codec.loads(wire), Item())
    assert seen == [(4, 7, 3)] * 3
