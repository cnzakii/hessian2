"""Interoperability with dubbo-go-hessian2 (interop/go, pinned in go.mod).

`fixtures/go.json` holds Go's bytes and a description of each Go value;
regenerate it with `make fixtures-go`. The test that runs Go skips without a
Go toolchain.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import hessian2
import pytest
from describe import describe, unordered

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "go.json").read_text())
CODEC = hessian2.Codec(unknown_class="hessian_object")
NAMES = sorted(FIXTURES)


def wire(name: str) -> bytes:
    return base64.b64decode(FIXTURES[name]["go"])


@pytest.mark.parametrize("name", NAMES)
def test_decodes_go_bytes(name: str) -> None:
    values = CODEC.loads_many(wire(name))
    assert unordered([describe(value) for value in values]) == FIXTURES[name]["values"]


GO = shutil.which("go")

# Where Go's view of our bytes differs from the value, and why.
GO_READS_DIFFERENTLY = {
    # We write 0.001 * 9 as 0x5f 9, as Java does; Go reads 0x5f as 9 / 1000,
    # which rounds to a different binary64.
    "double_mill_ulp": "0x5f rounding",
}


@pytest.fixture(scope="module")
def go_views(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    directory = tmp_path_factory.mktemp("ours")
    for name in NAMES:
        (directory / f"{name}.bin").write_bytes(
            CODEC.dumps_many(CODEC.loads_many(wire(name)))
        )
    result = subprocess.run(
        [GO or "go", "run", ".", "describe", str(directory)],
        cwd=ROOT / "interop" / "go",
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(GO is None, reason="requires a Go toolchain")
@pytest.mark.parametrize("name", NAMES)
def test_go_reads_our_bytes(go_views: dict[str, Any], name: str) -> None:
    view = go_views[name]
    assert "error" not in view, view
    same = view["values"] == FIXTURES[name]["values"]
    reason = GO_READS_DIFFERENTLY.get(name)
    assert same == (reason is None), reason or (
        view["values"],
        FIXTURES[name]["values"],
    )
