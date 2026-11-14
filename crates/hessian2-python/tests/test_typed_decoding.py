"""Public contracts for typed decoding."""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import re
import sys
import typing
from decimal import Decimal
from typing import Any, Generic, NamedTuple, Protocol, TypedDict, TypeVar

import attrs
import hessian2
import pytest
from assertions import same
from hessian2 import Codec, Converter, DecodeError, HessianObject

T = TypeVar("T")


@hessian2.java_class("com.example.Address")
@dataclasses.dataclass
class Address:
    city: str


@hessian2.java_class("com.example.User")
@dataclasses.dataclass
class User:
    name: str
    tags: tuple[str, ...] = ()
    address: Address | None = None


@hessian2.java_class("com.example.Cat")
@dataclasses.dataclass
class Cat:
    lives: int


@hessian2.java_class("com.example.Dog")
@dataclasses.dataclass
class Dog:
    good: bool


@hessian2.java_class("com.example.Point")
class Point(NamedTuple):
    x: int
    y: float


@hessian2.java_class("com.example.Failure")
@dataclasses.dataclass
class Failure(Exception):
    detail_message: str
    code: int = 0

    def __post_init__(self) -> None:
        super().__init__(self.detail_message)


class Counts(TypedDict):
    ids: tuple[int, ...]


@hessian2.java_class("com.example.Animal")
@dataclasses.dataclass
class Animal:
    name: str


@hessian2.java_class("com.example.Puppy")
@dataclasses.dataclass
class Puppy(Animal):
    age: int = 0


@hessian2.java_class("com.example.Owner")
@dataclasses.dataclass
class Owner:
    pet: Animal


@hessian2.java_class("java.io.IOException")
@dataclasses.dataclass
class IOFailure(OSError):
    detail_message: str


@pytest.mark.parametrize(
    ("value", "annotation", "expected"),
    [
        (["a"], tuple[str, ...], ("a",)),
        (["a", "a"], set[str], {"a"}),
        ([1], frozenset[int], frozenset({1})),
        ([1, "x"], tuple[int, str], (1, "x")),
        ({"a": [1]}, dict[str, tuple[int, ...]], {"a": (1,)}),
        ({"ids": [1], "other": [2]}, Counts, {"ids": (1,), "other": [2]}),
        ([[1]], list[frozenset[int]], [frozenset({1})]),
        (3, float, 3.0),
        (None, int | None, None),
        (2**70, int, 2**70),
        (Decimal("1.5"), Decimal, Decimal("1.5")),
        (dt.date(2026, 1, 2), dt.date, dt.date(2026, 1, 2)),
        ([1, "a"], list[Any], [1, "a"]),
        ([1, "a"], Any | None, [1, "a"]),
        ("a", typing.LiteralString, "a"),
        (1, typing.Literal["a"], 1),
    ],
    ids=repr,
)
def test_type_shapes_values(value: Any, annotation: Any, expected: Any) -> None:
    decoded = hessian2.loads(hessian2.dumps(value), type=annotation)

    assert same(decoded, expected)


@pytest.mark.parametrize(
    ("value", "annotation", "message"),
    [
        ([1], list[str], "expected str, got int"),
        (True, int, "expected int, got bool"),
        (None, int, "expected int, got None"),
        ({"a": 1}, list[int], "expected list[int], got a map"),
        ([1], dict[str, int], "expected dict, got a list"),
        ([1], tuple[int, str], "expected a tuple of 2 items, got a list of 1"),
        (
            dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
            dt.date,
            "expected date, got datetime",
        ),
        ([[1]], set[Any], "unhashable set item"),
        ({"other": [1]}, Counts, "expected dict, got a map without key 'ids'"),
        (Dog(True), Cat, "expected Cat, got an object of class 'com.example.Dog'"),
    ],
    ids=repr,
)
def test_values_that_do_not_match_raise(
    value: Any, annotation: Any, message: str
) -> None:
    with pytest.raises(DecodeError, match=re.escape(message)):
        hessian2.loads(hessian2.dumps(value), type=annotation)


@pytest.mark.parametrize(
    ("value", "annotation"),
    [(User("ada", ("x",), Address("Paris")), User), (Point(1, 2.0), Point)],
    ids=["dataclass", "named-tuple"],
)
def test_an_annotation_trusts_the_classes_it_names_without_registration(
    value, annotation
):
    decoded = hessian2.loads(hessian2.dumps(value), type=annotation)

    assert type(decoded) is type(value)
    assert decoded == value


def test_a_union_picks_its_member_by_class_name() -> None:
    pets = [Cat(9), Dog(True), None]

    assert same(hessian2.loads(hessian2.dumps(pets), type=list[Cat | Dog | None]), pets)


