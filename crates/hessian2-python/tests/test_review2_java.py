from __future__ import annotations

import base64
import datetime as dt
import json
import struct
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import hessian2
import pytest
from describe import describe, unordered
from test_java_interop import JAVA, WRITERS, needs_java

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "java.json"
FIXTURES = {
    name: case
    for name, case in json.loads(FIXTURE_PATH.read_text()).items()
    if name.startswith("extra.review2_")
}
BOUNDARIES = {"extra.review2_zoned_min", "extra.review2_zoned_max"}
CASES = [
    (name, writer)
    for name, case in FIXTURES.items()
    for writer in WRITERS
    if case["caucho"] or writer == "lite"
]


def wire(name, writer):
    case = FIXTURES[name]
    return base64.b64decode(case[writer] or case["caucho"])


@pytest.mark.parametrize(
    ("name", "writer"),
    [case for case in CASES if case[0] not in BOUNDARIES],
    ids=lambda value: value,
)
def test_review2_java_writer_fixtures(name, writer):
    value = hessian2.loads(wire(name, writer))
    expected = FIXTURES[name]["values"][0]
    if name == "extra.review2_boxed_boundaries" and writer == "lite":
        original = [0.1, 1.4e-45, 1.17549435e-38, 3.4028235e38]
        for index, number in enumerate(original, start=4):
            assert value[index] == number
        assert value[:4] == [-128, 127, -32768, 32767]
    else:
        assert describe(value) == expected
    if name == "extra.review2_time_nested_refs":
        assert value[0] is value[3]
        assert value[1] is value[4] and value[2] is value[5]
        assert value[6] is value[7]
    elif name == "extra.review2_bigint_refs":
        assert value[0] is value[2] and value[1] is value[3]
    elif name == "extra.review2_boxed_refs":
        assert value[0] == value[1] and value[2] is value[3]
    elif name == "extra.review2_time_overlap":
        assert [item.fold for item in value] == [0, 1]
        assert [item.utcoffset() for item in value] == [
            dt.timedelta(hours=-4),
            dt.timedelta(hours=-5),
        ]


@pytest.mark.parametrize("name", sorted(BOUNDARIES))
def test_java_zoned_year_boundary(name):
    if name.endswith("min"):
        expected = dt.datetime(1, 1, 1, tzinfo=ZoneInfo("Etc/GMT-1"))
    else:
        expected = dt.datetime(9999, 12, 31, 23, 59, 59, tzinfo=ZoneInfo("Etc/GMT+1"))
    value = hessian2.loads(wire(name, "lite"))
    assert value == expected and value.tzinfo == expected.tzinfo


@needs_java
@pytest.mark.parametrize("reader", WRITERS)
def test_review2_java_reads_python_and_original_bytes(tmp_path, reader):
    assert JAVA is not None
    for name, writer in CASES:
        if name in BOUNDARIES:
            continue
        raw = wire(name, writer)
        (tmp_path / f"{name}-{writer}-java.bin").write_bytes(raw)
        decoded = hessian2.loads(raw)
        (tmp_path / f"{name}-{writer}-python.bin").write_bytes(hessian2.dumps(decoded))
    extra_values = ({"a", "b"}, frozenset(), -(2**1000), 2**64)
    for index, value in enumerate(extra_values):
        (tmp_path / f"python-{index}.bin").write_bytes(hessian2.dumps(value))
    result = subprocess.run(
        [*JAVA, "describe", reader, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    views = json.loads(result.stdout)
    for name, writer in CASES:
        if name in BOUNDARIES:
            continue
        prefix = f"{name}-{writer}"
        ours = views[f"{prefix}-python"]
        assert "error" not in ours, ours
        decoded = hessian2.loads(wire(name, writer))
        assert unordered(ours["values"]) == unordered([describe(decoded)])
        original = views[f"{prefix}-java"]
        if writer == reader or name not in {
            "extra.review2_boxed_boundaries",
            "extra.review2_boxed_refs",
        }:
            assert "error" not in original, original
            assert unordered(original["values"]) == unordered([describe(decoded)])
    for index, value in enumerate(extra_values):
        view = views[f"python-{index}"]
        assert "error" not in view, view
        if isinstance(value, set | frozenset):
            assert sorted(view["values"][0]["list"], key=repr) == [
                describe(item) for item in sorted(value)
            ]
        else:
            assert view["values"] == [describe(value)]


def test_explicit_boxed_reference_resolves_to_the_completed_value():
    box = hessian2.HessianObject("com.caucho.hessian.io.FloatHandle", {"_value": 1.25})
    tail = []
    first, second, a, b = hessian2.loads(hessian2.dumps([box, box, tail, tail]))
    assert first == 1.25 and first is second and a is b


def test_float_handle_preserves_real_caucho_float32_value():
    value = hessian2.loads(wire("extra.review2_boxed_boundaries", "caucho"))
    expanded = struct.unpack(">f", struct.pack(">f", 0.1))[0]
    assert value[4] == expanded and value[4] != 0.1


@pytest.mark.parametrize(
    ("signum", "mag", "expected"),
    [(1, [0, 0, -1], 2**32 - 1), (-1, [0, -2147483648], -(2**31)), (0, [], 0)],
)
def test_bigint_leading_zero_words_and_sign(signum, mag, expected):
    raw = hessian2.dumps(
        hessian2.HessianObject("java.math.BigInteger", {"signum": signum, "mag": mag})
    )
    assert hessian2.loads(raw) == expected


@pytest.mark.parametrize(
    ("name", "fields"),
    [
        ("java.math.BigInteger", {"signum": True, "mag": [1]}),
        ("java.math.BigInteger", {"signum": 1, "mag": [2**32]}),
        ("java.math.BigInteger", {"signum": -2, "mag": []}),
        ("com.caucho.hessian.io.ByteHandle", {"_value": "1"}),
        ("com.caucho.hessian.io.ShortHandle", {"_value": 1.5}),
        ("com.caucho.hessian.io.FloatHandle", {"_value": None}),
        ("com.alibaba.com.caucho.hessian.io.java8.ZoneOffsetHandle", {"seconds": "0"}),
        (
            "com.alibaba.com.caucho.hessian.io.java8.DurationHandle",
            {"seconds": 0, "nanos": []},
        ),
    ],
)
def test_review2_wrong_field_types_fail_in_our_decoder(name, fields):
    with pytest.raises(hessian2.DecodeError):
        hessian2.loads(hessian2.dumps(hessian2.HessianObject(name, fields)))
