//! References wire contracts.

use hessian2_core::{ClassDef, Reader, Token, Writer};

fn written(write: impl FnOnce(&mut Writer)) -> Vec<u8> {
    let mut writer = Writer::new();
    write(&mut writer);
    writer.into_bytes()
}

#[test]
fn lists_maps_and_objects_share_reference_numbers() {
    let bytes = written(|w| {
        let class = w.define_class("Car", &["color", "model"]);
        assert_eq!(w.begin_object(class), 0);
        w.write_str("red");
        w.write_str("corvette");
        assert_eq!(w.begin_list(2, Some("[int")), 1);
        w.write_int(0);
        w.write_ref(0);
        assert_eq!(w.begin_map(None), 2);
        w.end_map();
        assert_eq!(w.begin_list(0, Some("[int")), 3);
    });
    let mut reader = Reader::new(&bytes);
    assert_eq!(reader.next_token().unwrap(), Token::Object { class: 0 });
    assert_eq!(
        reader.class(0),
        &ClassDef {
            name: "Car".into(),
            fields: vec!["color".into(), "model".into()]
        }
    );
    reader.skip().unwrap();
    reader.skip().unwrap();
    assert_eq!(
        reader.next_token().unwrap(),
        Token::List {
            type_name: Some(0),
            len: Some(2)
        }
    );
    assert_eq!(reader.type_name(0), "[int");
    assert_eq!(reader.next_token().unwrap(), Token::Int(0));
    assert_eq!(reader.next_token().unwrap(), Token::Ref(0));
    assert_eq!(reader.next_token().unwrap(), Token::Map { type_name: None });
    assert!(reader.end().unwrap());
    assert_eq!(
        reader.next_token().unwrap(),
        Token::List {
            type_name: Some(0),
            len: Some(0)
        }
    );
    assert!(reader.is_empty());
}

#[test]
fn class_and_reference_indexes_survive_multiple_top_level_values() {
    let mut writer = Writer::new();
    for index in 0..17 {
        let class = writer.define_class(&format!("C{index}"), &["self"]);
        assert_eq!(writer.begin_object(class), index);
        writer.write_ref(index);
    }
    assert_eq!(writer.begin_list(0, Some("t")), 17);
    assert_eq!(writer.begin_map(Some("t")), 18);
    writer.end_map();
    writer.write_ref(0);
    writer.write_ref(18);
    let mut reader = Reader::new(writer.as_bytes());
    for class in 0..17 {
        assert_eq!(reader.next_token().unwrap(), Token::Object { class });
        assert_eq!(reader.class(class).name, format!("C{class}"));
        assert_eq!(reader.next_token().unwrap(), Token::Ref(class));
    }
    reader.skip().unwrap();
    reader.skip().unwrap();
    assert_eq!(reader.next_token().unwrap(), Token::Ref(0));
    assert_eq!(reader.next_token().unwrap(), Token::Ref(18));
    assert!(reader.is_empty());
}

#[test]
fn skipping_nested_beans_keeps_all_three_stream_tables() {
    let mut writer = Writer::new();
    let class = writer.define_class("Bean", &["matrix", "next"]);
    assert_eq!(writer.begin_object(class), 0);
    assert_eq!(writer.begin_list(2, Some("[[int")), 1);
    assert_eq!(writer.begin_list(0, Some("[int")), 2);
    assert_eq!(writer.begin_list(2, Some("[int")), 3);
    writer.write_int(1);
    writer.write_int(2);
    writer.write_ref(0);
    writer.begin_object(class);
    writer.begin_map(Some("[int"));
    writer.end_map();
    writer.write_ref(3);
    writer.write_ref(2);

    let mut reader = Reader::new(writer.as_bytes());
    reader.skip().unwrap();
    assert_eq!(reader.class(0).name, "Bean");
    assert_eq!(reader.type_name(0), "[[int");
    assert_eq!(reader.type_name(1), "[int");
    assert_eq!(reader.next_token().unwrap(), Token::Object { class: 0 });
    assert_eq!(
        reader.next_token().unwrap(),
        Token::Map { type_name: Some(1) }
    );
    assert!(reader.end().unwrap());
    assert_eq!(reader.next_token().unwrap(), Token::Ref(3));
    assert_eq!(reader.next_token().unwrap(), Token::Ref(2));
    assert!(reader.is_empty());
}

#[test]
fn class_and_value_indexes_cross_compact_integer_boundaries() {
    let mut writer = Writer::new();
    for index in 0..=2048 {
        let class = writer.define_class(&format!("C{index}"), &["self"]);
        assert_eq!(class, index);
        assert_eq!(writer.begin_object(class), index);
        writer.write_ref(index);
    }
    let mut reader = Reader::new(writer.as_bytes());
    for index in 0..=2048 {
        assert_eq!(reader.next_token().unwrap(), Token::Object { class: index });
        assert_eq!(reader.class(index).name, format!("C{index}"));
        assert_eq!(reader.next_token().unwrap(), Token::Ref(index));
    }
    assert!(reader.is_empty());
}

#[test]
fn a_resumed_reader_keeps_the_stream_tables() {
    let mut writer = Writer::new();
    let class = writer.define_class("Bean", &["x"]);
    writer.begin_object(class);
    writer.write_int(1);
    writer.write_ref(0);
    writer.begin_object(class);
    writer.write_int(2);

    let input = writer.as_bytes();
    let mut reader = Reader::new(input);
    assert_eq!(reader.next_token().unwrap(), Token::Object { class: 0 });
    assert_eq!(reader.next_token().unwrap(), Token::Int(1));
    let state = reader.into_state();

    let mut reader = Reader::resume(input, state);
    assert_eq!(reader.next_token().unwrap(), Token::Ref(0));
    assert_eq!(reader.next_token().unwrap(), Token::Object { class: 0 });
    assert_eq!(reader.class(0).name, "Bean");
    assert_eq!(reader.next_token().unwrap(), Token::Int(2));
    assert!(reader.is_empty());
}

#[test]
fn a_reader_state_gives_the_offset_of_the_next_unread_byte() {
    let mut reader = Reader::new(b"\x91\x92");
    reader.next_token().unwrap();

    let state = reader.into_state();

    assert_eq!(state.offset(), 1);
}

#[test]
fn reference_indexes_use_literal_integer_headers_at_compact_boundaries() {
    for (index, expected) in [
        (0, &b"Q\x90"[..]),
        (1, b"Q\x91"),
        (46, b"Q\xbe"),
        (47, b"Q\xbf"),
        (48, b"Q\xc8\x30"),
        (2047, b"Q\xcf\xff"),
        (2048, b"Q\xd4\x08\0"),
        (262143, b"Q\xd7\xff\xff"),
        (262144, b"QI\0\x04\0\0"),
    ] {
        let mut writer = Writer::new();
        for _ in 0..=index {
            writer.begin_list(0, None);
        }
        let start = writer.as_bytes().len();

        writer.write_ref(index);

        assert_eq!(&writer.as_bytes()[start..], expected, "{index}");
        let mut reader = Reader::new(writer.as_bytes());
        for _ in 0..=index {
            reader.skip().unwrap();
        }
        assert_eq!(reader.next_token().unwrap(), Token::Ref(index));
    }
}
