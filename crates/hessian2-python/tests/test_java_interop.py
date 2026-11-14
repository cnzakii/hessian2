"""Public contracts for java interop."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import shutil
import subprocess
import typing
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, TypedDict
from zoneinfo import ZoneInfo

import hessian2
import pytest
from assertions import same
from conftest import JAVA_FIXTURES as FIXTURES
from conftest import java_wire as wire
from describe import describe, unordered
from hessian2 import Codec, Converter, HessianObject

ROOT = Path(__file__).resolve().parents[3]

WRITERS = ("caucho", "lite")

CODEC = hessian2.Codec(unknown_class="hessian_object")

LIST_KEYED = dict[tuple[str, ...], int]

CASE_TYPES = {
    "suite.replyTypedMap_3": LIST_KEYED,
    "suite.replyUntypedMap_3": LIST_KEYED,
}


def decode(name: str, data: bytes) -> list[Any]:
    """Decode a case's values, with the annotation it needs, if any."""
    if name in CASE_TYPES:
        return CODEC.loads_many(data, types=[CASE_TYPES[name]])
    return CODEC.loads_many(data)


def original(name: str) -> bytes:
    """Caucho's bytes, or hessian-lite's for values Caucho cannot write."""
    return wire(name, "caucho" if FIXTURES[name]["caucho"] else "lite")


CASES = [
    (name, writer)
    for name in FIXTURES
    for writer in WRITERS
    if (FIXTURES[name]["caucho"] or writer == "lite")
    and name not in {"extra.review2_zoned_min", "extra.review2_zoned_max"}
]

IDS = [f"{name}-{writer}" for name, writer in CASES]

UNTAGGED_CASES = [case for case in CASES if not case[0].startswith("extra.typed_")]

UNTAGGED_IDS = [f"{name}-{writer}" for name, writer in UNTAGGED_CASES]


_LONGS_IN_INT_RANGE = {
    f"suite.replyLong_{suffix}"
    for suffix in [
        "0",
        "1",
        "15",
        "m8",
        "0x10",
        "0x7ff",
        "m9",
        "m0x800",
        "0x800",
        "0x3ffff",
        "m0x801",
        "m0x40000",
        "0x40000",
        "0x7fffffff",
        "m0x40001",
        "m0x80000000",
    ]
}

REENCODE_DIFFERS = {
    # A Python int that fits 32 bits is written as a Hessian int, not a long.
    **dict.fromkeys(_LONGS_IN_INT_RANGE | {"extra.long_small"}, "long as int"),
    # Objects with a long field holding a small value.
    **dict.fromkeys(
        [
            "extra.person",
            "extra.employee",
            "extra.object_cycle",
            "extra.stream_shared",
            "extra.large_nested_bean",
            "extra.mutual_beans",
        ],
        "long field as int",
    ),
    # Decoding drops list and map type names, so they are written untyped.
    **dict.fromkeys(
        [f"suite.replyTypedFixedList_{n}" for n in (0, 1, 7, 8)]
        + [f"suite.replyTypedMap_{n}" for n in (0, 1, 2, 3)]
        + [
            "extra.int_array",
            "extra.string_array",
            "extra.map_cycle",
            "extra.map_mixed_keys",
            "extra.array_cycle",
            "extra.array_types",
            "extra.collection_types",
            "extra.map_types",
            "extra.throwable",
            "extra.stream_collections",
        ],
        "type name dropped",
    ),
    # Java sizes binary chunks by its output buffer position; ours are 0x8000.
    "suite.replyBinary_65536": "binary chunk size",
    "extra.set_hash": "type name dropped",
    # Decoded as aware datetimes, which encode as java.util.Date.
    **dict.fromkeys(
        [
            "extra.time_instant",
            "extra.time_offset_datetime",
            "extra.time_zoned_datetime",
        ],
        "java.time instant as Date",
    ),
    # Python drops nanoseconds below a microsecond.
    "extra.time_local_time_nanos": "nanoseconds dropped",
    # Values within 64 bits decode as int and encode as int or long.
    **dict.fromkeys(
        ["extra.bigint_small", "extra.bigint_zero"], "small BigInteger as int"
    ),
}

