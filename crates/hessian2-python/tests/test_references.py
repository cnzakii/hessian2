"""Public contracts for references."""

from __future__ import annotations

import datetime as dt
import gc
import re
import typing
from dataclasses import InitVar, dataclass, field
from datetime import UTC
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

import hessian2
import pytest
from assertions import same
from hessian2 import Codec, Converter, DecodeError, HessianObject


def roundtrip(value: Any, codec: Codec | None = None) -> Any:
    codec = codec or Codec()
    return codec.loads(codec.dumps(value))


def test_shared_values_stay_shared() -> None:
    inner = [1]
    outer = roundtrip([inner, inner, {"x": inner}])

    assert outer[0] is outer[1] is outer[2]["x"]


def test_lists_can_reference_themselves():
    value = [1]
    value.append(value)

    decoded = roundtrip(value)

    assert decoded[1] is decoded


def test_maps_can_reference_themselves():
    value = {}
    value["self"] = value

    decoded = roundtrip(value)

    assert decoded["self"] is decoded


def test_dumps_many_shares_references_across_top_level_values():
    shared = {"k": 1}
    data = hessian2.dumps_many([shared, shared, "end"])

    first, second, _ = hessian2.loads_many(data)

    assert first is second


def test_an_empty_stream_decodes_to_no_values():
    assert same(hessian2.loads_many(b""), [])


def test_self_referencing_unknown_object() -> None:
    wire = b"C\x01N\x91\x04next\x60Q\x90"

    value = Codec(unknown_class="hessian_object").loads(wire)

    assert value.fields["next"] is value


class Point(typing.NamedTuple):
    x_pos: int
    label: str = "origin"


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


@pytest.mark.parametrize(
    "field_naming", [None, "camelCase"], ids=["unchanged-names", "camel-case"]
)
@pytest.mark.parametrize(
    ("container", "key"),
    [(lambda c: [c, c], 0), (lambda c: (c, c), 0), (lambda c: {"child": c}, "child")],
    ids=["list", "tuple", "dict"],
)
def test_registered_children_keep_identity_across_fields_and_containers(
    field_naming, container, key
):
    codec = Codec()

    @dataclass(frozen=True, slots=True)
    class Child:
        user_name: str

    @dataclass
    class Parent:
        first_child: Child
        children: object

    codec.register(Child, name="Child", field_naming=field_naming)
    codec.register(Parent, name="Parent", field_naming=field_naming)
    child = Child("ann")
    original = Parent(child, container(child))

    decoded = codec.loads(codec.dumps(original))

    assert type(decoded) is Parent
    assert type(decoded.first_child) is Child
    assert decoded.first_child is decoded.children[key]
    assert decoded.first_child is not child
    assert decoded.first_child.user_name == "ann"


@pytest.mark.parametrize(
    ("value", "annotation"),
    [
        (Decimal("-123.4500"), list[dict[Decimal, Any] | Decimal]),
        (UUID("123e4567-e89b-12d3-a456-426614174000"), list[dict[UUID, Any] | UUID]),
        (dt.datetime(2024, 2, 29, 1, 2, 3, 456000, tzinfo=UTC), None),
    ],
    ids=["decimal", "uuid", "datetime"],
)
def test_standard_library_map_keys_keep_their_type_and_value(value, annotation):
    original = [{value: {"value": value}}, value]

    assert same(Codec().loads(Codec().dumps(original), type=annotation), original)


@pytest.mark.parametrize(
    ("value", "annotation"),
    [
        (Decimal("-123.4500"), list[dict[Decimal, Any] | Decimal]),
        (UUID(int=5), list[dict[UUID, Any] | UUID]),
    ],
    ids=["decimal", "uuid"],
)
def test_object_map_keys_share_identity_with_later_values(value, annotation):
    original = [{value: {"value": value}}, value]

    decoded = Codec().loads(Codec().dumps(original), type=annotation)

    key = next(iter(decoded[0]))

    assert key is decoded[0][key]["value"] is decoded[1]


def test_enum_map_keys_share_identity_with_values():
    class Color(Enum):
        RED = 1

    codec = Codec()
    codec.register(Color, name="Color", field_naming=None)
    original = [{Color.RED: {"value": Color.RED}}, Color.RED]

    decoded = codec.loads(codec.dumps(original))

    key = next(iter(decoded[0]))

    assert key is decoded[0][key]["value"] is decoded[1] is Color.RED