def test_a_registered_class_cannot_fill_an_unrelated_annotation():
    codec = Codec()
    codec.register(Dog)

    with pytest.raises(DecodeError, match=re.escape("expected Cat")):
        codec.loads(codec.dumps(Dog(True)), type=Cat)


def test_any_positions_take_registered_classes():
    codec = Codec()
    codec.register(Dog)

    assert same(codec.loads(codec.dumps([Dog(True)]), type=list[Any]), [Dog(True)])


def test_any_positions_reject_unregistered_classes():
    codec = Codec()

    with pytest.raises(DecodeError, match=re.escape("not registered")):
        codec.loads(hessian2.dumps([Cat(1)]), type=list[Any])


def test_a_base_class_position_rejects_an_unregistered_subclass():
    codec = Codec()
    codec.register(Owner)

    with pytest.raises(DecodeError, match=re.escape("expected Animal")):
        codec.loads(codec.dumps(Owner(Puppy("rex", 2))))


@pytest.mark.parametrize(
    ("value", "annotation"),
    [(Owner(Puppy("rex", 2)), Owner), ([Puppy("a")], list[Animal])],
    ids=["field", "list-item"],
)
def test_a_registered_subclass_fills_a_base_class_position(value, annotation):
    codec = Codec()
    codec.register(Puppy)

    assert same(codec.loads(hessian2.dumps(value), type=annotation), value)


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [(int | float, 1), (float | int, 1), (float | str, 1.0)],
    ids=["int-first", "int-last", "converted-float"],
)
def test_union_members_prefer_the_incoming_python_type(annotation, expected):
    decoded = hessian2.loads(hessian2.dumps(1), type=annotation)

    assert same(decoded, expected)


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (list[int] | tuple[str, ...], "decode a Hessian list"),
        (dict[str, int] | Counts, "decode a Hessian map"),
        ("User", "string annotation"),
        (list["User"], "string annotation"),
    ],
    ids=repr,
)
def test_annotations_that_cannot_decode_raise(annotation: Any, message: str) -> None:
    with pytest.raises(TypeError, match=message):
        hessian2.loads(hessian2.dumps([]), type=annotation)


def test_none_in_types_leaves_a_position_untyped():
    data = hessian2.dumps_many([1, None])

    assert same(hessian2.loads_many(data, types=[None, type(None)]), [1, None])


def test_none_type_in_types_requires_null():
    data = hessian2.dumps_many([1, None])

    with pytest.raises(DecodeError, match=re.escape("expected None, got int")):
        hessian2.loads_many(data, types=[type(None), None])


def test_hessian_object_annotation_keeps_any_object_raw() -> None:
    codec = Codec()
    codec.register(Dog)

    raw = codec.loads(codec.dumps(Dog(True)), type=HessianObject)

    assert raw == HessianObject("com.example.Dog", {"good": True})


def test_registration_decodes_field_types_without_a_top_level_type():
    codec = Codec()
    codec.register(User)
    user = User("ada", ("x", "y"))

    decoded = codec.loads(codec.dumps(user))

    assert decoded == user
    assert type(decoded.tags) is tuple


def test_registration_rejects_a_field_of_the_wrong_type():
    codec = Codec()
    codec.register(User)
    wrong = hessian2.dumps(HessianObject("com.example.User", {"name": 1}))

    with pytest.raises(DecodeError, match=re.escape("expected str, got int")):
        codec.loads(wrong)


def test_typed_object_back_references_keep_identity():
    shared = Address("Paris")

    pair = hessian2.loads(hessian2.dumps([shared, shared]), type=list[Address])

    assert pair[0] is pair[1]


def test_a_back_reference_fits_the_same_container_annotation():
    shared = [1]

    pair = hessian2.loads(
        hessian2.dumps([shared, shared]), type=tuple[list[int], list[int]]
    )

    assert pair[0] is pair[1]
    assert same(pair, ([1], [1]))


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (tuple[list[int], tuple[int, ...]], "expected tuple[int, ...], got a back"),
        (tuple[list[int], list[str]], "expected list[str]"),
    ],
    ids=["container-type", "item-type"],
)
def test_back_references_must_fit_each_positions_annotation(annotation, message):
    shared = [1]
    data = hessian2.dumps([shared, shared])

    with pytest.raises(DecodeError, match=re.escape(message)):
        hessian2.loads(data, type=annotation)


@pytest.mark.parametrize(
    ("value", "annotation", "args"),
    [(Failure("boom", 7), Failure, ("boom",)), (IOFailure("disk"), IOFailure, ())],
    ids=["exception", "oserror"],
)
def test_exception_subclasses_restore_fields_and_post_init_args(
    value, annotation, args
):
    decoded = hessian2.loads(hessian2.dumps(value), type=annotation)

    assert type(decoded) is annotation
    assert decoded == value
    assert decoded.args == args


