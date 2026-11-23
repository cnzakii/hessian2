# Contributing

Bug fixes, interoperability cases, documentation, API feedback, and measured
performance improvements are welcome. Please follow the
[Code of Conduct](CODE_OF_CONDUCT.md). Report suspected vulnerabilities
privately as described in [SECURITY.md](SECURITY.md), not in public issues.

Discuss new public APIs, dependencies, or substantial design changes before
starting them. A small fix with a reproducer can go directly to a pull request.

## Development

You need CPython 3.11+, Rust stable, uv, Make, and a C compiler/linker.
The core crate is also tested with Rust 1.88, the minimum version declared
in Cargo.toml. The usual development Python interpreter is 3.13.

```console
rustup component add rustfmt clippy
uv sync --locked
make check
```

Use `make lint`, `make test-rust`, or `make test-python` while iterating.
`make test-python` builds the extension in release mode. On Windows, use a
shell with Make or run the commands in the Makefile individually.

To test free-threaded Python without changing your main environment:

```console
UV_PROJECT_ENVIRONMENT=target/dev-314t uv sync --locked --python 3.14t
UV_PROJECT_ENVIRONMENT=target/dev-314t uv run --locked --python 3.14t pytest
```

CI tests the supported Python versions, free-threaded builds, the minimum
Rust version, and Linux/macOS/Windows. CPython 3.11–3.15 and the free-threaded
3.14t/3.15t builds are required checks. You need not reproduce the whole
matrix locally, but run the focused tests and `make check`.

GitHub Actions are pinned to commit SHAs. Dependabot proposes grouped monthly
updates for Actions, uv dependencies, and both Cargo workspaces; updates are
reviewed and tested, not auto-merged. Normal CI and distribution builds use
Rust stable; a separate job tests the minimum Rust version declared in
Cargo.toml. When changing that minimum, update the MSRV jobs too. Wheel
installation and import tests are required, not just a successful build.

## Interoperability

