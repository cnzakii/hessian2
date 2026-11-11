---
name: hessian2-knowledge
description: Retrieve, verify, and, when explicitly requested, maintain source-based serialization-engineering knowledge in docs/knowledge. Use for factual questions about the Hessian 2.0 wire format, Java and Go implementations, testing, architecture, Python/Rust boundaries, tooling, dependencies, licenses, or established practice, including stale or conflicting claims. Keep project decisions and code-specific audit conclusions out of the collection.
---

# hessian2 Knowledge

Use `docs/knowledge/` as a factual reference layer. It supplies evidence and
source maps; it does not decide what hessian2 should implement.

## Retrieve And Verify

1. Resolve the repository root and read `docs/knowledge/README.md` for evidence
   labels and freshness semantics.
2. Search document metadata before loading topic files:

   ```console
   rg -n '^(title|description|topics|checked_at):' docs/knowledge --glob '*.md'
   ```

3. Read only documents matching the wire-format term, tool, claim, or evidence
   class. Follow direct citations when exact current wording or provenance
   matters.
4. Keep these evidence classes distinct, in this order of authority:
   - specification: the vendored Hessian 2.0 draft, the only normative source;
   - reference implementation: Caucho's `com.caucho:hessian`, which settles
     only where the draft contradicts itself;
   - observed practice: behavior in another named, pinned implementation,
     evidence for interoperability, not correctness;
   - literature and official guidance: published algorithms and documentation
     from the tool or language owner;
   - methodological synthesis: a conclusion across sources.
5. Verify live primary sources when the answer depends on mutable
   implementation behavior, tool behavior, package metadata, or current
   practice.
6. Report unavailable, superseded, materially changed, or conflicting sources.
   Do not silently resolve uncertainty or rewrite the collection during a
   read-only task.

## Maintain The Collection

Modify `docs/knowledge/` only when knowledge maintenance is explicitly
requested.

1. Update the existing owner document; add a topic only for a distinct reusable
   domain.
2. Keep documents factual and independent of hessian2 code, audits, plans,
   benchmark results, release decisions, and temporary recommendations.
3. Use `title`, `description`, `topics`, and `checked_at` frontmatter on topic
   documents. Change `checked_at` only after reviewing every external claim in
   that document.
4. Cite primary sources beside the supported claims. Pin implementation
   observations to a version or commit.
5. Replace stale claims instead of appending a contradictory account. Do not
   copy remote source archives without an offline or reproducibility need.
6. Update `docs/knowledge/README.md` only when the document set or domain layout
   changes.
7. Verify frontmatter, relative links, absence of personal paths, and the
   separation between source facts, synthesis, and project choices.

Do not create reports, decision logs, generated indexes, or monitoring
machinery for ordinary knowledge work.
