"""Hessian 2.0 serialization with a Rust core."""

from __future__ import annotations

import dataclasses
import enum
import functools
import sys
from collections.abc import Callable
from typing import Any, TypeVar, overload

from ._core import DecodeError, EncodeError, HessianObject, __version__, _Codec

__all__ = [
    "Codec",
    "Converter",
    "DecodeError",
    "EncodeError",
    "HessianObject",
    "__version__",
    "dumps",
    "dumps_many",
    "loads",
    "loads_many",
]

_T = TypeVar("_T")

_REQUIRED, _VALUE, _FACTORY = 0, 1, 2
_KINDS = (
    "cls must be a dataclass, an attrs class, a named tuple, an Enum, or a Converter"
)


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

    For example, a pydantic model, validated as it decodes::

        codec.register(
            "com.example.User",
            Converter(User, to_fields=User.model_dump, from_fields=User.model_validate),
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

    Args:
        field_naming: ``"camelCase"`` sends a field named ``user_name`` as
            ``userName``; ``None`` sends Python names unchanged.
        unknown_class: What decoding does with an object whose class is
            not registered: ``"raise"`` raises :class:`DecodeError`,
            ``"hessian_object"`` returns a :class:`HessianObject`, and
            ``"dict"`` returns its fields as a ``dict``.
        max_depth: How deeply lists, maps, and objects may nest. Deeper input
            raises :class:`DecodeError`, deeper values :class:`RecursionError`.
            Each level needs stack space; raise it only on threads with a
            large stack.
    """

    @overload
    def register(
        self, class_name: str, *, replace: bool = False
    ) -> Callable[[_T], _T]: ...
    @overload
    def register(self, class_name: str, cls: _T, *, replace: bool = False) -> _T: ...
    def register(
        self, class_name: str, cls: Any = None, *, replace: bool = False
    ) -> Any:
        """Map the Hessian ``class_name`` to a Python class.

        Args:
            class_name: The Java class name, such as ``"com.example.User"``.
            cls: A dataclass, an attrs class, a named tuple, an
                :class:`enum.Enum`, or a :class:`Converter` for any other
                type. Omitted, ``register`` returns a class decorator.
            replace: Allow replacing what ``class_name`` or ``cls`` is
                already registered to, including the built-in mappings of
                JDK classes.

        Returns:
            ``cls``, so ``register`` also works as a decorator.

        Raises:
            TypeError: If ``cls`` is none of those.
            ValueError: If ``class_name`` is registered to another type, or
                ``cls`` to another name, and ``replace`` is false; or if
                field naming maps multiple attributes to the same wire name.

        Registering the same class under the same name again updates its
        mapping. Decoding builds instances without calling ``__init__``, as
        :mod:`pickle` does, then runs ``__post_init__`` or
        ``__attrs_post_init__`` if defined.
        """
        if cls is None:
            return functools.partial(self.register, class_name, replace=replace)
        if isinstance(cls, Converter):
            self._register_converter(
                class_name, cls.cls, cls.to_fields, cls.from_fields, replace
            )
        elif isinstance(cls, type) and issubclass(cls, enum.Enum):
            self._register_enum(class_name, cls, replace)
        elif _is_named_tuple(cls):
            self._register_tuple(
                class_name, cls, self._specs(_tuple_fields(cls)), replace
            )
        elif isinstance(cls, type):
            fields, post_init = _fields(cls)
            self._register_class(
                class_name, cls, self._specs(fields), post_init, replace
            )
        else:
            raise TypeError(f"{_KINDS}, not {cls!r}")
        return cls

    def _specs(
        self, fields: list[tuple[str, int, Any]]
    ) -> list[tuple[str, str, int, Any]]:
        """Add each field's wire name under this codec's naming rule."""
        camel = self.field_naming == "camelCase"
        return [
            (attr, _camel(attr) if camel else attr, kind, default)
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
    raise TypeError(f"{_KINDS}, not {cls.__qualname__}")


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
