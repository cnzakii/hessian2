"""Hessian 2.0 serialization with a Rust core."""

from __future__ import annotations

import collections.abc
import dataclasses
import datetime
import decimal
import enum
import functools
import sys
import types
import typing
import uuid
from collections.abc import Callable, Iterable
from typing import Annotated, Any, Literal, TypeVar, overload

from ._core import (
    DecodeError,
    EncodeError,
    HessianObject,
    Reader,
    Typed,
    __version__,
    _Codec,
    _Hint,
    _Type,
)

__all__ = [
    "BigInteger",
    "Codec",
    "Converter",
    "Date",
    "DecodeError",
    "EncodeError",
    "HessianObject",
    "Instant",
    "LocalDateTime",
    "Long",
    "OffsetDateTime",
    "Reader",
    "Typed",
    "ZonedDateTime",
    "__version__",
    "as_object",
    "dumps",
    "dumps_many",
    "java_class",
    "java_name",
    "loads",
    "loads_many",
    "reader",
]

_C = TypeVar("_C", bound=type)

_REQUIRED, _VALUE, _FACTORY = 0, 1, 2
_KINDS = "a dataclass, an attrs class, a named tuple, or an Enum"
# The class attribute holding a declaration; the extension reads it too.
_DECLARATION = "__hessian2__"
_UNSET: Any = object()


class _Tag:
    """The Java type an annotated ``int`` or ``datetime`` is written as."""

    __slots__ = ("hint", "java")

    def __init__(self, hint: str, java: str | None) -> None:
        self.hint = hint
        self.java = java

    def __repr__(self) -> str:
        return f"<hessian2 {self.hint} tag>"


# Where Python values alone cannot tell which Java type a peer expects, and
# the peer does not convert: a Java field or parameter of these types fails
# to decode a small int or a java.util.Date. An annotation with one of these
# writes its values as that type; values stay plain int and datetime.
Long = Annotated[int, _Tag("long", "long")]
BigInteger = Annotated[int, _Tag("big_integer", "java.math.BigInteger")]
# What a datetime is written as by default, aware or naive, named so a
# position can require one: a datetime can be either, as Java's classes cannot.
Date = Annotated[datetime.datetime, _Tag("date", "java.util.Date")]
LocalDateTime = Annotated[
    datetime.datetime, _Tag("local_datetime", "java.time.LocalDateTime")
]
Instant = Annotated[datetime.datetime, _Tag("instant", "java.time.Instant")]
OffsetDateTime = Annotated[
    datetime.datetime, _Tag("offset_datetime", "java.time.OffsetDateTime")
]
ZonedDateTime = Annotated[
    datetime.datetime, _Tag("zoned_datetime", "java.time.ZonedDateTime")
]


class _Declaration:
    """A class's Java name, field naming, and the names visible where it was
    defined, for resolving its annotations."""

    __slots__ = ("entry", "field_naming", "name", "namespace")

    def __init__(
        self, name: str, field_naming: str | None, namespace: dict[str, Any]
    ) -> None:
        self.name = name
        self.field_naming = field_naming
        self.namespace = namespace
        # What any codec encodes the class with, built on first encode.
        self.entry: object = None


def java_class(
    name: str, *, field_naming: Literal["camelCase"] | None = "camelCase"
) -> Callable[[_C], _C]:
    """Declare the Java class that a Python class encodes as.

    Any codec encodes instances of the class without registering it.
    Decoding builds the class where an annotation names it, as in
    ``loads(data, type=cls)`` or a field annotation, and elsewhere, such as
    in ``Any`` positions, only once it is registered with
    :meth:`Codec.register`. Subclasses are not declared by inheritance.
    The decorator records the declaration only, so it may come before or
    after ``@dataclass`` or ``@attrs.define``.

    Args:
        name: The Java class name, such as ``"com.example.User"``.
        field_naming: ``"camelCase"`` sends a field named ``user_name`` as
            ``userName``; ``None`` sends Python names unchanged.

    Returns:
        A class decorator that returns the class unchanged.

    Raises:
        TypeError: If ``name`` is not a string.
        ValueError: If ``name`` is empty or ``field_naming`` is neither
            value.
    """
    _check_name(name)
    _check_naming(field_naming)

    def decorate(cls: _C) -> _C:
        if not isinstance(cls, type):
            raise TypeError(f"java_class decorates a class, not {cls!r}")
        namespace = _local_types(sys._getframe(1))
        setattr(cls, _DECLARATION, _Declaration(name, field_naming, namespace))
        return cls

    return decorate


class Converter:
    """Map any type to a Hessian object through a ``dict`` of its fields.

    Args:
        cls: The Python type; exact instances of it encode through
            ``to_fields``.
        to_fields: Return an instance's fields as a ``dict`` keyed by Java
            field name, in the order they are sent. Called once per object
            in a stream; subsequent references reuse that object.
        from_fields: Build a value from a received ``dict`` of fields.
            All fields are decoded before this runs; a reference back to
            the value during construction raises :class:`DecodeError`.

    Register a converter with its Java class name. For example, a pydantic
    model, validated as it decodes::

        codec.register(
            Converter(User, to_fields=User.model_dump, from_fields=User.model_validate),
            name="com.example.User",
        )
    """

    __slots__ = ("cls", "from_fields", "to_fields")

    def __init__(
        self,
        cls: type,
        to_fields: Callable[[Any], dict[str, Any]],
        from_fields: Callable[[dict[str, Any]], Any],
    ) -> None:
        self.cls = cls
        self.to_fields = to_fields
        self.from_fields = from_fields

    def __repr__(self) -> str:
        return f"Converter({self.cls.__qualname__})"


