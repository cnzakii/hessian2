"""Public contracts for declarations."""

from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import enum
import gc
import re
import sys
import types
import typing
import weakref
from decimal import Decimal
from typing import Any, NamedTuple, TypedDict
from uuid import UUID

import attrs
import hessian2
import pytest
from assertions import same
from hessian2 import Codec, Converter, DecodeError, EncodeError, HessianObject


@hessian2.java_class("com.example.Address")
@dataclasses.dataclass
class Address:
    city: str
    postal_code: str = ""


@hessian2.java_class("com.example.Customer")
@dataclasses.dataclass
class Customer:
    user_name: str
    home: Address | None = None
    others: list[Address] = dataclasses.field(default_factory=list)
    friends: list[Customer] = dataclasses.field(default_factory=list)


def test_declarations_choose_java_names_and_camel_case_fields_during_encoding():
    value = Customer("ann", Address("Hangzhou", "310000"))

    raw = hessian2.loads(
        hessian2.dumps(value), type=HessianObject, unknown_class="hessian_object"
    )

    assert raw.class_name == "com.example.Customer"
    assert raw.fields["userName"] == "ann"
    assert raw.fields["home"].fields == {"city": "Hangzhou", "postalCode": "310000"}


def test_a_declaration_alone_does_not_trust_a_class_for_untyped_decoding():
    data = hessian2.dumps(Customer("ann"))

    with pytest.raises(DecodeError, match=re.escape("not registered")):
        hessian2.loads(data)


def test_encoding_a_declaration_does_not_register_it_for_decoding():
    codec = Codec()
    data = codec.dumps(Customer("ann"))

    with pytest.raises(DecodeError, match=re.escape("not registered")):
        codec.loads(data)


def test_registering_a_declared_class_trusts_its_annotated_children():
    codec = Codec()
    codec.register(Customer)
    value = Customer("ann", Address("Hangzhou", "310000"))

    assert same(codec.loads(hessian2.dumps(value)), value)


def test_disabling_declaration_field_naming_preserves_python_names():
    @hessian2.java_class("Snake", field_naming=None)
    @dataclasses.dataclass
    class Snake:
        user_name: str

    assert hessian2.dumps(Snake("a")) == b"C\x05Snake\x91\x09user_name\x60\x01a"


@pytest.mark.parametrize(
    ("arguments", "error_type"),
    [
        ({"name": 1}, TypeError),
        ({"name": ""}, ValueError),
        ({"name": "X", "field_naming": "snake"}, ValueError),
    ],
    ids=["non-string-name", "empty-name", "unknown-naming"],
)
def test_declarations_reject_invalid_public_options(arguments, error_type):
    with pytest.raises(error_type):
        hessian2.java_class(**arguments)


def test_java_class_decorates_only_classes():
    with pytest.raises(TypeError):
        hessian2.java_class("X")(object())


def test_subclasses_are_not_declared_by_inheritance() -> None:
    @dataclasses.dataclass
    class Special(Address):
        floor: int = 0

    with pytest.raises(EncodeError, match=re.escape("Special has no Hessian form")):
        hessian2.dumps(Special("x"))
    with pytest.raises(TypeError, match=re.escape("has no @hessian2.java_class")):
        Codec().register(Special)


@dataclasses.dataclass
class _Undeclared:
    x: int


class _Plain:
    pass


class _Ids(list):
    pass


ADVICE = pytest.mark.parametrize(
    ("cls", "advice"),
    [
        (_Undeclared, "declare it with @hessian2.java_class or register it"),
        (_Plain, "register a Converter for it"),
    ],
    ids=["record", "other-class"],
)


@ADVICE
def test_a_value_without_a_form_names_the_fix_for_its_kind(cls, advice):
    value = cls(1) if cls is _Undeclared else cls()

    with pytest.raises(
        EncodeError,
        match=re.escape(f"{cls.__qualname__} has no Hessian form; {advice}"),
    ):
        hessian2.dumps(value)


@ADVICE
def test_java_name_without_a_name_names_the_fix_for_its_kind(cls, advice):
    with pytest.raises(
        TypeError,
        match=re.escape(f"{cls.__qualname__} has no Java class name; {advice}"),
    ):
        hessian2.java_name(cls)


