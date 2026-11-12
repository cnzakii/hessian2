"""Encode and decode throughput of hessian2, with pickle, msgpack, and orjson
as references on equivalent data.

    uv run --group bench python benchmarks/bench.py --fast -o result.json
    uv run --group bench python -m pyperf compare_to ...

No maintained Python Hessian 2.0 library exists to compare against.
"""

from __future__ import annotations

import dataclasses
import datetime
import pickle
import uuid
from decimal import Decimal
from typing import Any

import hessian2
import msgpack
import orjson
import pyperf

codec = hessian2.Codec()


@dataclasses.dataclass
class User:
    user_id: int
    name: str
    email: str
    age: int
    score: float
    active: bool
    created: datetime.datetime
    tags: list[str]


codec.register(User, name="com.example.User")


def user(n: int) -> User:
    return User(
        user_id=10_000_000_000 + n,
        name=f"user-{n}",
        email=f"user{n}@example.com",
        age=20 + n % 50,
        score=n * 0.37,
        active=n % 3 == 0,
        created=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)
        + datetime.timedelta(seconds=n),
        tags=["a", "bb", "ccc"][: n % 4],
    )


USERS = [user(n) for n in range(1000)]
DICTS = [
    dataclasses.asdict(u) | {"created": int(u.created.timestamp() * 1000)}
    for u in USERS
]
SCENARIOS: dict[str, Any] = {
    "users": USERS,
    "dicts": DICTS,
    "ints": [n * 7919 % 1_000_003 - 500_000 for n in range(10_000)],
    "floats": [n * 0.1 for n in range(10_000)],
    "strings": [f"string number {n} 中文" for n in range(1000)],
    "text_1mb": "x" * 1_000_000,
    "bytes_1mb": b"x" * 1_000_000,
    "decimals_uuids": [
        [Decimal(f"{n}.{n % 100:02d}"), uuid.UUID(int=n)] for n in range(1000)
    ],
}


def _orjson_dumps(value: Any) -> bytes:
    return orjson.dumps(value, option=orjson.OPT_PASSTHROUGH_DATETIME, default=_default)


def _default(value: Any) -> Any:
    if isinstance(value, datetime.datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, bytes):
        return value.decode("latin-1")
    if isinstance(value, Decimal | uuid.UUID):
        return str(value)
    raise TypeError


def _msgpack_dumps(value: Any) -> bytes:
    return msgpack.packb(value, default=_msgpack_default)


def _msgpack_default(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    return _default(value)


LIBRARIES = {
    "hessian2": (codec.dumps, codec.loads),
    "pickle": (pickle.dumps, pickle.loads),
    "msgpack": (_msgpack_dumps, msgpack.unpackb),
    "orjson": (_orjson_dumps, orjson.loads),
}


def main() -> None:
    runner = pyperf.Runner()
    for scenario, value in SCENARIOS.items():
        for library, (dumps, loads) in LIBRARIES.items():
            data = dumps(value)
            runner.bench_func(f"{scenario}/dumps/{library}", dumps, value)
            runner.bench_func(f"{scenario}/loads/{library}", loads, data)


if __name__ == "__main__":
    main()