class Codec(_Codec):
    """A Hessian 2.0 encoder and decoder with registered classes.

    Standard library types map to JDK classes: :class:`decimal.Decimal` to
    ``java.math.BigDecimal``, :class:`uuid.UUID` to ``java.util.UUID``, an
    ``int`` beyond 64 bits to ``java.math.BigInteger``, and :mod:`datetime`
    types to ``java.util.Date`` (aware ``datetime``) or ``java.time`` classes.
    A codec is safe to share between threads; each call keeps its own stream
    state and registration snapshot. Registrations do not change calls in
    progress. User callbacks and shared input objects are not synchronized.

    Encoding writes instances of declared and registered classes. Decoding
    builds an object of a class the wire names when that class is trusted
    where the object appears, and fits the annotation there: an annotation
    trusts the classes it names, through ``type=`` or a field's annotation,
    and :meth:`register` trusts a class wherever it fits, as in ``Any``
    positions and positions annotated with a base class.

    Args:
        unknown_class: What decoding does with an object whose class is
            not registered: ``"raise"`` raises :class:`DecodeError`,
            ``"hessian_object"`` returns a :class:`HessianObject`, and
            ``"dict"`` returns its fields as a ``dict``. It applies where an
            annotation allows any value; ``loads``, ``loads_many``, and
            ``Reader.load`` can override it for one call.
        max_depth: How deeply lists, maps, and objects may nest. Deeper input
            raises :class:`DecodeError`, deeper values :class:`EncodeError`.
            Each level needs stack space; raise it only on threads with a
            large stack.
    """

    @overload
    def register(
        self, tp: Converter, /, *, name: str, replace: bool = False
    ) -> None: ...
    @overload
    def register(
        self,
        tp: Any,
        /,
        *,
        name: str | None = None,
        field_naming: Literal["camelCase"] | None = ...,
        replace: bool = False,
    ) -> None: ...
    def register(
        self,
        tp: Any,
        /,
        *,
        name: str | None = None,
        field_naming: Any = _UNSET,
        replace: bool = False,
    ) -> None:
        """Trust a class: let decoding build it wherever its type fits.

        An annotation that names a class decodes it without registration.
        Register the classes that arrive where no annotation names them: in
        ``Any`` or ``object`` positions, in ``loads`` without ``type=``, and
        as subclasses in positions annotated with their base class. A class
        without a declaration, such as one from another library, is named
        here with ``name=``, for this codec only. Registering the same class
        under the same name again does nothing new.

        Args:
            tp: A class declared with :func:`java_class`, a class to name
                with ``name``, or a :class:`Converter`.
            name: The Java class name, for a class without a declaration or
                a converter. For a declared class it must match.
            field_naming: For a class named here: ``"camelCase"`` (the
                default) sends ``user_name`` as ``userName``; ``None`` sends
                Python names unchanged.
            replace: Allow replacing what ``name`` or the class ``tp`` is
                already registered to, including the built-in mappings of
                JDK classes.

        Raises:
            TypeError: If ``tp`` is not a class or converter, has no Java
                name, is not one that encodes as an object, or has
                annotations that cannot be resolved.
            ValueError: If a name or class is registered to another, and
                ``replace`` is false; if ``name`` differs from the declared
                one; or if field naming maps several attributes to one wire
                name.

        Decoding builds instances without calling ``__init__``, as
        :mod:`pickle` does for most classes, sets each field as its
        annotation types it, then runs ``__post_init__`` or
        ``__attrs_post_init__`` if defined. An ``Exception`` subclass so
        built has empty ``args``; its ``__post_init__`` can set them.
        """
        if isinstance(tp, Converter):
            if name is None:
                raise TypeError("register a Converter with name=, its Java class name")
            if field_naming is not _UNSET:
                raise TypeError("a Converter's to_fields chooses its field names")
            _check_name(name)
            if tp.cls in (Typed, HessianObject):
                # Encoding writes them as the values they stand for.
                raise TypeError(f"{tp.cls.__name__} cannot have a converter")
            entry = self._converter_entry(name, tp.cls, tp.to_fields, tp.from_fields)
            self._publish([(entry, replace)])
            return
        if not isinstance(tp, type):
            raise TypeError(
                f"register takes a class or a Converter, not {tp!r}; an annotation "
                "decodes the classes it names without registration"
            )
        declared = _declaration(tp)
        if declared is not None:
            if field_naming is not _UNSET:
                raise TypeError(
                    f"{tp.__qualname__} takes its field naming from @java_class"
                )
            if name is not None and name != declared.name:
                raise ValueError(
                    f"{tp.__qualname__} is declared as {declared.name!r}, not {name!r}"
                )
        else:
            if name is None:
                raise TypeError(
                    f"{tp.__qualname__} has no @hessian2.java_class; pass name="
                )
            _check_name(name)
            naming = "camelCase" if field_naming is _UNSET else field_naming
            _check_naming(naming)
            declared = _Declaration(name, naming, _local_types(sys._getframe(1)))
        self._publish([(self._entry(tp, declared), replace)])

    def java_name(self, tp: Any, /) -> str | None:
        """Return the Java class a type always encodes as, under this codec.

        The name is the one ``Class.getName`` gives, such as
        ``"java.lang.String"`` or ``"[B"``. A type whose values are always
        a Java primitive, as ``float`` is a ``double`` and ``hessian2.Long``
        a ``long``, gives the primitive, which reads into its box as well;
        with ``| None``, which a primitive cannot hold, it gives the box,
        such as ``"java.lang.Long"``. ``tuple[X, ...]`` is a Java array:
        ``"[Ljava.lang.String;"`` for ``tuple[str, ...]``, ``"[I"`` for
        ``tuple[int, ...]``, and ``"[Ljava.lang.Integer;"`` with
        ``int | None`` items. Where the values of a type are
        several Java types, as an ``int`` is an ``int``, ``long``, or
        ``BigInteger`` by its size and a ``list`` any Java collection, the
        result is ``None``.

        Args:
            tp: A type or type expression, such as ``Order | None``.

        Returns:
            The Java class name, or ``None``.

        Raises:
            TypeError: If ``tp`` is a class with no Java form under this
                codec.
        """
        return self._name_of(_read(tp))

    def _name_of(self, node: _Node) -> str | None:
        """Return the Java class a node's values always encode as, as
        ``java_name`` does."""
        if isinstance(node, _TagNode):
            # A registered type keeps its own form, whatever the tag says.
            inner = node.inner
            if isinstance(inner, _ClassNode) and self._registered_name(inner.cls):
                return self._name_of(inner)
            return node.tag.java
        if isinstance(node, _ArrayNode):
            depth, cls, name = _component(node.item)
            # As the encoder names the component: a registration first.
            if cls is not None:
                name = self._registered_name(cls) or name or self._class_name(cls)
            # A component without a class is named; _class_name raises otherwise.
            assert name is not None
            # As Class.getName names arrays: [I, [Ljava.lang.String;, [[I.
            if not name.startswith("["):
                name = _PRIMITIVE_CODES.get(name, f"L{name};")
            return "[" * (depth + 1) + name
        if isinstance(node, _UnionNode):
            members = [m for m in node.members if not isinstance(m, _NoneNode)]
            if len(members) != 1:
                return None
            # The one other member is None: a union holds at least two.
            name = self._name_of(members[0])
            return _BOXES.get(name, name)
        if isinstance(node, _OtherNode) or node.bare is None:
            raise TypeError(f"{node.bare!r} is not a type")
        if isinstance(node, (_TupleNode, _NoneNode)):
            # Several Java types, unless a codec registers a converter.
            return self._registered_name(
                tuple if isinstance(node, _TupleNode) else type(None)
            )
        cls = getattr(node, "cls", None)
        if cls is None:
            # Any value, or a reference back.
            return None
        # With type arguments, only its own declaration or registration
        # names a class.
        if node.bare is cls or _declaration(cls) or self._registered_name(cls):
            return self._class_name(cls)
        return None

    def _class_name(self, tp: type) -> str | None:
        """Return the Java class a class's values always encode as."""
        registered = self._registered_name(tp)
        if registered is not None:
            return registered
        declared = _declaration(tp)
        if declared is not None:
            return declared.name
        found, name = _builtin_name(tp)
        if found:
            return name
        raise TypeError(f"{tp.__qualname__} has no Java class name; {self._advice(tp)}")

    def _entry(self, cls: type, declared: _Declaration) -> object:
        """Return the entry that encodes and decodes ``cls`` as ``declared``
        says, for ``_publish``."""
        name, naming = declared.name, declared.field_naming
        if issubclass(cls, enum.Enum):
            return self._enum_entry(name, cls)
        if _is_named_tuple(cls):
            fields = _tuple_fields(cls)
            hints = _field_hints(cls, fields, declared.namespace)
            specs = _specs(fields, naming, hints, declared.namespace)
            return self._tuple_entry(name, cls, specs)
        fields, post_init = _fields(cls)
        hints = _field_hints(cls, fields, declared.namespace)
        specs = _specs(fields, naming, hints, declared.namespace)
        return self._class_entry(name, cls, specs, _instance_new(cls), post_init)

    def _declared(self, cls: type) -> object:
        """Return the entry any codec encodes a declared class with, and
        decodes it with where an annotation names it.

        The extension calls this the first time any codec needs the entry.
        It is cached on the declaration, not in a registry, so it does not
        register the class name for ``Any`` positions, and it is freed with
        the class.
        """
        # The encoder calls this only for a class with its own declaration.
        declared = vars(cls)[_DECLARATION]
        declared.entry = self._entry(cls, declared)
        return declared.entry

    def _advice(self, cls: type) -> str | None:
        """Return how to give ``cls`` a Java class, for an error, or ``None``
        when it maps to Java types of its own.

        The extension calls this when a value or annotation has no form.
        """
        if _is_record(cls):
            return "declare it with @hessian2.java_class or register it"
        if cls is HessianObject or _builtin_name(cls)[0]:
            return None
        return "register a Converter for it"

    def _hint(self, tp: Any) -> _Hint:
        """Compile the encoding hint for an annotation; the extension caches it."""
        if isinstance(tp, _Scoped):
            return _Hint(_plan(tp.annotation, tp.namespace))
        return _Hint(_plan(tp))

    def _type(self, tp: Any) -> _Type:
        """Compile the decoding type for an annotation; the extension caches it.

        ``None`` means no annotation, as in ``dumps_many(types=)``; the
        annotation for ``None`` itself is ``type(None)``.
        """
        if isinstance(tp, _Scoped):
            return _Type(_type_plan(tp.annotation, tp.namespace))
        return _Type(None if tp is None else _type_plan(tp))