def test_declaration_survives_class_rebuilding_decorators() -> None:
    @attrs.define
    @hessian2.java_class("Inner")
    class Inner:
        x: int

    @hessian2.java_class("Slotted")
    @dataclasses.dataclass(slots=True, frozen=True)
    class Slotted:
        inner: Inner

    codec = Codec()
    codec.register(Slotted)

    assert same(codec.loads(codec.dumps(Slotted(Inner(1)))), Slotted(Inner(1)))


def test_enums_and_named_tuples_can_be_declared() -> None:
    @hessian2.java_class("com.example.Color")
    class Color(enum.Enum):
        RED = 1

    @hessian2.java_class("com.example.Point")
    class Point(NamedTuple):
        x_pos: int
        color: Color

    codec = Codec()
    codec.register(Point)

    data = hessian2.dumps(Point(1, Color.RED))

    assert b"xPos" in data
    assert same(codec.loads(data), Point(1, Color.RED))


def test_registration_trusts_only_the_class_and_its_annotated_fields() -> None:
    @hessian2.java_class("Leaf")
    @dataclasses.dataclass
    class Leaf:
        x: int

    @hessian2.java_class("Keyed")
    @dataclasses.dataclass
    class Keyed:
        x: int

    class Holder(TypedDict):
        leaf: Leaf

    @hessian2.java_class("Root")
    @dataclasses.dataclass
    class Root:
        maybe: Leaf | None
        by_key: dict[str, Keyed]
        holder: Holder
        values: typing.Annotated[list[tuple[Leaf, ...]], "meta"]

    codec = Codec()
    codec.register(Root)
    value = Root(Leaf(1), {"k": Keyed(2)}, {"leaf": Leaf(3)}, [(Leaf(4),)])
    # The fields name Leaf and Keyed, so they decode without registration.

    assert same(codec.loads(codec.dumps(value)), value)
    # Registering Root trusts Root alone: Leaf where any value may be is not.
    with pytest.raises(DecodeError, match=re.escape("not registered")):
        codec.loads(codec.dumps(Leaf(1)))
    with pytest.raises(TypeError, match=re.escape("takes a class or a Converter")):
        codec.register(list[Leaf] | Keyed)
    codec.register(Root)


def test_an_undeclared_class_requires_a_name_at_registration(unnamed_models):
    plain, _, _ = unnamed_models

    with pytest.raises(TypeError, match=re.escape("pass name=")):
        Codec().register(plain, field_naming=None)


def test_an_unnamed_field_class_reports_its_name_where_it_decodes(unnamed_models):
    _, uses, data = unnamed_models

    with pytest.raises(DecodeError, match=re.escape("Plain has no Java class name")):
        hessian2.loads(data, type=uses)


def test_registering_an_unnamed_field_class_allows_typed_decoding(unnamed_models):
    plain, uses, data = unnamed_models
    codec = Codec()
    codec.register(plain, name="com.example.Plain")

    assert same(codec.loads(data, type=uses), uses([plain(1)]))


def test_java_name_reads_an_undeclared_class_from_the_codec_registry(unnamed_models):
    plain, _, _ = unnamed_models
    codec = Codec()
    codec.register(plain, name="com.example.Plain")

    assert codec.java_name(plain) == "com.example.Plain"


def test_java_name_rejects_an_unnamed_unregistered_class(unnamed_models):
    plain, _, _ = unnamed_models

    with pytest.raises(TypeError, match=re.escape("no Java class name")):
        hessian2.java_name(plain)


@pytest.mark.parametrize(
    ("case", "error_type", "message"),
    [
        ("other-name", ValueError, "declared as 'com.example.Address'"),
        ("other-naming", TypeError, "field naming from @java_class"),
        ("annotation", TypeError, "takes a class or a Converter"),
        ("unnamed-converter", TypeError, "name="),
        ("converter-naming", TypeError, "to_fields"),
    ],
)
def test_registration_rejects_options_that_disagree_with_the_declared_mapping(
    case, error_type, message
):
    codec = Codec()
    operations = {
        "other-name": lambda: codec.register(Address, name="Other"),
        "other-naming": lambda: codec.register(Address, field_naming=None),
        "annotation": lambda: codec.register(list[Address], name="X"),
        "unnamed-converter": lambda: codec.register(
            Converter(Address, vars, lambda f: Address(**f))
        ),
        "converter-naming": lambda: codec.register(
            Converter(Address, vars, lambda f: Address(**f)),
            name="A",
            field_naming=None,
        ),
    }

    with pytest.raises(error_type, match=re.escape(message)):
        operations[case]()