REENCODE_DIFFERS_FOR = {
    # Caucho's generic bean form; we write hessian-lite's {"value"} form.
    ("extra.uuid", "caucho"): "UUID form",
    # hessian-lite writes BigInteger's fields in the reverse order.
    ("extra.bigint_large", "lite"): "BigInteger field order",
    ("extra.bigint_word_boundary", "lite"): "BigInteger field order",
    # Java had computed and cached bitLengthPlusOne; we write zero, which
    # Java reads as not yet computed.
    ("extra.bigint_word_boundary", "caucho"): "BigInteger cache",
    # Caucho boxes a Byte, Short, or Float written as Object; we write the
    # plain number, as hessian-lite does.
    **{
        (f"extra.boxed_{kind}", "caucho"): "boxed number"
        for kind in ("byte", "short", "float")
    },
}


# The Java fixtures describe the original Java object, before these lossy writes.
WIRE_VALUES = {
    ("extra.double_negative_zero", "caucho"): [{"double": "0"}],
    ("extra.double_negative_zero", "lite"): [{"double": "0"}],
    ("extra.shadowed_fields", "caucho"): [
        {"object": "interop.ShadowChild", "fields": {"same": {"str": "parent"}}}
    ],
    ("extra.review2_boxed_boundaries", "lite"): [
        {
            "list": [
                {"int": "-128"},
                {"int": "127"},
                {"int": "-32768"},
                {"int": "32767"},
                {"double": "3fb999999999999a"},
                {"double": "369ff868bf4d956a"},
                {"double": "380fffffff9fdba8"},
                {"double": "47efffffe54daff8"},
                {"double": "7ff8000000000000"},
                {"double": "7ff0000000000000"},
                {"double": "fff0000000000000"},
            ]
        }
    ],
}


@pytest.mark.parametrize(
    ("name", "writer"),
    [(n, w) for n, w in CASES if n != "extra.throwable"],
    ids=[f"{n}-{w}" for n, w in CASES if n != "extra.throwable"],
)
def test_java_golden_bytes_decode_to_the_peer_value(name, writer):
    expected = WIRE_VALUES.get((name, writer), FIXTURES[name]["values"])

    decoded = decode(name, wire(name, writer))

    assert [describe(value) for value in decoded] == expected


@pytest.mark.parametrize("writer", WRITERS)
def test_java_throwable_fields_and_shared_suppression_list_decode(writer):
    error = CODEC.loads(wire("extra.throwable", writer))
    fields = error.fields
    cause = fields["cause"]

    assert error.class_name == cause.class_name == "java.lang.Exception"
    assert (
        set(fields)
        == set(cause.fields)
        == {"detailMessage", "cause", "stackTrace", "suppressedExceptions"}
    )
    assert fields["detailMessage"] == "消息"
    assert cause.fields["detailMessage"] == "cause"
    assert cause.fields["cause"] is None
    assert fields["stackTrace"] == cause.fields["stackTrace"] == []
    assert fields["suppressedExceptions"] is cause.fields["suppressedExceptions"]
    assert fields["suppressedExceptions"] == []


@pytest.mark.parametrize(
    ("name", "writer"),
    [
        (n, w)
        for n, w in UNTAGGED_CASES
        if n != "extra.shadowed_fields"
        and n not in REENCODE_DIFFERS
        and (n, w) not in REENCODE_DIFFERS_FOR
        and not n.startswith("extra.review2_")
    ],
    ids=[
        f"{n}-{w}"
        for n, w in UNTAGGED_CASES
        if n != "extra.shadowed_fields"
        and n not in REENCODE_DIFFERS
        and (n, w) not in REENCODE_DIFFERS_FOR
        and not n.startswith("extra.review2_")
    ],
)
def test_lossless_python_mappings_reencode_the_exact_java_golden_bytes(name, writer):
    raw = wire(name, writer)

    assert CODEC.dumps_many(decode(name, raw)) == raw


def _java() -> list[str] | None:
    home = os.environ.get("JAVA_HOME")
    java = str(Path(home) / "bin" / "java") if home else shutil.which("java")
    build = ROOT / "interop" / "java" / "build"
    if not java or not build.is_dir():
        return None
    # macOS ships a /usr/bin/java placeholder that fails without a JDK.
    if subprocess.run([java, "-version"], capture_output=True, check=False).returncode:
        return None
    lib = ROOT / "interop" / "java" / "lib"
    return [java, "-cp", f"{build}{os.pathsep}{lib}/*", "interop.Interop"]


JAVA = _java() if os.environ.get("HESSIAN2_LIVE_JAVA") == "1" else None


