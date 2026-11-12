"""Hessian-only regression benchmarks for text, field plans, and references.

Run against each installed build with the same interpreter version::

    python benchmarks/bench_paths.py --fast -o paths.json
"""

from __future__ import annotations

import dataclasses

import hessian2
import pyperf

codec = hessian2.Codec()


@dataclasses.dataclass
class Point:
    number: int
    name: str
    score: float
    enabled: bool


codec.register(Point, name="Point", field_naming=None)


class Converted:
    pass


codec.register(
    hessian2.Converter(
        Converted,
        to_fields=lambda value: {f"f{i}": i for i in range(32)},
        from_fields=lambda fields: Converted(),
    ),
    name="Converted",
)

wide = dataclasses.make_dataclass("Wide", [(f"f{i}", int) for i in range(1024)])
codec.register(wide, name="Wide", field_naming=None)
shared = Converted()
CASES = {
    "ascii": "a" * 1_000_000,
    "chinese": "中" * 333_333,
    "korean": "한" * 333_333,
    "mixed": ("hello 世界 " * 20 + "😀") * 3333,
    "emoji": "😀" * 250_000,
    "chinese_tail": "中" * 20_000,
    "short_strings": [f"string number {i} 中文" for i in range(1000)],
    "points": [Point(i, f"name-{i}", i * 0.5, bool(i % 2)) for i in range(1000)],
    "dicts": [
        {"number": i, "name": f"name-{i}", "score": i * 0.5} for i in range(1000)
    ],
    "wide": hessian2.HessianObject("Wide", {f"f{i}": i for i in reversed(range(1024))}),
    "references": [shared] * 1000,
    "bigints": [2**100 + i for i in range(2000)],
}


def main() -> None:
    runner = pyperf.Runner()
    for name, value in CASES.items():
        data = codec.dumps(value)
        runner.bench_func(f"{name}/dumps", codec.dumps, value)
        runner.bench_func(f"{name}/loads", codec.loads, data)


if __name__ == "__main__":
    main()