def test_loads_many_applies_one_annotation_per_value():
    data = hessian2.dumps_many([1, ["a"]])

    assert same(hessian2.loads_many(data, types=[int, tuple[str, ...]]), [1, ("a",)])


@pytest.mark.parametrize(
    ("annotations", "message"),
    [([int], "trailing data"), ([int, list[str], int], "unexpected end of input")],
    ids=["too-few", "too-many"],
)
def test_loads_many_requires_exactly_the_annotated_value_count(annotations, message):
    data = hessian2.dumps_many([1, ["a"]])

    with pytest.raises(DecodeError, match=re.escape(message)):
        hessian2.loads_many(data, types=annotations)


@hessian2.java_class("com.example.Result")
@dataclasses.dataclass
class Result(Generic[T]):
    code: int
    data: T
    items: list[T] = dataclasses.field(default_factory=list)


def test_generic_type_arguments_trust_the_class_in_the_argument():
    result = Result(0, Address("Paris"), [Address("Rome")])

    assert same(hessian2.loads(hessian2.dumps(result), type=Result[Address]), result)


def test_generic_type_arguments_reject_a_mismatched_field():
    data = hessian2.dumps(Result(0, 5))

    with pytest.raises(
        DecodeError, match=re.escape("expected str, got int - at `$.data`")
    ):
        hessian2.loads(data, type=Result[str])


def test_unsubscripted_generic_parameters_accept_plain_values():
    assert same(hessian2.loads(hessian2.dumps(Result(0, 5)), type=Result), Result(0, 5))


def test_unsubscripted_generic_parameters_require_registration_for_objects():
    with pytest.raises(DecodeError, match=re.escape("not registered")):
        hessian2.loads(hessian2.dumps(Result(0, Address("Paris"))), type=Result)


class Named(Protocol):
    name: str


@hessian2.java_class("com.example.Pet")
@dataclasses.dataclass
class Pet:
    name: str


@hessian2.java_class("com.example.Shelter")
@dataclasses.dataclass
class Shelter:
    resident: Named


def test_protocol_positions_take_registered_classes():
    codec = Codec()
    codec.register(Pet)

    assert same(
        codec.loads(hessian2.dumps(Shelter(Pet("rex"))), type=Shelter),
        Shelter(Pet("rex")),
    )


def test_protocol_positions_reject_unregistered_classes():
    with pytest.raises(DecodeError, match=re.escape("not registered")):
        hessian2.loads(hessian2.dumps(Shelter(Pet("rex"))), type=Shelter)


@pytest.mark.parametrize(
    ("value", "annotation", "path"),
    [
        (
            HessianObject(
                "com.example.User",
                {
                    "name": "ada",
                    "address": HessianObject("com.example.Address", {"city": 1}),
                },
            ),
            User,
            "$.address.city",
        ),
        ({"k": ["x"]}, dict[str, list[int]], "$['k'][0]"),
    ],
    ids=["field", "map-list-item"],
)
def test_typed_errors_identify_the_path_inside_a_value(value, annotation, path):
    data = hessian2.dumps(value)

    with pytest.raises(DecodeError, match=re.escape(path)):
        hessian2.loads(data, type=annotation)


def test_typed_stream_errors_identify_the_value_index():
    data = hessian2.dumps_many([1, "x"])

    with pytest.raises(DecodeError, match=re.escape("$[1]")):
        hessian2.loads_many(data, types=[int, int])


@pytest.mark.parametrize("position", ["ignored-field", "jdk-field"])
def test_plain_data_positions_build_no_registered_class(position):
    ran = []

    @hessian2.java_class("com.example.PlainHook")
    @dataclasses.dataclass
    class PlainHook:
        def __post_init__(self):
            ran.append(self)

    codec = Codec()
    codec.register(PlainHook)
    hook = HessianObject("com.example.PlainHook", {})
    data = {
        "ignored-field": HessianObject(
            "com.example.Address", {"city": "a", "extra": hook}
        ),
        "jdk-field": HessianObject(
            "java.math.BigDecimal", {"value": "1.5", "extra": hook}
        ),
    }[position]
    annotation = {"ignored-field": Address, "jdk-field": Decimal}[position]

    codec.loads(hessian2.dumps(data), type=annotation)

    assert ran == []


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (HessianObject, HessianObject("java.math.BigDecimal", {"value": "1.5"})),
        (Decimal | HessianObject, Decimal("1.5")),
    ],
    ids=["explicit-raw", "builtin-union-member"],
)
def test_builtin_objects_follow_the_annotated_representation(annotation, expected):
    data = hessian2.dumps(Decimal("1.5"))

    assert same(hessian2.loads(data, type=annotation), expected)