needs_java = pytest.mark.skipif(
    JAVA is None, reason="set HESSIAN2_LIVE_JAVA=1 after make interop-java"
)

JAVA_READS_DIFFERENTLY = {
    # Caucho reads java.util.UUID with its bean deserializer, which ignores
    # hessian-lite's "value" field.
    ("extra.uuid", "caucho"): "UUID form",
    # hessian-lite's DENY_CLASS lists com.caucho.hessian.test., so it reads
    # those objects as maps.
    **{
        (f"suite.replyObject_{n}", "lite"): "denied class"
        for n in ("0", "1", "16", "2", "2a", "2b", "3")
    },
}


@needs_java
@pytest.mark.parametrize(
    ("name", "reader"),
    [(n, r) for n, r in UNTAGGED_CASES if (n, r) not in JAVA_READS_DIFFERENTLY],
    ids=[f"{n}-{r}" for n, r in UNTAGGED_CASES if (n, r) not in JAVA_READS_DIFFERENTLY],
)
def test_java_reads_python_mappings_as_the_same_values(java_views, name, reader):
    expected = [describe(value) for value in decode(name, original(name))]

    assert unordered(java_views[reader][name]["values"]) == unordered(expected)


@needs_java
def test_caucho_suite_inventory():
    assert JAVA is not None
    result = subprocess.run(
        [*JAVA, "inventory"], capture_output=True, text=True, check=True
    )
    inventory = json.loads(result.stdout)
    replies = {
        name.removeprefix("suite.") for name in FIXTURES if name.startswith("suite.")
    }
    assert len(replies) == 92 and len(inventory) == 90
    assert replies - set(inventory.values()) == {"replyString_null", "replyBinary_null"}
    assert all(
        arg == "arg" + reply.removeprefix("reply") for arg, reply in inventory.items()
    )


ARG_CASES = [
    (name, reader)
    for name, reader in CASES
    if name.startswith("suite.") and not name.endswith("_null")
]


@needs_java
@pytest.mark.parametrize(("name", "reader"), ARG_CASES)
def test_caucho_arg_checks_our_bytes(java_views, name, reader):
    view = java_views[reader][name]

    assert "error" not in view, view
    # Python discards the int/long distinction and typed-list metadata;
    # hessian-lite also denies Caucho's test bean classes.
    differs = (
        name in _LONGS_IN_INT_RANGE
        or name.startswith("suite.replyTypedFixedList_")
        or (name, reader) in JAVA_READS_DIFFERENTLY
    )
    assert view["arg"] is (not differs), view


@pytest.mark.parametrize(
    ("writer", "visible"), [("caucho", "parent"), ("lite", "child")]
)
def test_shadowed_field_matches_java_reader(writer, visible):
    # Both Java readers also keep the last wire occurrence in the visible child field.
    value = CODEC.loads(wire("extra.shadowed_fields", writer))

    assert value.class_name == "interop.ShadowChild"
    assert value.fields == {"same": visible}


@pytest.mark.parametrize("writer", WRITERS)
def test_java_collection_stream_references_keep_identity(writer):
    first, number, repeated, null, tail = CODEC.loads_many(
        wire("extra.stream_collections", writer)
    )

    assert first is repeated
    assert same((first, number, null, tail), ([None, "tail"], 123, None, "end"))


@pytest.mark.parametrize("writer", WRITERS)
def test_java_arrays_can_reference_themselves(writer):
    value = CODEC.loads(wire("extra.array_cycle", writer))

    assert type(value) is list
    assert value[0] == "self"
    assert value[1] is value


@pytest.mark.parametrize("writer", WRITERS)
def test_java_mutual_beans_preserve_both_reference_directions(writer):
    first, second, repeated = CODEC.loads(wire("extra.mutual_beans", writer))

    assert type(first) is type(second) is HessianObject
    assert first.class_name == second.class_name == "interop.Person"
    assert (first.fields["name"], second.fields["name"]) == ("first", "second")
    assert first is repeated and first is not second
    assert first.fields["friend"] is second
    assert second.fields["friend"] is first


class TypedExtra(TypedDict):
    count: hessian2.Long


@hessian2.java_class("interop.Typed", field_naming=None)
@dataclasses.dataclass
class Typed:
    amount: hessian2.BigInteger
    amounts: list[hessian2.BigInteger]
    instant: hessian2.Instant | None
    offset: hessian2.OffsetDateTime | None
    zoned: hessian2.ZonedDateTime | None
    ids: list[hessian2.Long]
    extra: TypedExtra
    any: hessian2.Long


