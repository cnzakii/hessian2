"""Public contracts for callbacks."""

from __future__ import annotations

import dataclasses
import gc
import re
import subprocess
import sys
import textwrap
import typing
import weakref
from dataclasses import dataclass
from enum import Enum
from typing import Any

import hessian2
import pytest
from assertions import same
from hessian2 import Codec, Converter, DecodeError, EncodeError, HessianObject


def test_list_changing_size_while_encoding() -> None:
    class Growing(list):  # type: ignore[type-arg]
        def __iter__(self):  # type: ignore[no-untyped-def]
            yield from (1, 2, 3)

    with pytest.raises(EncodeError, match=re.escape("changed size")):
        hessian2.dumps(Growing([1]))


def make_codec(**kwargs: Any) -> Codec:
    return Codec(**kwargs)


def test_converters_restore_their_python_value_from_fields():
    @dataclasses.dataclass
    class Point:
        x: int
        y: int

    codec = Codec()
    codec.register(
        Converter(Point, lambda p: {"x": p.x, "y": p.y}, lambda f: Point(**f)),
        name="Point",
    )

    decoded = codec.loads(codec.dumps(Point(1, 2)))

    assert type(decoded) is Point
    assert decoded == Point(1, 2)


def test_converter_repr_names_the_python_type():
    class Point:
        pass

    assert (
        repr(Converter(Point, lambda p: {}, lambda f: Point()))
        == f"Converter({Point.__qualname__})"
    )


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

    assert same(first, value)
    assert first is second
    assert calls == [value]
    assert same(hessian2.loads(hessian2.dumps(value)), value)


def test_converter_cycle_fails_during_decoding() -> None:
    class Node:
        def __init__(self, next: Any = None) -> None:
            self.next = next

    node = Node()
    node.next = node
    codec = Codec()
    codec.register(Converter(Node, vars, lambda f: Node(f["next"])), name="Node")

    with pytest.raises(DecodeError, match=re.escape("still being decoded")):
        codec.loads(codec.dumps(node))


@pytest.mark.parametrize(
    ("fields", "message"),
    [([1], "dict"), ({1: 2}, "str")],
    ids=["not-dict", "non-string-key"],
)
def test_converter_fields_must_be_a_string_keyed_dict(fields, message):
    codec = Codec()
    codec.register(Converter(complex, lambda c: fields, lambda f: 0j), name="Converted")

    with pytest.raises(EncodeError, match=re.escape(message)):
        codec.dumps(1j)


class Point(typing.NamedTuple):
    x_pos: int
    label: str = "origin"


@pytest.mark.parametrize("error_type", [ValueError, TypeError])
def test_decode_hook_value_and_type_errors_become_decode_errors_with_a_cause(
    error_type,
):
    @hessian2.java_class("Checked")
    @dataclasses.dataclass
    class Checked:
        x: int

        def __post_init__(self):
            raise error_type("invalid x")

    data = hessian2.dumps(HessianObject("Checked", {"x": -1}))

    with pytest.raises(DecodeError, match=re.escape("invalid x")) as caught:
        hessian2.loads(data, type=Checked)
    assert type(caught.value.__cause__) is error_type


@pytest.mark.parametrize("error_type", [ValueError, TypeError])
def test_encode_hook_value_and_type_errors_become_encode_errors_with_a_cause(
    error_type,
):
    class Money:
        pass

    def broken(value):
        raise error_type("cannot convert")

    codec = Codec()
    codec.register(Converter(Money, broken, lambda f: Money()), name="Money")

    with pytest.raises(EncodeError, match=re.escape("cannot convert")) as caught:
        codec.dumps(Money())
    assert type(caught.value.__cause__) is error_type


def test_decode_hook_programming_errors_propagate_unchanged():
    @hessian2.java_class("Buggy")
    @dataclasses.dataclass
    class Buggy:
        x: int

        def __post_init__(self):
            raise AttributeError("bug")

    with pytest.raises(AttributeError, match=re.escape("bug")):
        hessian2.loads(hessian2.dumps(HessianObject("Buggy", {"x": 1})), type=Buggy)


