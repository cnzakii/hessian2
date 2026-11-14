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
- **Typed decoding.** `loads(data, type=Order)` builds and checks the
  classes an annotation names, as msgspec does. Peer input constructs no
  other class.
- **Python objects.** Dataclasses, attrs classes, named tuples, and enums
  map to Java classes with `@hessian2.java_class`. Use a Converter for
  Pydantic models and other types.
- **Shared references.** Preserve object identity across nested objects and
  collections, including cycles in lists, dictionaries, and dataclass/attrs
  instances.
- **Java types.** Decimals, UUIDs, dates, durations, and large integers map
  to their Java types, and annotations such as `hessian2.Long` name a Java
  type where a Python value cannot.

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

# Decoding builds the classes the type names: Order, and the Customer and
# Address its fields refer to. Other classes in the input raise DecodeError.
received = hessian2.loads(payload, type=Order)

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

`loads` accepts bytes-like input and requires exactly one value.
`dumps_many` and `loads_many` write and read several top-level values that
share the stream's class definitions and references:

```python
import hessian2

shared = {"status": "ready"}
stream = hessian2.dumps_many(["header", shared, shared])
header, first, second = hessian2.loads_many(stream)
print(header, first["status"], first is second)  # header ready True
```

`loads_many(data, types=[...])` decodes exactly one value per annotation.
When a value's type depends on the values before it, as when a header names
what follows, read one value at a time, as Java's `Hessian2Input.readObject`
and `pickle.Unpickler.load` do:

```python
import hessian2

stream = hessian2.dumps_many(["point", [1, 2]])
reader = hessian2.reader(stream)
kind = reader.load(type=str)
point = reader.load(type=tuple[int, int])  # the header chose this type
print(kind, point)  # point (1, 2)
```

Each `load` shares the stream's class definitions and references with the
ones before it. `reader.tell()` gives the offset of the next value, and
`load` raises `EOFError` after the last one. A value that fails to decode
leaves the position unknown, so later calls raise `DecodeError`.

## Typed decoding

`type=` gives the annotation a value must match. Decoding builds the
classes it names and checks every value against it; anything else raises
`DecodeError`. Fields of declared and registered classes decode by their own
annotations, with or without `type=`. Three things decide what decoding
builds:

| | Decides | Set with |
|---|---|---|
| Naming | Which Java class a Python class is | `@hessian2.java_class(name)` on your classes; `codec.register(cls, name=...)` or a `Converter` for classes you cannot decorate |
| Trust | Which classes peer input may construct at a position | The classes the annotation there names; and wherever they fit, the classes registered with the codec |
| Shape | What a value must look like | The annotation: `type=` for a top-level value, field annotations below it |

An object on the wire names its Java class. Decoding builds it when that
class is trusted where the object appears and fits the annotation there.
Otherwise, a position that allows any value applies `unknown_class`, and any
other position raises `DecodeError`.

### Which classes are built

- `loads(data, type=Order)` builds `Order` and the classes its fields name,
  through lists, dicts, unions, `X | None`, and type arguments such as
  `Result[Order]`. No registration is needed.
- A union such as `Cat | Dog` picks its member by Java class name. A union
  may have one member that decodes a list, one that decodes a map, and one
  class per Java name.
- A position annotated with a base class, such as one standing for a Java
  interface, takes registered subclasses: annotate the base and register
  each implementation.
- Where any value is allowed, as for `Any`, `object`, a `Protocol`, a type
  parameter without an argument, and `loads` without `type=`, only
  registered classes are built. A `HessianObject` annotation keeps any
  object raw.
- A map where the annotation names one class, such as `Order | None`, fills
  that class by field name, as Java's `readObject(Order.class)` does.
  Nothing in the map, not even a `"class"` key, chooses the class, and keys
  that name no field are ignored. Where the annotation also allows a dict,
  the map stays a dict; where it names several classes, a map raises
  `DecodeError`.
- Fields a class does not have, keys a TypedDict does not declare, and the
  fields of JDK values decode as plain data that builds no class.

`codec.register(cls)` trusts that one class wherever it fits. Its fields
still decode by their own annotations.

### How values are checked

- Types are checked strictly, as msgspec checks them: `bool` is not an
  `int`, an `int` becomes a `float` where one is expected, and a
  `tuple[str, ...]` or `set[str]` builds that container from a Java list.
- A type that contains itself, such as `Node[int]` whose `next` is a
  `Node[T] | None`, a recursive TypedDict, or a recursive type alias, is
  checked at every level. A union that contains itself directly, as in
  `type Loop = int | Loop`, raises `TypeError`.
- A back-reference is checked, contents included, against the annotation
  where it appears, but not converted: it is the same object, so it must
  already have that shape. An instance your code changed after it was
  built, in a post-init method or a reader's earlier result, is checked by
  its class only.
