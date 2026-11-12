"""Interoperability with Caucho's reference implementation and hessian-lite.

`fixtures/java.json` holds, for Caucho's conformance suite
(`TestHessian2Servlet.reply*`) and extra cases, the bytes both Java writers
produce and a structural description of the Java value. Regenerate it with
`make fixtures-java`. Tests that run Java skip unless `make interop-java` has
built the harness.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import hessian2
import pytest
from describe import describe, unordered

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "java.json").read_text(encoding="utf-8")
)
WRITERS = ("caucho", "lite")
CODEC = hessian2.Codec(unknown_class="hessian_object")


def wire(name: str, writer: str) -> bytes:
    case = FIXTURES[name]
    return base64.b64decode(case[writer] or case["caucho"])


def original(name: str) -> bytes:
    """Caucho's bytes, or hessian-lite's for values Caucho cannot write."""
    return wire(name, "caucho" if FIXTURES[name]["caucho"] else "lite")


# Caucho cannot write java.time values on JDK 17; those cases have no
# Caucho bytes.
# test_review2_java.py checks the extra.review2_ boundary cases one by one.
CASES = [
    (name, writer)
    for name in FIXTURES
    for writer in WRITERS
    if (FIXTURES[name]["caucho"] or writer == "lite")
    and not name.startswith("extra.review2_")
]
IDS = [f"{name}-{writer}" for name, writer in CASES]

# Values Java itself writes lossily: the bytes carry something else.
JAVA_LOSSY = {
    # (int) -0.0 == 0, so Java writes 0x5b (0.0).
    "extra.double_negative_zero",
}
JAVA_LOSSY_FOR = {("extra.shadowed_fields", "caucho")}

# Where our bytes for the decoded value differ from Java's, and why.
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


@pytest.mark.parametrize(("name", "writer"), CASES, ids=IDS)
def test_decodes_java_bytes(name: str, writer: str) -> None:
    values = CODEC.loads_many(wire(name, writer))
    if name == "extra.throwable":
        error = values[0]
        assert error.class_name == "java.lang.Exception"
        fields = error.fields
        assert set(fields) == {
            "detailMessage",
            "cause",
            "stackTrace",
            "suppressedExceptions",
        }
        assert fields["detailMessage"] == "消息"
        assert fields["stackTrace"] == fields["suppressedExceptions"] == []
        cause = fields["cause"]
        assert cause.class_name == "java.lang.Exception"
        assert set(cause.fields) == set(fields)
        assert (
            cause.fields["detailMessage"] == "cause" and cause.fields["cause"] is None
        )
        assert cause.fields["stackTrace"] == []
        assert cause.fields["suppressedExceptions"] is fields["suppressedExceptions"]
        return  # Writers traverse fields in different orders, hence different ref numbers.
    expected = FIXTURES[name]["values"]
    if name in JAVA_LOSSY or (name, writer) in JAVA_LOSSY_FOR:
        assert [describe(value) for value in values] != expected
    else:
        assert [describe(value) for value in values] == expected


@pytest.mark.parametrize(
    ("name", "writer"),
    [(n, w) for n, w in CASES if n != "extra.shadowed_fields"],
    ids=[f"{n}-{w}" for n, w in CASES if n != "extra.shadowed_fields"],
)
def test_reencodes_java_bytes(name: str, writer: str) -> None:
    raw = wire(name, writer)
    same = CODEC.dumps_many(CODEC.loads_many(raw)) == raw
    reason = REENCODE_DIFFERS.get(name) or REENCODE_DIFFERS_FOR.get((name, writer))
    assert same == (reason is None), reason or "bytes differ from Java's"


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


JAVA = _java()
needs_java = pytest.mark.skipif(JAVA is None, reason="run `make interop-java` first")

# Where a Java reader's view of our bytes differs from the original value.
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


@pytest.fixture(scope="module")
def java_views(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """What each Java reader decodes from our encoding of every case."""
    assert JAVA is not None
    directory = tmp_path_factory.mktemp("ours")
    for name in FIXTURES:
        values = CODEC.loads_many(original(name))
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


@needs_java
@pytest.mark.parametrize(("name", "reader"), CASES, ids=IDS)
def test_java_reads_our_bytes(
    java_views: dict[str, dict[str, Any]], name: str, reader: str
) -> None:
    view = java_views[reader][name]
    assert "error" not in view, view
    # Our bytes come from decoding the original ones, so compare with those.
    expected = [describe(value) for value in CODEC.loads_many(original(name))]
    same = unordered(view["values"]) == unordered(expected)
    reason = JAVA_READS_DIFFERENTLY.get((name, reader))
    assert same == (reason is None), reason or (view["values"], expected)


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
def test_shadowed_fields_keep_the_last_slot(writer):
    # A field hiding a superclass field of the same name is written twice.
    # Java's readers keep the last value, as HessianObject's dict does, so
    # reencoding writes the field once.
    raw = wire("extra.shadowed_fields", writer)
    shadow = CODEC.loads(raw)
    assert CODEC.dumps(shadow) != raw
    assert len(shadow.fields) == len(set(shadow.fields))


@pytest.mark.parametrize("writer", WRITERS)
def test_java_collection_stream_continuation_and_identity(writer):
    raw = wire("extra.stream_collections", writer)
    first, number, repeated, null, tail = CODEC.loads_many(raw)
    assert type(first) is list and first == [None, "tail"]
    assert first is repeated
    assert number == 123 and null is None and tail == "end"
    assert raw.endswith(b"Q\x90N\x03end")


@pytest.mark.parametrize("writer", WRITERS)
def test_java_array_and_mutual_bean_cycles(writer):
    array = CODEC.loads(wire("extra.array_cycle", writer))
    assert type(array) is list and array[0] == "self" and array[1] is array
    first, second, repeated = CODEC.loads(wire("extra.mutual_beans", writer))
    assert type(first) is type(second) is hessian2.HessianObject
    assert first.class_name == second.class_name == "interop.Person"
    assert first.fields["name"] == "first" and second.fields["name"] == "second"
    assert first is repeated and first is not second
    assert first.fields["friend"] is second and second.fields["friend"] is first