@hessian2.java_class("java.lang.Exception")
@dataclasses.dataclass
class JavaException(Exception):
    detail_message: str | None = None
    cause: JavaException | None = None
    stack_trace: list[Any] = dataclasses.field(default_factory=list)
    suppressed_exceptions: list[Any] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        super().__init__(self.detail_message)


@hessian2.java_class("java.lang.IllegalStateException")
@dataclasses.dataclass
class IllegalState(Exception):
    detail_message: str


@hessian2.java_class("interop.Person")
@dataclasses.dataclass
class Person:
    name: str


@needs_java
@pytest.mark.parametrize("reader", WRITERS)
@pytest.mark.parametrize(
    ("annotation", "value", "expected"),
    [
        (tuple[str, ...], ("a", None), "[Ljava.lang.String;"),
        (tuple[Any, ...], ("a", 1, None), "[Ljava.lang.Object;"),
        (tuple[list[int], ...], ([1],), "[Ljava.lang.Object;"),
        (tuple[int | str, ...], (1, "a"), "[Ljava.lang.Object;"),
        (tuple[None, ...], (None,), "[Ljava.lang.Object;"),
        (tuple[int, ...], (1, 2), "[I"),
        (tuple[int | None, ...], (1, None), "[Ljava.lang.Integer;"),
        (tuple[hessian2.Long, ...], (1,), "[J"),
        (tuple[float, ...], (1.5,), "[D"),
        (tuple[bool, ...], (True,), "[Z"),
        (tuple[Decimal, ...], (Decimal("1.5"),), "[Ljava.math.BigDecimal;"),
        (
            tuple[hessian2.Date, ...],
            (dt.datetime(2026, 1, 1, tzinfo=dt.UTC),),
            "[Ljava.util.Date;",
        ),
        (tuple[Person, ...], (Person("a"),), "[Linterop.Person;"),
        (tuple[tuple[int, ...], ...], ((1,), ()), "[[I"),
        (tuple[bytes, ...], (b"x",), "[[B"),
    ],
    ids=[
        "strings",
        "objects",
        "lists",
        "mixed",
        "nulls",
        "ints",
        "boxed",
        "longs",
        "doubles",
        "booleans",
        "decimals",
        "dates",
        "people",
        "nested",
        "binaries",
    ],
)
def test_java_reads_variadic_tuples_as_the_declared_array_class(
    tmp_path, reader, annotation, value, expected
):
    data = hessian2.dumps(value, type=annotation)

    views = java_types(tmp_path, reader, {"array": (data, "java.lang.Object")})

    assert views["array"] == {"class": expected}


@needs_java
@pytest.mark.parametrize("reader", WRITERS)
def test_java_reads_arrays_of_converted_values_as_java_name_gives(
    tmp_path: Path, reader: str
) -> None:
    codec = Codec()
    # An int written as a BigDecimal, inside an array its tag would name.
    codec.register(
        Converter(
            int,
            to_fields=lambda value: {"value": str(value)},
            from_fields=lambda fields: int(fields["value"]),
        ),
        name="java.math.BigDecimal",
        replace=True,
    )
    annotation = tuple[hessian2.BigInteger, ...]
    data = codec.dumps((7,), type=annotation)
    views = java_types(tmp_path, reader, {"array": (data, "java.lang.Object")})

    assert views["array"] == {"class": "[Ljava.math.BigDecimal;"}