def test_registration_accepts_the_name_a_declaration_already_gives():
    codec = Codec()

    codec.register(Address, name="com.example.Address")

    assert same(codec.loads(codec.dumps(Address("a"))), Address("a"))


def test_local_classes_resolve_names_defined_before_them() -> None:
    # This module uses string annotations, as `from __future__` makes them.
    @hessian2.java_class("Early")
    @dataclasses.dataclass
    class Early:
        x: int

    @hessian2.java_class("Late")
    @dataclasses.dataclass
    class Late:
        early: Early
        later: Later | None = None

    @hessian2.java_class("Later")
    @dataclasses.dataclass
    class Later:
        x: int

    @hessian2.java_class("Self")
    @dataclasses.dataclass
    class Self:
        early: Early
        children: list[Self]

    codec = Codec()
    codec.register(Self)

    assert same(codec.loads(codec.dumps(Self(Early(1), []))), Self(Early(1), []))
    with pytest.raises(TypeError, match=re.escape("name 'Later' is not defined")):
        codec.register(Late)
    with pytest.raises(EncodeError, match=re.escape("name 'Later' is not defined")):
        hessian2.dumps(Late(Early(1)))


def test_generic_dataclasses_resolve_their_type_parameters() -> None:
    T = typing.TypeVar("T")

    @hessian2.java_class("Box")
    @dataclasses.dataclass
    class Box(typing.Generic[T]):
        item: T

    codec = Codec()
    codec.register(Box)

    assert same(codec.loads(codec.dumps(Box(1))), Box(1))


@pytest.mark.parametrize(
    ("tp", "expected"),
    [
        (Customer, "com.example.Customer"),
        (Customer | None, "com.example.Customer"),
        (typing.Annotated[Customer, "meta"], "com.example.Customer"),
        (str, "java.lang.String"),
        (bytes, "[B"),
        (Decimal, "java.math.BigDecimal"),
        (UUID, "java.util.UUID"),
        (dt.date, "java.time.LocalDate"),
        (dt.time, "java.time.LocalTime"),
        (dt.timedelta, "java.time.Duration"),
        (hessian2.BigInteger, "java.math.BigInteger"),
        (hessian2.Instant, "java.time.Instant"),
        (hessian2.Date, "java.util.Date"),
        (hessian2.LocalDateTime | None, "java.time.LocalDateTime"),
        (hessian2.OffsetDateTime, "java.time.OffsetDateTime"),
        (hessian2.ZonedDateTime, "java.time.ZonedDateTime"),
        (hessian2.Long, "long"),
        (hessian2.Long | None, "java.lang.Long"),
        (tuple[str, ...], "[Ljava.lang.String;"),
        (tuple[Any, ...], "[Ljava.lang.Object;"),
        (tuple[int, ...], "[I"),
        (tuple[int | None, ...], "[Ljava.lang.Integer;"),
        (tuple[hessian2.Long, ...], "[J"),
        (tuple[tuple[bool, ...], ...], "[[Z"),
        (tuple[bytes, ...], "[[B"),
        (tuple[Customer, ...], "[Lcom.example.Customer;"),
        # Java reads a list typed [object as Object[].
        (tuple[list[int], ...], "[Ljava.lang.Object;"),
        (tuple[int | str, ...], "[Ljava.lang.Object;"),
        (tuple[tuple[int, ...] | None, ...], "[[I"),
        (bool, "boolean"),
        (bool | None, "java.lang.Boolean"),
        (float, "double"),
        (float | None, "java.lang.Double"),
        (int, None),
        (int | None, None),
        (dt.datetime, None),
        (list[Customer], None),
        (dict, None),
        (Customer | Address, None),
        (Any, None),
        (object, None),
        (type(None), None),
        (HessianObject, None),
        (hessian2.Typed, None),
    ],
    ids=repr,
)
def test_java_name_returns_the_class_an_annotation_always_encodes_as(
    tp: Any, expected: str | None
) -> None:
    assert hessian2.java_name(tp) == expected


def test_java_name_uses_the_current_codecs_converter_registration():
    codec = Codec()
    codec.register(
        Converter(Decimal, lambda d: {"v": str(d)}, lambda f: Decimal(f["v"])),
        name="com.example.Money",
        replace=True,
    )

    assert codec.java_name(Decimal) == "com.example.Money"


