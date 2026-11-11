# Hessian serialization fuzzing

This isolated cargo-fuzz package tests the public Rust API. It adds no runtime
dependencies to either library.

- `reader`: arbitrary bytes through token reading, complete-value skipping,
  checked table lookups, CESU-8 decoding, and UTF-16 boundary scanning must not
  panic or stop making progress. Invalid input may return an error.
- `writer_roundtrip`: arbitrary numeric bits, strings, lone UTF-16 surrogates,
  and binary data must round-trip. Lists, maps, objects, cycles, and shared
  tables span several top-level values in the same stream.

Install cargo-fuzz and a nightly Rust toolchain if absent, then run from the
repository root:

```console
cargo +nightly fuzz run reader -- -max_total_time=30 -max_len=4096 -seed=1
cargo +nightly fuzz run writer_roundtrip -- -max_total_time=30 -max_len=70000 -seed=1
```

For chunk-boundary coverage, put a 70,000-byte file in
`fuzz/corpus/writer_roundtrip/` before the run. Seed `reader` with the small
wire fixtures in the integration tests. Corpus, crash artifacts, coverage,
and build output are ignored by the repository. Keep minimized regressions
as ordinary tests.

`make check` does not include this excluded package. Check it separately:

```console
cargo fmt --manifest-path fuzz/Cargo.toml -- --check
cargo +nightly clippy --manifest-path fuzz/Cargo.toml --all-targets --locked -- -D warnings
```

These are bounded local smoke targets, not evidence of exhaustive safety.
`reader` exercises the existing recursive skip limit on the fuzzer's stack;
it does not establish safety on small application thread stacks. Python hook
and concurrency behavior is covered by the separate pytest regressions; no
Python coverage-guided fuzz target is included.
