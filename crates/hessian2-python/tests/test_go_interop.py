"""Public contracts for go interop."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import hessian2
import pytest
from assertions import same
from conftest import GO_FIXTURES as FIXTURES
from conftest import go_wire as wire
from describe import describe, unordered

ROOT = Path(__file__).resolve().parents[3]

CODEC = hessian2.Codec(unknown_class="hessian_object")

NAMES = sorted(FIXTURES)


@pytest.mark.parametrize("name", NAMES)
def test_decodes_go_bytes(name: str) -> None:
    values = CODEC.loads_many(wire(name))

    assert unordered([describe(value) for value in values]) == FIXTURES[name]["values"]


GO = shutil.which("go") if os.environ.get("HESSIAN2_LIVE_GO") == "1" else None


@pytest.mark.skipif(
    GO is None, reason="set HESSIAN2_LIVE_GO=1 to run the pinned Go peer"
)
@pytest.mark.parametrize(
    ("annotation", "value", "expected"),
    [
        (tuple[Any, ...], ["bob", 2], "[]hessian.Object"),
        (tuple[str, ...], ["bob"], "[]string"),
        (tuple[int, ...], [1], "[]int32"),
        (tuple[hessian2.Long, ...], [1], "[]int64"),
        (list[Any], ["bob"], "[]interface {}"),
    ],
    ids=["objects", "strings", "ints", "longs", "list"],
)
def test_go_reads_annotated_arrays_as_the_corresponding_typed_slice(
    tmp_path, annotation, value, expected
):
    (tmp_path / "value.bin").write_bytes(hessian2.dumps(value, type=annotation))

    result = subprocess.run(
        [GO, "run", ".", "types", str(tmp_path)],
        cwd=ROOT / "interop" / "go",
        capture_output=True,
        encoding="utf-8",
        check=True,
    )

    assert same(json.loads(result.stdout), {"value": expected})


@pytest.mark.skipif(
    GO is None, reason="set HESSIAN2_LIVE_GO=1 to run the pinned Go peer"
)
@pytest.mark.parametrize("name", NAMES)
def test_go_reads_python_mappings_as_the_expected_peer_values(go_views, name):
    expected = {"double_mill_ulp": [{"double": "3f826e978d4fdf3b"}]}.get(
        name, FIXTURES[name]["values"]
    )

    assert go_views[name] == {"values": expected}