- A converter's result is checked by its class and type arguments, not
  field by field.
- `Literal` and other special forms decode any value. Use a validation hook
  or a Converter for constraints beyond types.
- An error inside a value says where it happened, as in
  `expected str, got int - at $.address.city`.

## Classes

| Declare or register | Object representation |
|---|---|
| dataclass, attrs class | One field per declared attribute |
| `typing.NamedTuple`, `collections.namedtuple` | One field per named item |
| `enum.Enum` | A `name` field, as Java writes an enum |
| `Converter` for any other type | The field dictionary returned by `to_fields` |

`@hessian2.java_class(name)` records the Java class name on a class. Every
codec, including the module-level functions, encodes declared classes. A
subclass is not declared by inheriting from a declared class. A class you
cannot decorate, such as one from another library, is named with
`codec.register(cls, name="com.example.Name")` and then encodes and decodes
with that codec only.

Fields match by name, not position. Missing fields take their defaults;
missing required fields raise `DecodeError`. Unknown fields are consumed and
ignored. Field names are sent in camelCase by default; pass
`field_naming=None` to `java_class` or `register` to send Python names
unchanged. Field naming must not map two Python attributes to the same wire
name: `user_name` and `userName` conflict.

Dataclass and attrs decoding bypasses `__init__`, fills fields, then invokes
`__post_init__` or `__attrs_post_init__`. Dataclass InitVars use their
defaults; required InitVars needed by post-init are rejected at
registration. attrs validators and converters do not run. A dataclass or
attrs subclass of `Exception` decodes too, with empty `args` unless its
`__post_init__` sets them; an `ExceptionGroup` subclass cannot, because a
group cannot exist without its exceptions.

A Java enum decodes from the object Java writes or from a map with its
`"name"`, as Java's `EnumDeserializer` reads one. A `StrEnum` or `IntEnum`
with no Java class name is written as its value and decodes from it where an
annotation names it.

## Java types a Python value cannot express

A Python `int` may be a Java `int`, `long`, or `BigInteger`, and an aware
`datetime` a `Date`, `Instant`, `OffsetDateTime`, or `ZonedDateTime`. Java
converts where it declares the type: an `int` reads into a `long` field. It
does not convert into `BigInteger` or the `java.time` types, nor where the
type is not declared, such as an `Object` or a raw `List` whose `Long`
elements Java's generics erase. There, annotate the value with
`hessian2.Long`, `hessian2.BigInteger`, `hessian2.Instant`,
`hessian2.OffsetDateTime`, or `hessian2.ZonedDateTime`. Values stay plain
`int` and `datetime`, and type checkers read the annotations as those types:

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

