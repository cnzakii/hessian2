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

class _Codec:
    def __new__(
        cls,
        *,
        field_naming: Literal["camelCase"] | None = None,
        unknown_class: Literal["raise", "hessian_object", "dict"] = "raise",
        max_depth: int = 256,
    ) -> Self: ...
    @property
    def field_naming(self) -> Literal["camelCase"] | None: ...
    @property
    def unknown_class(self) -> Literal["raise", "hessian_object", "dict"]: ...
    @property
    def max_depth(self) -> int: ...
    def dumps(self, /, obj: Any) -> bytes: ...
    def dumps_many(self, /, values: Iterable[Any]) -> bytes: ...
    def loads(self, /, data: ReadableBuffer) -> Any: ...
    def loads_many(self, /, data: ReadableBuffer) -> list[Any]: ...
    def _register_class(
        self,
        name: str,
        cls: type,
        fields: list[tuple[str, str, int, Any]],
        post_init: tuple[str, tuple[Any, ...]] | None,
        replace: bool,
        /,
    ) -> None: ...
    def _register_tuple(
        self,
        name: str,
        cls: type,
        fields: list[tuple[str, str, int, Any]],
        replace: bool,
        /,
    ) -> None: ...
    def _register_enum(self, name: str, cls: type, replace: bool, /) -> None: ...
    def _register_converter(
        self,
        name: str,
        type: type,
        encode: Callable[[Any], dict[str, Any]],
        decode: Callable[[dict[str, Any]], Any],
        replace: bool,
        /,
    ) -> None: ...
