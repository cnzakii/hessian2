from __future__ import annotations

import abc
import dataclasses
import datetime as dt
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Generic, NamedTuple, TypeVar
from zoneinfo import ZoneInfo

import hessian2
import pytest
from hessian2 import Codec, Converter, DecodeError, EncodeError, HessianObject


@pytest.mark.parametrize("base", [dict, list, int, dt.datetime])
def test_static_extension_bases_remain_rejected(base):
    cls = dataclasses.make_dataclass("Extension", [("value", int)], bases=(base,))
    codec = Codec()
    codec.register("Extension", cls)
    wire = hessian2.dumps(HessianObject("Extension", {"value": 1}))
    with pytest.raises(TypeError):
        object.__new__(cls)
    with pytest.raises(TypeError):
        codec.loads(wire)


@pytest.mark.parametrize("slots", [False, True])
def test_python_new_metaclass_and_init_subclass_are_bypassed(slots):
    events = []

    class Meta(type):
        def __call__(cls, *args, **kwargs):
            events.append("call")
            return super().__call__(*args, **kwargs)

    class Base(metaclass=Meta):
        __slots__ = ("__dict__",)

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__(**kwargs)

            def new(cls, *args, **kwargs):
                events.append("new")
                return object.__new__(cls)

            cls.__new__ = staticmethod(new)

    @dataclasses.dataclass(slots=slots)
    class Item(Base):
        value: int

    codec = Codec()
    codec.register("Item", Item)
    value = codec.loads(hessian2.dumps(HessianObject("Item", {"value": 3})))
    assert type(value) is Item and value.value == 3
    assert events == []
    object.__setattr__(value, "extra", 4)
    assert value.__dict__["extra"] == 4


def test_abc_generic_and_class_reassignment():
    T = TypeVar("T")

    @dataclasses.dataclass
    class Item(abc.ABC, Generic[T]):
        value: T

    @dataclasses.dataclass
    class Other(Item):
        pass

    codec = Codec()
    codec.register("Item", Item)
    codec.register("Other", Other)
    item = Item(3)
    item.__class__ = Other
    decoded = codec.loads(codec.dumps(item))
    assert type(decoded) is Other and decoded.value == 3
    Item.__abstractmethods__ = frozenset({"missing"})
    with pytest.raises(TypeError, match="abstract"):
        codec.loads(hessian2.dumps(HessianObject("Item", {"value": 3})))


def test_descriptor_can_change_instance_class_during_decode():
    @dataclasses.dataclass
    class Item:
        first: int
        second: int

    class Other(Item):
        pass

    def store(instance, value):
        instance.__dict__["first"] = value
        instance.__class__ = Other

    Item.first = property(lambda self: self.__dict__["first"], store)
    codec = Codec()
    codec.register("Item", Item)
    decoded = codec.loads(
        hessian2.dumps(HessianObject("Item", {"first": 1, "second": 2}))
    )
    assert type(decoded) is Other and (decoded.first, decoded.second) == (1, 2)


def test_big_integer_subclass_uses_numeric_value():
    class Number(int):
        def __neg__(self):
            return 7

    value = Number(-(2**100))
    assert hessian2.loads(hessian2.dumps(value)) == int(value)


def test_time_with_zone_but_no_offset_is_naive():
    value = dt.time(12, 34, 56, 123456, tzinfo=ZoneInfo("Europe/Paris"))
    assert value.utcoffset() is None
    assert hessian2.loads(hessian2.dumps(value)) == value.replace(tzinfo=None)


def test_datetime_subclass_has_same_semantics_on_both_offset_paths():
    class Zero(dt.tzinfo):
        def utcoffset(self, value):
            return dt.timedelta(0)

        def dst(self, value):
            return dt.timedelta(0)

    class DateTime(dt.datetime):
        def utcoffset(self):
            return dt.timedelta(hours=1)

    utc = DateTime(2024, 1, 1, tzinfo=dt.UTC)
    zero = DateTime(2024, 1, 1, tzinfo=Zero())
    assert utc.timestamp() == zero.timestamp()
    assert hessian2.dumps(utc) == hessian2.dumps(zero)


@pytest.mark.parametrize(
    "value",
    [dt.datetime(2024, 1, 1), 2**100, [dt.date(2024, 1, 1)]],
    ids=["nested-time-handles", "bigint-mag", "date-in-list"],
)
def test_builtin_encoding_respects_depth(value):
    with pytest.raises(RecursionError):
        Codec(max_depth=1).dumps(value)


def test_named_tuple_cycle_is_rejected_on_encode():
    # A tuple exists only after its items, so no reader can resolve a
    # reference to it from inside them; refuse to write what cannot be read.
    class Node(NamedTuple):
        children: list

    codec = Codec()
    codec.register("Node", Node)
    children = []
    value = Node(children)
    children.append(value)
    with pytest.raises(EncodeError, match="contains itself"):
        codec.dumps(value)
    # Sharing without a cycle still keeps identity.
    leaf = Node([])
    first, second = codec.loads(codec.dumps([leaf, leaf]))
    assert first is second


