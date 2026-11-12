"""Small wire fixtures checked against Caucho 4.0.66 / hessian-lite 4.0.5."""

import struct
from dataclasses import dataclass
from enum import Enum

import hessian2
import pytest


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        (b"U\x01t\x90Z", [0]),
        (b"V\x01t\x91\x90", [0]),
        (b"W\x90Z", [0]),
        (b"X\x91\x90", [0]),
        (b"\x71\x01t\x90", [0]),
        (b"\x79\x90", [0]),
        (b"M\x01t\x01k\x91Z", {"k": 1}),
        (b"H\x01k\x91Z", {"k": 1}),
        (b"R\0\0R\0\x01a\x01b", "ab"),
        (b"A\0\0A\0\x01a\x21b", b"ab"),
        (b"\x02\xed\xa0\xbd\xed\xb0\xb8", "🐸"),
        (b"\x02\xf0\x9f\x90\xb8", "🐸"),
        (b"\x03\xed\xa0\x80x\xed\xb0\x80", "\ud800x\udc00"),
    ],
)
def test_legal_wire_forms(wire, expected):
    assert hessian2.loads(wire) == expected


@pytest.mark.parametrize(
    "length", [0, 15, 16, 31, 32, 1023, 1024, 32768, 32769, 65535, 65536]
)
def test_string_binary_boundaries(length):
    for value in ("a" * length, b"a" * length):
        assert hessian2.loads(hessian2.dumps(value)) == value


def test_surrogate_pair_at_java_chunk_boundary():
    value = "a" * 32767 + "🐸"
    wire = hessian2.dumps(value)
    assert wire[:3] == b"R\x7f\xff"
    assert wire[32770:] == b"\x02\xed\xa0\xbd\xed\xb0\xb8"
    assert hessian2.loads(wire) == value


@pytest.mark.parametrize(
    "wire",
    [
        b"\x01\xc0\x80",
        b"\x01\xc1\xbf",
        b"\x01\xe0\x80\x80",
        b"\x01\xc2",
        b"\x01\xc2a",
        b"\x01\xe4\xb8",
        b"\x02\xf0\x80\x80\x80",
        b"\x02\xf4\x90\x80\x80",
        b"\x02\xf5\x80\x80\x80",
        b"\x01\xf0\x9f\x90\xb8",
        b"Q\x90",
        b"Q\x80",
        b"\x79Q\x91",
        b"\x60",
        b"\x70\x90",
        b"X\x49\x7f\xff\xff\xff",
        b"C\x01X\x49\x7f\xff\xff\xff",
        b"S\xff\xffx",
        b"B\xff\xffx",
        b"H\x90Z",
    ],
)
def test_malformed_inputs_raise_decode_error(wire):
    with pytest.raises(hessian2.DecodeError):
        hessian2.loads(wire)


def test_stream_tables_and_cycles():
    codec = hessian2.Codec(unknown_class="hessian_object")
    obj = hessian2.HessianObject("Node")
    obj.fields["self"] = obj
    items = [obj]
    items.append(items)
    wire = codec.dumps_many([obj, items, obj])
    first, second, third = codec.loads_many(wire)
    assert first is third is second[0]
    assert first.fields["self"] is first
    assert second[1] is second
    assert hessian2.loads_many(b"M\x01tZ\x70\x90U\x90Z") == [{}, [], []]
    with pytest.raises(hessian2.DecodeError, match="trailing"):
        codec.loads(wire)


def test_tables_reset_between_calls_and_class_shapes_stay_distinct():
    codec = hessian2.Codec(unknown_class="dict")
    first = hessian2.HessianObject("X", {"a": 1})
    second = hessian2.HessianObject("X", {"b": 2})
    wire = codec.dumps_many([first, second])
    assert codec.loads_many(wire) == [{"a": 1}, {"b": 2}]
    assert codec.dumps_many([first, second]) == wire
    assert codec.loads_many(wire) == [{"a": 1}, {"b": 2}]
    for reference in [b"Q\x90", b"\x60", b"\x70\x90"]:
        with pytest.raises(hessian2.DecodeError):
            codec.loads(reference)


def test_registered_object_can_reference_itself_before_completion():
    codec = hessian2.Codec()

    @dataclass
    class Node:
        self: object = None

    codec.register(Node, name="Node", field_naming=None)

    node = codec.loads(b"C\x04Node\x91\x04self\x60Q\x90")
    assert node.self is node


def test_converter_and_enum_reference_slots():
    codec = hessian2.Codec()

    class E(Enum):
        A = 1

    codec.register(E, name="E", field_naming=None)

    @dataclass
    class Box:
        value: object

    codec.register(
        hessian2.Converter(Box, lambda x: {"value": x.value}, lambda f: Box(**f)),
        name="Box",
    )
    box = Box(1)
    a, b, c, d = codec.loads_many(codec.dumps_many([E.A, box, E.A, box]))
    assert a is c is E.A
    assert b is d
    for name, field in [("E", "name"), ("Box", "value")]:
        cyclic = hessian2.HessianObject(name)
        cyclic.fields[field] = cyclic
        with pytest.raises(hessian2.DecodeError, match="still being decoded"):
            codec.loads(hessian2.dumps(cyclic))


@pytest.mark.parametrize("millis", [-(1 << 63), (1 << 63) - 1])
def test_out_of_python_range_dates_fail_cleanly(millis):
    with pytest.raises(hessian2.DecodeError, match="datetime range"):
        hessian2.loads(b"J" + struct.pack(">q", millis))


# Class, field, and type names are Java identifiers, which cannot hold an
# unpaired surrogate; only hand-built streams carry one, and they are rejected.
@pytest.mark.parametrize(
    "wire",
    [
        b"\x70\x01\xed\xa0\x80",
        b"M\x01\xed\xa0\x80Z",
        b"C\x01X\x91\x01\xed\xa0\x80\x60\x90",
        b"C\x01\xed\xa0\x80\x90\x60",
    ],
)
def test_lone_surrogate_metadata_is_rejected(wire):
    with pytest.raises(hessian2.DecodeError):
        hessian2.Codec(unknown_class="dict").loads(wire)


@pytest.mark.parametrize(
    "value",
    [hessian2.HessianObject("\ud800"), hessian2.HessianObject("X", {"\ud800": 0})],
)
def test_lone_surrogate_metadata_is_not_encoded(value):
    with pytest.raises(UnicodeEncodeError):
        hessian2.dumps(value)
