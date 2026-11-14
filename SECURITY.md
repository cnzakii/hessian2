# Security Policy

## Supported versions

Before the first release, reports against the current main branch are
accepted. After releases begin, security fixes target the latest release;
older releases are not maintained separately.

## What to report

Please report crashes, panics, nontermination, invalid reference handling,
missing input validation, unexpected class loading, and failures to release
objects after decoding. Include the version, Python build (including whether
it is free-threaded), platform, a minimal reproducer, and the expected impact.
Do not include production data or secrets.

Do not open a public issue or pull request containing a suspected
vulnerability or a harmful input. Use GitHub's
[private vulnerability reporting](https://github.com/cnzakii/hessian2/security/advisories/new)
for this repository. If it is temporarily unavailable, do not post the
reproducer publicly to request a contact.

No response-time or supported service-level commitment is implied.

## Security boundary

- Unknown class names never cause imports. Only built-in mappings and
  classes the application trusts can be constructed: the classes an
  annotation names at that position, through `type=` and field annotations,
  and the classes and converters registered with the decoding Codec.
  `@java_class` alone names a class; decoding trusts it only where an
  annotation names it.
- Annotations and registration are trust decisions. Descriptors, default
  factories, post-init hooks, Converter callbacks, an enum's `_missing_`,
  and custom map-key hashing/equality can run application code during
  decoding. Values application code changes are not checked again. Fields a class
  does not have, keys a TypedDict does not declare, and the fields of JDK
  values decode as plain data that builds no class. Annotations check the types of incoming values, not
  other constraints.
- Input lengths and references are checked. `max_depth` bounds nesting,
  not total allocations or CPU time. Each level takes about 1 KiB of
  thread stack, so the default of 256 needs about 300 KiB, more than
  musl's default thread stack; increasing it increases stack usage.
- A map key no annotation types must be one whose hash a peer cannot make
  collide with many others, which would make decoding quadratic: a
  string, bytes, bool, `None`, float, 64-bit int, date or time, or enum
  member. Annotating other key types, such as `dict[UUID, str]`, accepts
  that cost.
- Limit message sizes before reading untrusted messages into memory. Large
  flat containers and expensive user-defined keys are not bounded by depth.
- A shared Codec does not make user callbacks or mutable input objects
  thread-safe. Synchronize application-owned state separately.

Please allow time for investigation, a fix, and coordinated disclosure.
The project cannot guarantee that all reports describe vulnerabilities or
that a fix will be available on a particular date.