def test_a_registered_object_can_reference_itself_through_containers():
    @dataclass
    class Node:
        links: object

    codec = Codec()
    codec.register(Node, name="Node", field_naming=None)
    value = Node(None)
    value.links = [{"next": value}]

    decoded = codec.loads(codec.dumps(value))

    assert type(decoded) is Node
    assert decoded.links[0]["next"] is decoded


def test_registered_objects_can_reference_each_other_through_containers():
    @dataclass
    class Node:
        label: str
        links: object = None

    codec = Codec()
    codec.register(Node, name="Node", field_naming=None)
    first, second = Node("first"), Node("second")
    first.links = [{"next": second}]
    second.links = [{"next": first}]

    a, b, repeated = codec.loads(codec.dumps([first, second, first]))

    assert a is repeated and a is not b
    assert a.links[0]["next"] is b
    assert b.links[0]["next"] is a
    assert (a.label, b.label) == ("first", "second")


@pytest.mark.parametrize(
    ("mode", "expected_type", "fields"),
    [
        ("dict", dict, lambda v: v),
        ("hessian_object", HessianObject, lambda v: v.fields),
    ],
    ids=["dict", "object"],
)
def test_unknown_nested_objects_keep_cycles_and_shared_identity(
    mode, expected_type, fields
):
    codec = Codec(unknown_class=mode)

    @dataclass
    class Known:
        child: object

    codec.register(Known, name="Known", field_naming=None)
    unknown = HessianObject("Unknown", {"value": 7})
    unknown.fields["self"] = unknown
    wire = Codec().dumps(HessianObject("Known", {"child": [unknown, unknown]}))

    decoded = codec.loads(wire)

    a, b = decoded.child

    assert type(decoded) is Known
    assert type(a) is expected_type
    assert a is b and fields(a)["self"] is a
    assert same(fields(a)["value"], 7)


def test_an_unknown_nested_class_raises_in_raise_mode():
    codec = Codec()

    @dataclass
    class Known:
        child: object

    codec.register(Known, name="Known", field_naming=None)
    wire = hessian2.dumps(HessianObject("Known", {"child": [HessianObject("Unknown")]}))

    with pytest.raises(DecodeError, match=re.escape("'Unknown' is not registered")):
        codec.loads(wire)


def test_an_unknown_nested_cyclic_object_can_be_reencoded():
    codec = Codec(unknown_class="hessian_object")
    value = HessianObject("Unknown")
    value.fields["self"] = value

    wire = codec.dumps([value, value])

    assert same(codec.dumps(codec.loads(wire)), wire)


def test_registered_values_share_identity_across_stream_values():
    @dataclass
    class N:
        x: int

    codec = Codec()
    codec.register(N, name="N", field_naming=None)
    a, b = N(1), N(2)

    first, second, mapping, last = codec.loads_many(
        codec.dumps_many([a, b, {"a": a}, b])
    )

    assert type(first) is type(second) is N
    assert mapping["a"] is first
    assert last is second
    assert first == a and second == b


def test_stream_values_keep_object_and_container_cycles():
    codec = Codec(unknown_class="hessian_object")
    obj = HessianObject("Node")
    obj.fields["self"] = obj
    items = [obj]
    items.append(items)

    first, second, third = codec.loads_many(codec.dumps_many([obj, items, obj]))

    assert first is third is second[0]
    assert first.fields["self"] is first
    assert second[1] is second


def test_objects_with_the_same_class_name_can_have_different_field_lists():
    codec = Codec(unknown_class="dict")
    values = [HessianObject("X", {"a": 1}), HessianObject("X", {"b": 2})]

    assert same(codec.loads_many(codec.dumps_many(values)), [{"a": 1}, {"b": 2}])


def test_each_encode_call_starts_fresh_stream_tables():
    codec = Codec()
    value = HessianObject("X", {"a": 1})

    first = codec.dumps(value)

    assert codec.dumps(value) == first