def test_java_name_registrations_are_per_codec():
    codec = Codec()
    codec.register(
        Converter(Decimal, lambda d: {"v": str(d)}, lambda f: Decimal(f["v"])),
        name="com.example.Money",
        replace=True,
    )

    assert hessian2.java_name(Decimal) == "java.math.BigDecimal"


def test_java_name_rejects_an_unresolved_string_annotation():
    with pytest.raises(TypeError, match=re.escape("not a type")):
        Codec().java_name("Customer")


@pytest.mark.parametrize(
    "kind",
    ["fixed", "empty", "annotated", "nullable", "none"],
    ids=[
        "fixed-tuple",
        "empty-tuple",
        "annotated-tuple",
        "nullable-tuple",
        "none-type",
    ],
)
def test_java_name_uses_converters_for_tuple_and_none_positions(kind):
    codec = Codec()
    codec.register(
        Converter(tuple, lambda v: {"items": list(v)}, lambda f: tuple(f["items"])),
        name="com.example.Pair",
    )
    codec.register(
        Converter(type(None), lambda v: {}, lambda f: None), name="com.example.Nil"
    )
    annotation, expected = {
        "fixed": (tuple[int, str], "com.example.Pair"),
        "empty": (tuple[()], "com.example.Pair"),
        "annotated": (typing.Annotated[tuple[int], "x"], "com.example.Pair"),
        "nullable": (tuple[int, str] | None, "com.example.Pair"),
        "none": (type(None), "com.example.Nil"),
    }[kind]

    assert codec.java_name(annotation) == expected


def test_a_plain_fixed_tuple_has_no_single_java_class_name():
    assert hessian2.java_name(tuple[int, str]) is None


@pytest.mark.parametrize(
    ("operation", "mode", "expected"),
    [
        ("loads", "dict", {"x": 1}),
        (
            "loads_many",
            "hessian_object",
            [HessianObject("com.example.Unknown", {"x": 1})],
        ),
    ],
)
def test_decode_calls_can_override_the_codecs_unknown_class_mode(
    operation, mode, expected
):
    codec = Codec()

    data = hessian2.dumps(HessianObject("com.example.Unknown", {"x": 1}))

    assert same(getattr(codec, operation)(data, unknown_class=mode), expected)
    assert codec.unknown_class == "raise"


def test_decode_calls_reject_invalid_unknown_class_overrides():
    with pytest.raises(ValueError, match=re.escape("unknown_class")):
        Codec().loads(b"N", unknown_class="raw")


_DeclaredValue = typing.TypeVar("_DeclaredValue")


@hessian2.java_class("Box")
@dataclasses.dataclass
class DeclaredBox(typing.Generic[_DeclaredValue]):
    item: _DeclaredValue


def test_generic_arguments_type_objects_nested_in_lists():
    value = [DeclaredBox(Address("Hangzhou"))]

    assert same(
        hessian2.loads(hessian2.dumps(value), type=list[DeclaredBox[Address]]), value
    )


def test_generic_arguments_reject_wrong_fields_inside_list_items():
    with pytest.raises(DecodeError, match=re.escape("$[0].item")):
        hessian2.loads(
            hessian2.dumps([DeclaredBox(1)]), type=list[DeclaredBox[Address]]
        )


@pytest.mark.parametrize(
    "annotation",
    [DeclaredBox[Address], DeclaredBox[Address] | None],
    ids=["generic", "nullable"],
)
def test_a_generic_class_name_does_not_depend_on_its_type_arguments(annotation):
    assert Codec().java_name(annotation) == "Box"


def test_generic_class_arguments_refuse_encoding_tags_they_cannot_apply():
    with pytest.raises(TypeError, match=re.escape("do not apply")):
        hessian2.dumps(DeclaredBox(1), type=DeclaredBox[hessian2.Long])


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
def test_subscripted_type_aliases_substitute_their_arguments() -> None:
    T = typing.TypeVar("T")
    pair = typing.TypeAliasType("Pair", list[T], type_params=(T,))
    same = typing.TypeAliasType("Same", T, type_params=(T,))

    assert hessian2.dumps([1], type=pair[hessian2.Long]) == b"\x79\xe1"
    assert hessian2.dumps(1, type=same[hessian2.Long]) == b"\xe1"


