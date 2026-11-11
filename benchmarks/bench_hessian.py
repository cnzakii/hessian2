"""Compare complete Python, Java, and Go Hessian string calls on shared bytes.

    uv run --group bench python benchmarks/bench_hessian.py

Requires JDK 17+, Go, and the Java interop jars (make interop-java).
Results and generated samples are written under target/hessian-benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import timeit
from pathlib import Path

import hessian2

ROOT = Path(__file__).resolve().parents[1]
TEXTS = {
    "ascii": "a" * 1_000_000,
    "chinese": "中" * 333_333,
    "mixed": ("hello 世界 " * 20 + "😀") * 3333,
    "emoji": "😀" * 250_000,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    if args.iterations < 1 or args.rounds < 1:
        parser.error("iterations and rounds must be positive")
    java_home = os.environ.get("JAVA_HOME")
    java = str(Path(java_home) / "bin/java") if java_home else shutil.which("java")
    javac = str(Path(java_home) / "bin/javac") if java_home else shutil.which("javac")
    go = shutil.which("go")
    if not java or not javac or not go:
        parser.error("requires java, javac, and go")
    directory = ROOT / "target/hessian-benchmark"
    directory.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, text in TEXTS.items():
        wire = hessian2.dumps(text)
        (directory / f"{name}.txt").write_text(text, encoding="utf-8")
        (directory / f"{name}.bin").write_bytes(wire)
        results[name] = {
            "hessian2": {
                operation: statistics.median(
                    timeit.repeat(
                        lambda fn=fn, value=value: fn(value),
                        number=args.iterations,
                        repeat=args.rounds,
                    )
                )
                * 1e6
                / args.iterations
                for operation, fn, value in [
                    ("dumps", hessian2.dumps, text),
                    ("loads", hessian2.loads, wire),
                ]
            }
        }
    classpath = os.pathsep.join(
        [
            str(directory),
            str(ROOT / "interop/java/build"),
            str(ROOT / "interop/java/lib/*"),
        ]
    )
    subprocess.run(
        [
            javac,
            "-cp",
            classpath,
            "-d",
            str(directory),
            str(ROOT / "interop/java/src/interop/TextBenchmark.java"),
        ],
        check=True,
    )
    java_result = subprocess.check_output(
        [
            java,
            "-cp",
            classpath,
            "interop.TextBenchmark",
            str(directory),
            str(args.iterations),
            str(args.rounds),
        ],
        text=True,
    )
    executable = directory / ("go-textbench.exe" if os.name == "nt" else "go-textbench")
    subprocess.run(
        [go, "build", "-o", str(executable), "./cmd/textbench"],
        cwd=ROOT / "interop/go",
        check=True,
    )
    go_result = subprocess.check_output(
        [str(executable), str(directory), str(args.iterations), str(args.rounds)],
        text=True,
    )
    for name, peers in json.loads(java_result).items():
        results[name].update(peers)
    for name, peers in json.loads(go_result).items():
        results[name]["go"] = peers
    (directory / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print("Microseconds per call; median of rounds, warmed inputs.")
    for name, peers in results.items():
        print(name)
        for peer, result in peers.items():
            print(
                f"  {peer:10} dumps {result['dumps']:9.1f}  loads {result['loads']:9.1f}"
            )


if __name__ == "__main__":
    main()
