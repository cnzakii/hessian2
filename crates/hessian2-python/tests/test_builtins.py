"""Public contracts for builtins."""

from __future__ import annotations

import ast
import datetime
import inspect
import math
import re
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import hessian2
import pytest
from assertions import same
from hessian2 import Codec, DecodeError, EncodeError, HessianObject

UTC = datetime.UTC


def roundtrip(value: Any, codec: Codec | None = None) -> Any:
    codec = codec or Codec()
    return codec.loads(codec.dumps(value))


ROUND_TRIPS = [
    None,
    True,
    False,
    0,
    -16,
    47,
    -(2**31),
    2**31 - 1,
    2**31,
    -(2**63),
    2**63 - 1,
    0.0,
    1.0,
    -128.0,
    32767.0,
    0.001,
    3.14159,
    1e300,
    -1e-300,
    math.inf,
    "",
    "hello",
    "中文",
    "a😀b",
    "x\ud83dy",
    "\udc00",
    "a" * 0x8000 + "😀",
    "😀" * 20000,
    b"",
    b"\x00\xff" * 50000,
    [],
    [1, "two", [3.0, None]],
    {},
    {"a": 1, 2: "b", 3.5: [None]},
]


@pytest.mark.parametrize("value", ROUND_TRIPS, ids=lambda v: repr(v)[:40])
def test_python_values_keep_their_type_and_value(value: Any) -> None:
    assert same(roundtrip(value), value)


def test_nan_round_trips() -> None:
    assert math.isnan(roundtrip(math.nan))


@pytest.mark.parametrize(
    ("value", "converted"),
    [
        ((1, 2), [1, 2]),
        ({3}, [3]),
        (frozenset({4}), [4]),
        (bytearray(b"ab"), b"ab"),
        (memoryview(b"cd"), b"cd"),
    ],
    ids=["tuple", "set", "frozenset", "bytearray", "memoryview"],
)
def test_python_values_map_to_the_documented_decoded_type(
    value: Any, converted: Any
) -> None:
    assert same(roundtrip(value), converted)


class Flag(int):
    pass


class Text(str):
    pass


class Ratio(float):
    pass


class Bag(dict):  # type: ignore[type-arg]
    pass


class Items(list):  # type: ignore[type-arg]
    pass


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Flag(3), 3),
        (Text("t"), "t"),
        (Ratio(0.5), 0.5),
        (Bag(a=1), {"a": 1}),
        (Items([1]), [1]),
    ],
    ids=["int", "str", "float", "dict", "list"],
)
def test_subclasses_decode_as_their_builtin_base(value, expected):
    decoded = roundtrip(value)

    assert same(decoded, expected)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (Decimal("NaN"), "BigDecimal"),
        (Decimal("-Infinity"), "BigDecimal"),
        (object(), "register"),
        (complex(1, 2), "register"),
    ],
    ids=repr,
)
def test_values_without_a_hessian_mapping_raise_encode_error(
    value: Any, message: str
) -> None:
    with pytest.raises(EncodeError, match=message):
        hessian2.dumps(value)


@pytest.mark.parametrize(
    ("error", "base"), [(DecodeError, ValueError), (EncodeError, TypeError)]
)
def test_codec_errors_have_the_documented_python_base(error, base):
    assert issubclass(error, base)


@pytest.mark.parametrize("buffer", [bytearray(b"\x91"), memoryview(b"\x91")], ids=type)
def test_loads_accepts_buffers(buffer: Any) -> None:
    assert same(hessian2.loads(buffer), 1)


def test_loads_rejects_non_buffers() -> None:
    with pytest.raises(TypeError):
        hessian2.loads("\x91")  # type: ignore[arg-type]


@pytest.mark.parametrize("codec", [hessian2, Codec()], ids=["module", "codec"])
def test_single_value_methods_accept_keywords(codec):
    assert same(codec.loads(data=codec.dumps(obj=7)), 7)


@pytest.mark.parametrize("codec", [hessian2, Codec()], ids=["module", "codec"])
def test_stream_methods_accept_keywords(codec):
    assert same(codec.loads_many(data=codec.dumps_many(values=[7, None])), [7, None])


UNKNOWN = b"C\x0bcom.x.Thing\x92\x01a\x01b\x60\x91\x02hi"


def test_unknown_class_error() -> None:
    with pytest.raises(DecodeError, match=re.escape("'com.x.Thing' is not registered")):
        hessian2.loads(UNKNOWN)


def test_unknown_class_mode_builds_a_hessian_object():
    value = Codec(unknown_class="hessian_object").loads(UNKNOWN)

    assert type(value) is HessianObject
    assert value == HessianObject("com.x.Thing", {"a": 1, "b": "hi"})