@pytest.mark.parametrize(
    "tp",
    [
        hessian2.Long | hessian2.BigInteger,
        hessian2.Instant | hessian2.ZonedDateTime,
        list[hessian2.Long] | tuple[hessian2.BigInteger, ...],
        dict[str, hessian2.Long] | dict[hessian2.Long, str],
    ],
    ids=repr,
)
def test_unions_reject_tags_that_compete_for_a_value(tp: Any) -> None:
    with pytest.raises(TypeError, match=re.escape("same values")):
        hessian2.dumps(None, type=tp)


def test_encoding_a_declared_class_does_not_keep_the_class_alive():
    @hessian2.java_class("Temp")
    @dataclasses.dataclass
    class Temp:
        count: hessian2.Long

    codec = Codec()
    codec.dumps(Temp(1))
    ref = weakref.ref(Temp)
    del Temp

    gc.collect()

    assert ref() is None


def test_a_name_registered_to_another_class_is_refused() -> None:
    @hessian2.java_class("Taken")
    @dataclasses.dataclass
    class Other:
        pass

    @hessian2.java_class("Holder")
    @dataclasses.dataclass
    class Holder:
        other: Other | None = None

    @dataclasses.dataclass
    class Squatter:
        pass

    codec = Codec()
    codec.register(Squatter, name="Taken")

    with pytest.raises(ValueError, match=re.escape("already registered")):
        codec.register(Other)
    # A field's annotation still decodes the class it declares.
    codec.register(Holder)
    assert same(codec.loads(codec.dumps(Holder(Other()))), Holder(Other()))


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
def test_type_aliases_substitute_parameters_by_identity() -> None:
    A, B, C = typing.TypeVar("A"), typing.TypeVar("B"), typing.TypeVar("C")
    swapped = typing.TypeAliasType("Swapped", tuple[B, A], type_params=(A, B))
    unused = typing.TypeAliasType("Unused", list[A], type_params=(A, C))

    assert hessian2.dumps((1, 2), type=swapped[int, hessian2.Long]) == b"\x7a\xe1\x92"
    assert hessian2.dumps([1], type=unused[hessian2.Long, str]) == b"\x79\xe1"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
def test_recursive_annotations_terminate() -> None:
    ns: dict[str, Any] = {"hessian2": hessian2}
    exec(
        "type Json = dict[str, Json] | list[Json] | int\n"
        "type Tree = dict[str, Tree] | hessian2.Long\n",
        ns,
    )

    assert hessian2.dumps({"x": [1]}, type=ns["Json"]) == hessian2.dumps({"x": [1]})
    assert same(hessian2.loads(hessian2.dumps({"x": [1]}), type=ns["Json"]), {"x": [1]})
    # Tags below the first level would be lost, so they are refused.
    with pytest.raises(TypeError, match=re.escape("contains itself")):
        hessian2.dumps(1, type=ns["Tree"])


def test_recursive_typed_dicts_refuse_tags_they_cannot_apply() -> None:
    class Node(TypedDict):
        value: hessian2.Long
        children: list[Node]

    with pytest.raises(TypeError, match=re.escape("contains itself")):
        hessian2.dumps({"value": 1, "children": []}, type=Node)


def test_replace_needs_a_class() -> None:
    with pytest.raises(TypeError, match=re.escape("takes a class or a Converter")):
        Codec().register(list[Address], replace=True)


@pytest.mark.parametrize(
    "tp",
    [
        list[int] | tuple[hessian2.Long, ...],
        int | hessian2.Long,
        dict[str, int] | dict[str, hessian2.Long],
        _Ids | tuple[hessian2.Long, ...],
        collections.OrderedDict | dict[str, hessian2.Long],
    ],
    ids=repr,
)
def test_unions_reject_tags_an_untagged_member_competes_with(tp: Any) -> None:
    with pytest.raises(TypeError, match=re.escape("same values as another member")):
        hessian2.dumps([1], type=tp)


def test_registration_resolves_each_class_where_it_was_defined() -> None:
    def factory() -> tuple[type, Any]:
        @hessian2.java_class("Leaf")
        @dataclasses.dataclass
        class Leaf:
            n: int

        class Fields(TypedDict):
            leaf: Leaf

        @hessian2.java_class("Root")
        @dataclasses.dataclass
        class Root:
            fields: Fields

        return Root, Root({"leaf": Leaf(1)})

    root, value = factory()
    codec = Codec()
    codec.register(root)

    assert same(codec.loads(codec.dumps(value)), value)


