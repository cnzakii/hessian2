from collections.abc import Callable, Iterable
from typing import Any, ClassVar, Final, Literal, Self, final

from _typeshed import ReadableBuffer

__version__: Final[str]

class DecodeError(ValueError): ...
class EncodeError(TypeError): ...

@final
class HessianObject:
    __match_args__ = ("class_name", "fields")
    @property
    def class_name(self) -> str: ...
    @property
    def fields(self) -> dict[str, Any]: ...
    def __init__(
        self, class_name: str, fields: dict[str, Any] | None = None
    ) -> None: ...
    def __eq__(self, other: object, /) -> bool: ...
    __hash__: ClassVar[None]  # type: ignore[assignment]

@final
class _Hint:
    def __new__(cls, plan: object, /) -> Self: ...

class _Codec:
    def __new__(
        cls,
        *,
        unknown_class: Literal["raise", "hessian_object", "dict"] = "raise",
        max_depth: int = 256,
    ) -> Self: ...
    @property
    def unknown_class(self) -> Literal["raise", "hessian_object", "dict"]: ...
    @property
    def max_depth(self) -> int: ...
    def dumps(self, /, obj: Any, *, type: Any = None) -> bytes: ...
    def dumps_many(
        self, /, values: Iterable[Any], *, types: Iterable[Any] | None = None
    ) -> bytes: ...
    def loads(
        self,
        /,
        data: ReadableBuffer,
        *,
        unknown_class: Literal["raise", "hessian_object", "dict"] | None = None,
    ) -> Any: ...
    def loads_many(
        self,
        /,
        data: ReadableBuffer,
        *,
        unknown_class: Literal["raise", "hessian_object", "dict"] | None = None,
    ) -> list[Any]: ...
    def _registered_name(self, ty: type, /) -> str | None: ...
    @staticmethod
    def _class_entry(
        name: str,
        cls: type,
        fields: list[tuple[str, str, int, Any, Any]],
        post_init: tuple[str, tuple[Any, ...]] | None,
        /,
    ) -> object: ...
    @staticmethod
    def _tuple_entry(
        name: str, cls: type, fields: list[tuple[str, str, int, Any, Any]], /
    ) -> object: ...
    @staticmethod
    def _enum_entry(name: str, cls: type, /) -> object: ...
    @staticmethod
    def _converter_entry(
        name: str,
        type: type,
        encode: Callable[[Any], dict[str, Any]],
        decode: Callable[[dict[str, Any]], Any],
        /,
    ) -> object: ...
    def _publish(self, entries: list[tuple[object, bool]], /) -> None: ...
