"""Mappings between Python standard library types and JDK classes.

Wire forms are checked against bytes hessian-lite or Caucho wrote (see
`interop/java`); edge cases cover values one side cannot represent.
"""

from __future__ import annotations

import datetime
import zoneinfo
from typing import Any

import hessian2
import pytest
from hessian2 import DecodeError, EncodeError, HessianObject

JAVA8 = "com.alibaba.com.caucho.hessian.io.java8."
UTC = datetime.UTC


def handle(name: str, **fields: Any) -> bytes:
    """Bytes of a hessian-lite java.time handle object."""
    return hessian2.dumps(HessianObject(JAVA8 + name, fields))


@pytest.mark.parametrize(
    ("value", "wire"),
    [
        # hessian-lite 4.0.5, LocalDate.of(2024, 2, 29).
        (
            datetime.date(2024, 2, 29),
            b"C\x30\x37" + JAVA8.encode() + b"LocalDateHandle"
            b"\x93\x03day\x05month\x04year\x60\xad\x92\xcf\xe8",
        ),
        # A typed list, as Java writes a HashSet.
        ({"a"}, b"\x71\x11java.util.HashSet\x01a"),
        (frozenset(), b"\x70\x11java.util.HashSet"),
    ],
    ids=repr,
)
def test_wire_form(value: Any, wire: bytes) -> None:
    assert hessian2.dumps(value) == wire


@pytest.mark.parametrize(
    "value",
    [
        datetime.date(1, 1, 1),
        datetime.date(9999, 12, 31),
        datetime.time(0, 0),
        datetime.time(23, 59, 59, 999_999),
        datetime.datetime(1, 1, 1),
        datetime.datetime(9999, 12, 31, 23, 59, 59, 999_999),
        datetime.timedelta(0),
        datetime.timedelta.max,
        datetime.timedelta.min,
        datetime.timedelta(microseconds=-1),
        2**64,
        -(2**63) - 1,
        2**1000,
        -(2**1000),
    ],
    ids=repr,
)
def test_round_trip(value: Any) -> None:
    decoded = hessian2.loads(hessian2.dumps(value))
    assert decoded == value
    assert type(decoded) is type(value)


def test_naive_datetime_stays_naive_and_keeps_microseconds() -> None:
    value = datetime.datetime(2024, 2, 29, 13, 45, 30, 123_456)
    decoded = hessian2.loads(hessian2.dumps(value))
    assert decoded == value
    assert decoded.tzinfo is None


def test_aware_datetime_is_a_date_in_milliseconds() -> None:
    value = datetime.datetime(
        2024, 1, 1, 8, tzinfo=datetime.timezone(datetime.timedelta(hours=8))
    )
    assert hessian2.dumps(value)[:1] == b"\x4b"
    assert hessian2.loads(hessian2.dumps(value)) == datetime.datetime(
        2024, 1, 1, tzinfo=UTC
    )


def test_aware_time_has_no_form() -> None:
    with pytest.raises(EncodeError, match="aware time"):
        hessian2.dumps(datetime.time(1, tzinfo=UTC))


def test_subclasses_encode_as_their_base() -> None:
    class Day(datetime.date):
        pass

    class Span(datetime.timedelta):
        pass

    assert hessian2.loads(hessian2.dumps(Day(2000, 1, 1))) == datetime.date(2000, 1, 1)
    assert hessian2.loads(hessian2.dumps(Span(days=1))) == datetime.timedelta(days=1)


def test_shared_date_is_a_reference() -> None:
    day = datetime.date(2000, 1, 1)
    decoded = hessian2.loads(hessian2.dumps([day, day]))
    assert decoded[0] is decoded[1]


def test_nanoseconds_below_a_microsecond_are_dropped() -> None:
    wire = handle("LocalTimeHandle", nano=1_999, second=0, minute=0, hour=0)
    assert hessian2.loads(wire) == datetime.time(0, 0, 0, 1)


def test_negative_duration_rounds_toward_negative_infinity() -> None:
    # Duration.ofNanos(-1): seconds -1, nanos 999_999_999.
    wire = handle("DurationHandle", nanos=999_999_999, seconds=-1)
    assert hessian2.loads(wire) == datetime.timedelta(microseconds=-1)