def test_java_name_and_encoding_use_the_converter_even_under_a_builtin_tag():
    codec = Codec()
    codec.register(
        Converter(int, lambda v: {"v": str(v)}, lambda f: int(f["v"])),
        name="custom.Int",
        replace=True,
    )

    assert (
        codec.dumps(5, type=hessian2.BigInteger) == b"C\x0acustom.Int\x91\x01v\x60\x015"
    )
    assert codec.java_name(hessian2.BigInteger) == "custom.Int"


class DecimalSubclass(Decimal):
    pass


def test_unregistered_decimal_subclasses_have_no_encoding_mapping():
    with pytest.raises(EncodeError):
        hessian2.dumps(DecimalSubclass("1"))


def test_unregistered_decimal_subclasses_have_no_java_name():
    with pytest.raises(TypeError, match=re.escape("no Java class name")):
        hessian2.java_name(DecimalSubclass)


def test_memoryview_has_the_java_byte_array_name():
    assert hessian2.java_name(memoryview) == "[B"


def test_tags_reach_through_qualifiers_and_annotated_unions() -> None:
    class Extra(TypedDict):
        count: typing.NotRequired[hessian2.Long]

    assert hessian2.dumps({"count": 1}, type=Extra) == b"H\x05count\xe1Z"
    # Annotated keeps a union from merging into the outer one.
    nested = typing.Annotated[hessian2.Long | list[hessian2.Long], "meta"] | str
    assert hessian2.dumps([1], type=nested) == b"\x79\xe1"


class Plain(TypedDict):
    count: int


@pytest.mark.parametrize(
    "tp",
    [
        typing.Annotated[int, "meta"] | hessian2.Long,
        Plain | dict[str, hessian2.Long],
        dt.datetime | hessian2.Instant,
    ],
    ids=repr,
)
def test_unions_reject_tags_competing_with_any_untagged_member(tp: Any) -> None:
    with pytest.raises(TypeError, match=re.escape("same values as another member")):
        hessian2.dumps(None, type=tp)


def test_annotated_and_typing_unions_decode() -> None:
    annotation = typing.Annotated[typing.Optional[Address], "meta"]  # noqa: UP045

    data = hessian2.dumps(Address("x"))

    assert same(hessian2.loads(data, type=annotation), Address("x"))


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (typing.NewType("user_id", str), "java.lang.String"),
        (typing.TypeVar("long_var", bound=hessian2.Long), "long"),
        (typing.TypeVar("free"), None),
    ],
    ids=["newtype", "bound-typevar", "unbound-typevar"],
)
def test_java_name_reads_newtypes_and_type_variable_bounds(annotation, expected):
    assert hessian2.java_name(annotation) == expected


def test_encoding_applies_the_tag_in_a_type_variable_bound():
    long_var = typing.TypeVar("long_var", bound=hessian2.Long)

    assert hessian2.dumps([7], type=list[long_var]) == b"y\xe7"


def test_decoding_a_newtype_uses_its_supertype():
    user_id = typing.NewType("user_id", str)

    assert same(hessian2.loads(hessian2.dumps("a"), type=user_id), "a")


_T = typing.TypeVar("_T")


@dataclasses.dataclass
class _Left(typing.Generic[_T]):
    left: _T


@dataclasses.dataclass
class _Right(typing.Generic[_T]):
    right: _T


@hessian2.java_class("com.example.Combined", field_naming=None)
@dataclasses.dataclass
class _Combined(_Left[str], _Right[hessian2.Long]):
    pass


_U = typing.TypeVar("_U")


@dataclasses.dataclass
class _Pair(typing.Generic[_T, _U]):
    first: _T
    second: _U


@dataclasses.dataclass
class _Partial(_Pair[int, _U], typing.Generic[_U]):
    pass


@hessian2.java_class("com.example.Final")
@dataclasses.dataclass
class _Final(_Partial[str]):
    pass


def test_generic_bases_bind_shared_type_variables_independently_during_encoding():
    value = _Combined(right=7, left="x")

    assert (
        hessian2.dumps(value)
        == b"C\x14com.example.Combined\x92\x05right\x04left\x60\xe7\x01x"
    )


@pytest.mark.parametrize(
    "value",
    [_Combined(right=7, left="x"), _Final(1, "ok")],
    ids=["multiple-bases", "partial-binding"],
)
def test_generic_bases_bind_their_type_parameters_during_decoding(value):
    assert same(hessian2.loads(hessian2.dumps(value), type=type(value)), value)


