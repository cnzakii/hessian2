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
  classes/callbacks registered with the decoding Codec can be constructed.
  `@java_class` alone allows encoding, not decoding.
- Registration is a trust decision. `Codec.register` also registers the
  declared classes that the registered type's field annotations refer to,
  so registering a class trusts those too. Descriptors, default factories,
  post-init hooks, Converter callbacks, and custom map-key hashing/equality
  can run application code during decoding. Type annotations do not validate
  incoming field values.
- Input lengths and references are checked. `max_depth` bounds nesting,
  not total allocations or CPU time; increasing it increases stack usage.
- Limit message sizes before reading untrusted messages into memory. Large
  flat containers and expensive user-defined keys are not bounded by depth.
- A shared Codec does not make user callbacks or mutable input objects
  thread-safe. Synchronize application-owned state separately.

Please allow time for investigation, a fix, and coordinated disclosure.
The project cannot guarantee that all reports describe vulnerabilities or
that a fix will be available on a particular date.
