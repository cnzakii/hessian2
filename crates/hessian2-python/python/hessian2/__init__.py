"""Hessian 2.0 serialization with a Rust core."""

from __future__ import annotations

import collections.abc
import dataclasses
import datetime
import decimal
import enum
import sys
import types
import typing
import uuid
from collections.abc import Callable, Iterator
from typing import Annotated, Any, Literal, TypeVar, overload

from ._core import (
    DecodeError,
    EncodeError,
    HessianObject,
    __version__,
    _Codec,
    _Hint,
)

__all__ = [
    "BigInteger",
    "Codec",
    "Converter",
    "DecodeError",
    "EncodeError",
    "HessianObject",
    "Instant",
    "Long",
    "OffsetDateTime",
    "ZonedDateTime",
    "__version__",
    "dumps",
    "dumps_many",
    "java_class",
    "java_name",
    "loads",
    "loads_many",
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
Long = Annotated[int, _Tag("long", None)]
BigInteger = Annotated[int, _Tag("big_integer", "java.math.BigInteger")]
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

    Any codec encodes instances of the class without registering it; a
    codec decodes the class only once it is registered with
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
    builds only registered classes: the registrations are the classes that
    peer input may construct.

    Args:
        unknown_class: What decoding does with an object whose class is
            not registered: ``"raise"`` raises :class:`DecodeError`,
            ``"hessian_object"`` returns a :class:`HessianObject`, and
            ``"dict"`` returns its fields as a ``dict``. ``loads`` and
            ``loads_many`` can override it for one call.
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
        """Let this codec decode a class, and the classes its fields refer to.

        ``tp`` may be a class or a type expression such as ``list[Order]``
        or ``Cat | Dog``. Every class declared with :func:`java_class` that
        it, or the annotations of a registered class's fields, refer to is
        registered too; other types are ignored. Registering the same class
        under the same name again does nothing new.

        Args:
            tp: A declared class, a type expression, a class to register
                under ``name``, or a :class:`Converter`.
            name: The Java class name, for a class without a declaration or
                a converter. For a declared class it must match.
            field_naming: For a class registered under ``name``:
                ``"camelCase"`` (the default) sends ``user_name`` as
                ``userName``; ``None`` sends Python names unchanged.
            replace: Allow replacing what ``name`` or the class ``tp`` is
                already registered to, including the built-in mappings of
                JDK classes. Classes its fields refer to are never replaced.

        Raises:
            TypeError: If a class that needs a Java name has none, a class
                is not one that encodes as an object, an annotation cannot
                be resolved, or ``name``, ``field_naming``, or ``replace`` is
                given with a type expression.
            ValueError: If a name or class is registered to another, and
                ``replace`` is false; if ``name`` differs from the declared
                one; or if field naming maps several attributes to one wire
                name.

        Decoding builds instances without calling ``__init__``, as
        :mod:`pickle` does, then runs ``__post_init__`` or
        ``__attrs_post_init__`` if defined.
        """
        if isinstance(tp, Converter):
            if name is None:
                raise TypeError("register a Converter with name=, its Java class name")
            if field_naming is not _UNSET:
                raise TypeError("a Converter's to_fields chooses its field names")
            _check_name(name)
            entry = self._converter_entry(name, tp.cls, tp.to_fields, tp.from_fields)
            self._publish([(entry, replace)])
            return
        if not isinstance(tp, type) and (
            name is not None or field_naming is not _UNSET or replace
        ):
            raise TypeError(
                f"name=, field_naming=, and replace= need a class, not {tp!r}"
            )
        namespace = _local_types(sys._getframe(1))
        explicit = None
        if name is not None or field_naming is not _UNSET:
            declared = _declaration(tp)
            if declared is not None:
                if field_naming is not _UNSET:
                    raise TypeError(
                        f"{tp.__qualname__} takes its field naming from @java_class"
                    )
                if name != declared.name:
                    raise ValueError(
                        f"{tp.__qualname__} is declared as {declared.name!r}, "
                        f"not {name!r}"
                    )
            else:
                if name is None:
                    raise TypeError(
                        f"{tp.__qualname__} has no @hessian2.java_class; pass name="
                    )
                _check_name(name)
                naming = "camelCase" if field_naming is _UNSET else field_naming
                _check_naming(naming)
                explicit = _Declaration(name, naming, namespace)
        self._register_reachable(tp, explicit, namespace, replace)

    def java_name(self, tp: Any, /) -> str | None:
        """Return the Java class a type always encodes as, under this codec.

        The name is the one ``Class.getName`` gives, such as
        ``"java.lang.String"`` or ``"[B"``. Where the type corresponds to
        several Java types, as ``int`` does to ``int``, ``Integer``,
        ``long``, and ``Long``, or ``list`` to every Java collection, the
        result is ``None``.

        Args:
            tp: A type or type expression, such as ``Order | None``.

        Returns:
            The Java class name, or ``None``.

        Raises:
            TypeError: If ``tp`` is a class with no Java form under this
                codec.
        """
        tp = _unalias(tp)
        origin = typing.get_origin(tp)
        if origin is Annotated:
            tag = _tag(tp)
            # A registered type keeps its own form, whatever the tag says.
            if tag is None or (
                isinstance(tp.__origin__, type) and self._registered_name(tp.__origin__)
            ):
                return self.java_name(tp.__origin__)
            return tag.java
        if origin in _UNIONS:
            members = [arg for arg in typing.get_args(tp) if arg is not type(None)]
            return self.java_name(members[0]) if len(members) == 1 else None
        if isinstance(origin, type) and (
            _declaration(origin) is not None or self._registered_name(origin)
        ):
            return self.java_name(origin)
        if origin is not None or tp is Any or tp is object:
            return None
        if not isinstance(tp, type):
            raise TypeError(f"{tp!r} is not a type")
        registered = self._registered_name(tp)
        if registered is not None:
            return registered
        declared = _declaration(tp)
        if declared is not None:
            return declared.name
        for base in tp.__mro__:
            if base in _AMBIGUOUS or typing.is_typeddict(base):
                return None
            # The encoder maps Decimal and UUID by exact type only.
            if base in _JAVA_NAMES and (base is tp or base not in _EXACT_JAVA_NAMES):
                return _JAVA_NAMES[base]
        raise TypeError(
            f"{tp.__qualname__} has no Java class name; declare it with "
            "@hessian2.java_class or register it"
        )

    def _register_reachable(
        self,
        root: Any,
        explicit: _Declaration | None,
        namespace: dict[str, Any],
        replace: bool,
    ) -> None:
        """Register the classes ``root`` refers to, and those their fields do.

        Every entry is built and checked before any is registered, so a
        failure registers nothing.
        """
        entries: list[tuple[object, bool]] = []
        seen: set[type] = set()
        # Each annotation resolves where the class that holds it was defined.
        pending: list[tuple[Any, str | None, dict[str, Any]]] = [
            (root, None, namespace)
        ]
        while pending:
            annotation, referrer, local = pending.pop()
            for cls in _referenced(annotation, local):
                if cls in seen:
                    continue
                seen.add(cls)
                is_root = cls is root
                declared = explicit if is_root and explicit else _declaration(cls)
                if declared is None:
                    if _is_record(cls) and self._registered_name(cls) is None:
                        where = f"{referrer} refers to " if referrer else ""
                        raise TypeError(
                            f"{where}{cls.__qualname__}, which has no Java class "
                            "name; declare it with @hessian2.java_class or "
                            "register it with name="
                        )
                    continue
                if not is_root and self._registered_name(cls) == declared.name:
                    continue
                hints, entry = self._entry(cls, declared)
                entries.append((entry, replace and is_root))
                pending.extend(
                    (hint, f"{cls.__qualname__}.{field}", declared.namespace)
                    for field, hint in hints.items()
                )
        self._publish(entries)

    def _entry(
        self, cls: type, declared: _Declaration
    ) -> tuple[dict[str, Any], object]:
        """Return the field annotations of ``cls`` and the entry that
        encodes and decodes it as ``declared`` says, for ``_publish``."""
        name, naming = declared.name, declared.field_naming
        if issubclass(cls, enum.Enum):
            return {}, self._enum_entry(name, cls)
        if _is_named_tuple(cls):
            fields = _tuple_fields(cls)
            hints = _field_hints(cls, fields, declared.namespace)
            specs = _specs(fields, naming, hints, declared.namespace)
            return hints, self._tuple_entry(name, cls, specs)
        fields, post_init = _fields(cls)
        hints = _field_hints(cls, fields, declared.namespace)
        specs = _specs(fields, naming, hints, declared.namespace)
        return hints, self._class_entry(name, cls, specs, post_init)

    def _declared(self, cls: type) -> object:
        """Return the entry any codec encodes a declared class with.

        The encoder calls this on the first instance of a declared class
        that no codec has encoded yet. The entry is cached on the
        declaration, not in a registry, so the class still does not decode
        and nothing keeps it alive.
        """
        # The encoder calls this only for a class with its own declaration.
        declared = vars(cls)[_DECLARATION]
        _, declared.entry = self._entry(cls, declared)
        return declared.entry

    def _hint(self, tp: Any) -> _Hint:
        """Compile the encoding hint for an annotation; the extension caches it."""
        return _Hint(_plan(tp))


_UNIONS = (typing.Union, types.UnionType)
# The values each kind of plan applies to, for finding union conflicts.
_PLAN_KINDS = {
    "long": "int",
    "big_integer": "int",
    "instant": "datetime",
    "offset_datetime": "datetime",
    "zoned_datetime": "datetime",
    "items": "sequence",
    "tuple": "sequence",
    "map": "dict",
    "keys": "dict",
}
_SEQUENCES = frozenset(
    {
        list,
        set,
        frozenset,
        collections.abc.Sequence,
        collections.abc.MutableSequence,
        collections.abc.Set,
        collections.abc.MutableSet,
        collections.abc.Collection,
        collections.abc.Iterable,
    }
)
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
}
_EXACT_JAVA_NAMES = frozenset({decimal.Decimal, uuid.UUID})
# Types that encode as several Java types; datetime must precede date.
_AMBIGUOUS = frozenset(
    {
        int,
        bool,
        float,
        datetime.datetime,
        list,
        tuple,
        set,
        frozenset,
        dict,
        type(None),
        HessianObject,
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
        if isinstance(value, (type, *_TYPE_PARAMETERS, *_ALIASES))
        or typing.get_origin(value) is not None
    }


def _unalias(tp: Any) -> Any:
    while True:
        if isinstance(tp, _ALIASES):
            tp = tp.__value__
            continue
        origin = typing.get_origin(tp)
        if not isinstance(origin, _ALIASES):
            break
        # A subscripted generic alias, such as Pair[Long] for
        # ``type Pair[T] = list[T]``: substitute its arguments.
        value = origin.__value__
        # shortcut: a TypeVarTuple parameter does not pair one-to-one; rare.
        bound = dict(zip(origin.__type_params__, typing.get_args(tp), strict=False))
        if value in bound:
            tp = bound[value]
        else:
            # By identity: the value may use the parameters in another order,
            # or not all of them.
            used = getattr(value, "__parameters__", ())
            tp = value[tuple(bound[p] for p in used)] if used else value
    if origin in _QUALIFIERS:
        return _unalias(typing.get_args(tp)[0])
    return tp


def _tag(annotated: Any) -> _Tag | None:
    return next((m for m in annotated.__metadata__ if isinstance(m, _Tag)), None)


def _resolve(owner: Any, namespace: dict[str, Any]) -> dict[str, Any]:
    """Return the evaluated annotations of a class or TypedDict."""
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
    hints = _resolve(cls, namespace)
    return {attr: hints[attr] for attr, _, _ in fields if attr in hints}


def _plan(tp: Any, namespace: dict[str, Any] | None = None) -> Any:
    """Return the encoding hint for an annotation, as nested tuples, or
    ``None`` when it has no tags. ``namespace`` resolves the annotations of a
    TypedDict defined in a function.

    Raises:
        TypeError: If tags would not apply, such as tags inside a type that
            contains itself, which a hint cannot express.
    """
    namespace = namespace or {}
    recursive: list[Any] = []

    def walk(tp: Any, seen: tuple[Any, ...]) -> Any:
        own = _recursion_key(tp)
        if own is not None:
            if own in seen:
                recursive.append(own)
                return None
            seen = (*seen, own)
        plan = _walk_plan(tp, seen, walk, namespace)
        if plan is not None and own is not None and own in recursive:
            raise TypeError(
                f"{tp!r} contains itself and has tags, which apply at its "
                "first level only; move the tags out of the recursion"
            )
        return plan

    return walk(tp, ())


def _walk_plan(
    tp: Any,
    seen: tuple[Any, ...],
    walk: Callable[[Any, tuple[Any, ...]], Any],
    namespace: dict[str, Any],
) -> Any:
    tp = _unalias(tp)
    origin = typing.get_origin(tp)
    if origin is Annotated:
        tag = _tag(tp)
        if tag is not None:
            return tag.hint
        return walk(tp.__origin__, seen)
    args = typing.get_args(tp)
    if origin in _UNIONS:
        plans: list[Any] = []
        untagged: set[str] = set()
        for arg in args:
            plan = walk(arg, seen)
            if isinstance(plan, tuple) and plan[0] == "union":
                plans.extend(plan[1])
            elif plan is not None:
                plans.append(plan)
            elif (kind := _value_kind(arg)) is not None:
                untagged.add(kind)
        # A hint knows the kind of value it applies to, not which member it
        # came from, so a tagged member must be the only one of its kind:
        # in list[int] | tuple[Long, ...], the tags would reach lists too.
        # Equal unions in any order also share one cached hint.
        kinds = [_PLAN_KINDS[p if isinstance(p, str) else p[0]] for p in plans]
        if len(set(kinds)) < len(kinds) or untagged.intersection(kinds):
            raise TypeError(
                f"{tp!r} has tags on a member that matches the same values as "
                "another member; keep one member for each kind of value"
            )
        if len(plans) <= 1:
            return plans[0] if plans else None
        return ("union", tuple(plans))
    container = origin if isinstance(origin, type) else None
    # Subclasses too, such as defaultdict: the encoder applies hints to them.
    if (
        origin in _SEQUENCES
        or (container is not None and issubclass(container, (list, set, frozenset)))
        or (origin is tuple and len(args) == 2 and args[1] is ...)
    ):
        item = walk(args[0], seen) if args else None
        return None if item is None else ("items", item)
    if origin is tuple:
        plans = tuple(walk(arg, seen) for arg in args)
        return ("tuple", plans) if any(p is not None for p in plans) else None
    if (
        origin in _MAPPINGS or (container is not None and issubclass(container, dict))
    ) and len(args) == 2:
        key, value = (walk(arg, seen) for arg in args)
        return None if key is None and value is None else ("map", key, value)
    if typing.is_typeddict(tp):
        keys = {
            key: plan
            for key, annotation in _resolve(tp, namespace).items()
            if (plan := walk(annotation, seen)) is not None
        }
        return ("keys", keys) if keys else None
    if any(walk(arg, seen) is not None for arg in args):
        # Such as Box[Long]: the encoder reads Box's own field annotations.
        raise TypeError(
            f"tags in the arguments of {tp!r} do not apply; tag the annotations "
            "of its fields, or use list, tuple, set, or dict"
        )
    return None


def _value_kind(tp: Any) -> str | None:
    """Return the kind of value, as ``_PLAN_KINDS`` names it, that an
    untagged annotation describes and a hint could apply to, or ``None``."""
    tp = _unalias(tp)
    origin = typing.get_origin(tp)
    if origin is Annotated:
        return _value_kind(tp.__origin__)
    cls = origin if isinstance(origin, type) else tp
    if typing.is_typeddict(cls):
        return "dict"
    # A bool takes no int hint, and a record class keeps its own form.
    if not isinstance(cls, type) or issubclass(cls, bool) or _is_record(cls):
        return None
    if issubclass(cls, int):
        return "int"
    if issubclass(cls, datetime.datetime):
        return "datetime"
    if cls in _SEQUENCES or issubclass(cls, (list, tuple, set, frozenset)):
        return "sequence"
    if cls in _MAPPINGS or issubclass(cls, dict):
        return "dict"
    return None


def _recursion_key(tp: Any) -> Any:
    """Return what a walk must not enter twice: a type alias or TypedDict."""
    if isinstance(tp, _ALIASES) or isinstance(typing.get_origin(tp), _ALIASES):
        return tp
    return tp if typing.is_typeddict(tp) else None


def _referenced(
    tp: Any, namespace: dict[str, Any], seen: tuple[Any, ...] = ()
) -> Iterator[type]:
    """Yield the classes a type expression refers to."""
    own = _recursion_key(tp)
    if own is not None:
        if own in seen:
            return
        seen = (*seen, own)
    tp = _unalias(tp)
    origin = typing.get_origin(tp)
    if origin is Annotated:
        yield from _referenced(tp.__origin__, namespace, seen)
    elif origin is not None:
        if isinstance(origin, type):
            # Such as Box in Box[Order]; built-in origins have no declaration.
            yield origin
        for arg in typing.get_args(tp):
            yield from _referenced(arg, namespace, seen)
    elif typing.is_typeddict(tp):
        for annotation in _resolve(tp, namespace).values():
            yield from _referenced(annotation, namespace, seen)
    elif isinstance(tp, type):
        yield tp


def _is_record(cls: type) -> bool:
    """Return whether a class looks meant to encode as a Java object."""
    attr = sys.modules.get("attr")
    return (
        dataclasses.is_dataclass(cls)
        or _is_named_tuple(cls)
        or issubclass(cls, enum.Enum)
        or (attr is not None and attr.has(cls))
    )


def _specs(
    fields: list[tuple[str, int, Any]],
    naming: str | None,
    hints: dict[str, Any],
    namespace: dict[str, Any],
) -> list[tuple[str, str, int, Any, Any]]:
    """Add each field's wire name and encoding hint."""
    camel = naming == "camelCase"
    return [
        (
            attr,
            _camel(attr) if camel else attr,
            kind,
            default,
            _plan(hints[attr], namespace) if attr in hints else None,
        )
        for attr, kind, default in fields
    ]


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
loads_many = _default.loads_many
java_name = _default.java_name
