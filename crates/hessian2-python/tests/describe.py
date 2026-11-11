"""Structural descriptions of decoded values, matching `interop/java`'s `Describe`.

Integers of every width are ``{"int"}``, floats ``{"double"}`` with their bit
pattern, and a list, map, or object seen before is ``{"ref": n}``, numbered in
first-visit order. Strings and binaries longer than 1024 units are described
by SHA-256.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import struct
import uuid
from decimal import Decimal
from typing import Any

import hessian2

LARGE = 1024
EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)


def describe(value: Any) -> Any:
    return _Describer().value(value)


class _Describer:
    def __init__(self) -> None:
        self.seen: dict[int, int] = {}

    def value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            return {"bool": value}
        if isinstance(value, int):
            return {"int": str(value)}
        if isinstance(value, float):
            return {
                "double": format(struct.unpack(">Q", struct.pack(">d", value))[0], "x")
            }
        if isinstance(value, str):
            units = value.encode("utf-16-le", "surrogatepass")
            if len(units) // 2 > LARGE:
                return {"str_sha256": hashlib.sha256(units).hexdigest()}
            return {"str": value}
        if isinstance(value, bytes):
            if len(value) > LARGE:
                return {"bin_sha256": hashlib.sha256(value).hexdigest()}
            return {"bin": base64.b64encode(value).decode()}
        if isinstance(value, datetime.datetime):
            if value.utcoffset() is None:
                return {"local_datetime": f"{_date(value)}T{_time(value)}"}
            return {"date": str((value - EPOCH) // datetime.timedelta(milliseconds=1))}
        if isinstance(value, datetime.date):
            return {"local_date": _date(value)}
        if isinstance(value, datetime.time):
            return {"local_time": _time(value)}
        if isinstance(value, datetime.timedelta):
            micros = value // datetime.timedelta(microseconds=1)
            seconds, micros = divmod(micros, 1_000_000)
            return {"duration": f"{seconds}.{micros:06d}"}
        if isinstance(value, Decimal):
            return {"decimal": str(value)}
        if isinstance(value, uuid.UUID):
            return {"uuid": str(value)}
        if id(value) in self.seen:
            return {"ref": self.seen[id(value)]}
        self.seen[id(value)] = len(self.seen)
        if isinstance(value, list | tuple):
            return {"list": [self.value(item) for item in value]}
        if isinstance(value, dict):
            return {"map": [[self.value(k), self.value(v)] for k, v in value.items()]}
        if isinstance(value, hessian2.HessianObject):
            return {
                "object": value.class_name,
                "fields": {k: self.value(v) for k, v in value.fields.items()},
            }
        raise TypeError(f"cannot describe {type(value).__qualname__}")


def _date(value: datetime.date) -> str:
    return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"


def _time(value: datetime.time | datetime.datetime) -> str:
    return f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}.{value.microsecond:06d}"


def unordered(description: Any) -> Any:
    """Sorts map entries, for peers whose maps do not keep insertion order."""
    if isinstance(description, list):
        return [unordered(item) for item in description]
    if not isinstance(description, dict):
        return description
    if "map" in description:
        pairs = [unordered(pair) for pair in description["map"]]
        return {"map": sorted(pairs, key=repr)}
    return {key: unordered(value) for key, value in description.items()}