@needs_java
@pytest.mark.parametrize("reader", WRITERS)
@pytest.mark.parametrize("case", ["dates", "nullable", "converted"])
def test_java_reads_the_values_of_annotated_arrays(tmp_path, reader, case):
    long_id = typing.NewType("long_id", hessian2.Long)
    retagged = Annotated[long_id, *typing.get_args(hessian2.Long)[1:]]
    binary = Codec()
    binary.register(
        Converter(
            bytes,
            lambda value: {"value": str(value[0])},
            lambda fields: bytes([int(fields["value"])]),
        ),
        name="java.math.BigDecimal",
        replace=True,
    )
    data, expected = {
        "dates": (
            hessian2.dumps((dt.date(2026, 1, 2),), type=tuple[dt.date, ...]),
            [{"local_date": "2026-01-02"}],
        ),
        "nullable": (
            hessian2.dumps((7, None), type=tuple[retagged | None, ...]),
            [{"int": "7"}, None],
        ),
        "converted": (
            binary.dumps((b"\x07",), type=tuple[bytes, ...]),
            [{"decimal": "7"}],
        ),
    }[case]
    (tmp_path / "array.bin").write_bytes(data)

    result = subprocess.run(
        [*JAVA, "describe", reader, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert same(json.loads(result.stdout)["array"], {"values": [{"list": expected}]})


def _one_field(name: str, field: str, ty: type) -> type:
    cls = dataclasses.make_dataclass(name, [(field, ty)])
    return hessian2.java_class(f"com.example.{name}")(cls)


@pytest.mark.parametrize("writer", WRITERS)
def test_java_maps_decode_as_the_classes_annotations_name(writer: str) -> None:
    # Java reads a map into a declared class, whatever map type it names.
    types = tuple[
        _one_field("Hash", "hash", int),
        _one_field("Linked", "linked", int),
        _one_field("Sorted", "sorted", int),
        _one_field("Table", "table", int),
        _one_field("Property", "property", str),
        _one_field("Concurrent", "concurrent", int),
    ]

    decoded = hessian2.loads(wire("extra.map_types", writer), type=types)

    assert [dataclasses.astuple(value) for value in decoded] == [
        (1,),
        (2,),
        (3,),
        (4,),
        ("value",),
        (5,),
    ]


@pytest.mark.parametrize("writer", WRITERS)
def test_java_throwable_decodes_as_a_declared_exception(writer: str) -> None:
    error = hessian2.loads(wire("extra.throwable", writer), type=JavaException)

    assert isinstance(error, JavaException) and str(error) == "消息"
    assert error.cause is not None and error.cause.detail_message == "cause"
    assert error.cause.cause is None and error.stack_trace == []


def java_types(tmp_path: Path, reader: str, cases: dict[str, tuple[bytes, str]]) -> Any:
    """What a Java reader holds after reading each value as the given type."""
    assert JAVA is not None
    for name, (data, java_type) in cases.items():
        (tmp_path / f"{name}.bin").write_bytes(data)
        (tmp_path / f"{name}.type").write_text(java_type, encoding="utf-8")
    result = subprocess.run(
        [*JAVA, "types", reader, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@needs_java
@pytest.mark.parametrize("reader", WRITERS)
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("big_integer", {"class": "java.math.BigInteger"}),
        (
            "long_list",
            {
                "class": "java.util.ArrayList",
                "items": ["java.lang.Long", "java.lang.Long"],
            },
        ),
        ("long_object", {"class": "java.lang.Long"}),
        ("date", {"class": "java.util.Date"}),
        ("exception", {"class": "java.lang.RuntimeException", "message": "boom"}),
        (
            "declared_exception",
            {"class": "java.lang.IllegalStateException", "message": "boom"},
        ),
    ],
)
def test_java_reads_tagged_values_as_the_declared_class(
    tmp_path, reader, case, expected, tagged_java_inputs
):
    views = java_types(tmp_path, reader, {"value": tagged_java_inputs[case]})

    assert views["value"] == expected


@needs_java
@pytest.mark.parametrize("reader", WRITERS)
def test_java_big_integer_positions_reject_a_bare_integer(tmp_path, reader):
    views = java_types(
        tmp_path, reader, {"value": (hessian2.dumps(5), "java.math.BigInteger")}
    )

    assert "expected map/object" in views["value"]["error"]


@needs_java
@pytest.mark.parametrize(
    ("case", "field", "part", "expected"),
    [
        ("typed", "amount", "class", "java.math.BigInteger"),
        ("typed", "amounts", "items", ["java.math.BigInteger"]),
        ("typed", "ids", "items", ["java.lang.Long"]),
        ("typed", "extra", "values", ["java.lang.Long"]),
        ("typed", "any", "class", "java.lang.Long"),
        ("sharing", "amounts", "items", ["java.math.BigInteger"]),
        ("sharing", "ids", "items", ["java.lang.Long"]),
    ],
)
@pytest.mark.parametrize("reader", WRITERS)
def test_java_reads_each_annotated_field_with_its_own_type(
    tmp_path, reader, case, field, part, expected, tagged_java_inputs
):
    views = java_types(
        tmp_path, reader, {"value": tagged_java_inputs[f"{case}-{reader}"]}
    )

    assert views["value"]["fields"][field][part] == expected


@needs_java
@pytest.mark.parametrize(
    ("case", "field", "expected"),
    [
        ("typed-lite", "instant", "java.time.Instant"),
        ("typed-lite", "offset", "java.time.OffsetDateTime"),
        ("typed-lite", "zoned", "java.time.ZonedDateTime"),
    ],
)
def test_hessian_lite_reads_annotated_java_time_fields(
    tmp_path, case, field, expected, tagged_java_inputs
):
    views = java_types(tmp_path, "lite", {"value": tagged_java_inputs[case]})

    assert views["value"]["fields"][field] == {"class": expected}


@needs_java
def test_hessian_lite_reads_a_local_datetime_tag_as_local_datetime(
    tmp_path, tagged_java_inputs
):
    views = java_types(
        tmp_path, "lite", {"value": tagged_java_inputs["local_datetime"]}
    )

    assert views["value"] == {"class": "java.time.LocalDateTime"}


@pytest.mark.parametrize(
    ("name", "writer"),
    [
        ("extra.typed_fields", "lite"),
        ("extra.typed_without_time", "caucho"),
        ("extra.typed_without_time", "lite"),
    ],
)
def test_java_typed_objects_decode_their_integer_and_collection_fields(name, writer):
    codec = Codec()
    codec.register(Typed)

    value = codec.loads(wire(name, writer))

    assert same(
        (value.amount, value.amounts, value.ids, value.extra, value.any),
        (5, [6], [7], {"count": 8}, 9),
    )


@pytest.mark.parametrize("writer", WRITERS)
def test_java_typed_objects_preserve_null_java_time_fields(writer):
    codec = Codec()
    codec.register(Typed)

    value = codec.loads(wire("extra.typed_without_time", writer))

    assert (value.instant, value.offset, value.zoned) == (None, None, None)


@pytest.mark.parametrize(
    ("field", "inspect_value", "expected"),
    [
        (
            "instant",
            lambda v: v,
            dt.datetime(2023, 11, 14, 22, 13, 20, 123000, tzinfo=dt.UTC),
        ),
        ("offset", lambda v: v.utcoffset(), dt.timedelta(hours=8)),
        ("zoned", lambda v: str(v.tzinfo), "Europe/Paris"),
    ],
)
def test_java_typed_objects_preserve_java_time_field_values(
    field, inspect_value, expected
):
    codec = Codec()
    codec.register(Typed)

    value = codec.loads(wire("extra.typed_fields", "lite"))

    assert same(inspect_value(getattr(value, field)), expected)


@needs_java
@pytest.mark.parametrize(
    ("name", "writer", "reader"),
    [
        ("extra.typed_fields", "lite", "lite"),
        ("extra.typed_without_time", "caucho", "caucho"),
        ("extra.typed_without_time", "caucho", "lite"),
        ("extra.typed_without_time", "lite", "caucho"),
        ("extra.typed_without_time", "lite", "lite"),
    ],
)
@pytest.mark.parametrize(
    ("field", "part", "expected"),
    [
        ("amount", "class", "java.math.BigInteger"),
        ("amounts", "items", ["java.math.BigInteger"]),
        ("ids", "items", ["java.lang.Long"]),
        ("extra", "values", ["java.lang.Long"]),
        ("any", "class", "java.lang.Long"),
    ],
)
def test_java_round_trip_keeps_each_declared_integer_field_type(
    tmp_path, name, writer, reader, field, part, expected
):
    codec = Codec()
    codec.register(Typed)
    value = codec.loads(wire(name, writer))

    views = java_types(
        tmp_path, reader, {"typed": (codec.dumps(value), "interop.Typed")}
    )

    assert views["typed"]["fields"][field][part] == expected


@needs_java
def test_hessian_lite_round_trip_keeps_the_zoned_datetime_field_type(tmp_path):
    codec = Codec()
    codec.register(Typed)
    value = codec.loads(wire("extra.typed_fields", "lite"))

    views = java_types(
        tmp_path, "lite", {"typed": (codec.dumps(value), "interop.Typed")}
    )

    assert views["typed"]["fields"]["zoned"] == {"class": "java.time.ZonedDateTime"}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("extra.review2_zoned_min", dt.datetime(1, 1, 1, tzinfo=ZoneInfo("Etc/GMT-1"))),
        (
            "extra.review2_zoned_max",
            dt.datetime(9999, 12, 31, 23, 59, 59, tzinfo=ZoneInfo("Etc/GMT+1")),
        ),
    ],
    ids=["year-one", "year-9999"],
)
def test_java_zoned_datetimes_at_python_year_edges_preserve_their_zone(name, expected):
    decoded = hessian2.loads(wire(name, "lite"))

    assert same(decoded, expected)
    assert decoded.tzinfo == expected.tzinfo