_UNIONS = (typing.Union, types.UnionType)
# The values each kind of plan applies to, for finding union conflicts.
_PLAN_KINDS = {
    "long": "int",
    "int": "int",
    "array": "sequence",
    "big_integer": "int",
    "date": "datetime",
    "local_datetime": "datetime",
    "instant": "datetime",
    "offset_datetime": "datetime",
    "zoned_datetime": "datetime",
    "items": "sequence",
    "tuple": "sequence",
    "map": "dict",
    "keys": "dict",
}
# Whether the datetimes each datetime tag writes are aware.
_AWARE = {
    "date": True,
    "local_datetime": False,
    "instant": True,
    "offset_datetime": True,
    "zoned_datetime": True,
}
# The annotations of collections, and the container each decodes as.
_CONTAINERS = {
    list: "list",
    collections.abc.Sequence: "list",
    collections.abc.MutableSequence: "list",
    collections.abc.Collection: "list",
    collections.abc.Iterable: "list",
    set: "set",
    collections.abc.Set: "set",
    collections.abc.MutableSet: "set",
    frozenset: "frozenset",
}
_SEQUENCES = frozenset(_CONTAINERS)
_MAPPINGS = frozenset({dict, collections.abc.Mapping, collections.abc.MutableMapping})
# Qualifiers whose argument is the annotation itself.
_QUALIFIERS = frozenset(
    getattr(typing, name)
    for name in ("Final", "Required", "NotRequired", "ReadOnly")
    if hasattr(typing, name)
)
_ALIASES: tuple[type, ...] = (
    (typing.TypeAliasType,) if sys.version_info >= (3, 12) else ()
)
_TYPE_PARAMETERS = (typing.TypeVar, typing.ParamSpec, typing.TypeVarTuple)
_JAVA_NAMES: dict[type, str] = {
    str: "java.lang.String",
    bytes: "[B",
    bytearray: "[B",
    memoryview: "[B",
    decimal.Decimal: "java.math.BigDecimal",
    uuid.UUID: "java.util.UUID",
    datetime.date: "java.time.LocalDate",
    datetime.time: "java.time.LocalTime",
    datetime.timedelta: "java.time.Duration",
    bool: "boolean",
    float: "double",
}
# The letter Class.getName uses for each primitive array component.
_PRIMITIVE_CODES = {
    "boolean": "Z",
    "byte": "B",
    "int": "I",
    "long": "J",
    "double": "D",
}
# The box of each primitive a type gives, which can also hold null.
_BOXES = {
    "int": "java.lang.Integer",
    "long": "java.lang.Long",
    "double": "java.lang.Double",
    "boolean": "java.lang.Boolean",
}
_EXACT_JAVA_NAMES = frozenset({decimal.Decimal, uuid.UUID})
# Types that encode as several Java types; datetime must precede date.
_AMBIGUOUS = frozenset(
    {
        int,
        datetime.datetime,
        list,
        tuple,
        set,
        frozenset,
        dict,
        type(None),
        HessianObject,
        Typed,
    }
)


def _check_name(name: object) -> None:
    if not isinstance(name, str):
        raise TypeError(f"a Java class name must be a str, not {name!r}")
    if not name:
        raise ValueError("a Java class name must not be empty")


def _check_naming(field_naming: object) -> None:
    if field_naming not in (None, "camelCase"):
        raise ValueError(
            f"field_naming must be None or 'camelCase', not {field_naming!r}"
        )


