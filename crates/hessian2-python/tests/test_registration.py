"""Public contracts for registration."""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import re
import sys
import uuid
from dataclasses import InitVar, dataclass
from decimal import Decimal
from typing import Any

import attrs
import hessian2
import pytest
from assertions import same
from hessian2 import Codec, Converter, DecodeError, EncodeError, HessianObject


def test_attrs_takes_self_factory_is_rejected() -> None:
    @attrs.define
    class Q:
        x: int = attrs.Factory(lambda self: 1, takes_self=True)

    with pytest.raises(TypeError, match=re.escape("takes_self")):
        Codec().register(Q, name="Q", field_naming=None)


class RegisteredColor(enum.Enum):
    RED = 1
    GREEN = 2


def test_registered_enums_encode_member_names_and_references():
    codec = Codec()
    codec.register(RegisteredColor, name="com.example.Color", field_naming=None)

    data = codec.dumps(
        [RegisteredColor.RED, RegisteredColor.GREEN, RegisteredColor.RED]
    )

    assert (
        data == b"\x7bC\x11com.example.Color\x91\x04name\x60\x03RED\x60\x05GREENQ\x91"
    )


def test_registered_enums_decode_member_names():
    codec = Codec()
    codec.register(RegisteredColor, name="com.example.Color", field_naming=None)

    assert codec.loads(codec.dumps(RegisteredColor.GREEN)) is RegisteredColor.GREEN


def test_registered_enums_reject_unknown_member_names():
    codec = Codec()
    codec.register(RegisteredColor, name="com.example.Color", field_naming=None)

    with pytest.raises(DecodeError, match=re.escape("no member")):
        codec.loads(
            hessian2.dumps(HessianObject("com.example.Color", {"name": "BLUE"}))
        )


@pytest.mark.parametrize("value", [7, 2**100], ids=["small-int", "big-int"])
def test_integer_converters_override_each_native_integer_form(value):
    codec = Codec()
    codec.register(
        Converter(int, lambda n: {"text": str(n)}, lambda f: int(f["text"])),
        name="java.math.BigInteger",
        replace=True,
    )

    assert same(codec.loads(codec.dumps(value)), value)


@pytest.mark.parametrize("value", [7, 2**100], ids=["small-int", "big-int"])
def test_replacing_an_integer_converter_restores_native_integer_encoding(value):
    codec = Codec()
    codec.register(
        Converter(int, lambda n: {"text": str(n)}, lambda f: int(f["text"])),
        name="java.math.BigInteger",
        replace=True,
    )
    codec.register(
        Converter(complex, lambda n: {"text": str(n)}, lambda f: complex(f["text"])),
        name="java.math.BigInteger",
        replace=True,
    )

    assert codec.dumps(value) == hessian2.dumps(value)


def test_a_converter_can_replace_a_builtin_python_mapping():
    codec = Codec()
    codec.register(
        Converter(uuid.UUID, lambda u: {"text": u.hex}, lambda f: uuid.UUID(f["text"])),
        name="java.util.UUID",
    )
    value = uuid.UUID(int=5)

    data = codec.dumps(value)

    assert same(
        Codec(unknown_class="hessian_object").loads(data, type=HessianObject),
        HessianObject("java.util.UUID", {"text": value.hex}),
    )
    assert same(codec.loads(data), value)


def test_replacing_a_builtin_mapping_affects_only_that_codec():
    codec = Codec()
    codec.register(
        Converter(uuid.UUID, lambda u: {"text": u.hex}, lambda f: uuid.UUID(f["text"])),
        name="java.util.UUID",
    )

    value = hessian2.loads(hessian2.dumps(uuid.UUID(int=5)), type=HessianObject)

    assert value.fields == {"value": "00000000-0000-0000-0000-000000000005"}


@dataclasses.dataclass
class A:
    x: int


@dataclasses.dataclass
class B:
    y: int


def test_registering_the_same_class_and_name_is_idempotent():
    codec = Codec()
    codec.register(A, name="first", field_naming=None)

    codec.register(A, name="first", field_naming=None)

    assert same(codec.loads(codec.dumps(A(1))), A(1))


def test_a_registered_class_needs_replace_to_move_to_another_name():
    codec = Codec()
    codec.register(A, name="first", field_naming=None)

    with pytest.raises(ValueError, match=re.escape("already registered as 'first'")):
        codec.register(A, name="second", field_naming=None)


def test_a_registered_name_needs_replace_to_move_to_another_class():
    codec = Codec()
    codec.register(A, name="first", field_naming=None)

    with pytest.raises(ValueError, match=re.escape("'first' is already registered")):
        codec.register(B, name="first", field_naming=None)


def test_replacing_a_name_releases_its_previous_class_for_registration():
    codec = Codec()
    codec.register(A, name="first", field_naming=None)
    codec.register(B, name="first", field_naming=None, replace=True)

    codec.register(A, name="second", field_naming=None)

    assert same(codec.loads(codec.dumps(A(1))), A(1))
    assert same(codec.loads(codec.dumps(B(1))), B(1))


def test_moving_a_class_to_a_new_name_preserves_the_old_decode_alias():
    codec = Codec()
    codec.register(A, name="second", field_naming=None)
    codec.register(A, name="third", field_naming=None, replace=True)

    assert same(codec.loads(hessian2.dumps(HessianObject("second", {"x": 2}))), A(2))