def test_a_converter_result_must_fit_its_annotation(wrong_converter):
    codec, data, annotation = wrong_converter

    with pytest.raises(
        DecodeError, match=re.escape("Money, got str from the converter")
    ):
        codec.loads(data, type=annotation)


def test_an_untyped_position_accepts_the_converter_result(wrong_converter):
    codec, data, _ = wrong_converter

    assert same(codec.loads(data), "wrong")


def test_unions_refuse_classes_declared_with_one_name() -> None:
    @hessian2.java_class("com.example.Address")
    @dataclasses.dataclass
    class Twin:
        city: str

    with pytest.raises(TypeError, match=re.escape("one Java class name")):
        hessian2.loads(hessian2.dumps(None), type=Address | Twin)


@hessian2.java_class("com.example.Box")
@dataclasses.dataclass
class Box(Generic[T]):
    value: T


@hessian2.java_class("com.example.IntBox")
@dataclasses.dataclass
class IntBox(Box[int]):
    pass


def test_a_generic_back_reference_fits_its_original_type_arguments():
    shared = Box(1)

    first, second = hessian2.loads(
        hessian2.dumps([shared, shared]), type=tuple[Box[int], Box[int]]
    )

    assert first is second
    assert first == Box(1)


def test_a_generic_back_reference_rejects_other_type_arguments():
    shared = Box(1)

    with pytest.raises(DecodeError, match=re.escape("$[1]")):
        hessian2.loads(hessian2.dumps([shared, shared]), type=tuple[Box[int], Box[str]])


def test_a_concrete_subclass_uses_its_generic_bases_arguments():
    with pytest.raises(
        DecodeError, match=re.escape("expected int, got str - at `$.value`")
    ):
        hessian2.loads(hessian2.dumps(IntBox("bad")), type=IntBox)


def test_a_registered_subclass_keeps_the_positions_generic_arguments():
    codec = Codec()
    codec.register(IntBox)

    with pytest.raises(
        DecodeError, match=re.escape("expected str, got int - at `$.value`")
    ):
        codec.loads(hessian2.dumps(IntBox(1)), type=Box[str])


class Partial(TypedDict, total=False):
    must: typing.Required[int]
    may: int


class Whole(TypedDict):
    must: int
    may: typing.NotRequired[int]


@pytest.mark.parametrize("annotation", [Partial, Whole])
def test_typed_dict_qualifiers_allow_missing_optional_keys(annotation):
    assert same(
        hessian2.loads(hessian2.dumps({"must": 1}), type=annotation), {"must": 1}
    )


@pytest.mark.parametrize("annotation", [Partial, Whole])
def test_typed_dict_qualifiers_require_keys_even_with_postponed_annotations(annotation):
    with pytest.raises(DecodeError, match=re.escape("without key 'must'")):
        hessian2.loads(hessian2.dumps({"may": 1}), type=annotation)


def test_exception_groups_refuse_to_decode() -> None:
    @hessian2.java_class("com.example.Group")
    @dataclasses.dataclass
    class Group(ExceptionGroup):
        detail_message: str = ""

    data = hessian2.dumps(HessianObject("com.example.Group", {"detailMessage": "x"}))

    with pytest.raises(TypeError, match=re.escape("exception group cannot exist")):
        hessian2.loads(data, type=Group)


class Page(TypedDict, Generic[T]):
    items: list[T]


def test_generic_typed_dicts_apply_their_type_arguments():
    assert same(
        hessian2.loads(hessian2.dumps({"items": ["x"]}), type=Page[str]),
        {"items": ["x"]},
    )


def test_generic_typed_dicts_reject_mismatched_items():
    with pytest.raises(DecodeError, match=re.escape("$['items'][0]")):
        hessian2.loads(hessian2.dumps({"items": ["x"]}), type=Page[int])


def test_generic_converter_results_keep_fitting_type_arguments(generic_converter):
    codec, data, box = generic_converter

    assert same(codec.loads(data, type=box[str]), box("x"))


def test_generic_converter_results_reject_mismatched_type_arguments(generic_converter):
    codec, data, box = generic_converter

    with pytest.raises(DecodeError, match=re.escape("from the converter")):
        codec.loads(data, type=box[int])


@hessian2.java_class("com.example.Node")
@dataclasses.dataclass
class Node(Generic[T]):
    value: T
    next: Node[T] | None = None


class Tree(TypedDict):
    value: int
    children: list[Tree]
    parent: typing.NotRequired[Tree | None]