def test_generic_bases_reject_a_value_for_the_other_bases_type_argument():
    with pytest.raises(DecodeError, match=re.escape("expected str")):
        hessian2.loads(hessian2.dumps(_Combined(right=7, left=8)), type=_Combined)


class _WithMetadata(TypedDict):
    value: int
    next: typing.NotRequired[typing.Annotated[_WithMetadata, "meta"]]


def test_metadata_does_not_hide_a_typed_dict_s_recursion() -> None:
    wire = hessian2.dumps({"value": 1, "next": {"value": 2}}, type=_WithMetadata)

    assert same(
        hessian2.loads(wire, type=_WithMetadata), {"value": 1, "next": {"value": 2}}
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
def test_a_tag_around_an_alias_is_not_recursion() -> None:
    scalar = typing.TypeAliasType("scalar", int)  # type: ignore[attr-defined,unused-ignore]
    tagged = typing.Annotated[scalar, *typing.get_args(hessian2.Long)[1:]]

    wire = hessian2.dumps(7, type=tagged)

    assert wire == b"\xe7"
    assert same(hessian2.loads(wire, type=tagged), 7)


def test_annotations_resolve_where_they_are_written() -> None:
    def module(name: str, source: str) -> types.ModuleType:
        made = types.ModuleType(name)
        made.__dict__.update(hessian2=hessian2, dataclasses=dataclasses, typing=typing)
        sys.modules[name] = made
        exec(
            compile("from __future__ import annotations\n" + source, name, "exec"),
            made.__dict__,
        )
        return made

    try:
        module(
            "_base_models",
            "Id = hessian2.Long\nclass Base(typing.TypedDict):\n    value: Id\n",
        )
        # An inherited annotation names the base module's Id, not this one.
        child = module(
            "_child_models",
            "import _base_models\nId = int\nclass Child(_base_models.Base):\n    pass\n",
        ).Child
        assert hessian2.dumps({"value": 7}, type=child) == b"H\x05value\xe7Z"
        # A field named int does not change what the annotation int means.
        counter = module(
            "_shadow_models",
            "@hessian2.java_class('Counter')\n"
            "@dataclasses.dataclass\n"
            "class Counter:\n"
            "    value: int\n"
            "    int: bool = False\n",
        ).Counter
        with pytest.raises(DecodeError, match=re.escape("expected int")):
            hessian2.loads(hessian2.dumps(counter("x")), type=counter)
    finally:
        for name in ("_base_models", "_child_models", "_shadow_models"):
            sys.modules.pop(name, None)


_Item = typing.TypeVar("_Item")


class _Page(TypedDict, typing.Generic[_Item]):
    total: hessian2.Long
    items: list[_Item]


def test_a_generic_typed_dict_s_arguments_and_own_tags_both_apply() -> None:
    # Encoding and decoding read Page[...] alike: its own tags stay, and its
    # arguments type its parameters.
    wire = hessian2.dumps({"total": 1, "items": [2]}, type=_Page[hessian2.Long])

    assert wire == b"H\x05total\xe1\x05itemsy\xe2Z"
    assert (
        hessian2.dumps({"total": 1, "items": []}, type=_Page[int]).count(b"\xe1") == 1
    )
    assert same(hessian2.loads(wire, type=_Page[int]), {"total": 1, "items": [2]})


class _ValueMap(dict[str, _Item]):
    pass


def test_tags_in_arguments_that_do_not_type_items_are_refused() -> None:
    # _ValueMap's one argument is not its key and value.
    with pytest.raises(TypeError, match=re.escape("tags in the arguments")):
        hessian2.dumps(_ValueMap(x=7), type=_ValueMap[hessian2.Long])


def test_encoding_a_generic_reads_none_of_its_fields() -> None:
    @dataclasses.dataclass
    class Converted(typing.Generic[_Item]):
        value: _Item
        # Never resolved: the converter writes Converted.
        irrelevant: MissingType = None  # type: ignore[name-defined]  # noqa: F821

    codec = Codec()
    codec.register(
        Converter(Converted, lambda v: {"v": v.value}, lambda f: Converted(f["v"])),
        name="com.example.Converted",
    )

    assert codec.java_name(Converted[int]) == "com.example.Converted"
    assert codec.dumps(Converted(7), type=Converted[int]) == codec.dumps(Converted(7))


@dataclasses.dataclass
class _ListRecord(list[_Item]):
    value: _Item


@dataclasses.dataclass
class _DictRecord(dict[str, _Item]):
    value: _Item


@pytest.mark.parametrize(
    ("record", "other", "data"),
    [
        (_ListRecord, list[hessian2.Long], [8]),
        (_DictRecord, dict[str, hessian2.Long], {"k": 8}),
    ],
    ids=["list-base", "dict-base"],
)
@pytest.mark.parametrize("order", ["record-first", "container-first"])
def test_a_converted_record_keeps_its_mapping_in_a_container_union(
    record, other, data, order
):
    codec = Codec()
    codec.register(
        Converter(record, lambda v: {"v": v.value}, lambda f: record(f["v"])),
        name=f"com.example.{record.__name__}",
    )
    annotation = {
        "record-first": record[int] | other,
        "container-first": other | record[int],
    }[order]

    assert codec.dumps(record(7), type=annotation) == codec.dumps(record(7))


@pytest.mark.parametrize(
    ("record", "other", "data"),
    [
        (_ListRecord, list[hessian2.Long], [8]),
        (_DictRecord, dict[str, hessian2.Long], {"k": 8}),
    ],
    ids=["list-base", "dict-base"],
)
@pytest.mark.parametrize("order", ["record-first", "container-first"])
def test_a_plain_container_keeps_its_tags_in_a_converted_record_union(
    record, other, data, order
):
    codec = Codec()
    codec.register(
        Converter(record, lambda v: {"v": v.value}, lambda f: record(f["v"])),
        name=f"com.example.{record.__name__}",
    )
    annotation = {
        "record-first": record[int] | other,
        "container-first": other | record[int],
    }[order]

    assert codec.dumps(data, type=annotation) == codec.dumps(data, type=other)


@hessian2.java_class("com.example.Nest")
@dataclasses.dataclass
class _Nest(typing.Generic[_Item]):
    value: _Item


def test_nested_generics_compile_in_linear_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annotation: Any = int
    value: Any = 7
    # Reading each field and argument twice per level took 2**40 steps, and
    # reading each level's fields again took 40**2.
    for _ in range(40):
        annotation, value = _Nest[annotation], _Nest(value)
    # Complexity bound: private call counts avoid a machine-dependent timing assertion.
    strip, reads = hessian2._strip, []
    monkeypatch.setattr(hessian2, "_strip", lambda tp: reads.append(tp) or strip(tp))
    hessian2._type_plan(annotation)

    assert len(reads) <= 41
    monkeypatch.undo()
    assert same(
        hessian2.loads(hessian2.dumps(value, type=annotation), type=annotation), value
    )


class _OptionalOutside(TypedDict):
    x: typing.Annotated[typing.NotRequired[int], "meta"]


class _RequiredOutside(TypedDict, total=False):
    x: typing.Annotated[typing.Required[int], "meta"]


def test_annotated_not_required_keys_can_be_absent():
    assert same(hessian2.loads(hessian2.dumps({}), type=_OptionalOutside), {})


def test_annotated_required_keys_cannot_be_absent():
    with pytest.raises(DecodeError, match=re.escape("without key 'x'")):
        hessian2.loads(hessian2.dumps({}), type=_RequiredOutside)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="TypeAliasType is 3.12+")
def test_an_alias_of_none_is_the_nullable_member() -> None:
    null = typing.TypeAliasType("null", type(None))  # type: ignore[attr-defined,unused-ignore]
    aliased = tuple[hessian2.Long | null, ...]  # type: ignore[valid-type]
    explicit = tuple[hessian2.Long | None, ...]

    assert (
        hessian2.java_name(aliased)
        == hessian2.java_name(explicit)
        == "[Ljava.lang.Long;"
    )
    assert hessian2.dumps([7], type=aliased) == hessian2.dumps([7], type=explicit)


def test_a_new_type_defined_in_a_function_resolves() -> None:
    local_id = typing.NewType("local_id", hessian2.Long)  # type: ignore[valid-newtype]

    @hessian2.java_class("com.example.LocalUser")
    @dataclasses.dataclass
    class LocalUser:
        user_id: local_id

    wire = hessian2.dumps(LocalUser(7))

    assert wire.endswith(b"\xe7")
    assert same(hessian2.loads(wire, type=LocalUser), LocalUser(7))