def test_named_tuple_override_arity_defaults_and_references():
    Pair = namedtuple("Pair", "left right", defaults=[9])  # noqa: PYI024

    class Custom(Pair):
        def __new__(cls, *args):
            raise AssertionError("must bypass __new__")

    codec = Codec()
    codec.register("Pair", Custom)
    wire = hessian2.dumps(HessianObject("Pair", {"left": [1], "ignored": 3}))
    decoded = codec.loads(wire)
    assert type(decoded) is Custom and decoded == ([1], 9)
    first, second, child = codec.loads(codec.dumps([decoded, decoded, decoded.left]))
    assert first is second and first.left is child
    for items in [(), (1,), (1, 2, 3)]:
        with pytest.raises(EncodeError, match="items"):
            codec.dumps(tuple.__new__(Custom, items))
    with pytest.raises(DecodeError, match="missing field"):
        codec.loads(hessian2.dumps(HessianObject("Pair", {"right": 3})))


def test_initvar_inheritance_kw_only_and_override():
    seen = []

    @dataclasses.dataclass
    class Base:
        seed: dataclasses.InitVar[int] = 2

    @dataclasses.dataclass
    class Item(Base):
        seed: dataclasses.InitVar[int] = 4
        extra: dataclasses.InitVar[int] = dataclasses.field(default=7, kw_only=True)
        value: int = 3

        def __post_init__(self, seed, extra):
            seen.append((seed, extra, self.value))

    codec = Codec()
    codec.register("Item", Item)
    wire = codec.dumps(Item())
    assert b"seed" not in wire and b"extra" not in wire
    assert codec.loads(wire) == Item()
    assert seen == [(4, 7, 3)] * 3


@pytest.mark.parametrize(
    "result", [None, 1, dt.timedelta(days=1), RuntimeError("offset")]
)
def test_offset_none_bad_type_range_and_exception(result):
    class Zone(dt.tzinfo):
        def utcoffset(self, value):
            if isinstance(result, Exception):
                raise result
            return result

    value = dt.datetime(2024, 1, 1, tzinfo=Zone())
    if result is None:
        assert hessian2.loads(hessian2.dumps(value)) == value.replace(tzinfo=None)
    else:
        error = (
            type(result) if isinstance(result, Exception) else (TypeError, ValueError)
        )
        with pytest.raises(error):
            hessian2.dumps(value)


@pytest.mark.parametrize(
    "value",
    [
        dt.datetime(1, 1, 1, tzinfo=dt.UTC),
        dt.datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=dt.UTC),
        dt.datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=dt.UTC),
        dt.datetime(2024, 11, 3, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=0),
        dt.datetime(2024, 11, 3, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1),
        dt.datetime(2024, 3, 10, 2, 30, tzinfo=ZoneInfo("America/New_York")),
        dt.datetime(2024, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=-5, seconds=-3))),
    ],
    ids=repr,
)
def test_time_conversion_matches_datetime_arithmetic(value):
    expected = value.astimezone(dt.UTC).replace(
        microsecond=value.microsecond // 1000 * 1000
    )
    assert hessian2.loads(hessian2.dumps(value)) == expected


@pytest.mark.parametrize(
    "millis", [-(2**63), 2**63 - 1, -62135596800001, 253402300800000]
)
def test_wire_dates_outside_python_year_range(millis):
    with pytest.raises(DecodeError, match="range"):
        hessian2.loads(b"J" + millis.to_bytes(8, "big", signed=True))


def test_spare_refs_reentry_exception_cleanup_and_threads():
    codec = Codec()
    barrier = Barrier(4)

    class Value:
        pass

    def encode(value):
        shared = []
        a, b = hessian2.loads(hessian2.dumps([shared, shared]))
        assert a is b
        if value.fail:
            raise ValueError("callback")
        return {"value": [shared, shared]}

    codec.register("Value", Converter(Value, encode, lambda fields: fields["value"]))

    def work(worker):
        barrier.wait()
        for _ in range(40):
            value = Value()
            value.fail = True
            with pytest.raises(ValueError, match="callback"):
                codec.dumps([[], value])
            value.fail = False
            shared = []
            a, b, first, second = codec.loads(
                codec.dumps([value, value, shared, shared])
            )
            assert a is b and a[0] is a[1] and first is second
            assert a[0] is not first
            assert codec.loads(codec.dumps([worker])) == [worker]

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(work, range(4)))


def test_builtin_internal_slots_do_not_shift_later_references():
    for value in [2**100, dt.datetime(2024, 1, 1), dt.timedelta(microseconds=-1)]:
        shared = []
        a, first, second, b = hessian2.loads(
            hessian2.dumps([value, shared, shared, value])
        )
        assert a is b and first is second
        assert a == value


def test_plain_class_registration_during_base_changes():
    class Left:
        pass

    class Right:
        pass

    @dataclasses.dataclass
    class Item(Left):
        value: int

    barrier = Barrier(2)

    def mutate():
        barrier.wait()
        for index in range(1000):
            Item.__bases__ = (Left if index % 2 else Right,)

    def register():
        barrier.wait()
        codec = Codec()
        wire = hessian2.dumps(HessianObject("Item", {"value": 3}))
        for _ in range(1000):
            codec.register("Item", Item)
            assert codec.loads(wire).value == 3

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(mutate), pool.submit(register)]
        for future in futures:
            future.result(timeout=30)
