# Changelog

User-visible changes to hessian2 are recorded here.

## Unreleased

### Added

- Python: `@hessian2.java_class(name)` declares a class's Java name on the
  class. Every codec encodes declared classes without registration.
- Python: `Long`, `BigInteger`, `Instant`, `OffsetDateTime`, and
  `ZonedDateTime` annotations write an `int` or aware `datetime` as that Java
  type, in fields, collection items, dict and TypedDict values, and top-level
  values given with `dumps(..., type=)` or `dumps_many(..., types=)`. Java
  peers previously rejected small ints in `BigInteger` fields and datetimes
  in `java.time` fields.
- Python: A list, set, or dict seen again under different tags is written
  again instead of as a reference, so each position gets its own Java types.
- Python: `Codec.java_name` and `hessian2.java_name` return the Java class a
  type always encodes as: the primitive for `float`, `bool`, and `Long`,
  such as `"double"`, and its box with `| None`, such as `"java.lang.Long"`.
- Python: `loads` and `loads_many` take `unknown_class=` to override the
  codec's setting for one call.
- Python: `loads(..., type=)` and `loads_many(..., types=)` decode values as
  annotations type them, as msgspec does: tuples, sets, frozensets, and the
  classes an annotation names are built, with no registration needed, and a
  value of another type raises `DecodeError`. A union picks its class by
  the Java class name; a position annotated with a base class takes
  registered subclasses. Type arguments, as in `Result[Order]`, type the
  fields that use the class's parameters, at every level of a type that
  contains itself, such as `Node[int]`. With `types=`, `loads_many`
  requires exactly that many values.
- Python: `Codec.reader(data)` and `hessian2.reader(data)` return a
  `Reader` that decodes one value per `load(type=...)`, sharing the stream's
  class definitions and references, as Java's `Hessian2Input` reads; the
  type of a value can depend on the values before it. `tell()` gives the
  offset of the next value.
- Python: `hessian2.Typed(value, annotation)` writes a value as a value with
  that annotation, in positions that have none, such as dict values and list
  items: `Typed(7, hessian2.Long)` is a Java long anywhere. A `Typed` cannot
  hold another, and neither it nor `HessianObject` can have a converter.
- Python: Encoding, decoding, and `java_name` read every annotation alike,
  from one reading of it: a `NewType` as its supertype, a type variable as
  its bound, or as any value without one, and a generic TypedDict such as
  `Page[Long]` with both its own tags and its arguments; each generic base
  binds its own type parameters, so bases can reuse one `TypeVar`.
- Python: `Codec.as_object` and `hessian2.as_object` return the
  `HessianObject` that `dumps` writes for an object, one level deep and
  without encoding: its Java class name and fields by wire name, with
  `Typed` values where a field's annotation changes how it is written, so
  rebuilding the fields as a map keeps their Java types.
- Python: `Date` and `LocalDateTime` annotations name the Java type of a
  `datetime` position, which `java_name` returns, and reject a datetime of
  the other kind, aware or naive. Bytes are as without them.
- Python: Decoding with `Date`, `LocalDateTime`, `Instant`,
  `OffsetDateTime`, or `ZonedDateTime` checks that the datetime is aware,
  or for `LocalDateTime` naive.
- Python: A map decodes as the one class its annotation names, filled by
  field name, as Java's `Hessian2Input.readObject(Class)` reads one; the
  map's keys never choose the class.
- Python, breaking: A map key that no annotation types decodes only when a
  peer cannot make it collide with others, which makes a dict take
  quadratic time: a `str`, `bytes`, `bool`, `None`, `float`, a 64-bit
  `int`, a date or time, or an enum member, as msgpack-python allows only
  `str` and `bytes` keys by default. Other keys, such as lists,
  `BigDecimal`, `UUID`, or objects, need an annotation such as
  `dict[UUID, str]`.
- Python, breaking: A back-reference is checked against its annotation
  once per stream, not again after post-init methods, default factories,
  or converters run, or in a later `Reader.load`; values application code
  changes are its own.