@pytest.mark.parametrize(
    ("name", "pairs", "writer"),
    [
        ("extra.review2_time_nested_refs", [(0, 3), (1, 4), (2, 5), (6, 7)], "lite"),
        ("extra.review2_bigint_refs", [(0, 2), (1, 3)], "caucho"),
        ("extra.review2_bigint_refs", [(0, 2), (1, 3)], "lite"),
        ("extra.review2_boxed_refs", [(2, 3)], "caucho"),
        ("extra.review2_boxed_refs", [(2, 3)], "lite"),
    ],
)
def test_java_builtin_objects_keep_their_reference_slots(name, pairs, writer):
    values = hessian2.loads(wire(name, writer))

    assert all(values[first] is values[second] for first, second in pairs)


@pytest.mark.parametrize("writer", WRITERS)
def test_java_boxed_scalar_duplicates_preserve_value(writer):
    values = hessian2.loads(wire("extra.review2_boxed_refs", writer))

    assert same(values[0], values[1])


def test_java_overlap_datetimes_preserve_fold_and_offset():
    values = hessian2.loads(wire("extra.review2_time_overlap", "lite"))

    assert [v.fold for v in values] == [0, 1]
    assert [v.utcoffset() for v in values] == [
        dt.timedelta(hours=-4),
        dt.timedelta(hours=-5),
    ]