@hessian2.java_class("com.example.Hook")
@dataclasses.dataclass
class Hook:
    def __post_init__(self) -> None:
        raise AssertionError("a registered class the annotation does not name")


def node(value: Any, after: Any = None) -> HessianObject:
    return HessianObject("com.example.Node", {"value": value, "next": after})


@pytest.mark.parametrize(
    ("value", "annotation", "expected"),
    [
        (node(1, node(2, node(3))), Node[int], Node(1, Node(2, Node(3)))),
        (
            {"value": 1, "children": [{"value": 2, "children": [], "parent": None}]},
            Tree,
            {"value": 1, "children": [{"value": 2, "children": [], "parent": None}]},
        ),
    ],
    ids=["recursive-generic", "recursive-typed-dict"],
)
def test_recursive_annotations_type_every_level(value, annotation, expected):
    assert same(hessian2.loads(hessian2.dumps(value), type=annotation), expected)


@pytest.mark.parametrize(
    ("value", "annotation", "path"),
    [
        (
            node(1, node(2, node(HessianObject("com.example.Hook", {})))),
            Node[int],
            "$.next.next.value",
        ),
        (
            {
                "value": 1,
                "children": [
                    {
                        "value": 2,
                        "children": [],
                        "parent": {
                            "value": HessianObject("com.example.Hook", {}),
                            "children": [],
                        },
                    }
                ],
            },
            Tree,
            "$['children'][0]['parent']['value']",
        ),
    ],
    ids=["recursive-generic", "recursive-typed-dict"],
)
def test_recursive_annotations_reject_nested_objects_before_running_their_hooks(
    value, annotation, path
):
    codec = Codec()
    codec.register(Hook)

    with pytest.raises(DecodeError, match=re.escape(path)):
        codec.loads(hessian2.dumps(value), type=annotation)


def test_recursive_generic_back_references_keep_identity_when_contents_fit():
    codec = Codec()
    codec.register(Node)
    shared = node(5, node(6))
    data = hessian2.dumps((shared, node(1, shared)))

    first, second = codec.loads(data, type=tuple[Any, Node[int]])

    assert second.next is first


def test_recursive_generic_back_references_reject_mismatched_nested_contents():
    codec = Codec()
    codec.register(Node)
    shared = node(5, node("x"))
    data = hessian2.dumps((shared, node(1, shared)))

    with pytest.raises(DecodeError, match=re.escape("back-reference")):
        codec.loads(data, type=tuple[Any, Node[int]])


@hessian2.java_class("com.example.TreeNode")
@dataclasses.dataclass(eq=False)
class TreeNode(Generic[T]):
    value: T
    parent: TreeNode[T] | None
    children: list[TreeNode[T]]


@hessian2.java_class("com.example.Loop")
@dataclasses.dataclass(eq=False)
class Loop(Generic[T]):
    # Before value, so the back-reference arrives before value is read.
    back: Loop[str] | None
    value: T


@pytest.mark.parametrize(
    "annotation", [TreeNode[int], TreeNode], ids=["generic", "unsubscripted"]
)
def test_a_child_can_reference_its_generic_parent_before_it_is_filled(annotation):
    root = TreeNode(1, None, [])
    root.children.append(TreeNode(2, root, []))

    decoded = hessian2.loads(hessian2.dumps(root), type=annotation)

    assert decoded.children[0].parent is decoded


def test_a_deferred_generic_self_reference_keeps_identity_when_fields_fit():
    value = Loop(None, "x")
    value.back = value

    decoded = hessian2.loads(hessian2.dumps(value), type=Loop[str])

    assert decoded.back is decoded
    assert same(decoded.value, "x")


def test_a_deferred_generic_self_reference_rejects_fields_that_do_not_fit():
    value = Loop(None, 7)
    value.back = value

    with pytest.raises(
        DecodeError, match=re.escape("back-reference to this Loop expects")
    ):
        hessian2.loads(hessian2.dumps(value), type=Loop[int])


@hessian2.java_class("com.example.Color")
class Color(enum.Enum):
    RED = 1
    GREEN = 2


class Level(enum.StrEnum):
    LOW = "low"


class Rank(enum.IntEnum):
    FIRST = 1


class Code(enum.IntEnum):
    DENIED = 2


@pytest.mark.parametrize(
    ("value", "member"),
    [({"name": "RED"}, Color.RED), ({"ordinal": 1, "name": "GREEN"}, Color.GREEN)],
    ids=["name", "extra-fields"],
)
def test_java_enum_maps_select_the_name_member(value, member):
    assert hessian2.loads(hessian2.dumps(value), type=Color) is member


