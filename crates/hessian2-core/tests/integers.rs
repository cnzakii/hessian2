//! Integers wire contracts.

use hessian2_core::{Reader, Token, Writer};

#[test]
fn integers_use_java_compact_forms_at_every_boundary() {
    for (value, bytes) in [
        (i32::MIN, &b"I\x80\0\0\0"[..]),
        (-262_145, b"I\xff\xfb\xff\xff"),
        (-262_144, b"\xd0\0\0"),
        (-2049, b"\xd3\xf7\xff"),
        (-2048, b"\xc0\0"),
        (-17, b"\xc7\xef"),
        (-16, b"\x80"),
        (0, b"\x90"),
        (47, b"\xbf"),
        (48, b"\xc8\x30"),
        (2047, b"\xcf\xff"),
        (2048, b"\xd4\x08\0"),
        (262_143, b"\xd7\xff\xff"),
        (262_144, b"I\0\x04\0\0"),
        (i32::MAX, b"I\x7f\xff\xff\xff"),
    ] {
        let mut writer = Writer::new();
        writer.write_int(value);
        assert_eq!(writer.as_bytes(), bytes, "{value}");
        assert_eq!(Reader::new(bytes).next_token().unwrap(), Token::Int(value));
        let mut full = vec![b'I'];
        full.extend(value.to_be_bytes());
        assert_eq!(Reader::new(&full).next_token().unwrap(), Token::Int(value));
    }
}

#[test]
fn longs_use_java_compact_forms_at_every_boundary() {
    for (value, bytes) in [
        (i64::MIN, &b"L\x80\0\0\0\0\0\0\0"[..]),
        (-2_147_483_649, b"L\xff\xff\xff\xff\x7f\xff\xff\xff"),
        (-2_147_483_648, b"Y\x80\0\0\0"),
        (-262_145, b"Y\xff\xfb\xff\xff"),
        (-262_144, b"\x38\0\0"),
        (-2049, b"\x3b\xf7\xff"),
        (-2048, b"\xf0\0"),
        (-9, b"\xf7\xf7"),
        (-8, b"\xd8"),
        (0, b"\xe0"),
        (15, b"\xef"),
        (16, b"\xf8\x10"),
        (2047, b"\xff\xff"),
        (2048, b"\x3c\x08\0"),
        (262_143, b"\x3f\xff\xff"),
        (262_144, b"Y\0\x04\0\0"),
        (2_147_483_647, b"Y\x7f\xff\xff\xff"),
        (2_147_483_648, b"L\0\0\0\0\x80\0\0\0"),
        (i64::MAX, b"L\x7f\xff\xff\xff\xff\xff\xff\xff"),
    ] {
        let mut writer = Writer::new();
        writer.write_long(value);
        assert_eq!(writer.as_bytes(), bytes, "{value}");
        assert_eq!(Reader::new(bytes).next_token().unwrap(), Token::Long(value));
        let mut full = vec![b'L'];
        full.extend(value.to_be_bytes());
        assert_eq!(Reader::new(&full).next_token().unwrap(), Token::Long(value));
    }
}