Interoperability decides wire behavior, and Java is the authority. Write what
Caucho's reference implementation and hessian-lite write. Read whatever the
[specification](http://hessian.caucho.com/doc/hessian-serialization.html)
allows or Java peers actually send, whichever is looser. Where the
specification contradicts itself, follow Java. Changes must preserve
reference numbering, class/type tables, UTF-16 string lengths, and error
handling.

Install JDK 17 and Go to regenerate and check the pinned peers:

```console
make fixtures-java fixtures-go
make test-python-live-java test-python-live-go
```

Set `JAVA_HOME` if Java is not on PATH. Inspect fixture diffs; do not update
expected output just to make a test pass. Add the smallest case reproducing
a bug, and distinguish peer differences from bugs in this library.

## Code conventions

### Boundaries

- Wire parsing, encoding forms, and per-stream tables live in the Rust core,
  which does not depend on PyO3. The binding maps Python values and keeps
  inputs, outputs, and errors natural for Python.
- Follow mainstream Python serialization libraries, such as `pickle`, `json`,
  and msgspec, for API shape and edge-case behavior.
- Validate once, where data enters: the peer's bytes and the arguments of
  public functions. Application code a caller registers (converters,
  post-init methods, default factories) is trusted.
- Before 1.0, replace an incorrect design directly, without aliases, shims,
  or compatibility layers.
- Prefer direct code, the standard library, and existing dependencies over
  speculative abstractions or new tooling.
- Both crates forbid unsafe code. Do not bypass input checks for speed.

### Docstrings and comments

- Python docstrings follow [PEP 257](https://peps.python.org/pep-0257/): a
  one-line summary in the imperative mood ("Return the …"), then
  [Google-style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
  `Args:`, `Returns:`, and `Raises:` sections where they add information.
  Document accepted values, results, errors, and thread safety, and keep
  `_core.pyi` consistent with the runtime docstrings.
- Rust doc comments follow
  [rustdoc](https://doc.rust-lang.org/rustdoc/how-to-write-documentation.html)
  and the [API Guidelines](https://rust-lang.github.io/api-guidelines/documentation.html):
  a summary sentence in the third person ("Returns the …"), with `# Errors`,
  `# Panics`, and `# Safety` sections where they apply. A module's `//!`
  comment says what the module owns.
- Inline comments explain why: a constraint, a peer's behavior, or a choice
  the code cannot show. Name peer behavior concretely ("Caucho writes …",
  "hessian-lite reads …"), with the version when it matters, and cite
  external sources by URL at a pinned version.
- No commented-out code, unexplained TODOs, or history; history belongs in
  commit messages.
- `make lint` runs rustfmt, Clippy, Ruff, and ty; their output is the style.

## Tests

A test states one claim about a contract and fails when that claim breaks.

### What to test

- Start from a contract the code owns: the Hessian 2.0 wire format, what
  Caucho or hessian-lite write and read, a public docstring, the type
  mapping, or an error a caller can act on.
- Name the plausible wrong implementation the test would catch. If no
  realistic bug fails it, do not write it. Do not test constants, types,
  annotation spelling, or the behavior of Python, PyO3, or the standard
  library.
- Encoding claims are exact bytes: what Caucho or hessian-lite write for that
  value. Decoding claims cover every form the specification allows or Java
  peers send.
- Take edge cases from the wire format: the first and last value of each
  compact form and one past each end; empty, chunked, and maximal strings and
  binary; surrogate pairs and lone surrogates; `-0.0` and NaN; references to
  self, to a value still being decoded, and out of range; truncation at every
  byte; unknown tags; trailing data; nesting at `max_depth` and one past it.
- Match the evidence to the claim: focused tests for behavior, golden bytes
  from real peers for interoperability, Hypothesis and cargo-fuzz for hostile
  input, and `benchmarks` for performance. A complexity bound may use a
  generous time limit, commented as a bound rather than a benchmark.

### Tests never justify defensive code

Do not add guards, `isinstance` checks, broad `except` clauses, or fallbacks
to the implementation so that a test can pass with an input the contract
excludes. Fix the test instead.

### How to write a test

- Name the test as its claim, such as
  `test_int_at_0x7ff_uses_the_two_byte_form`.
- Keep tests straight-line, with arrange, act, and assert separated by blank
  lines. One rule over many inputs is a `pytest.mark.parametrize` table, or
  a Rust table of rows, with ids naming the form or boundary.
- Write expected values as literals, and compare types too where the type is
  part of the claim: `True == 1` and `1.0 == 1` hide bugs.
- For errors, assert the specific exception and match, with
  `match=re.escape(...)`, only what a caller relies on, such as a path like
  `$.items[0]`, a class name, or a byte offset. Rust tests assert the error
  kind and offset.
- Put a test where its claim is owned:
  - `crates/hessian2-core/tests/<topic>.rs` for wire forms, tags, lengths,
    chunking, stream tables, and malformed bytes;
  - `crates/hessian2-python/tests/test_<feature>.py` for the mapping between
    Python and Hessian values, annotations, registration, callbacks, and
    Python errors, without testing wire forms again;
  - `crates/hessian2-python/tests/fixtures` for golden peer bytes, with their
    provenance.
- A regression test joins the file of the contract it protects, never one
  named after an issue or review.

## Changes and review

- Keep Python runtime code, stubs, tests, and README examples consistent.
- Keep PRs focused; describe behavior changes and breaking changes.
- Add regression tests for behavior changes, including failure paths.
- Do not commit build output, downloaded jars, environments, or raw benchmark
  files containing machine paths and host metadata.
- PRs are squash-merged: the PR title and description become the commit
  message on `main`, so keep both current.

README examples run in `test_readme.py`; Rust README examples run as doctests.
The Pydantic example needs the `readme` dependency group and is skipped
without it; CI runs it in one job.

The **Coverage** workflow uploads separate `python` and `rust` reports to
Codecov using GitHub OIDC, without a stored upload token. It also keeps HTML
reports as Actions artifacts. Python coverage measures the Python package;
Rust coverage measures the independent core's Rust tests. Neither report
measures the PyO3 binding's native coverage. Coverage does not install Java
or Go; live-peer tests are optional here, and the separate interoperability
job validates them. Fixture-based tests still run. The locked test dependencies
include `tzdata`, so timezone tests also run without a system IANA database.

Codecov repository access is managed through the
[Codecov GitHub App](https://github.com/apps/codecov).

Reproduce the reports locally:

```console
uv run --locked pytest --cov=hessian2 --cov-branch --cov-report=term-missing --cov-report=xml:target/coverage/python.xml
rustup component add llvm-tools-preview
cargo install cargo-llvm-cov --version 0.9.1 --locked
mkdir -p target/coverage
cargo llvm-cov -p hessian2-core --locked --lcov --output-path target/coverage/rust.lcov
```

Install pydantic only when checking its optional example:

```console
uv run --with pydantic pytest crates/hessian2-python/tests/test_readme.py
```

## Performance

Build the release extension before measuring:

```console
uv --directory crates/hessian2-python run --locked maturin develop --release
uv run --locked --group bench python benchmarks/bench_paths.py --fast -o target/paths.json
uv run --locked --group bench python benchmarks/bench.py --fast -o target/formats.json
```

For before/after measurements, use separate release builds with the same
Python version and samples, then `python -m pyperf compare_to BEFORE AFTER`.
Check small objects and short strings as well as the workload being optimized.
Report the machine, versions, sample construction, and whether strings were
already UTF-8 cached. Results from other formats are not wire-equivalent.

For the same Hessian samples across Python, Java, and Go:

```console
make interop-java
uv run --locked --group bench python benchmarks/bench_hessian.py
```

This also reports Go decoding its own chunk layout; the headline comparison
uses the same input bytes for all readers. See [fuzz/README.md](fuzz/README.md)
for differential and round-trip fuzzing.

## Releases

The Python package and Rust core share `[workspace.package].version` in the
root Cargo.toml, but have separate package artifacts and READMEs. The binding
crate is not published. During 0.x, use a minor version for breaking public
API changes and a patch version for compatible fixes.

Confirm package-index ownership and release approval before publishing.

Prepare a release in a focused PR:

1. Set the workspace version, update lockfiles if needed, and move the
   `Unreleased` changelog entries under a `## [X.Y.Z] - YYYY-MM-DD` heading.
   The tag run fails unless that heading exists for the tagged version. Keep
   an empty `Unreleased` section for subsequent changes.
2. Run `make check` with Java and Go, inspect regenerated fixture differences,
   and check Rust 1.88 and the supported Python matrix.
3. Build and test distributions, not just the editable development install:

```console
uv run --locked maturin build --release --locked --sdist \
  --manifest-path crates/hessian2-python/Cargo.toml --out target/dist
cargo package -p hessian2-core --locked
uv run --locked --group release twine check --strict target/dist/*
```

`--sdist` builds the wheel from the source archive, checking that its Rust
sources and READMEs are complete. Inspect the archives and install them into
clean environments. Check import, version, nested objects, shared references,
and free-threaded behavior. Per-version wheels are required; this project
does not use abi3.

The **Release** workflow (`.github/workflows/release.yml`) runs on
release-related PRs, manual dispatch, and `v*` tags. It builds a wheel for
each supported CPython on glibc Linux, musl Linux, macOS, and Windows, for
x86-64 and ARM64; its matrix is the authoritative list. Each wheel is
installed and tested in a clean environment, Linux wheels inside the matching
libc container, and the source archive is installed and tested separately.
A final job checks the complete artifact set before anything is published.
PR and manual runs never publish.

Publishing uses Trusted Publishing, so no registry token is stored. PyPI
(`hessian2`) and crates.io (`hessian2-core`) both trust owner `cnzakii`,
repository `hessian2`, workflow `release.yml`, and environment `release`. The
`release` environment accepts only `v*` tags and waits for maintainer
approval. Renaming the workflow file or the environment breaks publishing
until both registries are updated.

For each release, merge the release PR, then push a tag such as `v0.1.0` and
approve the `release` deployment. The tag must match the workspace version
and point to a commit on `main`. The tag run builds and validates its own
artifacts, publishes that Python artifact set without rebuilding it, and
publishes the Rust crate from the same commit. It then creates the GitHub
Release, using the version's changelog entry as its notes.

If publication partially succeeds, rerun only the failed jobs using the
original artifacts. Never replace an uploaded file or rewrite a released tag;
use a new version for bad artifacts.

## License

Contributions are licensed under the project's [MIT License](LICENSE).