def test_moving_a_class_changes_the_name_used_to_encode_it():
    codec = Codec()
    codec.register(A, name="second", field_naming=None)
    codec.register(A, name="third", field_naming=None, replace=True)

    assert codec.dumps(A(1)) == b"C\x05third\x91\x01x\x60\x91"


@dataclasses.dataclass
class Money:
    value: str


def test_a_builtin_java_name_requires_replace_for_another_python_class():
    with pytest.raises(ValueError, match=re.escape("built-in mapping")):
        Codec().register(Money, name="java.math.BigDecimal", field_naming=None)


def test_replace_allows_a_class_to_use_a_builtin_java_name():
    codec = Codec()
    codec.register(Money, name="java.math.BigDecimal", field_naming=None, replace=True)

    assert same(codec.loads(codec.dumps(Money("1.5"))), Money("1.5"))


def test_replacing_a_builtin_name_removes_the_original_python_mapping():
    codec = Codec()
    codec.register(Money, name="java.math.BigDecimal", field_naming=None, replace=True)

    with pytest.raises(EncodeError):
        codec.dumps(Decimal("1.5"))


def test_register_decorator_passes_replace() -> None:
    codec = Codec()

    @dataclasses.dataclass
    class P:
        x: int

    codec.register(P, name="P", field_naming=None)

    @dataclasses.dataclass
    class Q:
        x: int

    codec.register(Q, name="P", field_naming=None, replace=True)

    assert same(codec.loads(codec.dumps(Q(1))), Q(1))


@pytest.mark.parametrize("cls", [int, object(), dict], ids=repr)
def test_register_rejects_unsupported(cls: Any) -> None:
    with pytest.raises(TypeError):
        Codec().register(cls, name="X", field_naming=None)


def test_registration_is_per_codec() -> None:
    codec = Codec()

    @dataclasses.dataclass
    class A:
        x: int

    codec.register(A, name="A", field_naming=None)

    with pytest.raises(EncodeError):
        hessian2.dumps(A(1))


def test_replacing_an_old_decode_alias_preserves_the_current_encoder():
    codec = Codec()
    codec.register(A, name="Old", field_naming=None)
    codec.register(A, name="New", field_naming=None, replace=True)
    codec.register(B, name="Old", field_naming=None, replace=True)

    assert codec.dumps(A(1)) == b"C\x03New\x91\x01x\x60\x91"


def test_replacing_an_old_decode_alias_selects_the_new_class_when_decoding():
    codec = Codec()
    codec.register(A, name="Old", field_naming=None)
    codec.register(A, name="New", field_naming=None, replace=True)
    codec.register(B, name="Old", field_naming=None, replace=True)

    assert same(codec.loads(hessian2.dumps(HessianObject("Old", {"y": 2}))), B(2))


@dataclasses.dataclass
class Collision:
    user_name: str
    userName: str


def test_registration_rejects_colliding_wire_field_names():
    with pytest.raises(
        ValueError, match=re.escape("multiple Python fields map to 'userName'")
    ):
        Codec().register(Collision, name="Collision")


def test_disabling_field_naming_keeps_otherwise_colliding_names_distinct():
    codec = Codec()
    codec.register(Collision, name="Collision", field_naming=None)
    value = Collision("first", "second")

    assert same(codec.loads(codec.dumps(value)), value)


@dataclass
class Parent:
    name: str


@dataclass
class Child(Parent):
    age: int


@pytest.mark.parametrize(
    ("registered", "unregistered"),
    [(Parent, Child("bob", 3)), (Child, Parent("ann"))],
    ids=["parent-only", "child-only"],
)
def test_registration_does_not_trust_other_classes_in_the_hierarchy(
    registered, unregistered
):
    codec = Codec()
    codec.register(registered, name=registered.__name__, field_naming=None)

    with pytest.raises(EncodeError, match=re.escape("register")):
        codec.dumps([unregistered])


@pytest.mark.parametrize(
    "value", [Parent("ann"), Child("bob", 3)], ids=["parent", "child"]
)
def test_each_registered_class_uses_its_exact_mapping(value):
    codec = Codec()
    codec.register(Parent, name="Parent", field_naming=None)
    codec.register(Child, name="Child", field_naming=None)

    decoded = codec.loads(codec.dumps([value, value]))

    assert type(decoded[0]) is type(value)
    assert decoded[0] == value
    assert decoded[0] is decoded[1]


def test_initvar_without_default_cannot_be_registered_with_post_init():
    @dataclass
    class Input:
        value: int
        transient: InitVar[int]

        def __post_init__(self, transient):
            pass

    with pytest.raises(TypeError, match=re.escape("InitVar without a default")):
        Codec().register(Input, name="Input", field_naming=None)


@pytest.mark.parametrize("mode", ["raise", "dict"], ids=["reject", "plain-data"])
def test_unknown_class_decoding_never_imports_its_peer_chosen_name(mode, monkeypatch):
    name = "peer_module_that_does_not_exist"
    attempts = []

    class ImportProbe:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == name or fullname.startswith(name + "."):
                attempts.append(fullname)
            return None

    # Observe the import boundary; absence from sys.modules cannot detect a failed import.
    monkeypatch.setattr(sys, "meta_path", [ImportProbe(), *sys.meta_path])
    data = hessian2.dumps(HessianObject(name + ".Value"))
    codec = Codec(unknown_class=mode)

    with contextlib.suppress(DecodeError):
        codec.loads(data)

    assert attempts == []