def _declaration(cls: type) -> _Declaration | None:
    """Return a class's own declaration; subclasses do not inherit it."""
    declared = vars(cls).get(_DECLARATION)
    return declared if isinstance(declared, _Declaration) else None


def _local_types(frame: types.FrameType) -> dict[str, Any]:
    """Return the types visible in a function or class body, by name.

    A class defined there can refer to them in string annotations, which
    module globals do not resolve. Only types are kept, so the declaration
    does not keep other local values alive.
    """
    if frame.f_locals is frame.f_globals:
        return {}
    return {
        key: value
        for key, value in frame.f_locals.items()
        if isinstance(value, (type, typing.NewType, *_TYPE_PARAMETERS, *_ALIASES))
        or typing.get_origin(value) is not None
    }


def _resolve(owner: Any, namespace: dict[str, Any]) -> dict[str, Any]:
    """Return the evaluated annotations of a class or TypedDict, in the
    namespace of the function that defined it, if any."""
    # An explicit localns keeps class attributes, such as a field named int,
    # from shadowing the names annotations use, and keeps string annotations
    # TypedDict copies from a base in the base's module.
    # shortcut: under postponed annotations, names in a class body fail to
    # resolve, PEP 695 parameters resolve to a same-named module global if
    # there is one, and a function's local names resolve only for the class
    # declared or registered there, not for its subclasses elsewhere, a
    # generic's type arguments, or a generic it specializes. Resolve each
    # class in its own scope, as pydantic does, if users need them.
    local = {**namespace, owner.__name__: owner}
    try:
        return typing.get_type_hints(owner, localns=local, include_extras=True)
    except NameError as error:
        raise TypeError(
            f"cannot resolve the annotations of {owner.__qualname__}: {error}. "
            "Define the classes they name at module level, or before it in the "
            "same function"
        ) from error


def _field_hints(
    cls: type, fields: list[tuple[str, int, Any]], namespace: dict[str, Any]
) -> dict[str, Any]:
    """Return the annotations of ``cls``'s fields, as ``_class_hints``."""
    hints = _class_hints(cls, namespace)
    return {attr: hints[attr] for attr, _, _ in fields if attr in hints}


def _class_hints(cls: type, namespace: dict[str, Any]) -> dict[str, Any]:
    """Return the evaluated annotations of ``cls``, each with the type
    parameters of the class declaring it bound as the class statements from
    ``cls`` down to that class bind them, as in ``class IntBox(Box[int])``.
    Parameters of ``cls`` itself stay unbound."""
    hints = _resolve(cls, namespace)
    bindings = _bindings(cls)
    declaring: dict[str, type] = {}
    for owner in reversed(cls.__mro__):
        declaring.update(dict.fromkeys(_own_annotations(owner), owner))
    return {
        attr: _substitute(hint, bindings.get(declaring.get(attr, cls), {}))
        for attr, hint in hints.items()
    }


def _own_annotations(owner: type) -> Iterable[str]:
    """Return the names a class annotates in its own body."""
    if sys.version_info >= (3, 14):
        import annotationlib

        # FORWARDREF: names defined later must not fail here.
        return annotationlib.get_annotations(
            owner, format=annotationlib.Format.FORWARDREF
        )
    return vars(owner).get("__annotations__", {})


def _bindings(cls: type) -> dict[type, dict[Any, Any]]:
    """Return, for each generic class ``cls`` derives from, what the class
    statements between them bind its type parameters to, in terms of the
    parameters of ``cls``: each class binds its own, so independent classes
    can reuse one ``TypeVar``."""
    bindings: dict[type, dict[Any, Any]] = {}

    def visit(derived: type, bound: dict[Any, Any]) -> None:
        for base in vars(derived).get("__orig_bases__", derived.__bases__):
            origin = typing.get_origin(base) or base
            if (
                not isinstance(origin, type)
                or origin in (typing.Generic, typing.Protocol, object)
                or origin in bindings
            ):
                continue
            arguments = [_substitute(arg, bound) for arg in typing.get_args(base)]
            own = dict(
                zip(getattr(origin, "__parameters__", ()), arguments, strict=False)
            )
            bindings[origin] = own
            visit(origin, own)

    visit(cls, {})
    return bindings


# Reading annotations.
#
# An annotation is read once into a tree of nodes, as msgspec's inspect
# module and pydantic's core schema read one, and the encoding hint, the
# decoding type, and the Java name all derive from that tree: no two of them
# can read an annotation differently.


def _strip(tp: Any) -> tuple[Any, _Tag | None, Any, list[Any]]:
    """Return an annotation without what wraps it, the one place that reads
    wrappers: type aliases, qualifiers such as ``Required``, ``NewType``, and
    ``Annotated``; a type variable means its bound, or any value without
    one. Also return the outermost tag, ``Required`` or ``NotRequired`` if
    written, and the aliases passed, which a walk must not enter twice."""
    tag = presence = None
    aliases: list[Any] = []
    while True:
        origin = typing.get_origin(tp)
        alias = tp if isinstance(tp, _ALIASES) else origin
        if isinstance(alias, _ALIASES):
            if any(
                alias is seen or typing.get_origin(seen) is alias for seen in aliases
            ):
                raise TypeError(f"{alias!r} is an alias of itself")
            aliases.append(tp)
            if alias is tp:
                tp = tp.__value__
                continue
            # A subscripted generic alias, such as Pair[Long] for
            # ``type Pair[T] = list[T]``: substitute its arguments.
            value = alias.__value__
            # shortcut: a TypeVarTuple parameter does not pair one-to-one; rare.
            bound = dict(zip(alias.__type_params__, typing.get_args(tp), strict=False))
            if value in bound:
                tp = bound[value]
            else:
                # By identity: the value may use the parameters in another
                # order, or not all of them.
                used = getattr(value, "__parameters__", ())
                tp = value[tuple(bound[p] for p in used)] if used else value
        elif origin in _QUALIFIERS:
            if presence is None and origin in (typing.Required, typing.NotRequired):
                presence = origin
            tp = typing.get_args(tp)[0]
        elif origin is Annotated:
            if tag is None:
                tag = next((m for m in tp.__metadata__ if isinstance(m, _Tag)), None)
            tp = tp.__origin__
        elif isinstance(tp, typing.NewType):
            tp = tp.__supertype__
        elif isinstance(tp, typing.TypeVar) and tp.__bound__ is not None:
            tp = tp.__bound__
        else:
            if isinstance(tp, typing.TypeVar):
                return Any, tag, presence, aliases
            return tp, tag, presence, aliases