def test_a_map_decoded_as_an_enum_keeps_its_reference_slot():
    shared = {"name": "RED"}

    decoded = hessian2.loads(hessian2.dumps([shared, shared]), type=list[Color])

    assert decoded[0] is decoded[1] is Color.RED


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"name": "BLUE"}, "has no member 'BLUE'"),
        ({"ordinal": 0}, "needs a string name"),
        ({"name": 1}, "needs a string name"),
        ("RED", "expected Color, got str"),
    ],
    ids=["unknown-member", "missing-name", "non-string-name", "bare-name"],
)
def test_java_enum_decoding_rejects_invalid_member_forms(value, message):
    with pytest.raises(DecodeError, match=re.escape(message)):
        hessian2.loads(hessian2.dumps(value), type=Color)


@pytest.mark.parametrize("member", [Level.LOW, Rank.FIRST])
def test_undeclared_value_enums_decode_from_their_encoded_value(member):
    assert hessian2.loads(hessian2.dumps(member), type=type(member)) is member


@pytest.mark.parametrize(
    "annotation", [Level | str, str | Level], ids=["enum-first", "enum-last"]
)
def test_a_union_keeps_a_scalar_that_already_matches_a_member(annotation):
    assert same(hessian2.loads(hessian2.dumps("low"), type=annotation), "low")


@pytest.mark.parametrize(
    ("annotation", "value", "message"),
    [
        (Level, "high", "Level has no member with value 'high'"),
        (Rank | Code, 3, "Rank has no member with value 3"),
        (Rank, "1", "expected Rank, got str"),
    ],
    ids=["unknown-str-value", "unknown-union-value", "wrong-scalar-type"],
)
def test_value_enum_decoding_rejects_values_no_member_accepts(
    annotation, value, message
):
    with pytest.raises(DecodeError, match=re.escape(message)):
        hessian2.loads(hessian2.dumps(value), type=annotation)


@pytest.mark.parametrize(
    "annotation", [Rank | Code, Code | Rank], ids=["fall-through", "first-member"]
)
def test_value_enum_unions_try_another_enum_when_the_first_has_no_member(annotation):
    assert hessian2.loads(hessian2.dumps(2), type=annotation) is Code.DENIED


def test_a_registered_value_enum_decodes_as_a_java_enum():
    codec = Codec()
    codec.register(Level, name="com.example.Level")

    assert codec.loads(codec.dumps(Level.LOW), type=Level) is Level.LOW


def test_a_registered_value_enum_rejects_a_bare_value():
    codec = Codec()
    codec.register(Level, name="com.example.Level")

    with pytest.raises(DecodeError, match=re.escape("expected Level, got str")):
        codec.loads(codec.dumps("low"), type=Level)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
def test_recursive_aliases_decode_values_at_each_level():
    ns = {}
    exec("type Json = dict[str, Json] | list[Json] | int", ns)
    value = {"a": [1, {"b": []}]}

    assert same(hessian2.loads(hessian2.dumps(value), type=ns["Json"]), value)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
def test_recursive_aliases_reject_nested_objects_outside_the_alias():
    ns = {}
    exec("type Json = dict[str, Json] | list[Json] | int", ns)
    codec = Codec()
    codec.register(Hook)
    value = {"a": [{"b": HessianObject("com.example.Hook", {})}]}

    with pytest.raises(DecodeError, match=re.escape("$['a'][0]['b']")):
        codec.loads(hessian2.dumps(value), type=ns["Json"])


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
def test_an_alias_cannot_be_its_own_union_member():
    ns = {}
    exec("type Loop = int | Loop", ns)

    with pytest.raises(TypeError, match=re.escape("contains itself")):
        hessian2.loads(hessian2.dumps(1), type=ns["Loop"])


def test_undeclared_typed_dict_values_keep_objects_raw():
    codec = Codec()
    codec.register(Hook)
    hook = HessianObject("com.example.Hook", {})

    decoded = codec.loads(hessian2.dumps({"ids": [1], "extra": [hook]}), type=Counts)

    assert same(decoded, {"ids": (1,), "extra": [hook]})


def test_undeclared_typed_dict_keys_build_no_registered_class():
    @hessian2.java_class("com.example.Hook")
    @dataclasses.dataclass(frozen=True)
    class HookKey:
        pass

    codec = Codec()
    codec.register(Hook)

    with pytest.raises(
        DecodeError, match=re.escape("HessianObject needs an annotation")
    ):
        codec.loads(hessian2.dumps({"ids": [1], HookKey(): 1}), type=Counts)


class Chain(TypedDict, Generic[T]):
    value: T
    next: typing.NotRequired[Chain[T]]


@hessian2.java_class("com.example.Leaf")
@dataclasses.dataclass
class Leaf:
    value: int

    def __post_init__(self) -> None:
        raise AssertionError("the module's Leaf, not the one in scope")


