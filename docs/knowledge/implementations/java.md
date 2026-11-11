---
title: Java Hessian implementations
description: Behavior of Caucho's reference implementation and of hessian-lite beyond the specification text, including writer form choice, per-stream tables, strings, value objects, and the official conformance suite.
topics: [hessian, java, interop, strings, conformance]
checked_at: 2026-11-11
---

# Java Hessian Implementations

Sources (package `com.caucho.hessian.io` unless noted):

- Reference implementation: `com.caucho:hessian:4.0.66` sources jar, Maven
  Central (SHA-256 `f67be7845d6148ce840a7aeb4b060cb3d67860a705cc28bd4109a5f29d1f127f`).
- Observed practice: `org.apache.dubbo:hessian-lite:4.0.5` sources jar, Maven
  Central (SHA-256 `054be1f05c121d861985849b0172e5dcd36d27a495f075667a7716ce7c907a9e`),
  package `com.alibaba.com.caucho.hessian.io`, a fork of Caucho's code.
  Source repository: [apache/dubbo-hessian-lite](https://github.com/apache/dubbo-hessian-lite).

Both share the same reader and writer structure. Line numbers are given as
Caucho / hessian-lite.

## Writer Form Choice

- Ints, longs, and lengths use the shortest form the grammar allows.
- `writeDouble` tries, in order: `(int) value == value` gives `0x5b`, `0x5c`,
  `0x5d`, or `0x5e`; then `0.001 * (int)(value * 1000) == value` gives `0x5f`;
  otherwise `D` (`Hessian2Output.java:809-852` / `748-783`).
- Because the integral test is `(int) value == value`, `-0.0` is written as
  `0x5b` and its sign is lost.
- `writeUTCDate` writes `x4b` minutes when `time % 60000 == 0`, otherwise `x4a`
  milliseconds (`Hessian2Output.java:893`).
- `writeListBegin` uses `x70`-`x7f` up to length 7, `V`/`X` with an int length
  otherwise, and `U`/`W` with `Z` when the length is unknown.
- `MapSerializer` writes `java.util.HashMap` as an untyped map (`H`) and any
  other map class with its class name as the type (`M`), for example
  `java.util.LinkedHashMap` or `java.util.Hashtable`
  (Caucho `MapSerializer.java:88-108`).
- `java.lang.Float` differs between the two: Caucho writes an object of class
  `com.caucho.hessian.io.FloatHandle` with one double field `_value`
  (`BasicSerializer.java:328`); hessian-lite writes the double
  `Double.parseDouble(String.valueOf(f))`, the shortest decimal of the float
  read back as a double (hessian-lite `BasicSerializer.java:123`).
- Both write the fields of a bean class in the order their introspection
  returns, which differs between them: for `com.caucho.hessian.test.TestCons`
  Caucho writes `_first, _rest` and hessian-lite `_rest, _first` (observed by
  running both releases).
- On JDK 17, Caucho's generic serializer cannot write the immutable
  collections of `List.of` and `Map.of`: introspection fails with
  `InaccessibleObjectException` because `java.util` is not open.

## Per-Stream Tables

- `Hessian2Output` keeps the value, class, and type tables across `writeObject`
  calls until `reset()` (`Hessian2Output.java:1688` / `1557-1563`), so several
  top-level values in one stream share definitions and may refer to each other.
- Every list, map, and object takes one value-table slot in the order it
  starts.
- `writeType` writes a type name as a string the first time and as an int
  afterwards.

## Strings

- The writer emits each UTF-16 unit separately, so a supplementary character
  becomes two three-byte surrogate sequences (CESU-8), and an unpaired
  surrogate becomes one three-byte sequence.
- `parseUTF8Char` accepts one-, two-, and three-byte sequences and rejects
  lead bytes `0xf0` and above with "bad utf-8 encoding"
  (`Hessian2Input.java:2632` / `3413`). A peer that sends four-byte UTF-8 is
  rejected.
- `writeString` splits strings longer than `0x8000` units into `R` chunks and
  shortens a chunk by one when it would end on a high surrogate
  (`Hessian2Output.java:985-998` / `915-929`).
- After a non-final chunk the reader accepts any string header (`R`, `S`,
  `x00`-`x1f`, `x30`-`x33`) for the next chunk.
- Binary chunk sizes depend on the writer's buffer position (`writeBytes`,
  buffer `SIZE = 8 * 1024`, in both), so readers see arbitrary chunk lengths;
  65536 bytes written alone become eight `A` chunks of 8189 bytes and a final
  `x34` chunk of 6199.

