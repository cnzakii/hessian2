# hessian2 Repository Guidance

Read [CONTRIBUTING.md](CONTRIBUTING.md) first: its setup, checks,
interoperability rules, code conventions, and test rules apply to every
change. This file adds what an agent needs beyond them.

## Product Boundary

- `hessian2` maps Hessian 2.0 serialization bytes to Python objects and back,
  with a Rust core and a Python package.
- Hessian 2.0 serialization is the whole product. RPC envelopes, framework
  conventions, transports, and other formats belong to callers.
- The Python API comes first. The Rust crate is a public by-product and never
  a reason to bend the Python side.

## Repository Map

- `crates/hessian2-core`: Rust pull reader, writer, and string transcoding;
  independent of PyO3.
- `crates/hessian2-python`: PyO3 extension, Python package, stubs, and tests;
  golden bytes from Java and Go peers live in `tests/fixtures`.
- `interop/java`, `interop/go`: the pinned peers that regenerate those
  fixtures and run the live interoperability tests.
- `benchmarks`: Python, Java, and Go performance comparisons.
- `fuzz`: cargo-fuzz targets for the core reader and writer.

## Working Rules

- Read the affected implementation, callers, tests, exports, stubs, and
  configuration before editing. Update runtime exports, type stubs, and tests
  together when the Python API changes.
- Establish the requested outcome and its non-goals before adding files,
  options, or abstractions.
- Stop and ask before a change CONTRIBUTING.md says to discuss first: new
  public API, a dependency, or a substantial design change.
- Use `make lint`, `make test-rust`, or `make test-python` while iterating,
  then run `make check`.
- Report exactly which checks ran and their results. Never claim that a check
  passed without running it.
- Keep generated artifacts, build output, local environments, editor state,
  reports, and personal instructions out of version control.

Work is complete only after the relevant focused checks and `make check` pass,
followed by a read-only review of the final diff.
