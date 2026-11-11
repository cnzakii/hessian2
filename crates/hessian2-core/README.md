# hessian2-core

[Hessian 2.0](http://hessian.caucho.com/doc/hessian-serialization.html)
serialization primitives for Rust: a pull `Reader` that turns bytes into
tokens, and a `Writer` that turns calls into bytes.

> `hessian2-core` is alpha software. Its API may change before the first
> stable release.

- **Java-compatible bytes.** The writer picks the forms Java's Hessian
  implementations (Caucho Hessian 4.0.66 and hessian-lite 4.0.5) pick for the
  same values, and the reader accepts everything they send.
- **Per-stream tables.** Both sides keep the class definitions, type names,
  and reference numbers that Hessian shares across the values of one stream.
- **Zero-copy reading.** Strings and binaries borrow from the input unless
  they arrive in chunks.
- **No dependencies, no `unsafe`.**

The crate does not map values to Rust types: building values from tokens, and
choosing calls for values, belongs to the caller. The
[`hessian2`](https://github.com/cnzakii/hessian2) Python package is built this
way on top of it.

## Example

Write an object of a class `com.example.User`, then read it back:

```rust
use hessian2_core::{Reader, Token, Writer};

let mut writer = Writer::new();
let class = writer.define_class("com.example.User", &["name", "age"]);
writer.begin_object(class);
writer.write_str("alice");
writer.write_int(30);
let bytes = writer.into_bytes();

let mut reader = Reader::new(&bytes);
let Token::Object { class } = reader.next_token()? else {
    panic!("expected an object");
};
assert_eq!(reader.class(class).name, "com.example.User");
assert_eq!(reader.class(class).fields, ["name", "age"]);
assert_eq!(reader.next_token()?, Token::String(b"alice".as_slice().into()));
assert_eq!(reader.next_token()?, Token::Int(30));
assert!(reader.is_empty());
# Ok::<(), hessian2_core::Error>(())
```

`Token::String` holds the bytes as sent, which are UTF-8 except that Java
writes characters outside the Basic Multilingual Plane as surrogate pairs
(CESU-8); `hessian2_core::text` converts them.

## Reading

`Reader::next_token` returns one `Token` per value, or the start of a list,
map, or object whose items follow:

- `List { len: Some(n), .. }` is followed by `n` values; with `len: None`,
  call `Reader::end` before each value until it returns `true`.
- `Map { .. }` is followed by key and value pairs until `Reader::end`.
- `Object { class }` is followed by one value per field of
  `Reader::class(class)`.
- `Ref(n)` refers back to the `n`-th list, map, or object started in the
  stream.

Malformed or truncated input returns an `Error` with its offset; the reader
never panics on input. `Reader::skip` discards one complete value, nested at
most `MAX_SKIP_DEPTH` deep.

## Writing

The `Writer` methods write one value or start a container, in the shortest
form Java uses: `write_int`, `write_long`, `write_double`, `write_bool`,
`write_null`, `write_str`, `write_binary`, `write_date`, `begin_list`,
`begin_map` and `end_map`, `define_class` and `begin_object`, and `write_ref`.

## License

MIT
