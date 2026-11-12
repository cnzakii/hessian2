# hessian2

[![CI](https://github.com/cnzakii/hessian2/actions/workflows/ci.yml/badge.svg)](https://github.com/cnzakii/hessian2/actions/workflows/ci.yml)
[![Coverage checks](https://github.com/cnzakii/hessian2/actions/workflows/coverage.yml/badge.svg)](https://github.com/cnzakii/hessian2/actions/workflows/coverage.yml)
[![Codecov](https://codecov.io/gh/cnzakii/hessian2/branch/main/graph/badge.svg)](https://codecov.io/gh/cnzakii/hessian2)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Rust 1.88+](https://img.shields.io/badge/rust-1.88%2B-orange)
[![MIT license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Fast [Hessian 2.0](http://hessian.caucho.com/doc/hessian-serialization.html)
serialization for Python, powered by Rust. Exchange Python objects with
Java and Go applications without converting nested data to dictionaries first.

- **Fast.** Encode large strings at **1.7–12.3×** and decode them at
  **1.5–18.0×** the speed of Java Caucho Hessian in the
  [text benchmarks](#performance).
- **Python objects.** Round-trip dataclasses, attrs classes, named tuples,
  and enums. Use a Converter for Pydantic models and other custom types.
- **Shared references.** Preserve object identity across nested objects and
  collections, including cycles in lists, dictionaries, and dataclass/attrs
  instances.
- **Built-in Java mappings.** Send decimals, UUIDs, dates, durations, and
  large integers without writing conversion callbacks.

> Alpha: APIs may change before a stable release.

## Installation

Requires CPython 3.11+ and has no runtime dependencies. Install with pip:

```console
python -m pip install hessian2
```

Or add it to a uv project:

```console
uv add hessian2
```

For source builds and development setup, see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Quick start

For plain values, use the module-level functions:

```python
import hessian2

payload = hessian2.dumps({"name": "alice", "tags": ["customer"]})
received = hessian2.loads(payload)
print(received["tags"])  # ['customer']
```

### Nested objects, references, and built-in types

Declare the Java class names your peer uses. Here an order contains a
customer and an address, and both address fields point to **the same
Address instance**. Decimal, UUID, and an aware datetime use the built-in
Java mappings without declarations.

```python
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import hessian2


@hessian2.java_class("com.example.Address")
@dataclass
class Address:
    city: str
    postal_code: str


@hessian2.java_class("com.example.Customer")
@dataclass
class Customer:
    user_name: str
    address: Address


@hessian2.java_class("com.example.Order")
@dataclass
class Order:
    order_id: UUID
    customer: Customer
    delivery_address: Address
    total: Decimal
    created_at: datetime
    tags: list[str]


address = Address(city="Hangzhou", postal_code="310000")
order = Order(
    order_id=UUID("123e4567-e89b-12d3-a456-426614174000"),
    customer=Customer(user_name="alice", address=address),
    delivery_address=address,
    total=Decimal("19.90"),
    created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    tags=["paid", "delivery"],
)

# Python names become Java fields: orderId, deliveryAddress, postalCode, ...
# The address is sent once; the second occurrence is a Hessian reference.
payload = hessian2.dumps(order)

# Decoding builds only registered classes. Registering Order also registers
# Customer and Address, which its fields refer to.
codec = hessian2.Codec()
codec.register(Order)
received = codec.loads(payload)

print(received.customer.user_name)  # alice
print(received.delivery_address.city)  # Hangzhou
print(received.customer.address is received.delivery_address)  # True
print(received.total, type(received.total).__name__)  # 19.90 Decimal
print(received.order_id, type(received.order_id).__name__)
# 123e4567-e89b-12d3-a456-426614174000 UUID
print(received.created_at.isoformat())  # 2026-01-01T12:00:00+00:00
```

Nested values can be declared objects, lists, maps, and standard-library
values. Shared references are preserved. Lists, dictionaries,
dataclass/attrs instances, and `HessianObject` values also support cycles.
Named tuples reject cycles during encoding; Converter values cannot be
referenced back to while their fields are being decoded.

### Multiple values in one stream

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

## Performance

The following measurements compare hessian2 with **Caucho Hessian 4.0.66**,
the Java reference implementation, on four roughly 1 MB text samples. Encoding
is **1.7–12.3×** and decoding **1.5–18.0×** as fast in these samples.

### Encoding

Times are per complete encoding call, in microseconds (µs); lower is better.
Speedup is Caucho's time divided by hessian2's time.

| Text | hessian2 | Java Caucho | Speedup |
|---|---:|---:|---:|
| ASCII | 71 µs | 869 µs | **12.3×** |
| Chinese | 162 µs | 476 µs | **2.9×** |
| Mixed | 390 µs | 664 µs | **1.7×** |
| Emoji | 435 µs | 730 µs | **1.7×** |

### Decoding

Times are per complete decoding call. Both implementations read the
**same Hessian bytes**; speedup uses the same calculation as above.

| Text | hessian2 | Java Caucho | Speedup |
|---|---:|---:|---:|
| ASCII | 110 µs | 1,990 µs | **18.0×** |
| Chinese | 552 µs | 1,452 µs | **2.6×** |
| Mixed | 1,089 µs | 1,611 µs | **1.5×** |
| Emoji | 900 µs | 1,761 µs | **2.0×** |

For small messages and object workloads, benchmark representative application
data; the text results above do not predict their performance.

<details>
<summary>hessian-lite and Go results</summary>

Against Java hessian-lite, encoding speed is **1.6–12.2×** and decoding
speed **1.5–17.7×**. Against Go Hessian, encoding speed is **3.2–31.5×**;
decoding is workload-dependent: **Go is faster on mixed text**, where
hessian2 takes 26% more time.

All values below are microseconds per complete call; lower is better.
The hessian2 timings are in the tables above.

| Text | hessian-lite encode | hessian-lite decode | Go encode | Go decode |
|---|---:|---:|---:|---:|
| ASCII | 867 | 1,946 | 2,228 | 520 |
| Chinese | 495 | 1,258 | 1,773 | 669 |
| Mixed | 635 | 1,650 | 1,915 | 864 |
| Emoji | 723 | 1,873 | 1,397 | 22,651 |

Go's decoder is sensitive to chunk layout. On its own output, mixed-text
decoding takes 649 µs and emoji 4,081 µs, rather than 864 µs and 22,651 µs
on the shared input. The large emoji gap therefore depends heavily on
which writer produced the bytes.

</details>

<details>
<summary>Benchmark setup and reproduction</summary>

Apple M4, macOS, CPython 3.13.12, OpenJDK 17.0.20, Go 1.26.4.
Peers: Caucho Hessian 4.0.66, hessian-lite 4.0.5, dubbo-go-hessian2 1.13.3.
Release builds, warmed inputs, median of five rounds of 20 calls.

Samples contain roughly 0.9–1 MB of UTF-8: ASCII, Chinese, sparse emoji
mixed with English and Chinese, and emoji only. Every decoder reads the
same Hessian bytes. Both Java writers also produce exactly those bytes
for these samples. Java encoding includes copying the complete output
byte array. Multipliers use unrounded timings; displayed times are rounded.

With the release extension installed, JDK 17+ and Go available:

```console
make interop-java
uv run --locked --group bench python benchmarks/bench_hessian.py --iterations 20 --rounds 5
```

Results are written to `target/hessian-benchmark/results.json`.
For short strings, ordinary objects, wide classes, big integers, and repeated
references, use `benchmarks/bench_paths.py` for regression measurements.

</details>

<details>
<summary>Comparing with other Python formats</summary>

For Python-only applications, `benchmarks/bench.py` compares hessian2 with
pickle, MessagePack, and JSON on the same source values. The formats preserve
different types and references, so choose one that fits your application.

```console
uv run --locked --group bench python benchmarks/bench.py --fast -o target/formats.json
```

The `users` workload contains dataclasses. hessian2 and pickle reconstruct
those classes; msgpack calls `dataclasses.asdict` during encoding, orjson
encodes dataclasses natively, and both return dictionaries. Decimal/UUID
conversion and binary representation also differ. Include any conversions
your application needs when interpreting the results.

</details>

## Classes

| Declare or register | Object representation |
|---|---|
| dataclass, attrs class | One field per declared attribute |
| `typing.NamedTuple`, `collections.namedtuple` | One field per named item |
| `enum.Enum` | A `name` field, as Java writes an enum |
| `Converter` for any other type | The field dictionary returned by `to_fields` |

`@hessian2.java_class(name)` records the Java class name on a class. Every
codec, including the module-level functions, encodes declared classes. A
subclass is not declared by inheriting from a declared class.

Decoding builds only the classes registered with a codec, because that is
what lets peer input construct them and run their post-init code.
`codec.register(Order)` registers `Order` and every declared class its field
annotations refer to, through lists, dicts, unions, and `X | None`; it also
accepts type expressions such as `list[Order]` or `Cat | Dog`. A class that
has no declaration, such as one from another library, is registered with
`codec.register(cls, name="com.example.Name")` and then encodes and decodes
with that codec only.

Fields match by name, not position. Missing fields take their defaults;
missing required fields raise `DecodeError`. Unknown fields are consumed and
ignored. Field names are sent in camelCase by default; pass
`field_naming=None` to `java_class` or `register` to send Python names
unchanged. Field naming must not map multiple Python attributes to the same
wire name: `user_name` and `userName` conflict.

Dataclass and attrs decoding bypasses `__init__`, fills fields, then invokes
`__post_init__` or `__attrs_post_init__`. Dataclass InitVars use their defaults;
required InitVars needed by post-init are rejected at registration. attrs
validators and converters do not run. Annotations are not validated. Use a
validation hook or a Converter when validation is required.

### Java types a Python value cannot express

A Python `int` may be a Java `int`, `long`, or `BigInteger`, and an aware
`datetime` a `Date`, `Instant`, `OffsetDateTime`, or `ZonedDateTime`. Java
converts where it declares the type: an `int` reads into a `long` field.
It does not convert into `BigInteger`, `Instant`, `OffsetDateTime`, and
`ZonedDateTime`, nor in positions that do not declare the type, such as
`Object` and a value read as a raw `List` whose `Long` elements Java's
generics erase. There, annotate the value with `hessian2.Long`,
`hessian2.BigInteger`, `hessian2.Instant`, `hessian2.OffsetDateTime`, or
`hessian2.ZonedDateTime`. Values stay plain `int` and `datetime`, and type
checkers read the annotations as those types:

```python
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict

import hessian2


class Extra(TypedDict):  # A Java Map<String, Object> with known keys
    order_ref: hessian2.Long
    note: str


@hessian2.java_class("com.example.Payment")
@dataclass
class Payment:
    payment_id: int  # A Java long field reads an int
    amount: hessian2.BigInteger
    paid_at: hessian2.Instant
    item_ids: list[hessian2.Long]  # A Java List<Long>
    extra: Extra


payment = Payment(
    1, 5, datetime(2026, 1, 1, tzinfo=UTC), [7, 8], {"order_ref": 9, "note": "ok"}
)
codec = hessian2.Codec()
codec.register(Payment)
received = codec.loads(codec.dumps(payment))
print(received.amount, received.item_ids, received.paid_at.isoformat())
# 5 [7, 8] 2026-01-01T00:00:00+00:00

# A top-level value has no field annotation; pass its type instead.
ids = hessian2.dumps([7, 8], type=list[hessian2.Long])
print(hessian2.loads(ids))  # [7, 8]
```

`dumps_many(values, types=[...])` takes one annotation per value, or `None`.
`codec.java_name(tp)` returns the Java class a type always encodes as, such
as `"com.example.Payment"` or `"java.math.BigInteger"`, and `None` for types
like `int` and `list` that map to several Java types.

A list, set, or dict that appears again in the same stream is written as a
reference to the first, so Java shares one object, unless that position has
tags other than the first one's: a Java object holds one type, so the value
is written again, as pydantic writes each position by its own annotation. A
position without tags refers to the first.

### Options and registration conflicts

```python
import hessian2

codec = hessian2.Codec(
    unknown_class="raise",  # "hessian_object" or "dict" for unknown names
    max_depth=256,  # Maximum nesting of objects, lists, and maps
)
```

`loads` and `loads_many` take `unknown_class=` to override the codec's
setting for one call. Duplicate registration of the same type under the same
name is allowed. A name assigned to a different type, or a type assigned to a
different name, raises `ValueError` unless you explicitly pass
`replace=True`:

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
codec.register(OldPoint, name="com.example.Point")
# Explicit replacement; without replace=True this raises ValueError.
codec.register(NewPoint, name="com.example.Point", replace=True)
point = codec.loads(codec.dumps(NewPoint(3, 4)))
print(point)  # NewPoint(x=3, y=4)
```

`replace=True` can also override built-in JDK mappings. Moving a type to a
new encoding name keeps its old name as a decode alias. There is no global
registry: the module-level functions encode declared classes and decode
only built-in types.

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
    hessian2.Converter(
        Order,
        to_fields=Order.model_dump,
        from_fields=Order.model_validate,
    ),
    name="com.example.Order",
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
| `hessian2.Long`, `hessian2.BigInteger` annotations on an `int` | long, BigInteger |
| `hessian2.Instant`, `OffsetDateTime`, `ZonedDateTime` annotations on an aware `datetime` | the `java.time` class |

Typed Java lists and arrays decode as Python lists; typed maps decode as
dicts. Instant, OffsetDateTime, and ZonedDateTime decode as aware datetimes;
ZonedDateTime keeps its zone when `zoneinfo` knows it. Java nanoseconds are
truncated to Python microseconds. An aware `time` has no default Java mapping
and raises `EncodeError`.

## Compatibility

Tested in both directions with Caucho Hessian 4.0.66, hessian-lite 4.0.5,
and dubbo-go-hessian2 1.13.3. Java implementations determine the forms this
writer emits; individual type mappings still differ between peers.

- Java reads an int encoding into a `long` or `Long` field, and hessian-lite
  also into the elements of a `List<Long>` or `Map<String, Long>` field. A
  small integer still arrives as `Integer` in an `Object` position, a value
  read as a raw `List` (as `readObject(List.class)` does), and Caucho's
  generic fields; annotate it with `hessian2.Long` there.
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
A `ValueError` or `TypeError` from a converter, post-init method, default
factory, or field getter is raised as `DecodeError` or `EncodeError`, with
the original as its `__cause__`. Excess encoding depth raises `EncodeError`.
Lengths and references are checked, but `max_depth` does not cap total
memory or CPU use. Limit incoming message size in the application. See [SECURITY.md](SECURITY.md).

## Development and community

- [Contributing and release guide](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

[`hessian2-core`](crates/hessian2-core/README.md) provides the Rust reader
and writer, with a separate README and API. Both crates forbid unsafe code.

## License

MIT