@dataclasses.dataclass(eq=False, kw_only=True)
class _Node:
    """What an annotation means. ``raw`` is the annotation as written, for
    type references and messages, and ``bare`` without what wraps it."""

    raw: Any
    bare: Any
    # Whether a part of it refers back to it, as Tree does in list[Tree].
    recurs: bool = False


@dataclasses.dataclass(eq=False, kw_only=True)
class _AnyNode(_Node):
    """Any value: ``Any``, ``object``, or a form such as ``Literal``."""


@dataclasses.dataclass(eq=False, kw_only=True)
class _NoneNode(_Node):
    """``None`` only."""


@dataclasses.dataclass(eq=False, kw_only=True)
class _OtherNode(_Node):
    """Something not a type, such as an unresolved string annotation."""


@dataclasses.dataclass(eq=False, kw_only=True)
class _ClassNode(_Node):
    """A class, with its type arguments, and for a generic record the fields
    those arguments type, as in ``Box[Address]``."""

    cls: type
    args: tuple[_Node, ...] = ()
    # For a generic record, reads those fields when decoding needs them:
    # encoding writes a record's own fields, and naming needs only its class.
    fields: Callable[[], dict[str, _Node]] | None = None


@dataclasses.dataclass(eq=False, kw_only=True)
class _TagNode(_Node):
    tag: _Tag
    inner: _Node


@dataclasses.dataclass(eq=False, kw_only=True)
class _ItemsNode(_Node):
    """A list, set, or frozenset, or a bare ``tuple``, as ``kind`` says."""

    cls: type
    kind: str
    item: _Node | None


@dataclasses.dataclass(eq=False, kw_only=True)
class _ArrayNode(_Node):
    """``tuple[X, ...]``, a Java array."""

    item: _Node


@dataclasses.dataclass(eq=False, kw_only=True)
class _TupleNode(_Node):
    items: tuple[_Node, ...]


@dataclasses.dataclass(eq=False, kw_only=True)
class _MapNode(_Node):
    cls: type
    key: _Node | None
    value: _Node | None


@dataclasses.dataclass(eq=False, kw_only=True)
class _KeysNode(_Node):
    """A TypedDict: a type for each key, and the keys it requires."""

    cls: type
    fields: dict[str, _Node]
    required: tuple[str, ...]


@dataclasses.dataclass(eq=False, kw_only=True)
class _UnionNode(_Node):
    members: tuple[_Node, ...]


@dataclasses.dataclass(eq=False, kw_only=True)
class _RefNode(_Node):
    """A reference back to an annotation being read, as ``Tree`` in
    ``list[Tree]`` within ``Tree``."""


class _Reader:
    """Reads annotations into nodes; ``namespace`` resolves the string
    annotations of a TypedDict defined in a function."""

    def __init__(
        self, namespace: dict[str, Any] | None, inside: Iterable[Any] = ()
    ) -> None:
        self.namespace = namespace or {}
        # What the walk is inside of, and what it found itself inside of.
        self.open: list[Any] = list(inside)
        self.reached: list[Any] = []

    def read(self, raw: Any) -> _Node:
        bare, tag, _, aliases = _strip(raw)
        return self.stripped(raw, bare, tag, aliases)

    def stripped(
        self, raw: Any, bare: Any, tag: _Tag | None, aliases: list[Any]
    ) -> _Node:
        """Return the node of ``raw``, given what ``_strip`` returned for it."""
        origin = typing.get_origin(bare)
        cls = bare if origin is None else origin
        keys = list(aliases)
        if typing.is_typeddict(cls) or (
            origin is not None
            and getattr(cls, "__parameters__", ())
            and _is_record(cls)
        ):
            # A TypedDict, or a generic record with type arguments, such as
            # Page[int] or Node[int], can contain itself as well.
            keys.append(bare)
        if any(key in self.open for key in keys):
            self.reached.extend(key for key in keys if key in self.open)
            return _RefNode(raw=raw, bare=bare)
        self.open.extend(keys)
        try:
            node = self.inner(raw, bare, origin, cls)
        finally:
            del self.open[len(self.open) - len(keys) :]
        if tag is not None:
            node = _TagNode(raw=raw, bare=bare, tag=tag, inner=node)
        node.recurs = any(key in self.reached for key in keys)
        return node

    def inner(self, raw: Any, bare: Any, origin: Any, cls: Any) -> _Node:
        args = typing.get_args(bare)
        common = {"raw": raw, "bare": bare}
        if bare is None or bare is type(None):
            return _NoneNode(**common)
        if bare is Any or bare is object:
            return _AnyNode(**common)
        if origin in _UNIONS:
            return _UnionNode(**common, members=tuple(map(self.read, args)))
        if not isinstance(cls, type):
            if origin is not None:
                # shortcut: Literal and other special forms mean any value;
                # support them when an API needs them.
                return _AnyNode(**common)
            return _OtherNode(**common)
        if typing.is_typeddict(cls):
            # Before mappings: a TypedDict is a dict subclass.
            return self.typed_dict(common, cls, args)
        if cls is tuple and origin is not None and bare is not typing.Tuple:  # noqa: UP006
            if len(args) == 2 and args[1] is ...:
                return _ArrayNode(**common, item=self.read(args[0]))
            return _TupleNode(**common, items=tuple(map(self.read, args)))
        # Subclasses whose arguments are the item, or the key and value, too,
        # such as a defaultdict: hints apply to them.
        if (
            cls in _CONTAINERS
            or cls is tuple
            or (len(args) == 1 and issubclass(cls, (list, set, frozenset)))
        ):
            kind = "tuple" if cls is tuple else _CONTAINERS.get(cls, "list")
            item = self.read(args[0]) if args else None
            return _ItemsNode(**common, cls=cls, kind=kind, item=item)
        if cls in _MAPPINGS or (len(args) == 2 and issubclass(cls, dict)):
            key, value = map(self.read, args) if len(args) == 2 else (None, None)
            return _MapNode(**common, cls=cls, key=key, value=value)
        nodes = tuple(map(self.read, args))
        fields = None
        if args and getattr(cls, "__parameters__", ()) and _is_record(cls):
            # Read later as from here, so a field can refer back to this.
            inside, namespace = tuple(self.open), self.namespace
            fields = functools.partial(
                _Reader(namespace, inside).generic_fields, cls, args, nodes
            )
        return _ClassNode(**common, cls=cls, args=nodes, fields=fields)

    def typed_dict(
        self, common: dict[str, Any], cls: Any, args: tuple[Any, ...]
    ) -> _Node:
        resolved = _resolve(cls, self.namespace)
        # A generic TypedDict, such as Page[int], binds its parameters.
        bound = dict(zip(getattr(cls, "__parameters__", ()), args, strict=False))
        fields, required = {}, []
        for key, hint in resolved.items():
            given = _substitute(hint, bound)
            bare, tag, presence, aliases = _strip(given)
            fields[key] = self.stripped(given, bare, tag, aliases)
            # Where written, Required and NotRequired decide: under postponed
            # annotations __required_keys__ may not see them
            # (https://docs.python.org/3.13/library/typing.html#typing.TypedDict).
            if presence is typing.Required or (
                presence is not typing.NotRequired and key in cls.__required_keys__
            ):
                required.append(key)
        return _KeysNode(
            **common, cls=cls, fields=fields, required=tuple(sorted(required))
        )

    def generic_fields(
        self, cls: type, args: tuple[Any, ...], nodes: tuple[_Node, ...]
    ) -> dict[str, _Node]:
        """Return the nodes of the fields of ``cls[args]`` that use its type
        parameters, given the nodes of ``args``."""
        parameters = getattr(cls, "__parameters__", ())
        bound = dict(zip(parameters, args, strict=False))
        # A field typed by a parameter alone is its argument, read already
        # from the same place, so nesting stays linear.
        # shortcut: one that wraps a parameter, as list[T], is read again;
        # share nodes deeper if nesting that deep becomes common.
        read = dict(zip(parameters, nodes, strict=False))
        declared = _declaration(cls)
        local = {**self.namespace, **(declared.namespace if declared else {})}
        fields = {}
        for attr, hint in _class_hints(cls, local).items():
            given = _substitute(hint, bound)
            if isinstance(hint, typing.TypeVar) and hint in read:
                fields[attr] = read[hint]
            elif given is not hint:
                fields[attr] = self.read(given)
        return fields


