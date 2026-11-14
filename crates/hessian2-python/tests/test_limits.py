"""Public contracts for limits."""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import hessian2
import pytest
from assertions import same
from hessian2 import Codec, Converter, DecodeError, EncodeError, HessianObject


def nested_list(depth: int) -> Any:
    value: Any = None
    for _ in range(depth):
        value = [value]
    return value


def test_the_default_max_depth_is_256():
    assert Codec().max_depth == 256


@pytest.mark.parametrize("limit", [2, 256], ids=["custom", "default"])
def test_encoding_accepts_a_value_exactly_at_max_depth(limit):
    codec = Codec(max_depth=limit)
    value = nested_list(limit)

    data = codec.dumps(value)

    assert data == b"\x79" * limit + b"N"


@pytest.mark.parametrize("limit", [2, 256], ids=["custom", "default"])
def test_encoding_rejects_one_container_past_max_depth(limit):
    with pytest.raises(EncodeError, match=re.escape(f"max_depth={limit}")):
        Codec(max_depth=limit).dumps(nested_list(limit + 1))


@pytest.mark.parametrize("limit", [2, 256], ids=["custom", "default"])
def test_decoding_accepts_a_value_exactly_at_max_depth(limit):
    data = b"\x79" * limit + b"N"

    assert same(Codec(max_depth=limit).loads(data), nested_list(limit))


@pytest.mark.parametrize("limit", [2, 256], ids=["custom", "default"])
def test_decoding_rejects_one_container_past_max_depth(limit):
    with pytest.raises(DecodeError, match=re.escape(f"max_depth={limit}")):
        Codec(max_depth=limit).loads(b"\x79" * (limit + 1) + b"N")


def test_object_nesting_obeys_the_custom_depth_limit():
    with pytest.raises(DecodeError, match=re.escape("max_depth=2")):
        Codec(max_depth=2, unknown_class="dict").loads(b"C\x01A\x91\x01f\x60\x60\x60N")


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


@hessian2.java_class("limits.Box")
@dataclasses.dataclass
class DepthBox:
    value: object


@pytest.mark.parametrize(
    "value",
    [DepthBox([{"x": None}]), {"x": DepthBox([None])}],
    ids=["object-list-map", "map-object-list"],
)
def test_mixed_containers_count_together_at_max_depth(value):
    data = Codec(max_depth=3).dumps(value)
    codec = Codec(max_depth=3)
    codec.register(DepthBox)

    assert same(codec.loads(data), value)


@pytest.mark.parametrize(
    "value",
    [DepthBox([{"x": []}]), {"x": DepthBox([[]])}],
    ids=["object-list-map", "map-object-list"],
)
def test_encoding_mixed_containers_rejects_one_past_max_depth(value):
    with pytest.raises(EncodeError, match=re.escape("max_depth=3")):
        Codec(max_depth=3).dumps(value)


@pytest.mark.parametrize(
    "value",
    [DepthBox([{"x": []}]), {"x": DepthBox([[]])}],
    ids=["object-list-map", "map-object-list"],
)
def test_decoding_mixed_containers_rejects_one_past_max_depth(value):
    data = Codec().dumps(value)
    codec = Codec(max_depth=3)
    codec.register(DepthBox)

    with pytest.raises(DecodeError, match=re.escape("max_depth=3")):
        codec.loads(data)


def test_reference_at_depth_limit_does_not_expand_graph():
    codec = Codec(max_depth=1)

    @dataclass
    class Node:
        next: object = None

    codec.register(Node, name="Node", field_naming=None)

    node = Node()
    node.next = node

    wire = codec.dumps(node)

    assert wire == b"C\x04Node\x91\x04next\x60Q\x90"
    decoded = codec.loads(wire)
    assert type(decoded) is Node and decoded.next is decoded


