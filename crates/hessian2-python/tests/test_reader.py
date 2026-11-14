"""Public contracts for reader."""

from __future__ import annotations

import dataclasses
import re
import typing

import hessian2
import pytest
from assertions import same
from hessian2 import Codec, DecodeError


@hessian2.java_class("com.example.Address")
@dataclasses.dataclass
class Address:
    city: str


@hessian2.java_class("com.example.Pet")
@dataclasses.dataclass
class Pet:
    name: str


def test_a_reader_decodes_one_typed_value_per_load():
    data = hessian2.dumps_many(["find", ["A"], ["B"]])
    reader = hessian2.reader(memoryview(data))

    decoded = (
        reader.load(type=str),
        reader.load(type=list[str]),
        reader.load(type=tuple[str, ...]),
    )

    assert same(decoded, ("find", ["A"], ("B",)))


@pytest.mark.parametrize(
    ("count", "offset"),
    [(0, 0), (1, 5), (2, 8), (3, 11)],
    ids=["start", "string", "list", "end"],
)
def test_reader_tell_points_to_the_next_value(count, offset):
    reader = hessian2.reader(hessian2.dumps_many(["find", ["A"], ["B"]]))
    for _ in range(count):
        reader.load()

    assert reader.tell() == offset


def test_reader_references_keep_identity_across_load_calls():
    shared = ["A"]
    reader = hessian2.reader(hessian2.dumps_many([shared, ["B"], shared]))
    first = reader.load(type=list[str])
    reader.load(type=tuple[str, ...])

    assert reader.load() is first


def test_a_reader_raises_eof_after_the_last_value():
    reader = hessian2.reader(hessian2.dumps(None))
    reader.load()

    with pytest.raises(EOFError):
        reader.load()


def test_a_reader_load_reports_an_annotation_mismatch():
    reader = hessian2.reader(hessian2.dumps_many([1, "x", 2]))
    reader.load(type=int)

    with pytest.raises(DecodeError, match=re.escape("expected int, got str")):
        reader.load(type=int)


def test_a_failed_reader_keeps_the_last_successful_offset():
    reader = hessian2.reader(hessian2.dumps_many([1, "x", 2]))
    reader.load(type=int)

    with pytest.raises(DecodeError):
        reader.load(type=int)

    assert reader.tell() == 1


def test_a_reader_cannot_continue_after_a_decode_failure():
    reader = hessian2.reader(hessian2.dumps_many([1, "x", 2]))
    reader.load(type=int)

    with pytest.raises(DecodeError):
        reader.load(type=int)

    with pytest.raises(DecodeError, match=re.escape("cannot continue")):
        reader.load()


def test_a_reader_keeps_its_original_registration_snapshot():
    codec = Codec()
    reader = codec.reader(hessian2.dumps(Pet("rex")))
    codec.register(Pet)

    with pytest.raises(DecodeError, match=re.escape("not registered")):
        reader.load()


def test_a_new_reader_sees_the_latest_registrations():
    codec = Codec()
    codec.reader(hessian2.dumps(Pet("rex")))
    codec.register(Pet)

    assert same(codec.reader(hessian2.dumps(Pet("rex"))).load(), Pet("rex"))


def test_a_reader_refuses_loads_from_the_code_it_runs() -> None:
    reader = hessian2.reader(hessian2.dumps_many([1, 2]))
    refused = []

    class Sneaky:
        armed = False

        def __hash__(self) -> int:
            if Sneaky.armed:
                try:
                    reader.load()
                except RuntimeError as error:
                    refused.append(error)
            return 0

    # typing hashes the metadata when the annotation is made; arm it after.
    annotation = typing.Annotated[int, Sneaky()]
    Sneaky.armed = True
    # Looking the annotation up hashes it, inside the outer load.

    assert same(reader.load(type=annotation), 1)
    assert refused and "already decoding" in str(refused[0])
    assert same(reader.load(type=int), 2)


@hessian2.java_class("com.example.Order")
@dataclasses.dataclass
class Order:
    order_id: int
    address: Address
    note: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.note = self.note or "checked"


def test_reader_annotations_can_depend_on_earlier_values():
    body = hessian2.dumps_many(
        ["create", ["com.example.Order"], [{"orderId": 1, "address": {"city": "sh"}}]]
    )
    reader = hessian2.reader(body)
    signatures = {("create", ("com.example.Order",)): tuple[Order]}
    method = reader.load(type=str)
    names = reader.load(type=tuple[str, ...])

    arguments = reader.load(type=signatures[method, names])

    assert arguments == (Order(1, Address("sh")),)


def test_reader_reference_validation_stays_bounded_across_load_calls():
    checks = []
    item_type = typing.TypeVar("item_type")

    @hessian2.java_class("reader.CheckedBox")
    @dataclasses.dataclass
    class CheckedBox(typing.Generic[item_type]):
        value: item_type

        def __getattribute__(self, name):
            if name == "value":
                checks.append(name)
            return object.__getattribute__(self, name)

    shared = CheckedBox(1)
    data = hessian2.dumps_many([shared] * 20)
    checks.clear()
    reader = hessian2.reader(data)

    values = [reader.load(type=CheckedBox[int]) for _ in range(20)]

    assert values[0] is values[-1]
    # A validation complexity bound, not a benchmark: the public getter counts
    # field walks, independently of CPU speed and the shared value's size.
    assert len(checks) == 1


def test_reader_checks_a_back_reference_against_a_later_loads_annotation():
    shared = [1]
    reader = hessian2.reader(hessian2.dumps_many([shared] * 3))
    reader.load(type=list[int])
    reader.load(type=list[int])

    with pytest.raises(DecodeError):
        reader.load(type=list[str])