- Python: A position annotated with a Java enum decodes a map by its
  `"name"` key, as Java's `EnumDeserializer` reads one. A `StrEnum` or
  `IntEnum` with no Java class name decodes from its value, as it is
  written.
- Python, breaking: `tuple[X, ...]` annotations write Java arrays, as Java
  does: a list with the array's type name, such as `[string` for `String[]`
  and `[object` for `Object[]`, which Go needs to read `[]hessian.Object`.
  `tuple[int, ...]` items must fit a Java `int`. A codec's registration
  for the items' type, such as a converter for `int`, names the array, as
  it names each item. `java_name` gives the array's class, such as
  `"[Ljava.lang.String;"`. Annotate `list[X]` for a
  Java `List`: a Java `Object` position reads an array as an array.
- Python: A `DecodeError` says where in the value it happened, such as
  ``- at `$.address.city` ``.

### Changed

- Python: A stream defines each class name and field list once, whether a
  registered or declared class, a `HessianObject`, a converter, or a
  built-in value writes it first, as Java writes one definition per class.
- Python, breaking: `Codec.register(cls, *, name=None, field_naming=...,
  replace=False)` replaces `register(class_name, cls)` and its decorator
  form. It trusts one declared class, or a class it names, wherever its
  type fits: in positions that allow any value and in positions annotated
  with a base class. Classes that fields name need no registration.
- Python, breaking: Field naming belongs to each class and defaults to
  camelCase, where `Codec()` previously sent Python names unchanged. Pass
  `field_naming=None` to `java_class` or `register` for the old names.
  `Codec(field_naming=)` and `Codec.field_naming` are removed.
- Python, breaking: Fields of registered and declared classes decode as
  their annotations type them, with or without `type=`. A `tuple` or `set`
  field receives a `tuple` or `set` instead of a `list`, and a value that
  does not match the annotation, such as `null` in a field not annotated
  `X | None`, raises `DecodeError`.
- Python, breaking: Fields a class does not have, keys a TypedDict does not
  declare, and the fields of JDK values decode as plain data: an object
  there is a `HessianObject`, never a registered class, so peer input runs
  no class's code where no annotation asks for one.
- Python, breaking: A `ValueError` or `TypeError` raised by a converter,
  post-init method, default factory, or field getter is raised as
  `DecodeError` or `EncodeError`, with the original as its cause. Excess
  encoding depth raises `EncodeError` instead of `RecursionError`.

### Fixed

- Python: A dataclass or attrs subclass of `Exception`, including `OSError`
  and its subclasses, decodes instead of raising
  `TypeError: object.__new__(...) is not safe`.
- Python: Decoding nested objects uses less stack, about 1 KiB per level:
  on a 512 KiB thread stack, a chain of annotated objects decodes to the
  default `max_depth` of 256 and fails with `DecodeError` past it.

## [0.1.0] - 2026-11-12

### Added

- Python: Hessian 2.0 serialization through `dumps`, `loads`, `dumps_many`,
  and `loads_many`, with support for CPython 3.11–3.15 and free-threaded
  CPython 3.14/3.15.
- Python: Per-Codec registration of dataclasses, attrs classes, named tuples,
  and enums. Field names can be converted to camelCase; missing fields use
  defaults, and unknown fields are consumed and ignored.
- Python: `Converter` callbacks for other types, including Pydantic models.
  Encoding calls `to_fields` once per object per stream. A Converter value
  cannot be referenced back to while its fields are being decoded.
- Python: Nested objects and shared references, including cycles through
  lists, dictionaries, dataclass/attrs instances, and `HessianObject` values.
  Registered named tuples reject cycles during encoding.
- Python: Built-in mappings for large integers, Decimal, UUID, datetime,
  date, time, and timedelta, with Java-compatible representations.
- Python: `HessianObject` for values without registered Python classes,
  configurable unknown-class handling, explicit registration replacement,
  nesting limits, and typed public APIs.
- Rust: The independent `hessian2-core` crate provides a pull `Reader`,
  `Writer`, per-stream class/type/reference tables, and UTF-8/CESU-8
  conversion. The Python binding also preserves unpaired surrogates.