def test_instant_decodes_as_utc_datetime() -> None:
    wire = handle("InstantHandle", nanos=123_456_789, seconds=1_700_000_000)
    assert hessian2.loads(wire) == datetime.datetime(
        2023, 11, 14, 22, 13, 20, 123_456, tzinfo=UTC
    )


def _offset_datetime(zone: str | None) -> bytes:
    local = HessianObject(
        JAVA8 + "LocalDateTimeHandle",
        {
            "time": HessianObject(
                JAVA8 + "LocalTimeHandle",
                {"nano": 0, "second": 0, "minute": 0, "hour": 9},
            ),
            "date": HessianObject(
                JAVA8 + "LocalDateHandle", {"day": 1, "month": 7, "year": 2024}
            ),
        },
    )
    offset = HessianObject(JAVA8 + "ZoneOffsetHandle", {"seconds": 7200})
    if zone is None:
        return handle("OffsetDateTimeHandle", offset=offset, dateTime=local)
    return handle("ZonedDateTimeHandle", offset=offset, dateTime=local, zoneId=zone)


def test_offset_datetime_keeps_its_offset() -> None:
    decoded = hessian2.loads(_offset_datetime(None))
    assert decoded.utcoffset() == datetime.timedelta(hours=2)
    assert decoded == datetime.datetime(2024, 7, 1, 7, tzinfo=UTC)


def test_zoned_datetime_uses_its_zone() -> None:
    decoded = hessian2.loads(_offset_datetime("Europe/Paris"))
    assert decoded.tzinfo == zoneinfo.ZoneInfo("Europe/Paris")
    assert decoded == datetime.datetime(2024, 7, 1, 7, tzinfo=UTC)


@pytest.mark.parametrize("zone", ["No/Such_Zone", "../etc/passwd", ""])
def test_unknown_zone_keeps_the_offset(zone: str) -> None:
    decoded = hessian2.loads(_offset_datetime(zone))
    assert decoded.utcoffset() == datetime.timedelta(hours=2)
    assert decoded == datetime.datetime(2024, 7, 1, 7, tzinfo=UTC)


def test_caucho_boxed_numbers() -> None:
    for name in ("ByteHandle", "ShortHandle"):
        wire = hessian2.dumps(
            HessianObject("com.caucho.hessian.io." + name, {"_value": -3})
        )
        assert hessian2.loads(wire) == -3
    wire = hessian2.dumps(
        HessianObject("com.caucho.hessian.io.FloatHandle", {"_value": 1.5})
    )
    assert hessian2.loads(wire) == 1.5


@pytest.mark.parametrize(
    "wire",
    [
        # Outside the years datetime covers.
        handle("LocalDateHandle", day=1, month=1, year=10_000),
        handle("LocalDateHandle", day=30, month=2, year=2024),
        handle("LocalTimeHandle", nano=0, second=60, minute=0, hour=0),
        handle("InstantHandle", nanos=0, seconds=2**62),
        handle("DurationHandle", nanos=0, seconds=2**62),
        # Wrong field types and missing fields.
        handle("LocalDateHandle", day="1", month=1, year=2000),
        handle("LocalDateHandle", month=1, year=2000),
        handle("LocalDateTimeHandle", time=None, date=None),
        handle(
            "LocalDateTimeHandle",
            time=datetime.date(2000, 1, 1),
            date=datetime.date(2000, 1, 1),
        ),
        hessian2.dumps(
            HessianObject("java.math.BigInteger", {"signum": 2, "mag": [1]})
        ),
        hessian2.dumps(
            HessianObject("java.math.BigInteger", {"signum": 1, "mag": ["x"]})
        ),
        hessian2.dumps(
            HessianObject("com.caucho.hessian.io.FloatHandle", {"_value": "1"})
        ),
    ],
)
def test_invalid_values_raise_decode_error(wire: bytes) -> None:
    with pytest.raises(DecodeError):
        hessian2.loads(wire)


def test_registering_a_builtin_name_replaces_it() -> None:
    codec = hessian2.Codec()
    codec.register(
        hessian2.Converter(
            datetime.date, lambda d: {"iso": d.isoformat()}, lambda f: f["iso"]
        ),
        name=JAVA8 + "LocalDateHandle",
    )
    assert codec.loads(codec.dumps(datetime.date(2000, 1, 2))) == "2000-01-02"
