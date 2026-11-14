# Peer fixture provenance

`java.json` and `go.json` contain base64 Hessian streams and structural
descriptions produced by the checked-in peer harnesses. They are independent
golden input, not output captured from hessian2. This test reorganization
preserves both JSON files byte for byte. Historical names inside the corpus
are stable case identifiers.

Java peers (Maven coordinates):

| Peer | SHA-256 of jar |
| --- | --- |
| `com.caucho:hessian:4.0.66` | `e640042084a9f23436ce11430c4424832be783e0171f2c6b0e5eb9175a6c01c5` |
| `org.apache.dubbo:hessian-lite:4.0.5` | `9680163eb2284e49d1b39e2d14903cf08588b5f0aaeeac32e4c6787b41f79fa7` |
| `javax.servlet:javax.servlet-api:3.1.0` (harness dependency) | `af456b2dd41c4e82cf54f3e743bc678973d9fe35bd4d3071fa05c7e5333b8482` |

`interop/java/jars.sha256` is the executable checksum manifest. From the repo
root, regenerate with `JAVA_HOME=/path/to/jdk17 make fixtures-java`; that
target verifies the jars before compiling `interop/java/src/interop/Interop.java`.
The corpus includes Caucho's `TestHessian2Servlet.reply*` conformance examples
and the harness's explicit extra values. A null `caucho` entry means Caucho
could not write that case (notably java.time on JDK 17). An empty `lite` entry
shares Caucho's bytes. Tests request only available writers.

Go uses `github.com/apache/dubbo-go-hessian2 v1.13.3`, pinned in
`interop/go/go.mod`; module checksums and transitive versions are in
`interop/go/go.sum`. Regenerate with `make fixtures-go`, which runs the
checked-in `interop/go/main.go` harness. No Go toolchain or network is needed
to replay the golden bytes.

The inherited fixtures do not record their original generation timestamp,
JDK patch level, or Go compiler version; these cannot be reconstructed from
the bytes. Regeneration uses the pinned peer versions above.

Expected mapping losses are explicit in the tests: Python drops int/long
width and collection type names, rounds Date to milliseconds, and java.time
nanoseconds to microseconds. Java writes negative zero as positive zero;
Caucho FloatHandle values expand float32 to float64, whereas hessian-lite
writes its decimal double values. Duplicate inherited fields keep the last
wire slot. Caucho does not consume hessian-lite java.time handles or its UUID
value form, and hessian-lite denies some Caucho test bean classes. Go reads
double thousandths by division, which can differ by one ULP from Java's
multiplication. Tests of unequal reencoded bytes were replaced by exact-byte
or value claims at the owning layer.