def _read(tp: Any, namespace: dict[str, Any] | None = None) -> _Node:
    return _Reader(namespace).read(tp)


# How Java writes names in a Hessian list's type name, as "[string" for
# String[].
_WIRE_NAMES = {
    "java.lang.String": "string",
    "java.lang.Object": "object",
    "java.util.Date": "date",
    "[B": "[byte",
}


def _component(node: _Node) -> tuple[int, type | None, str | None]:
    """Return how Java names an array of ``node``'s component: the ``[``
    count of nested arrays, the class whose registration with a codec names
    it, and its Java name otherwise, or ``None`` for the class's own.
    ``bytes`` is ``byte[]``; a component with no one Java type, such as
    ``list`` or ``Any``, is ``Object``."""
    if isinstance(node, _TagNode):
        inner = node.inner
        cls = inner.cls if isinstance(inner, _ClassNode) else None
        return 0, cls, node.tag.java
    if isinstance(node, _UnionNode):
        members = [m for m in node.members if not isinstance(m, _NoneNode)]
        if len(members) != 1:
            return 0, None, "java.lang.Object"
        depth, cls, name = _component(members[0])
        # A primitive cannot hold None: int | None is an Integer.
        if depth == 0 and name is not None:
            name = _BOXES.get(name, name)
        return depth, cls, name
    if isinstance(node, _ArrayNode):
        depth, cls, name = _component(node.item)
        return depth + 1, cls, name
    cls = getattr(node, "cls", None)
    if cls is None or typing.is_typeddict(cls):
        return 0, None, "java.lang.Object"
    # A codec's registration names any class. Without one, an int item is
    # an int, a declared class has its own name, a JDK value type and its
    # subclasses the name they encode as, and one of several Java types or
    # a parameterized class is an Object.
    if cls is int:
        return 0, cls, "int"
    if _declaration(cls) is not None:
        return 0, cls, None
    if not isinstance(node, _ClassNode) or node.bare is not cls:
        return 0, cls, "java.lang.Object"
    found, name = _builtin_name(cls)
    return 0, cls, "java.lang.Object" if found and name is None else name


def _builtin_name(cls: type) -> tuple[bool, str | None]:
    """Return whether the encoder writes ``cls`` as a JDK or several Java
    types, and the Java name, or ``None`` for several."""
    for base in cls.__mro__:
        if base in _AMBIGUOUS or typing.is_typeddict(base):
            return True, None
        # The encoder maps Decimal and UUID by exact type only.
        if base in _JAVA_NAMES and (base is cls or base not in _EXACT_JAVA_NAMES):
            return True, _JAVA_NAMES[base]
    return False, None


def _plan(tp: Any, namespace: dict[str, Any] | None = None) -> Any:
    """Return the encoding hint for an annotation, as nested tuples, or
    ``None`` when it has no tags. ``namespace`` resolves the annotations of a
    TypedDict defined in a function.

    Raises:
        TypeError: If tags would not apply, such as tags inside a type that
            contains itself, which a hint cannot express.
    """
    return _hint_of(_read(tp, namespace))


def _hint_of(node: _Node) -> Any:
    plan = _hint_inner(node)
    if plan is not None and node.recurs:
        raise TypeError(
            f"{node.raw!r} contains itself and has tags or Java arrays, which "
            "apply at its first level only; move them out of the recursion"
        )
    return plan


def _hint_inner(node: _Node) -> Any:
    if isinstance(node, _TagNode):
        return node.tag.hint
    if isinstance(node, _UnionNode):
        plans: list[Any] = []
        untagged: set[str] = set()
        for member in node.members:
            plan = _hint_of(member)
            if isinstance(plan, tuple) and plan[0] == "union":
                plans.extend(plan[1])
            elif plan is not None:
                plans.append(plan)
            elif (kind := _value_kind(member)) is not None:
                untagged.add(kind)
        # A hint knows the kind of value it applies to, not which member it
        # came from, so a tagged member must be the only one of its kind:
        # in list[int] | tuple[Long, ...], the tags would reach lists too.
        # Equal unions in any order also share one cached hint.
        kinds = [_PLAN_KINDS[p if isinstance(p, str) else p[0]] for p in plans]
        if len(set(kinds)) < len(kinds) or untagged.intersection(kinds):
            raise TypeError(
                f"{node.bare!r} has tags on a member that matches the same values "
                "as another member; keep one member for each kind of value"
            )
        if len(plans) <= 1:
            return plans[0] if plans else None
        return ("union", tuple(plans))
    if isinstance(node, _ArrayNode):
        # Java writes an array as a list with its type name, which Go reads
        # by: Object[] is "[object", String[] "[string", Order[] "[com.x.Order".
        depth, cls, name = _component(node)
        wire = name if name is None else _WIRE_NAMES.get(name, name)
        base = cls if wire is None else wire if cls is None else (cls, wire)
        if depth == 1 and cls is int and name in ("int", "java.lang.Integer"):
            # An int[] item that does not fit an int would be cut short.
            return ("array", depth, base, "int")
        return ("array", depth, base, _hint_of(node.item))
    if isinstance(node, _ItemsNode):
        item = None if node.item is None else _hint_of(node.item)
        return None if item is None else ("items", item)
    if isinstance(node, _TupleNode):
        plans = [_hint_of(item) for item in node.items]
        return ("tuple", tuple(plans)) if any(p is not None for p in plans) else None
    if isinstance(node, _MapNode):
        key, value = (
            None if n is None else _hint_of(n) for n in (node.key, node.value)
        )
        return None if key is None and value is None else ("map", key, value)
    if isinstance(node, _KeysNode):
        keys = {
            key: plan
            for key, field in node.fields.items()
            if (plan := _hint_of(field)) is not None
        }
        return ("keys", keys) if keys else None
    if isinstance(node, _ClassNode) and any(
        _hint_of(arg) is not None for arg in node.args
    ):
        # Such as Box[Long]: the encoder reads Box's own field annotations.
        raise TypeError(
            f"tags in the arguments of {node.bare!r} do not apply; tag the "
            "annotations of its fields, or use list, tuple, set, or dict"
        )
    return None


