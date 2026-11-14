//! Objects wire contracts.

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

#[test]
fn object_class_indexes_use_literal_headers_at_compact_boundaries() {
    for (class, expected) in [
        (0, &b"\x60"[..]),
        (1, b"\x61"),
        (14, b"\x6e"),
        (15, b"\x6f"),
        (16, b"O\xa0"),
        (47, b"O\xbf"),
        (48, b"O\xc8\x30"),
        (2047, b"O\xcf\xff"),
        (2048, b"O\xd4\x08\0"),
        (262143, b"O\xd7\xff\xff"),
        (262144, b"OI\0\x04\0\0"),
    ] {
        let mut writer = Writer::new();
        for _ in 0..=class {
            writer.define_class("X", &[] as &[&str]);
        }
        let start = writer.as_bytes().len();

        writer.begin_object(class);

        assert_eq!(&writer.as_bytes()[start..], expected, "{class}");
        let mut reader = Reader::new(writer.as_bytes());
        assert_eq!(reader.next_token().unwrap(), Token::Object { class });
    }
}