def test_unknown_class_object_can_be_reencoded():
    value = Codec(unknown_class="hessian_object").loads(UNKNOWN)

    assert hessian2.dumps(value) == UNKNOWN


def test_hessian_object_supports_positional_pattern_matching():
    value = HessianObject("com.x.Thing", {"a": 1})

    match value:
        case HessianObject("com.x.Thing", {"a": a}):
            captured = a
        case _:
            pytest.fail("pattern did not match")

    assert same(captured, 1)


def test_unknown_class_dict() -> None:
    assert same(Codec(unknown_class="dict").loads(UNKNOWN), {"a": 1, "b": "hi"})


def test_hessian_object_repr_exposes_class_and_fields():
    assert repr(HessianObject("T", {"x": [1]})) == "HessianObject('T', {'x': [1]})"


@pytest.mark.parametrize(
    ("other", "expected"),
    [
        (HessianObject("T", {"x": [1]}), True),
        (HessianObject("U", {"x": [1]}), False),
        (("T", {"x": [1]}), False),
    ],
    ids=["same", "other-class", "tuple"],
)
def test_hessian_object_equality_uses_class_and_fields(other, expected):
    assert (HessianObject("T", {"x": [1]}) == other) is expected


def test_hessian_object_defaults_to_empty_fields():
    assert HessianObject("E").fields == {}


def test_hessian_object_is_unhashable():
    with pytest.raises(TypeError, match=re.escape("unhashable")):
        hash(HessianObject("T"))


def test_hessian_object_class_name_is_readonly():
    value = HessianObject("T")

    with pytest.raises(AttributeError):
        value.class_name = "U"


def test_unhashable_key_is_a_decode_error() -> None:
    with pytest.raises(DecodeError, match=re.escape("unhashable map key of type list")):
        hessian2.loads(hessian2.dumps({(1,): 2}), type=dict[list[int], int])


@pytest.mark.parametrize(
    "key",
    [None, True, 2**63 - 1, -(2**63), 1.5, "k", b"k", datetime.date(2026, 1, 2)],
    ids=repr,
)
def test_keys_a_peer_cannot_make_collide_decode_without_an_annotation(
    key: Any,
) -> None:
    decoded = hessian2.loads(hessian2.dumps({key: 1}))

    assert decoded == {key: 1}
    assert type(next(iter(decoded))) is type(key)


@pytest.mark.parametrize(
    "key",
    [(1, 2), 2**64, Decimal("1.5"), uuid.UUID(int=5)],
    ids=["list", "BigInteger", "BigDecimal", "UUID"],
)
@pytest.mark.parametrize(
    "annotation", [None, dict, dict[Any, int]], ids=["untyped", "dict", "any-key"]
)
def test_collision_prone_map_keys_are_rejected_without_a_key_type(key, annotation):
    data = hessian2.dumps({key: 1})

    with pytest.raises(
        DecodeError, match=re.escape("needs an annotation that names its type")
    ):
        hessian2.loads(data, type=annotation)


@pytest.mark.parametrize(
    ("key", "annotation"),
    [
        ((1, 2), tuple[int, ...]),
        (2**64, int),
        (Decimal("1.5"), Decimal),
        (uuid.UUID(int=5), uuid.UUID),
    ],
    ids=["list", "BigInteger", "BigDecimal", "UUID"],
)
def test_explicit_key_types_accept_collision_prone_map_keys(key, annotation):
    data = hessian2.dumps({key: 1})

    assert same(hessian2.loads(data, type=dict[annotation, int]), {key: 1})


@pytest.mark.parametrize(
    "value",
    [hessian2.HessianObject("\ud800"), hessian2.HessianObject("X", {"\ud800": 0})],
)
def test_lone_surrogate_metadata_is_not_encoded(value):
    with pytest.raises(UnicodeEncodeError):
        hessian2.dumps(value)


@pytest.mark.parametrize(
    ("value", "wire"),
    [
        (None, b"N"),
        (True, b"T"),
        (False, b"F"),
        (0, b"\x90"),
        (2**31, b"L\0\0\0\0\x80\0\0\0"),
        (0.0, b"\x5b"),
        ("hello", b"\x05hello"),
        (b"", b"\x20"),
        ([], b"\x78"),
        ({}, b"HZ"),
        (datetime.datetime(1998, 5, 8, 9, 51, tzinfo=UTC), b"K\0\xe3\x83\x8f"),
    ],
    ids=[
        "null",
        "true",
        "false",
        "int",
        "long",
        "double",
        "str",
        "bytes",
        "list",
        "dict",
        "aware-datetime",
    ],
)
def test_builtin_values_select_their_hessian_type(value, wire):
    assert hessian2.dumps(value) == wire


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        (b"\x02\xed\xa0\xbd\xed\xb0\xb8", "🐸"),
        (b"\x02\xf0\x9f\x90\xb8", "🐸"),
        (b"\x03\xed\xa0\x80x\xed\xb0\x80", "\ud800x\udc00"),
    ],
    ids=["cesu8", "utf8", "unpaired-surrogates"],
)
def test_wire_string_units_map_to_python_codepoints(wire, expected):
    assert same(hessian2.loads(wire), expected)


