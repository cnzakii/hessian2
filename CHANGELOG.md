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
  type always encodes as.
- Python: `loads` and `loads_many` take `unknown_class=` to override the
  codec's setting for one call.

### Changed

- Python, breaking: `Codec.register(tp, *, name=None, field_naming=...,
  replace=False)` replaces `register(class_name, cls)` and its decorator
  form. It registers declared classes, follows field annotations and type
  expressions such as `list[Order]` to the declared classes they refer to,
  and registers all of them or none. An unresolvable annotation raises
  `TypeError`.
- Python, breaking: Field naming belongs to each class and defaults to
  camelCase, where `Codec()` previously sent Python names unchanged. Pass
  `field_naming=None` to `java_class` or `register` for the old names.
  `Codec(field_naming=)` and `Codec.field_naming` are removed.
- Python, breaking: A `ValueError` or `TypeError` raised by a converter,
  post-init method, default factory, or field getter is raised as
  `DecodeError` or `EncodeError`, with the original as its cause. Excess
  encoding depth raises `EncodeError` instead of `RecursionError`.

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
