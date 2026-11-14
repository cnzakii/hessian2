---
name: hessian2-testing
description: Write, rewrite, or review hessian2 tests, or judge whether the suite states the right claims at the right layer. Use with hessian2-implementation when a change needs tests, and with hessian2-audit when reviewing them. Not for production design decisions.
---

# hessian2 Testing

A test states one claim about a contract and fails when that claim breaks.
Everything else in a test suite is cost.

## Choose The Claim

1. Start from a contract the code owns: the Hessian 2.0 wire format, what
   Caucho or hessian-lite write and read, a public docstring, the type
   mapping, or an error a caller can act on.
2. Before writing a test, name the plausible wrong implementation it would
   catch. If no realistic bug fails it, do not write it.
3. Test a claim once, at the layer that owns it:
   - wire forms, tags, lengths, chunking, stream tables, and malformed
     bytes belong to `crates/hessian2-core`, whose API is public;
   - Python tests own the mapping between Python values and Hessian values,
     annotations, typed decoding, registration, callbacks, and Python
     errors. They do not test wire forms again, except a single row showing
     that a core error reaches Python.
   Add an end-to-end case only where the wiring between layers can break.
4. Encoding claims are exact bytes: what Caucho or hessian-lite write for
   that value. Decoding claims cover every form the specification allows or
   Java peers actually send, which is wider than what we write.

## Choose The Edge Cases

Derive edge cases from the wire format and the contract, not from the code:

- for each compact form, its first and last value and one past each end
  (int, long, double, string and binary lengths, list lengths, class and
  reference indexes);
- empty, single, chunked (a non-final chunk), and maximal strings and
  binary; surrogate pairs and lone surrogates;
- `-0.0`, NaN, and the compact double forms; date forms;
- references to self, to a value still being decoded, and out of range;
  class definitions reused across a stream;
- truncation at every byte; unknown and reserved tags; trailing data;
- nesting exactly at `max_depth` and one past it;
- values Java peers really produce, from the golden fixtures.

Any byte string can reach the decoder, so malformed bytes are a real input
domain. The encoder's domain is the Python values its signature accepts; do
not invent values the annotations already exclude.

## Do Not Grow Defenses

Tests never justify defensive production code.

- Do not add guards, `isinstance` checks, broad `except`, or fallbacks to
  production code so a test can pass with an impossible input. Fix the
  test.
- The trust boundary is the peer's bytes and the public API's arguments.
  Validate there once. Application code the caller registers (converters,
  post-init methods, default factories) is trusted.
- When a check seems missing, report the boundary, the input that reaches
  it, and the consequence. Do not add it unasked.

## Write The Test

- Name the test as its claim, a sentence:
  `test_int_at_0x7ff_uses_the_two_byte_form`.
- Arrange, act, and assert are separated by single blank lines.
- One rule over many inputs is a `pytest.mark.parametrize` table, or a
  Rust table of rows, with readable ids naming the form or the boundary.
  Where one row can drive encoding, decoding, and a round trip, let it.
- Tests are straight-line code. No loops or branches decide what to
  assert; split the cases into rows instead.
- Expected values are literals, never recomputed by reimplementing the rule
  under test.
- Equality is type-strict: `True == 1` and `1.0 == 1` hide bugs. Compare
  types too where the type is part of the claim.
- Errors: assert the specific exception and, with `match=re.escape(...)`,
  only the part a caller relies on (a path such as `$.items[0]`, a class
  name, a byte offset). Rust asserts the error kind and offset. Exact text
  only where the message itself is the contract.
- Prefer real values and small fakes over patching internals. Patch only a
  boundary the test cannot reach otherwise.
- A complexity or resource bound (quadratic time, stack depth) may use a
  generous time bound or a thread with a fixed stack size; say in a comment
  that it is a bound, not a benchmark.

## Property Tests, Fuzzing, And Interop Evidence

- Hypothesis states the encode/decode round trip and "any bytes decode to a
  value or raise `DecodeError`", untyped and with `type=`. A failure it
  finds becomes a table row.
- cargo-fuzz targets in `fuzz/` explore the core reader and writer; they are
  seeded from fixture bytes and stay out of the default gate.
- Interop evidence is golden bytes from real peers under
  `tests/fixtures`, with provenance (peer, version, jar hash, regeneration
  command) beside them, replayed by unit tests. Live Java and Go peers run
  only when explicitly enabled.

## Do Not Test

- Python, the standard library, PyO3, or serde behavior;
- private helpers, plan tuples, or other implementation shape, unless a
  bound has no public observation (say why in the test);
- annotation or decorator spelling;
- the same rule again in a layer that only forwards it;
- performance beyond a stated complexity bound; benchmarks live in
  `benchmarks/`.

## Layout

- `crates/hessian2-core/tests/<topic>.rs` by wire type or concern: integers,
  doubles, dates, strings, binary, containers, objects, references,
  malformed input, limits, and the specification's own examples.
  `#[cfg(test)]` modules in `src` test private helpers only.
- `crates/hessian2-python/tests/test_<feature>.py` by public feature, such as
  built-ins, standard-library types, references, limits, malformed input,
  registration, object construction, callbacks, declarations, typed
  decoding, the reader, `as_object`, properties, free threading, and
  interop. Shared fixtures live in `conftest.py`.
- No file or test is named after a review round, an audit, or an issue
  number; a regression test joins the file of the contract it protects.

## Review A Test

For each test answer three questions; delete or rewrite it when one has no
answer:

1. Which claim does it state, and which layer owns that claim?
2. Which realistic bug makes it fail?
3. Is this the right layer and the smallest setup?
