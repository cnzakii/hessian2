"""Registered-object compositions; source map and decode-hook policy: TESTING.md."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from threading import Barrier
from typing import ClassVar
from uuid import UUID

import attrs
import pytest
from hessian2 import Codec, DecodeError, EncodeError, HessianObject


@pytest.mark.parametrize("field_naming", [None, "camelCase"])
@pytest.mark.parametrize("container", [list, dict, tuple])
def test_nested_dataclasses_and_container_aliases(field_naming, container):
    codec = Codec(field_naming=field_naming)

    @codec.register("Child")
    @dataclass(frozen=True, slots=True)
    class Child:
        user_name: str

    @codec.register("Parent")
    @dataclass
    class Parent:
        first_child: Child
        children: object

    child = Child("ann")
    children = {"child": child} if container is dict else container([child, child])
    original = Parent(child, children)
    wire = codec.dumps(original)
    decoded = codec.loads(wire)
    expected_children = list(children) if container is tuple else children
    assert decoded == Parent(child, expected_children)
    assert type(decoded) is Parent and type(decoded.first_child) is Child
    assert decoded is not original and decoded.first_child is not child
    if container is dict:
        assert decoded.children["child"] is decoded.first_child
    else:
        assert type(decoded.children) is list
        assert decoded.children[0] is decoded.children[1] is decoded.first_child
    assert wire.count(b"C\x05Child") == wire.count(b"C\x06Parent") == 1
    assert (b"userName" in wire) == (field_naming == "camelCase")
    assert (b"firstChild" in wire) == (field_naming == "camelCase")
    # Parent=0, Child=1, container=2; repeated child references use slot 1.
    assert wire.endswith(b"Q\x91Z" if container is dict else b"Q\x91Q\x91")


@pytest.mark.parametrize("kind", ["enum", "decimal", "uuid", "datetime"])
def test_special_types_as_nested_map_keys_and_values(kind):
    codec = Codec()

    @codec.register("Color")
    class Color(Enum):
        RED = 1

    values = {
        "enum": Color.RED,
        "decimal": Decimal("-123.4500"),
        "uuid": UUID("123e4567-e89b-12d3-a456-426614174000"),
        "datetime": datetime(2024, 2, 29, 1, 2, 3, 456000, tzinfo=UTC),
    }
    value = values[kind]
    original = [{value: {"value": value}}, value]
    wire = codec.dumps(original)
    decoded = codec.loads(wire)
    assert decoded == original
    key = next(iter(decoded[0]))
    result = decoded[0][key]["value"]
    assert type(key) is type(result) is type(value)
    if kind != "datetime":  # Hessian dates are scalars, outside the reference table.
        assert key is result is decoded[1]
        assert wire.endswith(b"Q\x92")
    if kind == "decimal":
        assert key.as_tuple() == value.as_tuple()


@pytest.mark.parametrize("present", [False, True])
def test_optional_fields_inside_registered_objects(present):
    codec = Codec()

    @codec.register("Item")
    @dataclass
    class Item:
        value: int

    @codec.register("Optional")
    @dataclass
    class Optional:
        child: Item | None = None

    original = Optional(Item(3) if present else None)
    decoded = codec.loads(codec.dumps([original]))[0]
    assert decoded == original and type(decoded) is Optional
    assert type(decoded.child) is (Item if present else type(None))


@pytest.mark.parametrize("factory", [dataclass, attrs.define, attrs.frozen])
def test_nested_missing_defaults_have_independent_factories(factory):
    codec = Codec()
    items_field = (
        field(default_factory=list)
        if factory is dataclass
        else attrs.field(factory=list)
    )

    @codec.register("Defaults")
    @factory
    class Defaults:
        value: int = 7
        items: list = items_field

    @codec.register("Pair")
    @dataclass
    class Pair:
        left: Defaults
        right: Defaults

    wire = Codec().dumps(
        HessianObject(
            "Pair",
            {"left": HessianObject("Defaults"), "right": HessianObject("Defaults")},
        )
    )
    decoded = codec.loads(wire)
    assert decoded == Pair(Defaults(), Defaults()) and type(decoded) is Pair
    assert type(decoded.left) is type(decoded.right) is Defaults
    assert decoded.left is not decoded.right
    assert decoded.left.items is not decoded.right.items
    decoded.left.items.append(1)
    assert decoded.right.items == []
    explicit = Pair(Defaults(9, [2]), Defaults(10, [3]))
    assert codec.loads(codec.dumps(explicit)) == explicit


@pytest.mark.parametrize("registration", ["both", "parent", "child"])
def test_inheritance_registration_is_exact(registration):
    codec = Codec()

    @dataclass
    class Parent:
        name: str

    @dataclass
    class Child(Parent):
        age: int

    if registration in ("both", "parent"):
        codec.register("Parent", Parent)
    if registration in ("both", "child"):
        codec.register("Child", Child)
    for cls, value in [(Parent, Parent("ann")), (Child, Child("bob", 3))]:
        registered = registration in ("both", "parent" if cls is Parent else "child")
        if not registered:
            with pytest.raises(EncodeError, match="register"):
                codec.dumps([value])
            continue
        wire = codec.dumps([value, value])
        decoded = codec.loads(wire)
        assert decoded == [value, value] and type(decoded[0]) is cls
        assert decoded[0] is decoded[1]
        if cls is Child:
            assert b"name" in wire and b"age" in wire


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("slots", [False, True])
def test_dataclass_kw_only_init_false_and_classvar(frozen, slots):
    codec = Codec()

    @codec.register("Options")
    @dataclass(frozen=frozen, slots=slots, kw_only=True)
    class Options:
        value: int
        cached: int = field(init=False, default=0)
        category: ClassVar[str] = "static"

    original = Options(value=1)
    object.__setattr__(original, "cached", 42)
    wire = codec.dumps({"options": original})
    decoded = codec.loads(wire)["options"]
    assert decoded == original and type(decoded) is Options
    assert decoded.cached == 42
    assert b"category" not in wire and b"cached" in wire
    assert (not hasattr(decoded, "__dict__")) == slots


def test_initvar_without_post_init_is_not_a_wire_field():
    codec = Codec()

    @codec.register("Input")
    @dataclass
    class Input:
        value: int
        transient: InitVar[int]

    original = Input(1, 99)
    wire = codec.dumps([original])
    decoded = codec.loads(wire)[0]
    assert decoded == original and type(decoded) is Input
    assert b"transient" not in wire


def test_initvar_default_is_passed_to_post_init():
    codec = Codec()
    seen = []

    @codec.register("Input")
    @dataclass
    class Input:
        value: int
        transient: InitVar[int] = 9

        def __post_init__(self, transient):
            seen.append(transient)

    original = Input(1)
    decoded = codec.loads(codec.dumps([original]))[0]
    assert decoded == original and type(decoded) is Input
    assert seen == [9, 9]


def test_initvar_without_default_cannot_be_registered_with_post_init():
    @dataclass
    class Input:
        value: int
        transient: InitVar[int]

        def __post_init__(self, transient):
            pass

    with pytest.raises(TypeError, match="InitVar without a default"):
        Codec().register("Input", Input)


@pytest.mark.parametrize("decorator", [attrs.define, attrs.frozen])
@pytest.mark.parametrize("slots", [False, True])
def test_attrs_decode_bypasses_converter_validator_but_runs_post_init(decorator, slots):
    codec = Codec()
    events = []

    def convert(value):
        events.append("convert")
        return int(value)

    def validate(instance, attribute, value):
        events.append("validate")
        if value < 0:
            raise ValueError("negative")

    @codec.register("Attrs")
    @decorator(slots=slots)
    class Attrs:
        value: int = attrs.field(converter=convert, validator=validate)

        def __attrs_post_init__(self):
            events.append("post_init")

    original = Attrs("3")
    assert events == ["convert", "validate", "post_init"]
    events.clear()
    decoded = codec.loads(codec.dumps([original]))[0]
    assert decoded == original and type(decoded) is Attrs
    assert events == ["post_init"]
    assert (not hasattr(decoded, "__dict__")) == slots
    events.clear()
    # Valid wire data does not imply attrs validation: decode restores fields.
    decoded = codec.loads(Codec().dumps(HessianObject("Attrs", {"value": -1})))
    assert decoded.value == -1 and events == ["post_init"]


@pytest.mark.parametrize("mutual", [False, True])
def test_registered_cycles_through_containers(mutual):
    codec = Codec()

    @codec.register("Node")
    @dataclass
    class Node:
        label: str
        links: object = None

    first = Node("first")
    second = Node("second") if mutual else first
    first.links = [{"next": second}]
    if mutual:
        second.links = [{"next": first}]
    wire = codec.dumps([first, second, first])
    a, b, c = codec.loads(wire)
    assert type(a) is type(b) is Node and a is c
    assert a.label == "first" and a.links[0]["next"] is b
    if mutual:
        assert a is not b and b.label == "second"
        assert b.links[0]["next"] is a
    else:
        assert a is b
    assert wire.count(b"C\x04Node") == 1
    assert wire.endswith(b"Q\x91")


@pytest.mark.parametrize("mode", ["raise", "hessian_object", "dict"])
def test_unknown_nested_class_modes_preserve_cycles(mode):
    codec = Codec(unknown_class=mode)

    @codec.register("Known")
    @dataclass
    class Known:
        child: object

    unknown = HessianObject("Unknown", {"value": 7})
    unknown.fields["self"] = unknown
    wire = Codec().dumps(HessianObject("Known", {"child": [unknown, unknown]}))
    if mode == "raise":
        with pytest.raises(DecodeError, match="'Unknown' is not registered"):
            codec.loads(wire)
        return
    decoded = codec.loads(wire)
    a, b = decoded.child
    assert type(decoded) is Known and a is b
    assert type(a) is (dict if mode == "dict" else HessianObject)
    fields = a if mode == "dict" else a.fields
    assert fields["value"] == 7 and fields["self"] is a
    if mode == "hessian_object":
        assert a.class_name == "Unknown"
        assert codec.dumps(decoded) == wire


def test_many_registered_definitions_and_value_references_share_stream():
    codec = Codec()

    @codec.register("N")
    @dataclass
    class N:
        x: int

    a, b = N(1), N(2)
    wire = codec.dumps_many([a, b, {"a": a}, b])
    assert wire == b"C\x01N\x91\x01x\x60\x91\x60\x92H\x01aQ\x90ZQ\x91"
    first, second, mapping, last = codec.loads_many(wire)
    assert first == a and second == b
    assert type(first) is type(second) is N
    assert mapping["a"] is first and last is second
    assert codec.dumps(a) == b"C\x01N\x91\x01x\x60\x91"
    with pytest.raises(DecodeError):
        codec.loads(b"Q\x90")


@pytest.mark.parametrize("shape", ["object-list-map", "map-object-list"])
def test_mixed_max_depth_exact_and_one_too_deep(shape):
    codec = Codec(max_depth=3)

    @codec.register("Box")
    @dataclass
    class Box:
        value: object

    at_limit = Box([{"x": None}]) if shape == "object-list-map" else {"x": Box([None])}
    too_deep = Box([{"x": []}]) if shape == "object-list-map" else {"x": Box([[]])}
    assert codec.loads(codec.dumps(at_limit)) == at_limit
    with pytest.raises(RecursionError, match="max_depth=3"):
        codec.dumps(too_deep)
    writer = Codec()
    writer.register("Box", Box)
    with pytest.raises(DecodeError, match="max_depth=3"):
        codec.loads(writer.dumps(too_deep))
    assert codec.loads(codec.dumps(at_limit)) == at_limit


def test_reference_at_depth_limit_does_not_expand_graph():
    codec = Codec(max_depth=1)

    @codec.register("Node")
    @dataclass
    class Node:
        next: object = None

    node = Node()
    node.next = node
    wire = codec.dumps(node)
    assert wire == b"C\x04Node\x91\x04next\x60Q\x90"
    decoded = codec.loads(wire)
    assert type(decoded) is Node and decoded.next is decoded


def test_threaded_registration_and_nested_aliases():
    codec = Codec()
    barrier = Barrier(4)

    @codec.register("Leaf")
    @dataclass(frozen=True, slots=True)
    class Leaf:
        value: int

    def work(worker):
        @dataclass
        class Batch:
            items: list

        barrier.wait(timeout=10)
        codec.register(f"Batch{worker}", Batch)
        for cycle in range(50):
            leaf = Leaf(cycle)
            original = Batch([leaf, {"again": leaf}])
            decoded = codec.loads(codec.dumps(original))
            assert decoded == original and type(decoded) is Batch
            assert type(decoded.items[0]) is Leaf
            assert decoded.items[0] is decoded.items[1]["again"]
        return Batch

    with ThreadPoolExecutor(max_workers=4) as pool:
        classes = list(pool.map(work, range(4)))
    for cls in classes:
        assert type(codec.loads(codec.dumps(cls([])))) is cls
