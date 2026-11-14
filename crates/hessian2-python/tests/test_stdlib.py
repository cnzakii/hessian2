"""Public contracts for stdlib."""

from __future__ import annotations

import datetime
import datetime as dt
import re
import uuid
import zoneinfo
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import hessian2
import pytest
from assertions import same
from conftest import java_wire as wire
from hessian2 import DecodeError, EncodeError, HessianObject


def test_caucho_uuid_bean_fields_restore_a_python_uuid():
    wire = b"C\x0ejava.util.UUID\x92\x0bmostSigBits\x0cleastSigBits\x60L\x12\x3e\x45\x67\xe8\x9b\x12\xd3L\xa4\x56\x42\x66\x14\x17\x40\x00"

    assert same(hessian2.loads(wire), uuid.UUID("123e4567-e89b-12d3-a456-426614174000"))


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("java.math.BigDecimal", "1_000"),
        ("java.math.BigDecimal", "NaN"),
        ("java.math.BigDecimal", " 1"),
        ("java.util.UUID", "123e4567e89b12d3a456426614174000"),
        ("java.util.UUID", "{123e4567-e89b-12d3-a456-426614174000}"),
    ],
)
def test_invalid_builtin_values(name: str, text: str) -> None:
    def s(value: str) -> bytes:
        return bytes([len(value)]) + value.encode()

    with pytest.raises(DecodeError):
        hessian2.loads(b"C" + s(name) + b"\x91" + s("value") + b"\x60" + s(text))


def test_big_integer_subclass_uses_numeric_value():
    class Number(int):
        def __neg__(self):
            return 7

    value = Number(-(2**100))

    assert same(hessian2.loads(hessian2.dumps(value)), int(value))


def test_time_with_zone_but_no_offset_is_naive():
    value = dt.time(12, 34, 56, 123456, tzinfo=ZoneInfo("Europe/Paris"))

    assert same(hessian2.loads(hessian2.dumps(value)), value.replace(tzinfo=None))


@pytest.mark.parametrize("zone", ["utc", "zero-offset"])
def test_datetime_subclasses_use_tzinfo_offsets_on_both_encoding_paths(zone):
    class Zero(dt.tzinfo):
        def utcoffset(self, value):
            return dt.timedelta(0)

        def dst(self, value):
            return dt.timedelta(0)

    class DateTime(dt.datetime):
        def utcoffset(self):
            return dt.timedelta(hours=1)

    value = DateTime(2024, 1, 1, tzinfo={"utc": dt.UTC, "zero-offset": Zero()}[zone])

    assert same(
        hessian2.loads(hessian2.dumps(value)), dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    )


def test_a_datetime_zone_returning_none_encodes_as_naive():
    class Zone(dt.tzinfo):
        def utcoffset(self, value):
            return None

    value = dt.datetime(2024, 1, 1, tzinfo=Zone())

    assert same(hessian2.loads(hessian2.dumps(value)), dt.datetime(2024, 1, 1))


@pytest.mark.parametrize(
    ("result", "error_type"),
    [(1, TypeError), (dt.timedelta(days=1), ValueError)],
    ids=["non-timedelta", "out-of-range"],
)
def test_invalid_datetime_offsets_raise_the_python_api_error(result, error_type):
    class Zone(dt.tzinfo):
        def utcoffset(self, value):
            return result

    value = dt.datetime(2024, 1, 1, tzinfo=Zone())

    with pytest.raises(error_type):
        hessian2.dumps(value)


def test_datetime_offset_callback_failures_propagate():
    class Zone(dt.tzinfo):
        def utcoffset(self, value):
            raise RuntimeError("offset")

    with pytest.raises(RuntimeError, match=re.escape("offset")):
        hessian2.dumps(dt.datetime(2024, 1, 1, tzinfo=Zone()))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (dt.datetime(1, 1, 1, tzinfo=dt.UTC), dt.datetime(1, 1, 1, tzinfo=dt.UTC)),
        (
            dt.datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=dt.UTC),
            dt.datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=dt.UTC),
        ),
        (
            dt.datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=dt.UTC),
            dt.datetime(1969, 12, 31, 23, 59, 59, 999000, tzinfo=dt.UTC),
        ),
        (
            dt.datetime(
                2024, 11, 3, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=0
            ),
            dt.datetime(2024, 11, 3, 5, 30, tzinfo=dt.UTC),
        ),
        (
            dt.datetime(
                2024, 11, 3, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1
            ),
            dt.datetime(2024, 11, 3, 6, 30, tzinfo=dt.UTC),
        ),
        (
            dt.datetime(2024, 3, 10, 2, 30, tzinfo=ZoneInfo("America/New_York")),
            dt.datetime(2024, 3, 10, 7, 30, tzinfo=dt.UTC),
        ),
        (
            dt.datetime(
                2024, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=-5, seconds=-3))
            ),
            dt.datetime(2024, 1, 1, 5, 0, 3, tzinfo=dt.UTC),
        ),
    ],
    ids=[
        "year-one",
        "year-9999",
        "before-epoch",
        "overlap-first",
        "overlap-second",
        "gap",
        "second-offset",
    ],
)
def test_aware_datetimes_normalize_to_utc_with_millisecond_precision(value, expected):
    assert same(hessian2.loads(hessian2.dumps(value)), expected)


