---
title: Hessian 2.0 specification notes and errata
description: What the Hessian 2.0 serialization draft defines, where its grammar, prose, and bytecode map disagree, and how Caucho's reference implementation reads each conflict.
topics: [hessian, specification, errata, wire-format]
checked_at: 2026-11-11
---

# Hessian 2.0 Specification Notes And Errata

Sources:

- Specification: [Hessian 2.0 Serialization Protocol](hessian-serialization.html),
  Internet-Draft, August 2007 (expired February 2008, never published as an
  RFC). Section numbers below refer to it.
- Reference implementation: `com.caucho:hessian:4.0.66`, sources jar from Maven
  Central (SHA-256 `f67be7845d6148ce840a7aeb4b060cb3d67860a705cc28bd4109a5f29d1f127f`),
  package `com.caucho.hessian.io`.

## What The Specification Fixes

- Eight primitive types (binary, boolean, date, double, int, long, null,
  string), three recursive types (list, map, object), and `ref` (§4).
- Big-endian integers throughout; dates are UTC milliseconds since the epoch,
  or whole minutes in the compact form (§4.3).
- String lengths count 16-bit characters, not bytes, and "string chunks may not
  split surrogate pairs" (§4.12).
- Three per-stream tables (§5): values (list, map, object, in the order they are
  encountered, before their contents, so refs may point at incomplete values,
  §4.11), class definitions (§5.2), and list/map type names (§5.3).
- "Writers MAY generate refs. Parsers MUST be able to recognize them" (§4.11).
- "Any parser expecting a list [or map] must also accept a null or a shared
  ref" (§4.6, §4.8).
- Type strings are application-defined; a dynamically typed reader may ignore
  them (§4.6).

## Internal Conflicts

The draft has three descriptions of each byte form: the grammar (§3), the
per-type sections (§4), and the bytecode map (§6). They disagree here:

| Topic | Grammar §3 | Section §4 | Bytecode map §6 | Reference implementation |
|---|---|---|---|---|
| `0x5f` double | 32-bit float cast to double | float (§4.4.5) | double as float | 32-bit int of thousandths: `0.001 * mills` (`Hessian2Input.java:1067`, `Hessian2Output.java:845-852`) |
| 32-bit long | `x59` | `x4c` (§4.7, §4.7.4) | `x59` | `BC_LONG_INT = 0x59` (`Hessian2Constants.java:113`) |
| Binary non-final chunk | `x41` | `b` / `x62` (§4.1) | `x41` (`A`) | `BC_BINARY_CHUNK = 'A'` (`Hessian2Constants.java:54`) |
| Compact date | `x4b` + 4 bytes | `x4b` + 5 bytes (§4.3), prose says 32-bit | 32-bit minutes | 4 bytes (`Hessian2Constants.java:63`) |
| Medium string tags | `x30`-`x34` | `x30`-`x33` (§4.12) | `x30`-`x33` | `x30`-`x33`; `x34` is binary (`Hessian2Constants.java:133`) |
| Short string length | 0-31 | under 32 (§4.12.1) | 0-32 | 0-31 (`STRING_DIRECT_MAX = 0x1f`) |
| Short binary length | 0-15 | under 15 (§4.1.1) | 0-16 | 0-15 (`BINARY_DIRECT_MAX = 0x0f`) |
| Chunk size | "64k chunks" | 16-bit length | — | writers choose; strings use `0x8000` (`Hessian2Output.java:985`) |

The `0x5f` conflict is the one that changes values: a reader that follows the
draft text decodes `5f 00 00 00 01` as a float bit pattern; Caucho and every
implementation listed under [Java](../implementations/java.md) and
[Go](../implementations/go.md) decode it as `0.001`.

## Errors In Examples

- §4.10.3 Figure 25 shows a stray `x90 # object definition ref #0` inside the
  second enum object; the class definition is already selected by `x60`.
- §4.11.1 Figure 27 uses `o`, the Hessian 1.0 object tag, which is not in the
  2.0 grammar.
- §4.11 says "the first list or map is '0'"; §5.1 includes objects in the same
  numbering, as does the reference implementation.
- §4.3.2 Figure 8 encodes 09:51:00 May 8, 1998 UTC as `x4b x4b x92 x0b xa0`;
  that instant is 14910351 minutes after the epoch, `x4b x00 xe3 x83 x8f`.
  The millisecond example in Figure 7 is correct.
- §6 gives the double-short range as `-32768.0 to 327676.0`; §4.4.4 gives
  `32767.0`.

## Silent Points

The draft does not say:

- how a writer chooses between equivalent forms (for example `I` versus a
  compact int);
- whether the tables reset between top-level values (Caucho keeps them until
  `reset()`, see [Java](../implementations/java.md#per-stream-tables));
- how to encode characters outside the BMP (UTF-8 four-byte sequences versus
  surrogate pairs);
- what a reader does with duplicate map keys, unknown object fields beyond
  "unrecognized keys will be ignored" for map-encoded objects (§4.8), or
  unpaired surrogates;
- any limit on nesting depth or length.