@pytest.mark.parametrize(
    "reference", [b"Q\x90", b"\x60", b"\x70\x90"], ids=["value", "class", "type"]
)
def test_a_decode_call_cannot_use_tables_from_an_earlier_call(reference):
    codec = Codec(unknown_class="dict")
    codec.loads(codec.dumps([HessianObject("X"), {1}]))

    with pytest.raises(DecodeError):
        codec.loads(reference)


def test_registered_object_can_reference_itself_before_completion():
    codec = hessian2.Codec()

    @dataclass
    class Node:
        self: object = None

    codec.register(Node, name="Node", field_naming=None)

    node = codec.loads(b"C\x04Node\x91\x04self\x60Q\x90")

    assert node.self is node


def test_enum_and_converter_reference_slots_resolve_completed_values(
    enum_converter_codec,
):
    codec, enum_type, box_type = enum_converter_codec
    box = box_type(1)

    a, b, c, d = codec.loads_many(
        codec.dumps_many([enum_type.A, box, enum_type.A, box])
    )

    assert a is c is enum_type.A
    assert b is d


@pytest.mark.parametrize(
    ("name", "field"), [("E", "name"), ("Box", "value")], ids=["enum", "converter"]
)
def test_placeholder_values_reject_back_references_before_completion(
    enum_converter_codec, name, field
):
    codec, _, _ = enum_converter_codec
    value = HessianObject(name)
    value.fields[field] = value

    with pytest.raises(DecodeError, match=re.escape("still being decoded")):
        codec.loads(hessian2.dumps(value))


@pytest.mark.parametrize(
    "value",
    [2**100, dt.datetime(2024, 1, 1), dt.timedelta(microseconds=-1)],
    ids=["bigint", "datetime", "duration"],
)
def test_builtin_internal_slots_do_not_shift_later_references(value):
    shared = []

    a, first, second, b = hessian2.loads(hessian2.dumps([value, shared, shared, value]))

    assert a is b
    assert first is second
    assert same(a, value)


def test_encode_keeps_temporary_referenced_values_alive():
    codec = hessian2.Codec()

    decoded = codec.loads_many(codec.dumps_many([] for _ in range(256)))

    assert all(value == [] for value in decoded)
    assert len({id(value) for value in decoded}) == 256


@pytest.mark.parametrize(
    "kind", ["converter", "factory", "default", "class", "initvar"]
)
def test_codec_registration_cycles_are_collected(kind):
    import weakref

    def build():
        codec = hessian2.Codec()
        if kind == "converter":

            class Value:
                pass

            def to_fields(value):
                return {"value": codec.max_depth}

            codec.register(
                hessian2.Converter(Value, to_fields, lambda f: Value()), name="Value"
            )
        elif kind == "factory":

            @dataclass
            class Value:
                value: object = field(default_factory=lambda: codec)

            codec.register(Value, name="Value", field_naming=None)
        elif kind == "default":

            @dataclass
            class Value:
                value: object = codec

            codec.register(Value, name="Value", field_naming=None)
        elif kind == "initvar":

            @dataclass
            class Value:
                value: InitVar[object] = codec

                def __post_init__(self, value):
                    pass

            codec.register(Value, name="Value", field_naming=None)
        else:

            @dataclass
            class Value:
                def owner(self):
                    return codec

            codec.register(Value, name="Value", field_naming=None)

        return weakref.ref(codec), weakref.ref(Value)

    codec_ref, class_ref = build()
    gc.collect()

    assert codec_ref() is None
    assert class_ref() is None


def test_codecs_sharing_registrations_are_collected_without_double_counting():
    import weakref

    def build():
        first = hessian2.Codec()
        second = hessian2.Codec()

        @dataclass
        class Value:
            left: object = first
            right: object = second

        first.register(Value, name="Value", field_naming=None)
        second.register(Value, name="Value", field_naming=None)
        first.register(Value, name="Alias", field_naming=None, replace=True)
        return weakref.ref(first), weakref.ref(second), weakref.ref(Value)

    refs = build()
    gc.collect()

    assert all(ref() is None for ref in refs)


def test_explicit_boxed_reference_resolves_to_the_completed_value():
    box = hessian2.HessianObject("com.caucho.hessian.io.FloatHandle", {"_value": 1.25})
    tail = []

    first, second, a, b = hessian2.loads(hessian2.dumps([box, box, tail, tail]))

    assert first == 1.25 and first is second and a is b
