#![no_main]

use std::borrow::Cow;

use hessian2_core::{Reader, Token, Writer, text};
use libfuzzer_sys::fuzz_target;

fn string(reader: &mut Reader<'_>, expected: &[u16]) {
    let Token::String(bytes) = reader.next_token().unwrap() else {
        panic!("expected string")
    };
    assert_eq!(text::cesu8_to_utf16(&bytes).as_deref(), Some(expected));
}

fuzz_target!(|data: &[u8]| {
    let mut bits = [0; 8];
    let len = data.len().min(8);
    bits[..len].copy_from_slice(&data[..len]);
    let long = i64::from_be_bytes(bits);
    let int = long as i32;
    let double = f64::from_be_bytes(bits);
    let boolean = bits[0] & 1 != 0;
    let utf8 = String::from_utf8_lossy(data);
    let units: Vec<u16> = data
        .chunks_exact(2)
        .map(|b| u16::from_be_bytes([b[0], b[1]]))
        .collect();

    let mut writer = Writer::new();
    writer.write_null();
    writer.write_bool(boolean);
    writer.write_int(int);
    writer.write_long(long);
    writer.write_double(double);
    writer.write_date(long);
    writer.write_str(&utf8);
    writer.write_utf16(&units);
    writer.write_binary(data);
    let list = writer.begin_list(3, Some("t"));
    writer.write_ref(list);
    let map = writer.begin_map(Some("t"));
    writer.write_str("self");
    writer.write_ref(map);
    writer.end_map();
    let class = writer.define_class("Node", &["child"]);
    let object = writer.begin_object(class);
    writer.write_ref(object);
    writer.begin_list(0, Some("t"));
    writer.begin_object(class);
    writer.write_ref(list);
    writer.write_ref(object);

    let mut reader = Reader::new(writer.as_bytes());
    assert_eq!(reader.next_token().unwrap(), Token::Null);
    assert_eq!(reader.next_token().unwrap(), Token::Bool(boolean));
    assert_eq!(reader.next_token().unwrap(), Token::Int(int));
    assert_eq!(reader.next_token().unwrap(), Token::Long(long));
    let Token::Double(decoded) = reader.next_token().unwrap() else {
        panic!("expected double")
    };
    assert!(decoded == double || (decoded.is_nan() && double.is_nan()));
    assert_eq!(reader.next_token().unwrap(), Token::Date(long));
    string(&mut reader, &utf8.encode_utf16().collect::<Vec<_>>());
    string(&mut reader, &units);
    assert_eq!(
        reader.next_token().unwrap(),
        Token::Binary(Cow::Borrowed(data))
    );
    assert_eq!(
        reader.next_token().unwrap(),
        Token::List {
            type_name: Some(0),
            len: Some(3)
        }
    );
    assert_eq!(reader.type_name(0), "t");
    assert_eq!(reader.next_token().unwrap(), Token::Ref(list));
    assert_eq!(
        reader.next_token().unwrap(),
        Token::Map { type_name: Some(0) }
    );
    string(
        &mut reader,
        &[b's' as u16, b'e' as u16, b'l' as u16, b'f' as u16],
    );
    assert_eq!(reader.next_token().unwrap(), Token::Ref(map));
    assert!(reader.end().unwrap());
    assert_eq!(reader.next_token().unwrap(), Token::Object { class });
    assert_eq!(reader.class(class).fields, ["child"]);
    assert_eq!(reader.next_token().unwrap(), Token::Ref(object));
    assert_eq!(
        reader.next_token().unwrap(),
        Token::List {
            type_name: Some(0),
            len: Some(0)
        }
    );
    assert_eq!(reader.next_token().unwrap(), Token::Object { class });
    assert_eq!(reader.next_token().unwrap(), Token::Ref(list));
    assert_eq!(reader.next_token().unwrap(), Token::Ref(object));
    assert!(reader.is_empty());

    let mut reader = Reader::new(writer.as_bytes());
    while !reader.is_empty() {
        reader.skip().unwrap();
    }
});
