//! Containers wire contracts.

use hessian2_core::{ErrorKind, Reader, Token, Writer};

#[test]
fn variable_length_lists_end_with_z() {
    let mut reader = Reader::new(b"W\x90\x91Z");
    assert_eq!(
        reader.next_token().unwrap(),
        Token::List {
            type_name: None,
            len: None
        }
    );
    assert!(!reader.end().unwrap());
    reader.skip().unwrap();
    reader.skip().unwrap();
    assert!(reader.end().unwrap());
}

#[test]
fn offsets_follow_reading_skipping_and_container_ends() {
    let mut writer = Writer::new();
    writer.write_bool(true);
    writer.write_bool(false);
    writer.write_str("é");
    writer.begin_map(None);
    writer.write_str("key");
    writer.write_null();
    writer.end_map();
    assert_eq!(writer.as_bytes(), b"TF\x01\xc3\xa9H\x03keyNZ");
    let mut reader = Reader::new(writer.as_bytes());
    assert_eq!(reader.offset(), 0);
    for (token, offset) in [(Token::Bool(true), 1), (Token::Bool(false), 2)] {
        assert_eq!(reader.next_token().unwrap(), token);
        assert_eq!(reader.offset(), offset);
    }
    reader.skip().unwrap();
    assert_eq!(reader.offset(), 5);
    assert_eq!(reader.next_token().unwrap(), Token::Map { type_name: None });
    assert!(!reader.end().unwrap());
    assert_eq!(reader.offset(), 6);
    reader.skip().unwrap();
    reader.skip().unwrap();
    assert_eq!(reader.offset(), 11);
    assert!(reader.end().unwrap());
    assert_eq!(reader.offset(), writer.as_bytes().len());
    assert!(reader.is_empty());
    let error = reader.end().unwrap_err();
    assert_eq!(error.kind(), ErrorKind::UnexpectedEof);
    assert_eq!(error.offset(), reader.offset());
}

#[test]
fn type_indexes_are_shared_by_maps_and_arrays_beyond_short_ints() {
    let mut writer = Writer::new();
    for index in 0..=48 {
        writer.begin_list(0, Some(&format!("[T{index}")));
    }
    for index in [0, 47, 48] {
        writer.begin_map(Some(&format!("[T{index}")));
        writer.end_map();
    }
    let mut reader = Reader::new(writer.as_bytes());
    for index in 0..=48 {
        assert_eq!(
            reader.next_token().unwrap(),
            Token::List {
                type_name: Some(index),
                len: Some(0),
            }
        );
        assert_eq!(reader.type_name(index), format!("[T{index}"));
    }
    for index in [0, 47, 48] {
        assert_eq!(
            reader.next_token().unwrap(),
            Token::Map {
                type_name: Some(index)
            }
        );
        assert!(reader.end().unwrap());
    }
    assert!(reader.is_empty());
}

#[test]
fn list_forms_decode_their_type_and_length() {
    for (wire, type_name, len) in [
        (&b"U\x01t\x90Z"[..], Some(0), None),
        (b"V\x01t\x91\x90", Some(0), Some(1)),
        (b"W\x90Z", None, None),
        (b"X\x91\x90", None, Some(1)),
        (b"\x71\x01t\x90", Some(0), Some(1)),
        (b"\x79\x90", None, Some(1)),
    ] {
        assert_eq!(
            Reader::new(wire).next_token().unwrap(),
            Token::List { type_name, len }
        );
        let mut reader = Reader::new(wire);
        reader.skip().unwrap();
        assert!(reader.is_empty());
    }
}

#[test]
fn list_headers_use_literal_compact_length_forms() {
    for (len, name, expected) in [
        (0, None, &b"\x78"[..]),
        (1, None, b"\x79"),
        (2, None, b"\x7a"),
        (3, None, b"\x7b"),
        (4, None, b"\x7c"),
        (5, None, b"\x7d"),
        (6, None, b"\x7e"),
        (7, None, b"\x7f"),
        (8, None, b"X\x98"),
        (47, None, b"X\xbf"),
        (2047, None, b"X\xcf\xff"),
        (2048, None, b"X\xd4\x08\0"),
        (262143, None, b"X\xd7\xff\xff"),
        (262144, None, b"XI\0\x04\0\0"),
        (48, None, b"X\xc8\x30"),
        (1024, None, b"X\xcc\0"),
        (0, Some("t"), b"\x70\x01t"),
        (1, Some("t"), b"\x71\x01t"),
        (2, Some("t"), b"\x72\x01t"),
        (3, Some("t"), b"\x73\x01t"),
        (4, Some("t"), b"\x74\x01t"),
        (5, Some("t"), b"\x75\x01t"),
        (6, Some("t"), b"\x76\x01t"),
        (7, Some("t"), b"\x77\x01t"),
        (8, Some("t"), b"V\x01t\x98"),
        (47, Some("t"), b"V\x01t\xbf"),
        (48, Some("t"), b"V\x01t\xc8\x30"),
        (1024, Some("t"), b"V\x01t\xcc\0"),
    ] {
        let mut writer = Writer::new();

        writer.begin_list(len, name);

        assert_eq!(writer.as_bytes(), expected, "{len} {name:?}");
    }
}

#[test]
fn maps_and_all_typed_list_forms_share_the_type_table() {
    let wire = b"M\x01tZ\x70\x90U\x90ZV\x90\x90M\x90Z";
    let mut reader = Reader::new(wire);

    for _ in 0..5 {
        reader.skip().unwrap();
    }

    assert!(reader.is_empty());
    assert_eq!(reader.type_name(0), "t");
}