# No stubtest dependency is installed: compare the calls permitted by the stub
# with CPython's signatures. PyO3 does not expose runtime type annotations.
STUB = ast.parse(Path(hessian2.__file__).with_name("_core.pyi").read_text())
STUB_METHODS = [
    (cls.name, member)
    for cls in STUB.body
    if isinstance(cls, ast.ClassDef)
    for member in cls.body
    if isinstance(member, ast.FunctionDef)
    and not any(
        isinstance(d, ast.Name) and d.id == "property" for d in member.decorator_list
    )
]


def test_core_stub_declares_all_runtime_extension_types():
    expected = {n.name for n in STUB.body if isinstance(n, ast.ClassDef)}
    actual = {
        name
        for name, value in vars(hessian2._core).items()
        if isinstance(value, type)
        and value.__module__ in {"hessian2", "hessian2._core"}
    }

    assert actual == expected


@pytest.mark.parametrize(
    ("class_name", "method"),
    STUB_METHODS,
    ids=[f"{c}.{m.name}" for c, m in STUB_METHODS],
)
@pytest.mark.parametrize("include_defaults", [False, True], ids=["required", "all"])
@pytest.mark.parametrize("keywords", [False, True], ids=["positional", "keywords"])
def test_core_runtime_accepts_calls_permitted_by_its_stub(
    class_name, method, include_defaults, keywords
):
    owner = getattr(hessian2._core, class_name)
    runtime = (
        owner if method.name in {"__init__", "__new__"} else getattr(owner, method.name)
    )
    signature = inspect.signature(runtime)
    arguments = method.args
    positional = [*arguments.posonlyargs, *arguments.args]
    defaults_start = len(positional) - len(arguments.defaults)
    instance_method = method.name not in {"__init__", "__new__"} and not any(
        isinstance(d, ast.Name) and d.id == "staticmethod"
        for d in method.decorator_list
    )
    pos, named = ([object()] if instance_method else []), {}
    for index, arg in enumerate(positional):
        if arg.arg == "self" or (arg.arg == "cls" and method.name == "__new__"):
            continue
        if not include_defaults and index >= defaults_start:
            continue
        if keywords and index >= len(arguments.posonlyargs):
            named[arg.arg] = object()
        else:
            pos.append(object())
    for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True):
        if include_defaults or default is None:
            named[arg.arg] = object()

    signature.bind(*pos, **named)


def test_all_names_the_supported_public_api():
    assert set(hessian2.__all__) == {
        "BigInteger",
        "Codec",
        "Converter",
        "Date",
        "DecodeError",
        "EncodeError",
        "HessianObject",
        "Instant",
        "LocalDateTime",
        "Long",
        "OffsetDateTime",
        "Reader",
        "Typed",
        "ZonedDateTime",
        "__version__",
        "as_object",
        "dumps",
        "dumps_many",
        "java_class",
        "java_name",
        "loads",
        "loads_many",
        "reader",
    }


@pytest.mark.parametrize("name", hessian2.__all__)
def test_each_public_export_exists_at_runtime(name):
    assert getattr(hessian2, name) is not None


def test_a_nan_map_key_decodes_as_a_float_nan():
    data = hessian2.dumps({math.nan: 1})

    decoded = hessian2.loads(data)
    key = next(iter(decoded))

    assert type(key) is float
    assert math.isnan(key)
    assert len(decoded) == 1
    assert same(decoded[key], 1)


def test_an_untyped_list_key_suggests_an_explicit_tuple_annotation():
    data = hessian2.dumps({(1,): 2})

    with pytest.raises(DecodeError, match=re.escape("dict[tuple[...], ...]")):
        hessian2.loads(data)


def test_a_peer_map_key_without_a_python_key_mapping_is_rejected():
    # This is a legal Hessian map with an empty map as its key.
    with pytest.raises(
        DecodeError, match=re.escape("a map key of type dict needs an annotation")
    ):
        hessian2.loads(b"HHZ\x90Z")
