---
title: Go Hessian implementation
description: Behavior of dubbo-go-hessian2 that differs from the Java implementations, in doubles and strings, and how its Java interoperability tests are driven.
topics: [hessian, go, interop, strings, doubles]
checked_at: 2026-11-11
---

# Go Hessian Implementation

Source: observed practice in
[apache/dubbo-go-hessian2](https://github.com/apache/dubbo-go-hessian2) at
commit `8a3c8bc`.

## Doubles

- `encFloat` (for `float64`, `double.go:40-63`) writes `0x5b`-`0x5e` for
  integral values and `D` otherwise; it never writes `0x5f`. Only `encFloat32`
  uses `0x5f`.
- The integral test is `float64(int64(v)) == v`, so `-0.0` is written as `0x5b`
  and its sign is lost, as in Java.
- The decoder reads `0x5f` as `float64(i32) / 1000` (`double.go:139-142`).
  Java computes `0.001 * mills`. The two expressions round differently: for
  integers in `[-200000, 200000)`, 53302 give a different `float64`
  (methodological synthesis: exhaustive comparison under IEEE 754 binary64).
  A value Java writes as `0x5f` may decode one unit in the last place away in
  Go.

## Dates And Objects

- Dates are always written as `x4a` milliseconds; `encDateInMinute`
  (`date.go:47`) has no caller.
- Struct fields are named by lowering the first rune of the Go field name
  (`lowerCamelCase`, `pojo.go:451`), so `ID` becomes `iD`, unless a `hessian`
  tag names the field.
- Maps are written in Go's map iteration order, which varies between runs.

## Strings

- The encoder walks runes and writes a supplementary character as a surrogate
  pair of three-byte sequences (`encodeUcs4Rune`, `string.go:57`).
- Chunks hold `CHUNK_SIZE = 4096` units (`const.go:43`), not `0x8000`. A chunk
  ends only between runes, so a surrogate pair is never split and a chunk may
  hold 4097 units (`string.go:108-135`).
- The decoder rejects lead bytes outside the one-, two-, and three-byte forms
  with "bad utf-8 encoding" (`string.go:404`), as Java does.
- A three-byte high surrogate followed by anything other than a low surrogate
  is kept as its three bytes (`string.go:377-400`); the regression test
  `TestDecodeStringLoneHighSurrogateRealWorld` (`string_test.go:243`) uses a
  byte stream produced by Java for `"test_go_hessian2\ud83d..."`.

## Java Interoperability Tests

`test_hessian/` is a Maven project depending on `com.alibaba:hessian-lite:3.2.13`
and `com.caucho:hessian:4.0.65`. Go tests run it to obtain bytes for a named
method of Caucho's `TestHessian2Servlet` and to check bytes written by Go (see
[Java](java.md#conformance-suite)).