@pytest.mark.parametrize(
    "millis", [-(2**63), 2**63 - 1, -62135596800001, 253402300800000]
)
def test_dates_outside_python_years_raise_decode_error(millis):
    with pytest.raises(DecodeError, match=re.escape("range")):
        hessian2.loads(b"J" + millis.to_bytes(8, "big", signed=True))


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
def test_standard_types_choose_their_java_mapping(value: Any, wire: bytes) -> None:
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
        datetime.datetime(2024, 2, 29, 23, 59, 59, 123000, tzinfo=UTC),
        datetime.datetime(1, 1, 1, tzinfo=UTC),
        datetime.datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
        datetime.datetime(1969, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
        Decimal("-123.4500"),
        Decimal("1E+3"),
        Decimal("0E-10"),
        uuid.UUID("123e4567-e89b-12d3-a456-426614174000"),
        uuid.UUID(int=0),
        uuid.UUID(int=2**128 - 1),
    ],
    ids=repr,
)
def test_standard_library_values_preserve_their_type_and_value(value: Any) -> None:
    decoded = hessian2.loads(hessian2.dumps(value))

    assert same(decoded, value)


def test_naive_datetime_stays_naive_and_keeps_microseconds() -> None:
    value = datetime.datetime(2024, 2, 29, 13, 45, 30, 123_456)

    decoded = hessian2.loads(hessian2.dumps(value))

    assert decoded == value
    assert decoded.tzinfo is None


def test_aware_datetime_is_a_date_in_milliseconds() -> None:
    value = datetime.datetime(
        2024, 1, 1, 8, tzinfo=datetime.timezone(datetime.timedelta(hours=8))
    )

    assert same(
        hessian2.loads(hessian2.dumps(value)), datetime.datetime(2024, 1, 1, tzinfo=UTC)
    )


def test_aware_time_has_no_form() -> None:
    with pytest.raises(EncodeError, match=re.escape("aware time")):
        hessian2.dumps(datetime.time(1, tzinfo=UTC))


class Day(datetime.date):
    pass


class Span(datetime.timedelta):
    pass


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Day(2000, 1, 1), datetime.date(2000, 1, 1)),
        (Span(days=1), datetime.timedelta(days=1)),
    ],
    ids=["date", "duration"],
)
def test_standard_library_subclasses_decode_as_their_builtin_base(value, expected):
    assert same(hessian2.loads(hessian2.dumps(value)), expected)


def test_shared_date_is_a_reference() -> None:
    day = datetime.date(2000, 1, 1)

    decoded = hessian2.loads(hessian2.dumps([day, day]))

    assert decoded[0] is decoded[1]


def test_nanoseconds_below_a_microsecond_are_dropped() -> None:
    wire = handle("LocalTimeHandle", nano=1_999, second=0, minute=0, hour=0)

    assert same(hessian2.loads(wire), datetime.time(0, 0, 0, 1))


def test_negative_duration_rounds_toward_negative_infinity() -> None:
    # Duration.ofNanos(-1): seconds -1, nanos 999_999_999.
    wire = handle("DurationHandle", nanos=999_999_999, seconds=-1)

    assert same(hessian2.loads(wire), datetime.timedelta(microseconds=-1))


def test_instant_decodes_as_utc_datetime() -> None:
    wire = handle("InstantHandle", nanos=123_456_789, seconds=1_700_000_000)

    assert same(
        hessian2.loads(wire),
        datetime.datetime(2023, 11, 14, 22, 13, 20, 123456, tzinfo=UTC),
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


@pytest.mark.parametrize(
    ("name", "value"),
    [("ByteHandle", -3), ("ShortHandle", -3), ("FloatHandle", 1.5)],
    ids=["byte", "short", "float"],
)
def test_caucho_boxed_number_handles_restore_python_numbers(name, value):
    data = hessian2.dumps(
        HessianObject("com.caucho.hessian.io." + name, {"_value": value})
    )

    assert same(hessian2.loads(data), value)


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

    assert same(codec.loads(codec.dumps(datetime.date(2000, 1, 2))), "2000-01-02")


def test_float_handle_preserves_real_caucho_float32_value():
    value = hessian2.loads(wire("extra.review2_boxed_boundaries", "caucho"))

    assert same(value[4], 0.10000000149011612)


@pytest.mark.parametrize(
    ("signum", "mag", "expected"),
    [(1, [0, 0, -1], 2**32 - 1), (-1, [0, -2147483648], -(2**31)), (0, [], 0)],
)
def test_bigint_leading_zero_words_and_sign(signum, mag, expected):
    raw = hessian2.dumps(
        hessian2.HessianObject("java.math.BigInteger", {"signum": signum, "mag": mag})
    )

    assert same(hessian2.loads(raw), expected)


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
def test_builtin_object_fields_reject_wrong_python_types(name, fields):
    with pytest.raises(hessian2.DecodeError):
        hessian2.loads(hessian2.dumps(hessian2.HessianObject(name, fields)))


@pytest.mark.parametrize(
    ("microseconds", "expected"),
    [(0, 0), (999, 0), (1000, 1000), (999999, 999000)],
    ids=["exact", "below-millisecond", "millisecond", "last-microsecond"],
)
def test_aware_datetimes_drop_submillisecond_precision(microseconds, expected):
    value = datetime.datetime(2024, 1, 1, microsecond=microseconds, tzinfo=UTC)

    decoded = hessian2.loads(hessian2.dumps(value))

    assert same(
        decoded, datetime.datetime(2024, 1, 1, microsecond=expected, tzinfo=UTC)
    )