## Value Objects

Classes listed in `META-INF/hessian/serializers` use `StringValueSerializer`,
which writes an object of that class with a single field `value` holding
`toString()`:

| Class | Caucho 4.0.66 | hessian-lite 4.0.5 |
|---|---|---|
| `java.math.BigDecimal` | `value` string | `value` string |
| `java.util.UUID` | no entry; generic bean serializer writes its private fields `mostSigBits` and `leastSigBits` | `value` string |
| `java.io.File`, `javax.management.ObjectName` | `value` string | `value` string |

`EnumSerializer` writes an object of the enum class with one field, `name`
(hessian-lite `EnumSerializer.java:92`).

Other JDK values, as written on OpenJDK 17 (observed practice: each writer's
output, read back by both readers):

| Value | Caucho 4.0.66 | hessian-lite 4.0.5 |
|---|---|---|
| `java.time.*` | write fails: `java.base` does not open the private fields to reflection | object `com.alibaba.com.caucho.hessian.io.java8.<Type>Handle` |
| `BigInteger` | object `java.math.BigInteger` with `signum`, `bitCountPlusOne`, `bitLengthPlusOne`, `lowestSetBitPlusTwo`, `firstNonzeroIntNumPlusTwo`, `mag` (`[int` list) | same fields in reverse order |
| `HashSet`, `LinkedHashSet`, `TreeSet`, `LinkedList` | typed list named by the class | same |
| `TreeMap`, `LinkedHashMap` | typed map named by the class | same |
| `Byte`, `Short`, `Float` as `Object` | object `com.caucho.hessian.io.ByteHandle`, `ShortHandle`, or `FloatHandle` with one field `_value` | plain int or double |
| `Locale` | object `com.caucho.hessian.io.LocaleHandle` with `value` | object `com.alibaba.com.caucho.hessian.io.LocaleHandle` with `value` |
| `java.sql.Timestamp`, `Date`, `Time` | object of the class with `value`, a date | same |

The `BigInteger` caches are zero until Java computes them, and a reader
recomputes them, so their values vary between writers of the same number.

hessian-lite's java.time handles and their fields, in written order:

| Handle | Fields |
|---|---|
| `LocalDateHandle` | `day`, `month`, `year` (int) |
| `LocalTimeHandle` | `nano`, `second`, `minute`, `hour` (int) |
| `LocalDateTimeHandle` | `time`, `date` (handles) |
| `DurationHandle`, `InstantHandle` | `nanos` (int), `seconds` (long) |
| `ZoneOffsetHandle` | `seconds` (int) |
| `OffsetDateTimeHandle` | `offset`, `dateTime` (handles) |
| `ZonedDateTimeHandle` | `offset`, `dateTime` (handles), `zoneId` (string) |

With the system property
`com.caucho.hessian.io.java.time.serializer.compactMode`, hessian-lite instead
writes `LocalDate` and `LocalTime` as a class definition with zero fields
followed by one long, which is not a well-formed Hessian object
(`java8/LocalDateSerializer.java`), and `LocalDateTime` as
`java.time.LocalDateTime` with long fields `date` (epoch day) and `time`
(nanosecond of day).

## Class Restrictions

hessian-lite loads classes through `ClassFactory`, whose static list is read
from the `DENY_CLASS` resource in the jar (`ClassFactory.java:75-90`; 133 lines
in 4.0.5, including the prefix `com.caucho.hessian.test.`). An object whose
class is denied is read as a map of its fields. Caucho 4.0.66's `ClassFactory`
denies only `java.lang.Runtime`, `java.lang.Process`, `java.lang.System`, and
`java.lang.Thread` by default and loads `HashMap` in their place
(`ClassFactory.java:76-84`, `210-224`).

## Conformance Suite

Caucho ships `com.caucho.hessian.test.TestHessian2Servlet` in the same jar:
92 `reply*` methods that return fixed values (int, long, and double boundaries
of every compact form, dates, strings and binary at chunk boundaries, typed and
untyped lists and maps, objects, refs, cycles) and 90 `arg*` methods that check
a received value. dubbo-go-hessian2 drives it by reflection from
`test_hessian/src/main/java/test/Hessian.java` (commit `8a3c8bc`), serializing
each `reply*` result with hessian-lite and checking Go output with `arg*`.