def test_generic_typed_dict_recursion_applies_type_arguments_through_qualifiers():
    value = {"value": 1, "next": {"value": 2, "next": {"value": 3}}}

    assert same(hessian2.loads(hessian2.dumps(value), type=Chain[int]), value)


def test_generic_typed_dict_recursion_rejects_a_mismatched_qualified_field():
    value = {"value": 1, "next": {"value": 2, "next": {"value": "x"}}}

    with pytest.raises(DecodeError, match=re.escape("$['next']['next']['value']")):
        hessian2.loads(hessian2.dumps(value), type=Chain[int])


def test_type_references_resolve_in_the_scope_they_were_written() -> None:
    @hessian2.java_class("com.example.LocalLeaf")
    @dataclasses.dataclass
    class Leaf:
        value: int

    class Branch(TypedDict):
        leaf: Leaf
        next: typing.NotRequired[Branch]

    @hessian2.java_class("com.example.Holder")
    @dataclasses.dataclass
    class Holder:
        branch: Branch

    def leaf(name: str) -> HessianObject:
        return HessianObject(name, {"value": 1})

    def holder(inner: str) -> bytes:
        branch = {"leaf": leaf("com.example.LocalLeaf"), "next": {"leaf": leaf(inner)}}
        return hessian2.dumps(HessianObject("com.example.Holder", {"branch": branch}))

    decoded = hessian2.loads(holder("com.example.LocalLeaf"), type=Holder)

    assert decoded.branch["next"]["leaf"] == Leaf(1)
    with pytest.raises(
        DecodeError,
        match=r"<locals>\.Leaf, got an object of class .com\.example\.Leaf.",
    ):
        hessian2.loads(holder("com.example.Leaf"), type=Holder)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
@pytest.mark.parametrize(
    "name", ["Same", "A", "G"], ids=["direct", "mutual", "generic"]
)
def test_aliases_of_themselves_raise(name):
    ns = {}
    exec("type Same = Same\ntype A = B\ntype B = A\ntype G[T] = G[T]", ns)
    annotations = {"Same": ns["Same"], "A": ns["A"], "G": ns["G"][int]}

    with pytest.raises(TypeError, match=re.escape("alias of itself")):
        hessian2.loads(hessian2.dumps(1), type=annotations[name])


@pytest.mark.parametrize(
    ("value", "annotation"),
    [
        (dt.datetime(2026, 1, 1, tzinfo=dt.UTC), hessian2.Date),
        (dt.datetime(2026, 1, 1, tzinfo=dt.UTC), hessian2.Instant),
        (dt.datetime(2026, 1, 1), hessian2.LocalDateTime),
        (dt.datetime(2026, 1, 1), hessian2.Date | hessian2.LocalDateTime),
    ],
    ids=["date", "instant", "local", "union-local"],
)
def test_datetime_annotations_accept_the_matching_awareness(value, annotation):
    data = hessian2.dumps(value)

    assert same(hessian2.loads(data, type=annotation), value)


@pytest.mark.parametrize(
    ("value", "annotation", "message"),
    [
        (
            dt.datetime(2026, 1, 1),
            hessian2.Date,
            "expected aware datetime, got naive datetime",
        ),
        (
            dt.datetime(2026, 1, 1),
            hessian2.Instant,
            "expected aware datetime, got naive datetime",
        ),
        (
            dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            hessian2.LocalDateTime,
            "expected naive datetime, got aware datetime",
        ),
    ],
    ids=["date", "instant", "local"],
)
def test_datetime_annotations_reject_the_other_awareness(value, annotation, message):
    with pytest.raises(DecodeError, match=re.escape(message)):
        hessian2.loads(hessian2.dumps(value), type=annotation)


def test_datetime_back_references_must_fit_the_positions_awareness():
    value = dt.datetime(2026, 1, 1)

    with pytest.raises(DecodeError, match=re.escape("back-reference")):
        hessian2.loads(
            hessian2.dumps([value, value]), type=tuple[dt.datetime, hessian2.Date]
        )


@hessian2.java_class("com.example.Order")
@dataclasses.dataclass
class Order:
    order_id: int
    address: Address
    note: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.note = self.note or "checked"


def test_maps_fill_the_annotated_class_ignoring_peer_class_keys_and_extra_fields():
    codec = Codec()
    codec.register(Hook)
    hook = HessianObject("com.example.Hook", {})
    sent = {
        "class": "com.example.Other",
        "orderId": 1,
        "address": {"city": "sh", "zip": hook},
        "tags": ["a"],
        "extra": [hook],
        7: "not a field",
    }

    decoded = codec.loads(hessian2.dumps(sent), type=Order)

    assert decoded == Order(1, Address("sh"), "checked", ("a",))
    assert type(decoded.address) is Address
    assert type(decoded.tags) is tuple


