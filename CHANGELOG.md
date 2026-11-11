# Changelog

User-visible changes are recorded here before each release. No public release
has been published from this repository yet.

## Unreleased

### Python

- Hessian 2.0 encoding and decoding through `dumps`, `loads`, and multi-value
  streams, with explicit per-Codec registration.
- Dataclass, attrs, named tuple, Enum, Converter, and standard-library mappings.
- Nested objects, shared references, configurable unknown-class handling,
  and free-threaded CPython support.
- GC-visible registration snapshots and unknown objects; replacing an old
  decode alias no longer removes the current encoder mapping.
- Converter field callbacks run once per object per stream. Explicit converters
  also override native scalar/container encoding; cycles through a Converter
  still being decoded raise `DecodeError`.
- Type stubs accept the keyword arguments supported by the four stream methods.
- Rename `on_unknown_class` to `unknown_class`. Ambiguous field names after
  naming conversion fail at registration.

### Rust

- Pull Reader and Writer with per-stream class, type, and reference tables.
- Faster string chunk scans and direct UTF-8/CESU-8 conversion; unpaired
  surrogates remain supported by the Python binding.
- Remove `text::utf16_len`; add `text::cesu8_to_utf8`,
  `text::contains_surrogate`, and `text::push_cesu8_str`.
- Both crates forbid unsafe code.
