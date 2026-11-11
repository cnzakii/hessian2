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
Rust version, and Linux/macOS/Windows. You need not reproduce the whole
matrix locally, but run the focused tests and `make check`. Python 3.15
preview jobs are advisory until the test dependencies support the prerelease
ABI; their failures remain visible and do not satisfy stable-version checks.

GitHub Actions are pinned to commit SHAs. Dependabot proposes grouped monthly
updates for Actions, uv dependencies, and both Cargo workspaces; updates are
reviewed and tested, not auto-merged. Normal CI and distribution builds use
Rust stable; a separate job tests the minimum Rust version declared in
Cargo.toml. When changing that minimum, update the MSRV jobs too. Wheel
installation and import tests are required, not just a successful build.

## Interoperability

Java determines the wire forms we write. Readers accept the specification
and forms real Java peers send. Changes must preserve reference numbering,
class/type tables, UTF-16 string lengths, and error handling.

Install JDK 17 and Go to regenerate and check the pinned peers:

```console
make fixtures-java fixtures-go
make test-python
```

Set `JAVA_HOME` if Java is not on PATH. Inspect fixture diffs; do not update
expected output just to make a test pass. Add the smallest case reproducing
a bug, and distinguish peer differences from bugs in this library.

## Changes and review

- Keep Python runtime code, stubs, tests, and README examples consistent.
- Keep Python mapping in the binding and wire rules in the Rust core.
- Both crates forbid unsafe code. Do not bypass input checks for speed.
- Keep PRs focused; describe behavior changes and breaking changes.
- Add regression tests for behavior changes, including failure paths.
- Do not commit build output, downloaded jars, environments, or raw benchmark
  files containing machine paths and host metadata.

README examples run in `test_readme.py`; Rust README examples run as doctests.

The **Coverage** workflow uploads separate `python` and `rust` reports to
Codecov using GitHub OIDC, without a stored upload token. It also keeps HTML
reports as Actions artifacts. Python coverage measures the Python package;
Rust coverage measures the independent core's Rust tests. Neither report
measures the PyO3 binding's native coverage. Repository owners must authorize
the Codecov GitHub App if Codecov requires it before accepting uploads.

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

Before the first release, configure package-index owners and release
approvals. The canonical repository is
[cnzakii/hessian2](https://github.com/cnzakii/hessian2); use its
[private vulnerability reporting](https://github.com/cnzakii/hessian2/security/advisories/new)
for security reports.

Prepare a release in a focused PR:

1. Update the workspace version, Cargo.lock, and CHANGELOG.md.
2. Run `make check` with Java and Go, inspect regenerated fixture differences,
   and check Rust 1.88 and the supported Python matrix.
3. Build and test distributions, not just the editable development install:

```console
uv run --locked maturin build --release --locked --sdist \
  --manifest-path crates/hessian2-python/Cargo.toml --out target/dist
cargo package -p hessian2-core --locked
uv run --with twine python -m twine check target/dist/*
```

`--sdist` builds the wheel from the source archive, checking that its Rust
sources and READMEs are complete. Inspect the archives and install them into
clean environments. Check import, version, nested objects, shared references,
and free-threaded behavior. Per-version wheels are required; this project
does not use abi3.

The manually dispatched **Distribution builds** workflow builds and tests
wheels for Python 3.11–3.14 and 3.14t on Linux x86-64, macOS arm64, and Windows
x86-64, plus source and Rust archives. It stores workflow artifacts but does
**not** publish to package indexes. This starter matrix does not promise
wheels for every architecture or libc.

Publishing requires separate maintainer approval and package-index setup.
Publish the tested artifacts, tag their exact source commit, and create
release notes from the changelog. If publication partially succeeds, keep
the original artifacts and retry only the failed step. Never replace an
uploaded file or rewrite a released tag; use a new version for bad artifacts.

## License

Contributions are licensed under the project's [MIT License](LICENSE).
