"""Validate the complete Python release artifact set before publication."""

from __future__ import annotations

import email
import sys
import tarfile
import zipfile
from pathlib import Path

from packaging.utils import parse_wheel_filename
from packaging.version import Version

PYTHONS = {"cp311", "cp312", "cp313", "cp314", "cp315"}
ABIS = {(p, p) for p in PYTHONS} | {("cp314", "cp314t"), ("cp315", "cp315t")}
PLATFORMS = {
    "manylinux-x86_64",
    "manylinux-aarch64",
    "musllinux-x86_64",
    "musllinux-aarch64",
    "macos-x86_64",
    "macos-aarch64",
    "windows-x86_64",
    "windows-aarch64",
}


def platform_key(tag: str) -> str:
    for arch in ("x86_64", "aarch64"):
        if tag in {f"manylinux2014_{arch}", f"manylinux_2_17_{arch}"}:
            return f"manylinux-{arch}"
        if tag == f"musllinux_1_2_{arch}":
            return f"musllinux-{arch}"
    if tag.startswith("macosx_"):
        if tag.endswith("_x86_64"):
            return "macos-x86_64"
        if tag.endswith("_arm64"):
            return "macos-aarch64"
    if tag == "win_amd64":
        return "windows-x86_64"
    if tag == "win_arm64":
        return "windows-aarch64"
    raise ValueError(f"Unexpected wheel platform: {tag}")


def check_metadata(raw: bytes, version: str) -> None:
    metadata = email.message_from_bytes(raw)
    if metadata["Name"] != "hessian2" or metadata["Version"] != version:
        raise ValueError("Package metadata does not match hessian2 release version")
    if metadata["Requires-Python"] != ">=3.11":
        raise ValueError("Unexpected Python version requirement")


def check(directory: Path, version: str) -> None:
    expected = {
        (platform, python, abi) for platform in PLATFORMS for python, abi in ABIS
    }
    seen = set()
    sources = []
    for path in sorted(directory.iterdir()):
        if path.name.endswith(".tar.gz"):
            sources.append(path)
            continue
        if path.suffix != ".whl":
            raise ValueError(f"Unexpected release artifact: {path.name}")
        name, wheel_version, build, tags = parse_wheel_filename(path.name)
        if name != "hessian2" or wheel_version != Version(version) or build:
            raise ValueError(f"Unexpected wheel name or version: {path.name}")
        keys = {(platform_key(t.platform), t.interpreter, t.abi) for t in tags}
        if len(keys) != 1:
            raise ValueError(f"Wheel spans incompatible targets: {path.name}")
        key = keys.pop()
        if key not in expected or key in seen:
            raise ValueError(f"Unexpected or duplicate target: {key}")
        seen.add(key)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                raise ValueError(f"Expected one METADATA file: {path.name}")
            check_metadata(archive.read(metadata[0]), version)
            if not {
                "hessian2/__init__.py",
                "hessian2/_core.pyi",
                "hessian2/py.typed",
            } <= set(names):
                raise ValueError(f"Missing Python package files: {path.name}")
            if not any(
                n.startswith("hessian2/_core.") and n.endswith((".so", ".pyd"))
                for n in names
            ):
                raise ValueError(f"Missing native extension: {path.name}")
    if seen != expected:
        raise ValueError(f"Missing wheel targets: {sorted(expected - seen)}")
    if len(sources) != 1 or sources[0].name != f"hessian2-{version}.tar.gz":
        raise ValueError("Expected exactly one version-matching source distribution")
    with tarfile.open(sources[0], "r:gz") as archive:
        metadata = [
            m for m in archive.getmembers() if m.name == f"hessian2-{version}/PKG-INFO"
        ]
        if len(metadata) != 1:
            raise ValueError("Source archive is missing its root PKG-INFO")
        stream = archive.extractfile(metadata[0])
        if stream is None:
            raise ValueError("Source PKG-INFO must be a file")
        check_metadata(stream.read(), version)
    print(f"Validated {len(seen)} wheels and one source distribution for {version}")


if __name__ == "__main__":
    check(Path(sys.argv[1]), sys.argv[2])