# A value where no annotation can be written, such as in a plain dict, carries
# its own: the id is a Java long.
row = hessian2.dumps({"id": hessian2.Typed(7, hessian2.Long)})
print(row == hessian2.dumps({"id": 7}, type=dict[str, hessian2.Long]))  # True
```

- `dumps(value, type=...)` and `dumps_many(values, types=[...])` annotate
  top-level values; `types` takes one annotation, or `None`, per value.
- `hessian2.Typed(value, annotation)` writes a value as one with that
  annotation where none can be written, such as in a plain dict or list.
  Decoding returns plain values.
- `tuple[X, ...]` is a Java array, and `list[X]` a Java `List`. An array is
  written as Java writes one, a list with the array's type name: `[string`
  for `tuple[str, ...]` (`String[]`), `[object` for `tuple[Any, ...]`
  (`Object[]`), and `[int` for `tuple[int, ...]` (`int[]`, whose items must
  fit an `int`); `int | None` items make an `Integer[]`. Java reads it into
  any array or collection it declares, and peers that read by type name,
  such as Go's `[]hessian.Object` for `Object[]`, need it.
- A `datetime` is a `java.util.Date` when aware and a `LocalDateTime` when
  naive. Where one Java type must be named for a position, such as a method
  parameter, annotate it `hessian2.Date` or `hessian2.LocalDateTime`: values
  are written as without the annotation, and a datetime of the other kind
  raises `EncodeError`. When decoding, these annotations and the `java.time`
  ones require an aware datetime, or a naive one for `LocalDateTime`.
- A list, set, or dict that appears again in a stream is written as a
  reference to the first, so Java shares one object, unless its position has
  other tags than the first one's: a Java object has one type, so the value
  is written again.

`codec.java_name(tp)` returns the Java class a type always encodes as, such
as `"com.example.Payment"`, `"java.math.BigInteger"`, or
`"[Ljava.lang.String;"` for `tuple[str, ...]`. A type always written as a
Java primitive gives the primitive, which Java also reads into its box:
`"double"` for `float`, `"boolean"` for `bool`, and `"long"` for
`hessian2.Long`. With `| None`, which a primitive cannot hold, it gives the
box, such as `"java.lang.Long"`. Types written as one of several Java types,
such as `int` (an int, long, or BigInteger by size) and `list`, give `None`.

## Options and registration conflicts

```python
import hessian2

codec = hessian2.Codec(
    unknown_class="raise",  # "hessian_object" or "dict" for unknown names
    max_depth=256,  # Maximum nesting of objects, lists, and maps
)
```

`loads`, `loads_many`, and `Reader.load` take `unknown_class=` to override
the codec's setting for one call.

Registering the same type under the same name again is allowed. A name
assigned to a different type, or a type assigned to a different name, raises
`ValueError` unless you pass `replace=True`:

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
new name keeps its old name as a decode alias. There is no global registry:
the module-level functions encode declared classes, decode the classes an
annotation names, and in `Any` positions decode only built-in types.

## Pydantic and other types

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

`codec.as_object(value)` returns the `HessianObject` that `dumps` writes for
an instance of a declared or registered class, a converter's type, or an
enum, without encoding it. It is one level deep: fields are keyed by Java
name, in order, and hold the value's own field values, except that a field
with an encoding annotation such as `hessian2.Long` holds a `Typed`, so it
keeps its Java type when moved, as into a map. Encoding the result writes
what encoding the value does, unless something in the value refers back to
it. Values written in another form, such as lists and `Decimal`, give
`None`; a class with no Hessian form raises `EncodeError`:

```python
from dataclasses import dataclass

import hessian2


@hessian2.java_class("com.example.Account")
@dataclass
class Account:
    owner: str
    account_id: hessian2.Long


account = Account("alice", 7)
obj = hessian2.as_object(account)
print(obj.class_name, list(obj.fields))  # com.example.Account ['owner', 'accountId']
print(obj.fields["accountId"] == hessian2.Typed(7, hessian2.Long))  # True
print(hessian2.dumps(obj) == hessian2.dumps(account))  # True
print(hessian2.as_object([account]))  # None
```

## Type mappings

| Python | Hessian / Java |
|---|---|
| `None`, `bool`, `float`, `str`, `bytes` | `null`, boolean, double, String, byte array |
| `int` | int within 32 bits, long within 64 bits, otherwise BigInteger |
| `list`, unregistered `tuple` | Untyped list; decoded as `list`, or the container an annotation names |
| `list` or `tuple` annotated `tuple[X, ...]` | Typed list with the Java array's type name, such as `[string` |
| `set`, `frozenset` | HashSet typed list; decoded as `list`, or the container an annotation names |
| `dict` | Untyped map; decoded as `dict` |
| aware `datetime` | java.util.Date; decoded in UTC, millisecond precision |
| naive `datetime`, `date`, naive `time`, `timedelta` | LocalDateTime, LocalDate, LocalTime, Duration |
| `Decimal`, `UUID` | BigDecimal, UUID |
| `hessian2.Long`, `hessian2.BigInteger` annotations on an `int` | long, BigInteger |
| `hessian2.Instant`, `OffsetDateTime`, `ZonedDateTime` annotations on an aware `datetime` | the `java.time` class |
| `hessian2.Date`, `LocalDateTime` annotations on an aware or naive `datetime` | java.util.Date, LocalDateTime, as without them |

- Typed Java lists and arrays decode as lists, and typed maps as dicts,
  unless an annotation names another container.
- `Instant`, `OffsetDateTime`, and `ZonedDateTime` decode as aware
  datetimes; a `ZonedDateTime` keeps its zone when `zoneinfo` knows it. Java
  nanoseconds are truncated to Python microseconds.
- An aware `time` has no Java mapping and raises `EncodeError`.
- Without an annotation, a map key decodes only when a peer cannot make keys
  collide, which would make building the dict take quadratic time: a `str`,
  `bytes`, `bool`, `None`, `float`, an `int` within 64 bits, a date or time,
  or an enum member. msgpack-python likewise allows only `str` and `bytes`
  keys by default. Other keys, such as lists, `Decimal`, and `UUID`, need an
  annotation, such as `dict[UUID, str]` or `dict[tuple[int, ...], str]`.

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
key methods can run Python code. Annotations check the types of input, not
other constraints.

`DecodeError` subclasses `ValueError`; `EncodeError` subclasses `TypeError`.
A `ValueError` or `TypeError` from a converter, post-init method, default
factory, or field getter is raised as `DecodeError` or `EncodeError`, with
the original as its `__cause__`. Excess encoding depth raises `EncodeError`.
Lengths and references are checked, but `max_depth` does not cap total
memory or CPU use. Limit incoming message size in the application. See
[SECURITY.md](SECURITY.md).

## Development and community

- [Contributing and release guide](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

[`hessian2-core`](crates/hessian2-core/README.md) provides the Rust reader
and writer, with a separate README and API. Both crates forbid unsafe code.

## License

MIT
