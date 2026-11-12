# Changelog

User-visible changes to hessian2 are recorded here.

## Unreleased

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