@pytest.mark.parametrize(
    "value",
    [dt.datetime(2024, 1, 1), 2**100, [dt.date(2024, 1, 1)]],
    ids=["nested-time-handles", "bigint-mag", "date-in-list"],
)
def test_builtin_encoding_respects_depth(value):
    with pytest.raises(hessian2.EncodeError, match=re.escape("max_depth=1")):
        Codec(max_depth=1).dumps(value)


T = TypeVar("T")


def test_annotated_objects_decode_exactly_max_depth_on_a_512_kib_stack(on_small_stack):
    chain = None
    for _ in range(256):
        chain = Node(1, chain)
    data = Codec(max_depth=300).dumps(chain)

    decoded = on_small_stack(lambda: hessian2.loads(data, type=Node[int]))

    assert decoded == chain


def test_annotated_objects_reject_one_past_max_depth_on_a_512_kib_stack(on_small_stack):
    chain = None
    for _ in range(257):
        chain = Node(1, chain)
    data = Codec(max_depth=300).dumps(chain)

    with pytest.raises(DecodeError, match=re.escape("max_depth=256")):
        on_small_stack(lambda: hessian2.loads(data, type=Node[int]))


@pytest.mark.parametrize("hook", ["post-init", "factory", "converter"])
def test_back_reference_checks_stay_linear_when_application_hooks_run(hook):
    fields = [
        ("items", list[int]),
        ("unused", object, dataclasses.field(default_factory=list)),
    ]
    classes = {
        "post-init": dataclasses.make_dataclass(
            "Hooked",
            [("items", list[int])],
            namespace={"__post_init__": lambda self: None},
        ),
        "factory": dataclasses.make_dataclass("Hooked", fields),
        "converter": dataclasses.make_dataclass("Hooked", [("items", list[int])]),
    }
    record = hessian2.java_class("limits.Hooked")(classes[hook])
    codec = Codec()
    converters = {
        "post-init": lambda: None,
        "factory": lambda: None,
        "converter": lambda: codec.register(
            Converter(
                record,
                lambda value: {"items": value.items},
                lambda f: record(f["items"]),
            ),
            name="limits.Hooked",
        ),
    }
    converters[hook]()

    # Converter fields are intentionally untyped; another list[int] position
    # checks the shared value before each callback.
    @hessian2.java_class("limits.Holder")
    @dataclasses.dataclass
    class Holder:
        items: list[int]
        callback: record

    shared = list(range(50_000))
    data = hessian2.dumps(
        [
            HessianObject(
                "limits.Holder",
                {
                    "items": shared,
                    "callback": HessianObject("limits.Hooked", {"items": shared}),
                },
            )
            for _ in range(3_000)
        ]
    )

    started = time.perf_counter()

    decoded = codec.loads(data, type=list[Holder])

    # A complexity bound, not a benchmark: clearing after each hook rewalks
    # 150 million elements; the successful check must survive all three hooks.

    assert time.perf_counter() - started < 1.5
    assert decoded[-1].items is decoded[0].items


@hessian2.java_class("com.example.Node")
@dataclasses.dataclass
class Node(Generic[T]):
    value: T
    next: Node[T] | None = None


@pytest.mark.parametrize(
    "annotation",
    [list[list[int]], tuple[list[int]]],
    ids=["homogeneous", "fixed-tuple"],
)
def test_typed_container_decoding_accepts_exactly_max_depth(annotation):
    assert same(
        Codec(max_depth=2).loads(hessian2.dumps([[1]]), type=annotation),
        {list[list[int]]: [[1]], tuple[list[int]]: ([1],)}[annotation],
    )


@pytest.mark.parametrize(
    "annotation",
    [list[list[list[int]]], tuple[list[list[int]]]],
    ids=["homogeneous", "fixed-tuple"],
)
def test_typed_container_decoding_rejects_one_past_max_depth(annotation):
    with pytest.raises(DecodeError, match=re.escape("max_depth=2")):
        Codec(max_depth=2).loads(hessian2.dumps([[[1]]]), type=annotation)