def _value_kind(node: _Node) -> str | None:
    """Return the kind of value, as ``_PLAN_KINDS`` names it, that an
    untagged node describes and a hint could apply to, or ``None``."""
    cls = getattr(node, "cls", None)
    # A record class keeps its own form, even one that subclasses list.
    if cls is not None and _is_record(cls):
        return None
    if isinstance(node, (_ItemsNode, _ArrayNode, _TupleNode)):
        return "sequence"
    if isinstance(node, (_MapNode, _KeysNode)):
        return "dict"
    if not isinstance(node, _ClassNode) or issubclass(node.cls, bool):
        # A bool takes no int hint.
        return None
    cls = node.cls
    if issubclass(cls, int):
        return "int"
    if issubclass(cls, datetime.datetime):
        return "datetime"
    # The abstract collections read as containers, not classes.
    if issubclass(cls, (list, tuple, set, frozenset)):
        return "sequence"
    if issubclass(cls, dict):
        return "dict"
    return None


class _Recursion(Exception):
    """Raised where a type annotation reaches itself again."""


def _type_plan(tp: Any, namespace: dict[str, Any] | None = None) -> Any:
    """Return the decoding type for an annotation, as nested tuples, or
    ``None`` for any value. ``namespace`` resolves the annotations of a
    TypedDict defined in a function.

    A generic class with type arguments, such as ``Box[Address]``, types the
    fields that use its parameters with those arguments; without them, a
    parameter means its bound, or any value, as msgspec reads it.
    Protocols decode as any value: Python checks them by structure, not by
    the class a peer names.

    A type that contains itself, such as ``Node[int]`` within its own
    fields, refers back by annotation, ``("ref", annotation)``, at the
    nearest list item, map key or value, or field: the extension compiles
    and caches the annotation when a value reaches it, as pydantic's
    definition references do, so every level keeps its type.

    Raises:
        TypeError: If the annotation is a string, a union has several
            members that decode a Hessian list, or several that decode a
            map, or several classes declared with one Java name, or the
            annotation is a union that contains itself directly.
    """
    return _type_of(_read(tp, namespace), namespace)


def _type_of(node: _Node, namespace: dict[str, Any] | None) -> Any:
    """Return the decoding type of a node read from an annotation, as
    ``_type_plan`` does."""
    try:
        return _decoded(node, namespace or {})
    except _Recursion:
        raise TypeError(f"{node.raw!r} is a union that contains itself") from None


def _decoded(node: _Node, namespace: dict[str, Any]) -> Any:
    """Return the decoding type of a node, raising ``_Recursion`` where it
    refers back before reaching a value inside a container or object."""

    def child(node: _Node | None) -> Any:
        """Return the type of a value inside a container or object."""
        if node is None:
            return None
        try:
            return _decoded(node, namespace)
        except _Recursion:
            # The scope too: it resolves the string annotations of a TypedDict
            # defined in a function, as it did at this level.
            return ("ref", _Scoped(node.raw, namespace) if namespace else node.raw)

    if isinstance(node, _RefNode):
        raise _Recursion
    if isinstance(node, _TagNode):
        if node.tag.hint in _AWARE:
            # Decoded datetimes keep only whether they have a zone.
            return ("datetime", _AWARE[node.tag.hint])
        return _decoded(node.inner, namespace)
    if isinstance(node, _NoneNode):
        return "none"
    if isinstance(node, _OtherNode):
        if isinstance(node.bare, (str, typing.ForwardRef)):
            raise TypeError(
                f"cannot decode as the string annotation {node.bare!r}; "
                "pass the type itself"
            )
        return None
    if isinstance(node, _UnionNode):
        members: list[Any] = []
        for member in node.members:
            plan = _decoded(member, namespace)
            if plan is None:
                return None
            members.extend(plan[1] if plan[0] == "union" else (plan,))
        # As msgspec requires: a list or map alone cannot tell such members
        # apart, and equal unions in any order share one cached type.
        for kinds, what in ((("items", "tuple"), "list"), (("map", "keys"), "map")):
            if sum(isinstance(m, tuple) and m[0] in kinds for m in members) > 1:
                raise TypeError(
                    f"{node.bare!r} has several members that decode a Hessian "
                    f"{what}; keep one"
                )
        # The wire names one class, so one name must not mean two.
        names = [
            declared.name
            for m in members
            if m[0] == "is" and (declared := _declaration(m[1])) is not None
        ]
        if len(names) != len(set(names)):
            raise TypeError(
                f"{node.bare!r} has several classes declared with one Java class name"
            )
        return ("union", tuple(members))
    if isinstance(node, _ItemsNode) and (node.cls in _CONTAINERS or node.cls is tuple):
        return ("items", node.kind, child(node.item))
    if isinstance(node, _ArrayNode):
        return ("items", "tuple", child(node.item))
    if isinstance(node, _TupleNode):
        return ("tuple", tuple(map(child, node.items)))
    if isinstance(node, _MapNode) and node.cls in _MAPPINGS:
        return ("map", child(node.key), child(node.value))
    if isinstance(node, _KeysNode):
        fields = {key: child(field) for key, field in node.fields.items()}
        return ("keys", fields, node.required)
    cls = getattr(node, "cls", None)
    # typing marks protocol classes so; typing.is_protocol is 3.13+.
    if cls is None or getattr(cls, "_is_protocol", False):
        return None
    if isinstance(node, _ClassNode) and node.fields is not None:
        fields = node.fields()
        return ("is", cls, {attr: child(field) for attr, field in fields.items()})
    return ("is", cls)