@pytest.mark.parametrize(
    ("direction", "expected_error", "cause"),
    [("decode", DecodeError, EncodeError), ("encode", EncodeError, DecodeError)],
)
def test_nested_codec_failures_take_the_outer_operations_error_class(
    direction, expected_error, cause
):
    @dataclasses.dataclass
    class P:
        a: int

    decode_codec = Codec()
    decode_codec.register(
        Converter(P, lambda p: {"a": p.a}, lambda f: hessian2.dumps(object())), name="P"
    )
    encode_codec = Codec()
    encode_codec.register(
        Converter(P, lambda p: hessian2.loads(b"\xff"), lambda f: P(**f)), name="P"
    )
    operations = {
        "decode": lambda: decode_codec.loads(decode_codec.dumps(P(1))),
        "encode": lambda: encode_codec.dumps(P(1)),
    }

    with pytest.raises(expected_error) as caught:
        operations[direction]()
    assert type(caught.value.__cause__) is cause


def test_registry_replacement_finalizer_can_reenter_codec():
    program = """
        from dataclasses import dataclass
        import hessian2
        codec = hessian2.Codec()
        events = []
        @dataclass
        class A:
            x: int = 0
        @dataclass
        class B:
            x: int = 0
        class Encode:
            def __call__(self, value):
                return {'x': value.x}
            def __del__(self):
                events.append(codec.loads(b'N'))
        codec.register(hessian2.Converter(A, Encode(), lambda f: A(**f)), name='X')
        codec.register(B, name='X', replace=True)
        assert events == [None]
        print('ok')
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(program)],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )

    assert result.stdout.strip() == "ok"


def test_invalid_enum_name_does_not_invoke_object_methods():
    codec = hessian2.Codec()
    events = []

    class E(Enum):
        A = 1

    codec.register(E, name="E", field_naming=None)

    @dataclass(eq=False)
    class Name:
        value: int

        def __hash__(self):
            events.append("hash")
            return hash("A")

        def __eq__(self, other):
            events.append("eq")
            return False

        def __str__(self):
            events.append("str")
            return "name"

    codec.register(Name, name="Name", field_naming=None)

    wire = hessian2.dumps(
        hessian2.HessianObject(
            "E", {"name": hessian2.HessianObject("Name", {"value": 1})}
        )
    )

    with pytest.raises(hessian2.DecodeError):
        codec.loads(wire)
    assert events == []


def test_map_encoding_uses_the_items_snapshot_from_before_callbacks():
    @dataclass
    class Value:
        x: int

    values = {"value": Value(1)}

    def encode(value):
        values["added"] = 2
        return {"x": value.x}

    codec = Codec()
    codec.register(Converter(Value, encode, lambda f: Value(**f)), name="Value")

    wire = codec.dumps(values)

    assert same(codec.loads(wire), {"value": Value(1)})


@pytest.mark.parametrize("event", ["post_init", "hash", "eq"])
def test_explicit_key_annotations_allow_registered_key_hooks(event):
    codec = hessian2.Codec()
    events = []

    @dataclass(eq=False)
    class Key:
        value: int

        def __post_init__(self):
            events.append("post_init")

        def __hash__(self):
            events.append("hash")
            return 0

        def __eq__(self, other):
            events.append("eq")
            return self.value == other.value

    codec.register(Key, name="Key", field_naming=None)

    first = hessian2.HessianObject("Key", {"value": 1})
    second = hessian2.HessianObject("Key", {"value": 2})
    data = b"H" + hessian2.dumps_many([first, 0, second, 0]) + b"Z"
    codec.loads(data, type=dict[Key, int])

    assert event in events


def test_encoding_existing_map_keys_does_not_rehash_them():
    codec = hessian2.Codec()
    events = []

    @dataclass(eq=False)
    class Key:
        value: int

        def __hash__(self):
            events.append("hash")
            return 0

        def __eq__(self, other):
            events.append("eq")
            return self.value == other.value

    codec.register(Key, name="Key", field_naming=None)

    values = {Key(1): 1, Key(2): 2}
    events.clear()
    codec.dumps(values)

    assert events == []


def test_list_size_mutation_is_an_ordinary_error():
    codec = hessian2.Codec()

    @dataclass
    class Value:
        x: int

    values = [Value(1)]

    def encode(value):
        values.append(None)
        return {"x": value.x}

    codec.register(
        hessian2.Converter(Value, encode, lambda f: Value(**f)), name="Value"
    )

    with pytest.raises(hessian2.EncodeError, match=re.escape("changed size")):
        codec.dumps(values)


def test_growing_list_stops_before_visiting_appended_elements():
    @dataclass
    class Value:
        x: int

    values = [Value(0)]
    calls = []

    def encode(value):
        calls.append(value)
        # Bound the reproduction if a wrong implementation visits new items.
        if len(calls) < 8:
            values.append(Value(len(calls)))
        return {"x": value.x}

    codec = Codec()
    codec.register(Converter(Value, encode, lambda f: Value(**f)), name="Value")

    with pytest.raises(EncodeError, match=re.escape("changed size")):
        codec.dumps(values)
    assert len(calls) == 1


def test_mutable_input_is_copied_before_callbacks():
    codec = hessian2.Codec()

    @dataclass
    class Value:
        x: int

        def __post_init__(self):
            data[:] = b"N" * len(data)

    codec.register(Value, name="Value", field_naming=None)

    value = hessian2.HessianObject("Value", {"x": 1})
    data = bytearray(hessian2.dumps_many([value, value]))

    decoded = codec.loads_many(data)

    assert decoded[0].x == 1
    assert decoded[0] is decoded[1]
    assert data == b"N" * len(data)


def test_register_during_loads_uses_one_snapshot():
    codec = hessian2.Codec()

    @dataclass
    class Replacement:
        x: int

    @dataclass
    class Original:
        x: int

        def __post_init__(self):
            codec.register(Replacement, name="Value", field_naming=None, replace=True)

    codec.register(Original, name="Value", field_naming=None)

    wire = hessian2.dumps_many(
        [
            hessian2.HessianObject("Value", {"x": 1}),
            hessian2.HessianObject("Value", {"x": 2}),
        ]
    )

    decoded = codec.loads_many(wire)

    assert decoded == [Original(1), Original(2)]
    assert codec.loads_many(wire) == [Replacement(1), Replacement(2)]


def test_register_during_dumps_uses_one_snapshot():
    codec = hessian2.Codec()

    @dataclass
    class Original:
        x: int

    codec.register(Original, name="Value", field_naming=None)

    @dataclass
    class Replacement:
        x: int

    @dataclass
    class Trigger:
        pass

    def encode(_):
        codec.register(Replacement, name="Value", field_naming=None, replace=True)
        return {}

    codec.register(
        hessian2.Converter(Trigger, encode, lambda _: Trigger()), name="Trigger"
    )
    wire = codec.dumps_many([Trigger(), Original(1)])

    decoded = hessian2.Codec(unknown_class="dict").loads_many(wire)

    assert decoded == [{}, {"x": 1}]
    with pytest.raises(hessian2.EncodeError):
        codec.dumps(Original(1))


def test_gc_keeps_the_old_registration_alive_during_a_call():

    codec = hessian2.Codec()
    seen = []

    class Original:
        pass

    @dataclass
    class Replacement:
        value: int

    class ToFields:
        def __call__(self, value):
            codec.register(Replacement, name="Value", field_naming=None, replace=True)
            gc.collect()
            assert callback_ref() is self
            seen.append(value)
            return {"value": 1}

    callback = ToFields()
    callback_ref = weakref.ref(callback)
    codec.register(
        hessian2.Converter(Original, callback, lambda f: Original()), name="Value"
    )
    del callback
    value = Original()

    wire = codec.dumps_many([value, value])

    assert seen == [value]
    gc.collect()
    assert callback_ref() is None
    first, second = codec.loads_many(wire)
    assert first is second and first == Replacement(1)


def test_post_init_mutations_do_not_invalidate_successful_back_reference_checks():
    seen = []

    @hessian2.java_class("callbacks.Hooked")
    @dataclasses.dataclass
    class Hooked:
        items: list[int]

        def __post_init__(self):
            seen.append(self)
            if len(seen) == 2:
                self.items[0] = "application mutation"

    # The second object's reference is checked before its trusted hook mutates it.
    shared = [1]
    data = hessian2.dumps(
        [HessianObject("callbacks.Hooked", {"items": shared}) for _ in range(3)]
    )

    decoded = hessian2.loads(data, type=list[Hooked])

    assert decoded[0].items is decoded[1].items is decoded[2].items
    assert same(decoded[-1].items, ["application mutation"])
