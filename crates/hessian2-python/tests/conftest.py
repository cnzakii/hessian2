"""Share Python fixtures and load pinned peer bytes once."""

import base64
import dataclasses
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Generic, TypeVar

import hessian2
import pytest
from hessian2 import Codec, Converter

T = TypeVar("T")


FIXTURE_DIR = Path(__file__).parent / "fixtures"
JAVA_FIXTURES = json.loads((FIXTURE_DIR / "java.json").read_text(encoding="utf-8"))
GO_FIXTURES = json.loads((FIXTURE_DIR / "go.json").read_text(encoding="utf-8"))


def java_wire(name: str, writer: str) -> bytes:
    """Return a peer's bytes; lite shares Caucho's bytes when its entry is empty."""
    case = JAVA_FIXTURES[name]
    return base64.b64decode(case[writer] or case["caucho"])


def go_wire(name: str) -> bytes:
    return base64.b64decode(GO_FIXTURES[name]["go"])


@pytest.fixture
def generic_converter():
    @hessian2.java_class("com.example.GenericConvertedBox")
    @dataclasses.dataclass
    class ConvertedBox(Generic[T]):
        value: T

    codec = Codec()
    codec.register(
        Converter(
            ConvertedBox, lambda box: {"v": box.value}, lambda f: ConvertedBox(f["v"])
        ),
        name="com.example.Boxed",
        replace=True,
    )
    return codec, codec.dumps(ConvertedBox("x")), ConvertedBox


@pytest.fixture
def wrong_converter():
    class Money:
        pass

    codec = Codec()
    codec.register(
        Converter(Money, lambda m: {}, lambda f: "wrong"), name="com.example.Money"
    )
    return codec, codec.dumps(Money()), Money


@pytest.fixture
def on_small_stack():
    def run(function):
        # A regression bound, not a benchmark; Future.result forwards worker errors.
        previous = threading.stack_size(512 * 1024)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(function).result(timeout=10)
        finally:
            threading.stack_size(previous)

    return run


@pytest.fixture
def unnamed_models():
    from hessian2 import HessianObject

    @dataclasses.dataclass
    class Plain:
        x: int

    @hessian2.java_class("Uses")
    @dataclasses.dataclass
    class Uses:
        plain: list[Plain]

    wire = hessian2.dumps(
        HessianObject("Uses", {"plain": [HessianObject("com.example.Plain", {"x": 1})]})
    )
    return Plain, Uses, wire


@pytest.fixture
def tagged_java_inputs():
    import datetime as dt
    from zoneinfo import ZoneInfo

    from hessian2 import HessianObject
    from test_java_interop import IllegalState, Typed

    moment = dt.datetime(2026, 1, 2, 3, 4, 5, 6000, tzinfo=ZoneInfo("Asia/Shanghai"))
    shared = [6]
    return {
        "big_integer": (
            hessian2.dumps(5, type=hessian2.BigInteger),
            "java.math.BigInteger",
        ),
        "long_list": (
            hessian2.dumps([1, 2], type=list[hessian2.Long]),
            "java.util.List",
        ),
        "long_object": (hessian2.dumps(5, type=hessian2.Long), "java.lang.Object"),
        "date": (hessian2.dumps(moment, type=hessian2.Date), "java.util.Date"),
        "local_datetime": (
            hessian2.dumps(moment.replace(tzinfo=None), type=hessian2.LocalDateTime),
            "java.time.LocalDateTime",
        ),
        "exception": (
            hessian2.dumps(
                HessianObject("java.lang.RuntimeException", {"detailMessage": "boom"})
            ),
            "java.lang.Throwable",
        ),
        "declared_exception": (
            hessian2.dumps(IllegalState("boom")),
            "java.lang.Throwable",
        ),
        "typed-caucho": (
            hessian2.dumps(Typed(5, [6], None, None, None, [7], {"count": 8}, 9)),
            "interop.Typed",
        ),
        "typed-lite": (
            hessian2.dumps(Typed(5, [6], moment, moment, moment, [7], {"count": 8}, 9)),
            "interop.Typed",
        ),
        "sharing-caucho": (
            hessian2.dumps(Typed(5, shared, None, None, None, shared, {"count": 8}, 9)),
            "interop.Typed",
        ),
        "sharing-lite": (
            hessian2.dumps(
                Typed(5, shared, moment, moment, moment, shared, {"count": 8}, 9)
            ),
            "interop.Typed",
        ),
    }


@pytest.fixture(scope="module")
def java_views(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    import subprocess

    from test_java_interop import CODEC, FIXTURES, JAVA, WRITERS, decode, original

    """What each Java reader decodes from our encoding of every case."""
    assert JAVA is not None
    directory = tmp_path_factory.mktemp("ours")
    for name in FIXTURES:
        values = decode(name, original(name))
        (directory / f"{name}.bin").write_bytes(CODEC.dumps_many(values))
    views = {}
    for reader in WRITERS:
        result = subprocess.run(
            [*JAVA, "describe", reader, str(directory)],
            capture_output=True,
            text=True,
            check=True,
        )
        views[reader] = json.loads(result.stdout)
    return views


@pytest.fixture
def custom_named_tuple():
    from collections import namedtuple

    pair = namedtuple("Pair", "left right", defaults=[9])  # noqa: PYI024

    class Custom(pair):
        def __new__(cls, *args):
            raise AssertionError("must bypass __new__")

    codec = Codec()
    codec.register(Custom, name="Pair", field_naming=None)
    return codec, Custom


@pytest.fixture(scope="module")
def go_views(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    import subprocess

    from test_go_interop import CODEC, GO, NAMES, ROOT, wire

    directory = tmp_path_factory.mktemp("ours")
    for name in NAMES:
        (directory / f"{name}.bin").write_bytes(
            CODEC.dumps_many(CODEC.loads_many(wire(name)))
        )
    result = subprocess.run(
        [GO or "go", "run", ".", "describe", str(directory)],
        cwd=ROOT / "interop" / "go",
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(result.stdout)


@pytest.fixture
def enum_converter_codec():
    from dataclasses import dataclass
    from enum import Enum

    codec = Codec()

    class E(Enum):
        A = 1

    @dataclass
    class Box:
        value: object

    codec.register(E, name="E", field_naming=None)
    codec.register(
        Converter(Box, lambda x: {"value": x.value}, lambda f: Box(**f)), name="Box"
    )
    return codec, E, Box