class _Scoped:
    """An annotation with the namespace its TypedDicts resolve in, as a
    type reference or a ``Typed`` field value from ``as_object`` names it;
    equal ones share one cached type or hint."""

    __slots__ = ("annotation", "namespace")

    def __init__(self, annotation: Any, namespace: dict[str, Any]) -> None:
        self.annotation = annotation
        self.namespace = namespace

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, _Scoped)
            and self.annotation == other.annotation
            and self.namespace == other.namespace
        )

    def __hash__(self) -> int:
        return hash(self.annotation)

    def __repr__(self) -> str:
        return repr(self.annotation)


def _substitute(hint: Any, bound: dict[Any, Any]) -> Any:
    """Return ``hint`` with the type parameters in ``bound`` replaced."""
    if isinstance(hint, typing.TypeVar):
        return bound.get(hint, hint)
    parameters = getattr(hint, "__parameters__", ())
    if not any(parameter in bound for parameter in parameters):
        return hint
    return hint[tuple(bound.get(parameter, parameter) for parameter in parameters)]


def _is_record(cls: type) -> bool:
    """Return whether a class looks meant to encode as a Java object."""
    # The marker attrs.has reads, without a module another thread may be importing.
    return (
        dataclasses.is_dataclass(cls)
        or _is_named_tuple(cls)
        or issubclass(cls, enum.Enum)
        or getattr(cls, "__attrs_attrs__", None) is not None
    )


def _specs(
    fields: list[tuple[str, int, Any]],
    naming: str | None,
    hints: dict[str, Any],
    namespace: dict[str, Any],
) -> list[tuple[str, str, int, Any, Any, Any, Any, Any]]:
    """Add each field's wire name, encoding hint, decoding type, annotation,
    and the annotation with its namespace, if any, to compile it in."""
    camel = naming == "camelCase"
    specs = []
    for attr, kind, default in fields:
        plan = type_plan = annotation = scoped = None
        if attr in hints:
            annotation = hints[attr]
            node = _read(annotation, namespace)
            plan, type_plan = _hint_of(node), _type_of(node, namespace)
            # With its namespace, so a Typed in as_object's result compiles it
            # as the field did, while showing the annotation itself.
            scoped = _Scoped(annotation, namespace) if namespace else None
        name = _camel(attr) if camel else attr
        specs.append((attr, name, kind, default, plan, type_plan, annotation, scoped))
    return specs


def _is_named_tuple(cls: Any) -> bool:
    return isinstance(cls, type) and issubclass(cls, tuple) and hasattr(cls, "_fields")


def _tuple_fields(cls: Any) -> list[tuple[str, int, Any]]:
    defaults = cls._field_defaults
    return [
        (attr, _VALUE, defaults[attr]) if attr in defaults else (attr, _REQUIRED, None)
        for attr in cls._fields
    ]


def _fields(
    cls: type,
) -> tuple[list[tuple[str, int, Any]], tuple[str, tuple[Any, ...]] | None]:
    """Return a class's fields, and its post-init method with arguments."""
    if dataclasses.is_dataclass(cls):
        fields = []
        for field in dataclasses.fields(cls):
            if field.default is not dataclasses.MISSING:
                fields.append((field.name, _VALUE, field.default))
            elif field.default_factory is not dataclasses.MISSING:
                fields.append((field.name, _FACTORY, field.default_factory))
            else:
                fields.append((field.name, _REQUIRED, None))
        if not hasattr(cls, "__post_init__"):
            return fields, None
        return fields, ("__post_init__", _init_var_defaults(cls))
    attr = sys.modules.get("attr")
    if attr is not None and attr.has(cls):
        fields = []
        for field in attr.fields(cls):
            default = field.default
            if default is attr.NOTHING:
                fields.append((field.name, _REQUIRED, None))
            elif isinstance(default, attr.Factory):
                if default.takes_self:
                    raise TypeError(
                        f"{cls.__qualname__}.{field.name} uses a takes_self factory"
                    )
                fields.append((field.name, _FACTORY, default.factory))
            else:
                fields.append((field.name, _VALUE, default))
        if not hasattr(cls, "__attrs_post_init__"):
            return fields, None
        return fields, ("__attrs_post_init__", ())
    raise TypeError(f"{cls.__qualname__} is not {_KINDS}")


def _instance_new(cls: type) -> Callable[..., Any]:
    """Return the ``__new__`` that makes an instance of ``cls`` without
    running its code.

    ``object.__new__`` refuses classes whose layout a built-in base sets. An
    exception's built-in base, such as ``Exception`` or ``OSError``, holds
    only arguments and traceback state, so its ``__new__`` serves; other
    built-in bases, such as ``int`` or ``dict``, hold the value itself,
    which fields cannot rebuild.
    """
    if issubclass(cls, BaseExceptionGroup):

        def refuse(cls: type) -> Any:
            raise TypeError(
                f"{cls.__qualname__} cannot decode: an exception group cannot "
                "exist without the exceptions it groups"
            )

        return refuse
    if issubclass(cls, BaseException):
        # The first class Python defines in C, as copyreg finds it.
        base = next(b for b in cls.__mro__ if not b.__flags__ & _HEAPTYPE)
        return base.__new__
    return object.__new__


_HEAPTYPE = 1 << 9


def _init_var_defaults(cls: Any) -> tuple[Any, ...]:
    """Return the defaults of a dataclass's ``InitVar`` pseudo-fields.

    They are never sent, so decoding passes these to ``__post_init__``.
    """
    defaults = []
    for field in cls.__dataclass_fields__.values():
        # dataclasses marks InitVars this way since Python 3.7.
        if field._field_type is not dataclasses._FIELD_INITVAR:  # ty: ignore[missing-attribute]
            continue
        if field.default is dataclasses.MISSING:
            raise TypeError(
                f"{cls.__qualname__}.{field.name} is an InitVar without a default, "
                "so decoding has no value to pass to __post_init__"
            )
        defaults.append(field.default)
    return tuple(defaults)


def _camel(name: str) -> str:
    body = name.lstrip("_")
    first, *rest = body.split("_")
    return (
        name[: len(name) - len(body)]
        + first
        + "".join(part[:1].upper() + part[1:] for part in rest)
    )


_default = Codec()

# Bound methods of a codec without registered classes; no Python frame per call.
dumps = _default.dumps
loads = _default.loads
dumps_many = _default.dumps_many
as_object = _default.as_object
loads_many = _default.loads_many
reader = _default.reader
java_name = _default.java_name