@needs_java
@pytest.mark.parametrize("reader", WRITERS)
@pytest.mark.parametrize(
    ("name", "writer"), [(n, w) for n, w in CASES if n.startswith("extra.review2_")]
)
def test_java_reads_python_mappings_of_builtin_edge_values(
    tmp_path, reader, name, writer
):
    value = hessian2.loads(wire(name, writer))
    (tmp_path / "python.bin").write_bytes(hessian2.dumps(value))
    result = subprocess.run(
        [*JAVA, "describe", reader, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert same(
        unordered(json.loads(result.stdout)["python"]["values"]),
        unordered([describe(value)]),
    )


@needs_java
@pytest.mark.parametrize(
    ("name", "writer", "reader"),
    [
        (n, w, r)
        for n, w in CASES
        if n.startswith("extra.review2_")
        for r in WRITERS
        if w == r
        or n not in {"extra.review2_boxed_boundaries", "extra.review2_boxed_refs"}
    ],
)
def test_java_peers_read_each_others_compatible_builtin_edge_values(
    tmp_path, name, writer, reader
):
    raw = wire(name, writer)
    (tmp_path / "peer.bin").write_bytes(raw)
    result = subprocess.run(
        [*JAVA, "describe", reader, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert same(
        unordered(json.loads(result.stdout)["peer"]["values"]),
        unordered([describe(hessian2.loads(raw))]),
    )


@needs_java
@pytest.mark.parametrize("reader", WRITERS)
@pytest.mark.parametrize(
    ("value", "expected"),
    [({"a", "b"}, [{"str": "a"}, {"str": "b"}]), (frozenset(), [])],
    ids=["set", "frozenset"],
)
def test_java_reads_all_elements_of_python_sets(tmp_path, reader, value, expected):
    (tmp_path / "python.bin").write_bytes(hessian2.dumps(value))

    result = subprocess.run(
        [*JAVA, "describe", reader, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    actual = json.loads(result.stdout)["python"]["values"][0]["list"]

    assert sorted(actual, key=repr) == expected


@needs_java
@pytest.mark.parametrize("reader", WRITERS)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2**64, "18446744073709551616"),
        (
            -(2**1000),
            "-10715086071862673209484250490600018105614048117055336074437503883703510511249361224931983788156958581275946729175531468251871452856923140435984577574698574803934567774824230985421074605062371141877954182153046474983581941267398767559165543946077062914571196477686542167660429831652624386837205668069376",
        ),
    ],
    ids=["word-boundary", "negative-large"],
)
def test_java_reads_python_big_integer_values(tmp_path, reader, value, expected):
    (tmp_path / "python.bin").write_bytes(hessian2.dumps(value))

    result = subprocess.run(
        [*JAVA, "describe", reader, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert same(json.loads(result.stdout)["python"], {"values": [{"int": expected}]})