def test_map_class_construction_works_in_list_items():
    value = [{"orderId": 2, "address": {"city": "x"}}]

    assert same(
        hessian2.loads(hessian2.dumps(value), type=list[Order]),
        [Order(2, Address("x"), "checked")],
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"orderId": 1, "address": {"city": 1}}, "$.address.city"),
        ({"orderId": 1}, "address"),
    ],
    ids=["nested-type", "required-field"],
)
def test_map_class_construction_rejects_invalid_fields(value, message):
    with pytest.raises(DecodeError, match=re.escape(message)):
        hessian2.loads(hessian2.dumps(value), type=Order)


@pytest.mark.parametrize(
    ("value", "annotation", "expected"),
    [
        ({"y": 2.5, "x": 1}, Point, Point(1, 2.5)),
        ({"value": {"city": "sh"}}, Box[Address], Box(Address("sh"))),
    ],
    ids=["named-tuple", "generic"],
)
def test_maps_fill_the_fields_of_named_tuples_and_generics(value, annotation, expected):
    assert same(hessian2.loads(hessian2.dumps(value), type=annotation), expected)


def test_maps_supply_fields_to_the_annotated_converter():
    @dataclasses.dataclass
    class Money:
        cents: int

    codec = Codec()
    codec.register(
        Converter(Money, lambda m: {"c": m.cents}, lambda f: Money(f["c"])),
        name="com.example.Money",
    )

    assert same(codec.loads(hessian2.dumps({"c": 5}), type=Money), Money(5))


def test_maps_cannot_choose_between_multiple_record_classes():
    with pytest.raises(DecodeError, match=re.escape("names no class to choose")):
        hessian2.loads(hessian2.dumps({"lives": 9}), type=Cat | Dog)


@pytest.mark.parametrize(
    "annotation", [Cat | dict[str, int], Any], ids=["dict-union", "any"]
)
def test_maps_stay_dicts_where_the_annotation_allows_a_dict(annotation):
    assert same(
        hessian2.loads(hessian2.dumps({"lives": 9}), type=annotation), {"lives": 9}
    )


def test_maps_constructed_as_classes_keep_reference_identity():
    shared = {"city": "sh"}

    first, second = hessian2.loads(hessian2.dumps([shared, shared]), type=list[Address])

    assert first is second


@dataclasses.dataclass
class _UnnamedDataclass:
    lives: int


@attrs.define
class _UnnamedAttrs:
    lives: int


class _UnnamedTuple(typing.NamedTuple):
    lives: int


@pytest.mark.parametrize(
    "cls",
    [_UnnamedDataclass, _UnnamedAttrs, _UnnamedTuple],
    ids=["dataclass", "attrs", "named-tuple"],
)
def test_a_map_cannot_fill_a_record_without_a_java_name(cls):
    data = hessian2.dumps({"lives": 9})

    with pytest.raises(
        DecodeError, match=re.escape(f"{cls.__qualname__} has no Java class name")
    ):
        hessian2.loads(data, type=cls)


def test_an_error_names_a_function_local_type_that_contains_itself():
    class Tree(TypedDict):
        value: int
        kids: list[Tree] | None

    @hessian2.java_class("com.example.Forest")
    @dataclasses.dataclass
    class Forest:
        tree: Tree

    data = hessian2.dumps(Forest({"value": 1, "kids": "x"}))

    with pytest.raises(DecodeError, match=re.escape(f"expected list[{Tree!r}] | None")):
        hessian2.loads(data, type=Forest)


class _PlainClass:
    pass


def test_a_map_for_a_plain_class_suggests_a_converter():
    data = hessian2.dumps({})

    with pytest.raises(
        DecodeError,
        match=re.escape(
            "_PlainClass has no Java class name: register a Converter for it"
        ),
    ):
        hessian2.loads(data, type=_PlainClass)


@pytest.mark.parametrize(
    "cls", [str, bool, float, bytes, hessian2.Typed], ids=lambda cls: cls.__name__
)
def test_a_map_at_a_native_type_is_a_plain_mismatch(cls):
    data = hessian2.dumps({})

    with pytest.raises(
        DecodeError, match=re.escape(f"expected {cls.__name__}, got a map") + "$"
    ):
        hessian2.loads(data, type=cls)


def test_value_enum_decoding_runs_the_trusted_missing_hook():
    class Mode(enum.StrEnum):
        READ = "read"

        @classmethod
        def _missing_(cls, value):
            return cls.__members__.get(value.upper())

    assert hessian2.loads(hessian2.dumps("Read"), type=Mode) is Mode.READ
