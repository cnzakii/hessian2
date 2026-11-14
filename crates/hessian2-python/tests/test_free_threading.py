"""Public contracts for free threading."""

from __future__ import annotations

import dataclasses
import gc
import re
import sys
import sysconfig
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, make_dataclass
from threading import Barrier, Event

import hessian2
import pytest
from assertions import same
from hessian2 import Codec, Converter, HessianObject

FREE_THREADED = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))

WORKERS = 8

CYCLES = 200


@pytest.mark.skipif(not FREE_THREADED, reason="requires free-threaded CPython")
def test_import_leaves_gil_disabled() -> None:
    assert not sys._is_gil_enabled()


def test_shared_codec_while_registering() -> None:
    codec = hessian2.Codec(unknown_class="hessian_object")
    barrier = Barrier(WORKERS)

    def work(worker: int) -> None:
        barrier.wait()
        for cycle in range(CYCLES):
            # Replace a shared and a private registration while other threads
            # encode and decode with the same codec; only the private one is
            # sure to still map to this class.
            if cycle % 25 == 0:
                gc.collect()
            shared = dataclasses.make_dataclass(f"Shared{worker}_{cycle}", [])
            codec.register(shared, name="item.shared", field_naming=None, replace=True)
            item = dataclasses.make_dataclass(f"Item{worker}_{cycle}", [("cycle", int)])
            codec.register(item, name=f"item.{worker}", field_naming=None, replace=True)
            assert same(codec.loads(codec.dumps(item(cycle))).cycle, cycle)
            value = {"worker": worker, "cycle": cycle, "items": [str(cycle)] * 8}
            assert same(codec.loads(codec.dumps(value)), value)
            unknown = hessian2.HessianObject(f"other.{worker}", {"c": cycle})
            assert same(codec.loads(codec.dumps(unknown)), unknown)

    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(work, range(WORKERS)))


def test_registered_class_round_trips_across_threads() -> None:
    codec = hessian2.Codec()

    @dataclasses.dataclass(frozen=True)
    class Point:
        x: int
        y: int

    codec.register(Point, name="com.example.Point", field_naming=None)

    barrier = Barrier(WORKERS)

    def work(worker: int) -> None:
        barrier.wait()
        for cycle in range(CYCLES):
            points = [Point(worker, cycle), Point(cycle, worker)]
            assert same(codec.loads(codec.dumps(points)), points)

    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(work, range(WORKERS)))


def test_threaded_registration_and_nested_aliases():
    codec = Codec()
    barrier = Barrier(4)

    @dataclass(frozen=True, slots=True)
    class Leaf:
        value: int

    codec.register(Leaf, name="Leaf", field_naming=None)

    def work(worker):
        @dataclass
        class Batch:
            items: list

        barrier.wait(timeout=10)
        codec.register(Batch, name=f"Batch{worker}", field_naming=None)
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


def test_spare_refs_reentry_exception_cleanup_and_threads():
    codec = Codec()
    barrier = Barrier(4)

    class Value:
        pass

    def encode(value):
        shared = []
        a, b = hessian2.loads(hessian2.dumps([shared, shared]))
        assert a is b
        if value.fail:
            raise ValueError("callback")
        return {"value": [shared, shared]}

    codec.register(
        Converter(Value, encode, lambda fields: fields["value"]), name="Value"
    )

    def work(worker):
        barrier.wait()
        for _ in range(40):
            value = Value()
            value.fail = True
            with pytest.raises(
                hessian2.EncodeError, match=re.escape("callback")
            ) as error:
                codec.dumps([[], value])
            assert type(error.value.__cause__) is ValueError
            value.fail = False
            shared = []
            a, b, first, second = codec.loads(
                codec.dumps([value, value, shared, shared])
            )
            assert a is b and a[0] is a[1] and first is second
            assert a[0] is not first
            assert same(codec.loads(codec.dumps([worker])), [worker])

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(work, range(4)))


def test_plain_class_registration_during_base_changes():
    class Left:
        pass

    class Right:
        pass

    @dataclasses.dataclass
    class Item(Left):
        value: int

    barrier = Barrier(2)

    def mutate():
        barrier.wait()
        for index in range(1000):
            Item.__bases__ = (Left if index % 2 else Right,)

    def register():
        barrier.wait()
        codec = Codec()
        wire = hessian2.dumps(HessianObject("Item", {"value": 3}))
        for _ in range(1000):
            codec.register(Item, name="Item", field_naming=None)
            assert same(codec.loads(wire).value, 3)

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(mutate), pool.submit(register)]
        for future in futures:
            future.result(timeout=30)


@pytest.mark.skipif(
    not sysconfig.get_config_var("Py_GIL_DISABLED") or not hasattr(sys, "_is_immortal"),
    reason="requires free-threaded CPython with sys._is_immortal",
)
def test_peer_field_names_are_not_immortal():
    field = "review_field_" + uuid.uuid4().hex
    wire = hessian2.dumps(hessian2.HessianObject("Unregistered", {field: None}))
    decoded = hessian2.Codec(unknown_class="dict").loads(wire)
    key = next(iter(decoded))

    assert key == field
    assert not sys._is_immortal(key)


@pytest.mark.skipif(
    not sysconfig.get_config_var("Py_GIL_DISABLED")
    or not hasattr(sys, "getunicodeinternedsize"),
    reason="requires free-threaded CPython with an observable intern table",
)
def test_rejected_class_does_not_grow_intern_table():
    field = "review_rejected_" + uuid.uuid4().hex
    wire = hessian2.dumps(hessian2.HessianObject("Unregistered", {field: None}))
    before = sys.getunicodeinternedsize()

    with pytest.raises(hessian2.DecodeError, match=re.escape("not registered")):
        hessian2.loads(wire)
    gc.collect()
    assert sys.getunicodeinternedsize() == before


def test_concurrent_registration_preserves_independent_entries():
    codec = hessian2.Codec()
    classes = [make_dataclass(f"Value{i}", [("x", int)]) for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda pair: codec.register(
                    pair[1], name=f"Value{pair[0]}", field_naming=None, replace=True
                ),
                enumerate(classes),
            )
        )
    for cls in classes:
        value = codec.loads(codec.dumps(cls(1)))
        assert type(value) is cls
        assert value.x == 1


def test_concurrent_replacement_does_not_change_an_inflight_decode():
    codec = hessian2.Codec()
    entered, release = Event(), Event()

    @dataclass
    class Replacement:
        x: int

    @dataclass
    class Original:
        x: int

        def __post_init__(self):
            entered.set()
            assert release.wait(5)

    codec.register(Original, name="Value", field_naming=None)

    wire = hessian2.dumps_many(
        [
            hessian2.HessianObject("Value", {"x": 1}),
            hessian2.HessianObject("Value", {"x": 2}),
        ]
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(codec.loads_many, wire)
        try:
            assert entered.wait(5)
            codec.register(Replacement, name="Value", field_naming=None, replace=True)
        finally:
            release.set()
        assert result.result(timeout=5) == [Original(1), Original(2)]

    assert codec.loads_many(wire) == [Replacement(1), Replacement(2)]
