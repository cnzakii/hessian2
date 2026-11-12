"""Exercise release completeness checks with small synthetic archives."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from release_check import ABIS, PLATFORMS, check

PLATFORM_TAGS = {
    "manylinux-x86_64": "manylinux_2_17_x86_64.manylinux2014_x86_64",
    "manylinux-aarch64": "manylinux_2_17_aarch64.manylinux2014_aarch64",
    "musllinux-x86_64": "musllinux_1_2_x86_64",
    "musllinux-aarch64": "musllinux_1_2_aarch64",
    "macos-x86_64": "macosx_10_13_x86_64",
    "macos-aarch64": "macosx_11_0_arm64",
    "windows-x86_64": "win_amd64",
    "windows-aarch64": "win_arm64",
}
METADATA = (
    b"Metadata-Version: 2.4\nName: hessian2\nVersion: 0.1.0\nRequires-Python: >=3.11\n"
)


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    for platform in PLATFORMS:
        for python, abi in ABIS:
            tag = PLATFORM_TAGS[platform]
            with zipfile.ZipFile(
                tmp_path / f"hessian2-0.1.0-{python}-{abi}-{tag}.whl", "w"
            ) as archive:
                archive.writestr("hessian2-0.1.0.dist-info/METADATA", METADATA)
                for name in ("__init__.py", "_core.pyi", "py.typed", "_core.so"):
                    archive.writestr(f"hessian2/{name}", b"")
    with tarfile.open(tmp_path / "hessian2-0.1.0.tar.gz", "w:gz") as archive:
        member = tarfile.TarInfo("hessian2-0.1.0/PKG-INFO")
        member.size = len(METADATA)
        archive.addfile(member, io.BytesIO(METADATA))
    return tmp_path


def test_complete_release(artifacts: Path) -> None:
    check(artifacts, "0.1.0")


def test_missing_wheel(artifacts: Path) -> None:
    next(artifacts.glob("*.whl")).unlink()
    with pytest.raises(ValueError, match="Missing wheel targets"):
        check(artifacts, "0.1.0")


def test_duplicate_target(artifacts: Path) -> None:
    original = next(artifacts.glob("*-manylinux_2_17_x86_64.*.whl"))
    duplicate = original.with_name(original.name.replace("manylinux_2_17_x86_64.", ""))
    duplicate.write_bytes(original.read_bytes())
    with pytest.raises(ValueError, match="duplicate target"):
        check(artifacts, "0.1.0")


def test_wrong_version(artifacts: Path) -> None:
    with pytest.raises(ValueError, match="version"):
        check(artifacts, "0.2.0")


def test_missing_source(artifacts: Path) -> None:
    next(artifacts.glob("*.tar.gz")).unlink()
    with pytest.raises(ValueError, match="source distribution"):
        check(artifacts, "0.1.0")


def test_missing_extension(artifacts: Path) -> None:
    wheel = next(artifacts.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        members = {
            n: archive.read(n) for n in archive.namelist() if not n.endswith(".so")
        }
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match="Missing native extension"):
        check(artifacts, "0.1.0")
