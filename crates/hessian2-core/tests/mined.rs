//! Wire regressions mined from Java/Go reuse, arrays, and SOFA generic beans.
//! Provenance and Python-facing limitations are recorded in TESTING.md.

use hessian2_core::{Reader, Token, Writer};

#[test]
fn duplicate_inherited_field_slots_remain_distinct_in_the_core() {
    // Caucho writes child then parent; hessian-lite writes parent then child.
    for values in [["child", "parent"], ["parent", "child"]] {
        let mut writer = Writer::new();
        let class = writer.define_class("interop.ShadowChild", &["same", "same"]);
        assert_eq!(writer.begin_object(class), 0);
        for value in values {
            writer.write_str(value);
        }
        let mut reader = Reader::new(writer.as_bytes());
        assert_eq!(reader.next_token().unwrap(), Token::Object { class: 0 });
        assert_eq!(reader.class(0).fields, ["same", "same"]);
        for value in values {
            assert_eq!(
                reader.next_token().unwrap(),
                Token::String(value.as_bytes().into())
            );
        }
        assert!(reader.is_empty());
    }
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
fn wide_beans_cross_field_count_and_output_buffer_boundaries() {
    for count in [0, 1, 47, 48, 255, 256, 2048] {
        let fields: Vec<_> = (0..count).map(|i| format!("field_{i}")).collect();
        let mut writer = Writer::new();
        let class = writer.define_class("Wide", &fields);
        writer.begin_object(class);
        for i in 0..count {
            writer.write_int(i);
        }
        writer.write_ref(0);
        let mut reader = Reader::new(writer.as_bytes());
        assert_eq!(reader.next_token().unwrap(), Token::Object { class: 0 });
        assert_eq!(reader.class(0).fields, fields);
        for i in 0..count {
            assert_eq!(reader.next_token().unwrap(), Token::Int(i));
        }
        assert_eq!(reader.next_token().unwrap(), Token::Ref(0));
        assert!(reader.is_empty());
    }
}
