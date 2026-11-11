# hessian2

[![CI](https://github.com/cnzakii/hessian2/actions/workflows/ci.yml/badge.svg)](https://github.com/cnzakii/hessian2/actions/workflows/ci.yml)
[![Coverage checks](https://github.com/cnzakii/hessian2/actions/workflows/coverage.yml/badge.svg)](https://github.com/cnzakii/hessian2/actions/workflows/coverage.yml)
[![Codecov](https://codecov.io/gh/cnzakii/hessian2/branch/main/graph/badge.svg)](https://codecov.io/gh/cnzakii/hessian2)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Rust 1.88+](https://img.shields.io/badge/rust-1.88%2B-orange)
[![MIT license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Hessian 2.0](http://hessian.caucho.com/doc/hessian-serialization.html)
serialization for Python, implemented in Rust. Exchange nested objects,
collections, dates, and shared references with Java and Go Hessian peers.

Requires CPython 3.11+. The Python package has no runtime dependencies.
The Rust core has no dependencies, and both crates forbid unsafe code.

> Alpha: APIs may change before a stable release. The package has not yet
> been published from this repository.

## Hessian across languages

The wire format is tested against these pinned implementations, in both
directions—not just by encoding and decoding with the same Python library:

| Peer | Version | What the tests check |
|---|---|---|
| Java Caucho Hessian | 4.0.66 | Reference conformance cases, compact numbers, string chunks, objects, references, and JDK values |
| Java hessian-lite | 4.0.5 | The same cases, plus its `java.time` handle forms |
| Go dubbo-go-hessian2 | 1.13.3 | Numbers, text, binary, collections, dates, and nested registered objects |

Java determines the forms this writer emits. Java implementations differ in
some value mappings, and Go has its own edge cases; see
[interoperability details](#interoperability-details). This is tested
interoperability, not a claim that every library supports every Java class.

### Same-protocol performance

Complete calls on large strings, in **µs, encode / decode**; lower is better.
All readers consume the **same Hessian bytes**. Both Java writers were also
checked to emit exactly those bytes for these samples.

| Input | Python hessian2 | Java Caucho | Java hessian-lite | Go Hessian |
|---|---:|---:|---:|---:|
| ASCII | **71 / 110** | 869 / 1,990 | 867 / 1,946 | 2,228 / 520 |
| Chinese | **162 / 552** | 476 / 1,452 | 495 / 1,258 | 1,773 / 669 |
| Mixed text | **390** / 1,089 | 664 / 1,611 | 635 / 1,650 | 1,915 / **864** |
| Emoji | **435 / 900** | 730 / 1,761 | 723 / 1,873 | 1,397 / 22,651 |

Apple M4, macOS, CPython 3.13.12, OpenJDK 17.0.20, Go 1.26.4; release builds,
warmed inputs, median of five rounds of 20 calls. Samples are roughly
0.9–1 MB of UTF-8. Java encoding includes copying the complete output byte
array. These are large-string measurements, not a general ranking of the
languages or object workloads.

Go's decoder is sensitive to chunk layout: on its own output, mixed text
was 649 µs and emoji 4,081 µs, versus 864 µs and 22,651 µs on the common
input. Do not compare those two layouts as if they were identical workloads.

Reproduce after building the release extension and Java harness:

```console
make interop-java
uv run --locked --group bench python benchmarks/bench_hessian.py --iterations 20 --rounds 5
```

## Quick start: nested objects and shared references

From a source checkout, build the Python package with uv and Rust installed:

```console
uv sync --locked
uv --directory crates/hessian2-python run --locked maturin develop --release
```

Register the Java names your peer uses. Here a user has two address fields
that point to **the same Address instance**, not two equal copies:

```python
from dataclasses import dataclass

import hessian2

codec = hessian2.Codec(field_naming="camelCase")


@codec.register("com.example.Address")
@dataclass
class Address:
    city: str
    postal_code: str


@codec.register("com.example.User")
@dataclass
class User:
    user_name: str
    home_address: Address
    delivery_address: Address
    tags: list[str]


address = Address(city="Hangzhou", postal_code="310000")
user = User(
    user_name="alice",
    home_address=address,
    delivery_address=address,
    tags=["customer", "newsletter"],
)

# Python names become Java fields: userName, homeAddress, postalCode, ...
# The shared address is sent once; the second field is a Hessian reference.
payload = codec.dumps(user)
received = codec.loads(payload)

print(received.user_name)  # alice
print(received.home_address.city)  # Hangzhou
print(received.home_address is received.delivery_address)  # True
```

Registration applies throughout the object graph: nested values can be
registered objects, lists, maps, and standard-library values. Shared
references are preserved. Lists, dictionaries, dataclass/attrs instances,
and `HessianObject` values also support cycles. Registered named tuples
reject cycles during encoding; Converter values cannot be referenced back
to while their fields are being decoded.

For plain values, use the module-level functions without creating a codec:

```python
import hessian2

payload = hessian2.dumps({"name": "alice", "tags": ["customer"]})
received = hessian2.loads(payload)
print(received["tags"])  # ['customer']
```

`loads` accepts bytes-like input and requires exactly one value. Use
`dumps_many` / `loads_many` for several top-level values sharing a stream's
class definitions and references:

```python
import hessian2

shared = {"status": "ready"}
stream = hessian2.dumps_many(["header", shared, shared])
header, first, second = hessian2.loads_many(stream)
print(header, first["status"], first is second)  # header ready True
```

## Registered classes

| Register | Object representation |
|---|---|
| dataclass, attrs class | One field per declared attribute |
| `typing.NamedTuple`, `collections.namedtuple` | One field per named item |
| `enum.Enum` | A `name` field, as Java writes an enum |
| `Converter` for any other type | The field dictionary returned by `to_fields` |

Fields match by name, not position. Missing fields take their defaults;
missing required fields raise `DecodeError`. Unknown fields are consumed and
ignored. Field naming must not map multiple Python attributes to the same
wire name: `user_name` and `userName` conflict under `"camelCase"`.

Dataclass and attrs decoding bypasses `__init__`, fills fields, then invokes
`__post_init__` or `__attrs_post_init__`. Dataclass InitVars use their defaults;
required InitVars needed by post-init are rejected at registration. attrs
validators and converters do not run. Annotations are not validated. Use a
validation hook or a Converter when validation is required.

### Options and registration conflicts

```python
import hessian2

codec = hessian2.Codec(
    field_naming="camelCase",  # None keeps Python field names unchanged
    unknown_class="raise",  # "hessian_object" or "dict" for unknown names
    max_depth=256,  # Maximum nesting of objects, lists, and maps
)
```

Duplicate registration of the same type under the same name is allowed.
A name assigned to a different type, or a type assigned to a different name,
raises `ValueError` unless you explicitly pass `replace=True`:

```python
from dataclasses import dataclass

import hessian2


@dataclass
class OldPoint:
    x: int


@dataclass
class NewPoint:
    x: int
    y: int = 0


codec = hessian2.Codec()
codec.register("com.example.Point", OldPoint)
# Explicit replacement; without replace=True this raises ValueError.
codec.register("com.example.Point", NewPoint, replace=True)
point = codec.loads(codec.dumps(NewPoint(3, 4)))
print(point)  # NewPoint(x=3, y=4)
```

`replace=True` can also override built-in JDK mappings. Moving a type to a
new encoding name keeps its old name as a decode alias. There is no global
registry; the module-level functions use only the built-in mappings.

### Pydantic and other types

A Converter adapts an arbitrary type through a field dictionary. Pydantic
v2 models can validate incoming fields with `model_validate`:

```python
from pydantic import BaseModel

import hessian2


class Order(BaseModel):
    order_id: int
    amount: float


codec = hessian2.Codec()
codec.register(
    "com.example.Order",
    hessian2.Converter(
        Order,
        to_fields=Order.model_dump,
        from_fields=Order.model_validate,
    ),
)
order = Order(order_id=1, amount=9.5)
received = codec.loads(codec.dumps(order))
print(received.order_id, received.amount)  # 1 9.5
```

Converter keys are sent unchanged; `field_naming` does not rename them.
`to_fields` runs once per object per stream. Further occurrences write
references to its first representation, not repeated callback calls.
Pydantic validation and coercion follow the model configuration; use
`model_validate(fields, strict=True)` in your callback for strict validation.
Plain classes and BaseModel classes are not automatically registered.

## Objects without Python classes

`HessianObject` sends a class name and fields without a corresponding Python
type. Its fields can themselves hold objects, collections, and references:

```python
import hessian2
from hessian2 import HessianObject

address = HessianObject("com.example.Address", {"city": "Hangzhou"})
user = HessianObject(
    "com.example.User",
    {"name": "alice", "home": address, "addresses": [address]},
)

codec = hessian2.Codec(unknown_class="hessian_object")
received = codec.loads(codec.dumps(user))
print(received.class_name)  # com.example.User
print(received.fields["home"].fields["city"])  # Hangzhou
print(received.fields["home"] is received.fields["addresses"][0])  # True
```

For an unregistered class, `unknown_class="raise"` raises `DecodeError`,
`"hessian_object"` keeps its class name and fields, and `"dict"` returns only
its fields. The setting also applies to nested unknown objects.

## Type mappings

| Python | Hessian / Java |
|---|---|
| `None`, `bool`, `float`, `str`, `bytes` | `null`, boolean, double, String, byte array |
| `int` | int within 32 bits, long within 64 bits, otherwise BigInteger |
| `list`, unregistered `tuple` | Untyped list; decoded as `list` |
| `set`, `frozenset` | HashSet typed list; decoded as `list` |
| `dict` | Untyped map; decoded as `dict` |
| aware `datetime` | java.util.Date; decoded in UTC, millisecond precision |
| naive `datetime`, `date`, naive `time`, `timedelta` | LocalDateTime, LocalDate, LocalTime, Duration |
| `Decimal`, `UUID` | BigDecimal, UUID |

Typed Java lists and arrays decode as Python lists; typed maps decode as
dicts. Instant, OffsetDateTime, and ZonedDateTime decode as aware datetimes;
ZonedDateTime keeps its zone when `zoneinfo` knows it. Java nanoseconds are
truncated to Python microseconds. An aware `time` has no default Java mapping
and raises `EncodeError`.

### Interoperability details

- Java can read an int encoding into a long field. In an `Object` or generic
  collection, however, a small Python integer arrives as `Integer`, not `Long`.
- Caucho sends `0.1f` as its exact double value (`0.10000000149011612`);
  hessian-lite sends its shortest decimal (`0.1`). Both incoming values are
  preserved as sent. Java's writer also drops negative zero's sign.
- `java.time` uses hessian-lite's handle forms. Caucho's default reflection
  serializer cannot write these on JDK 17 without module access. The compact
  java.time mode is not supported.
- Java sends supplementary characters as CESU-8 surrogate pairs. Both paired
  and unpaired surrogates are accepted. Four-byte UTF-8 is also accepted on
  read, though these Java peers do not write it.
- Go's compact millisecond-double decoder has a known rounding difference
  from Java for some values; the interop tests record this explicitly.

## Safety

Unknown class names never trigger imports. Registration is a trust decision:
descriptors, default factories, post-init hooks, Converter callbacks, and map
key methods can run Python code. Field annotations do not validate input.

`DecodeError` subclasses `ValueError`; `EncodeError` subclasses `TypeError`.
Excess encoding depth raises `RecursionError`. Lengths and references are
checked, but `max_depth` does not cap total memory or CPU use. Limit incoming
message size in the application. See [SECURITY.md](SECURITY.md).

## Other Python formats

For Python-only workloads, compare against the format you actually need.
`pickle`, MessagePack, and JSON do not emit Hessian bytes, and may return
plain dictionaries instead of registered Python classes.

The format comparison runs all of them on the same source values:

```console
uv run --locked --group bench python benchmarks/bench.py --fast -o target/formats.json
```

`users` uses dataclasses: hessian2 and pickle reconstruct those classes;
msgpack calls `dataclasses.asdict` during encoding, while orjson encodes
dataclasses natively, and both return dictionaries. Decimal/UUID conversion
and binary representation also differ. These numbers compare application
costs, not interchangeable formats.

For regression measurements of short strings, ordinary objects, wide
classes, big integers, and repeated references, use
`benchmarks/bench_paths.py`. Results depend on machine, interpreter, input
shape, and whether Python has cached a string's UTF-8 representation.

## Development and community

- [Contributing and release guide](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

The Rust reader and writer are a separate crate,
[`hessian2-core`](crates/hessian2-core/README.md), with its own README
and API. Python object mapping belongs to this package, not the Rust core.

## License

MIT
