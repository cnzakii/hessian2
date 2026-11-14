---
name: hessian2-implementation
description: Implement requested changes in the hessian2 Rust core, PyO3 binding, Python package, tests, packaging, CI, or project documentation. Use when creating, modifying, fixing, refactoring, replacing, or migrating hessian2 behavior or repository files. Do not use for read-only audits or factual research.
---

# hessian2 Implementation

Deliver the smallest complete change that satisfies the request and preserves
the repository boundaries in `AGENTS.md`.

## Workflow

1. Read `AGENTS.md` and every affected file before editing. Inspect callers,
   tests, public exports, type stubs, and configuration that share the changed
   contract.
2. Establish the requested outcome and explicit non-goals. Do not add adjacent
   features, compatibility layers, reports, or tooling without a current need.
3. Trace the owning layer before choosing the fix. Keep wire forms and per-stream
   tables in the Rust core, object mapping in the PyO3 layer, and Python-facing
   vocabulary natural for Python.
4. Use `hessian2-knowledge` when a decision depends on wire-format facts, Java peer behavior,
   established tooling behavior, packaging rules, or mature implementation
   practice. Separate source facts from project choices.
5. Implement the smallest coherent design. Update all affected layers required
   for correctness, including Python exports, stubs, and tests when their
   contract changes.
   Treat the README, runnable examples, and `docs/site` as coordinated public
   entry points, and reuse canonical content instead of maintaining copies.
6. Add the smallest check that would fail for a changed behavior, as
   `hessian2-testing` describes. Match evidence to the claim: focused tests for behavior, interoperability for real peers,
   fuzzing for hostile input/state exploration, and benchmarks for performance.
7. Run focused checks first, then the broader gate required by `AGENTS.md`.
   Read fresh output before claiming success.
8. Review the final changes with `hessian2-audit`. Address blocking findings
   through this workflow and rerun the affected checks.

## Implementation Boundaries

- Preserve the Hessian-serialization-only product boundary. RPC and framework
  concepts belong to callers.
- Keep the Rust core independent of PyO3.
- Prefer existing language features and mature dependencies over local
  machinery.
- Replace incorrect pre-release APIs directly. Do not retain aliases, shims, or
  duplicate implementations unless compatibility is explicitly required.
- Do not invent wire behavior, resource limits, safety claims, or
  performance claims without evidence.
- Do not add tests that only restate constants, types, or implementation
  spelling without protecting observable behavior.

## Comments and Documentation

- Python docstrings follow [PEP 257](https://peps.python.org/pep-0257/): a
  one-line summary in the imperative mood ("Return the …"), then
  [Google-style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
  `Args:`, `Returns:`, and `Raises:` sections where they add information.
  Document what callers rely on: accepted values, results, errors, and
  thread safety. Keep `_core.pyi` consistent with the runtime docstrings.
- Rust doc comments follow
  [rustdoc](https://doc.rust-lang.org/rustdoc/how-to-write-documentation.html)
  and the [API Guidelines](https://rust-lang.github.io/api-guidelines/documentation.html):
  a summary sentence in the third person ("Returns the …"), with `# Errors`,
  `# Panics`, and `# Safety` sections where they apply. A module's `//!`
  comment says what the module owns.
- Both crates forbid `unsafe` code. Lifting that needs a measured gain worth
  restating CPython's own safety checks, and a `// SAFETY:` comment on every
  block naming the invariants that make it sound.
- Inline comments explain why: a constraint, a peer's behavior, or a choice
  the code cannot show. They never restate the code.
- Name peer behavior concretely ("Caucho writes …", "hessian-lite reads …"),
  with the version when it matters.
- Cite external sources by URL: the specification, an algorithm, or upstream
  source at a pinned version. Never point code at repository documents such
  as `docs/knowledge` or skills; state the fact where it is used.
- No commented-out code, unexplained TODOs, or history ("now", "previously",
  "fixed"); history belongs in commit messages.

Stop and report the conflict instead of patching around missing authority,
unclear ownership, or an environment that cannot verify the requested result.
