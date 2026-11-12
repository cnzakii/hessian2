from __future__ import annotations

import dataclasses
import gc
import sys
import sysconfig
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import hessian2
import pytest

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
            assert codec.loads(codec.dumps(item(cycle))).cycle == cycle
            value = {"worker": worker, "cycle": cycle, "items": [str(cycle)] * 8}
            assert codec.loads(codec.dumps(value)) == value
            unknown = hessian2.HessianObject(f"other.{worker}", {"c": cycle})
            assert codec.loads(codec.dumps(unknown)) == unknown

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
            assert codec.loads(codec.dumps(points)) == points

    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(work, range(WORKERS)))
