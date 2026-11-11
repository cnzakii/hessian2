# hessian2 Repository Guidance

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
- `crates/hessian2-python`: PyO3 extension, Python package, stubs, and tests.
- `docs/knowledge`: source-based facts about the Hessian wire format and its
  implementations, not project decisions.
- `.agents/skills`: project workflows for implementation, audit, and
  knowledge work.

## Development

Install locked development dependencies with:

```console
uv sync --locked
```

Use focused checks while iterating:

```console
make lint
make test-rust
make test-python
```

Run the complete local gate before submitting a pull request:

```console
make check
```

## Change Rules

- Read the affected implementation, callers, tests, exports, stubs, and
  configuration before editing.
- Keep wire parsing, encoding forms, and per-stream tables in the Rust core.
  Keep Python inputs, outputs, and errors natural for Python.
- Interoperability decides wire behavior, and Java is the authority: write
  what Caucho's reference implementation and hessian-lite write, and accept
  whatever the specification allows or Java peers actually send, whichever is
  looser. Where the specification contradicts itself, follow Java.
- Follow mainstream Python serialization libraries for API shape and
  edge-case behavior.
- Update runtime exports, type stubs, and tests together when the Python API
  changes.
- Prefer direct code and existing dependencies over speculative abstractions,
  compatibility layers, or new tooling.
- Keep generated artifacts, build output, local environments, editor state,
  and personal instructions out of version control.

## Project Skills

- Use `hessian2-implementation` for changes to code, tests, packaging, CI, or
  project documentation.
- Use `hessian2-audit` for read-only review after implementation or on request.
- Use `hessian2-knowledge` for source-based factual research and explicitly
  requested maintenance of `docs/knowledge`.

Work is complete only after the relevant focused checks and required aggregate
gate finish successfully, followed by a read-only audit of the final changes.
