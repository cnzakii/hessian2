---
title: Hessian engineering knowledge
description: Source-based reference material for the Hessian 2.0 serialization protocol, the implementations used as interoperability references, and the algorithms behind hessian2.
topics: [index]
---

# Hessian Engineering Knowledge

This collection records facts about the Hessian 2.0 serialization protocol, the
implementations a peer is likely to run, and published algorithms. It does not
contain product code, audits, plans, benchmark results, or project decisions.

## Authority

1. **Specification.** The Hessian 2.0 Serialization Protocol Internet-Draft
   (Ferguson and Ong, Caucho, August 2007) is the only normative source. A
   verbatim copy is kept at
   [protocol/hessian-serialization.html](protocol/hessian-serialization.html),
   fetched from <http://hessian.caucho.com/doc/hessian-serialization.html>,
   SHA-256 `54963565d7293e76f67fa172b453dd2a2f7030d043672847c5bf6e462d1b36bc`.
   It is distributed under the IETF Trust terms in its copyright statement.
2. **Reference implementation.** The draft contradicts itself in several places
   (see [protocol/specification.md](protocol/specification.md)). Where it does,
   Caucho's own Java implementation, written by the draft's authors, shows
   which reading was meant.
3. **Interoperability references.** Other implementations show what peers send
   and accept. They are evidence for compatibility, not for what the
   specification means.
4. **Literature.** Algorithms are attributed to their published sources.

## Evidence Labels

- **Specification:** text of the vendored draft, cited by section.
- **Reference implementation:** `com.caucho:hessian`, pinned to a release.
- **Observed practice:** behavior in another pinned implementation.
- **Literature:** a published algorithm or its canonical implementation.
- **Methodological synthesis:** a conclusion derived across sources.

## Contents

- Protocol: [Specification notes and errata](protocol/specification.md)
- Implementations: [Java](implementations/java.md), [Go](implementations/go.md)
- Algorithms: [UTF-16 length of UTF-8 and CESU-8](algorithms/utf16-length.md),
  [Civil dates from day counts](algorithms/civil-dates.md)

## Freshness

`checked_at` means every external claim in a document was reviewed against its
cited source on that date. Pinned source revisions stay tied to their release
or commit.
