use std::borrow::Cow;

use hessian2_core::{ErrorKind, MAX_SKIP_DEPTH, Reader, Token, Writer, text};

// Observed with Caucho 4.0.66 and hessian-lite 4.0.5 TestHessian2Servlet.
#[test]
fn java_integer_boundaries() {
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
fn java_long_boundaries() {
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

#[test]
fn doubles_use_java_multiplication_for_thousandths() {
    for (value, bytes) in [
        (0.0, &b"\x5b"[..]),
        (1.0, b"\x5c"),
        (-128.0, b"\x5d\x80"),
        (127.0, b"\x5d\x7f"),
        (-129.0, b"\x5e\xff\x7f"),
        (128.0, b"\x5e\0\x80"),
        (-32768.0, b"\x5e\x80\0"),
        (32767.0, b"\x5e\x7f\xff"),
        (0.001, b"\x5f\0\0\0\x01"),
        (-0.001, b"\x5f\xff\xff\xff\xff"),
        (65.536, b"\x5f\0\x01\0\0"),
        (32768.0, b"\x5f\x01\xf4\0\0"),
        (2147483647.0, b"DA\xdf\xff\xff\xff\xc0\0\0"),
    ] {
        let mut writer = Writer::new();
        writer.write_double(value);
        assert_eq!(writer.as_bytes(), bytes);
        assert_eq!(
            Reader::new(bytes).next_token().unwrap(),
            Token::Double(value)
        );
    }
    for mills in [i32::MIN, -9, -1, 0, 1, 9, i32::MAX] {
        let mut bytes = vec![0x5f];
        bytes.extend(mills.to_be_bytes());
        let Token::Double(value) = Reader::new(&bytes).next_token().unwrap() else {
            panic!("not a double")
        };
        assert_eq!(value.to_bits(), (0.001 * f64::from(mills)).to_bits());
    }
    assert_ne!((0.001_f64 * 9.0).to_bits(), (9.0_f64 / 1000.0).to_bits());
}

#[test]
fn negative_zero_matches_java_bytes() {
    let mut writer = Writer::new();
    writer.write_double(-0.0);
    assert_eq!(writer.as_bytes(), b"\x5b");
}

#[test]
fn nan_matches_java_bytes() {
    let mut writer = Writer::new();
    writer.write_double(f64::from_bits(0x7ff8_0000_0000_0042));
    assert_eq!(writer.as_bytes(), b"D\x7f\xf8\0\0\0\0\0\0");
}

#[test]
fn dates_cover_minute_overflow_and_full_millisecond_range() {
    for millis in [
        i64::MIN,
        (i64::from(i32::MIN) - 1) * 60_000,
        i64::from(i32::MIN) * 60_000,
        -60_001,
        -60_000,
        -1,
        0,
        1,
        60_000,
        i64::from(i32::MAX) * 60_000,
        (i64::from(i32::MAX) + 1) * 60_000,
        i64::MAX,
    ] {
        let mut writer = Writer::new();
        writer.write_date(millis);
        let compact = millis % 60_000 == 0 && i32::try_from(millis / 60_000).is_ok();
        assert_eq!(writer.as_bytes()[0], if compact { b'K' } else { b'J' });
        assert_eq!(
            Reader::new(writer.as_bytes()).next_token().unwrap(),
            Token::Date(millis)
        );
        let mut full = vec![b'J'];
        full.extend(millis.to_be_bytes());
        assert_eq!(
            Reader::new(&full).next_token().unwrap(),
            Token::Date(millis)
        );
    }
}

#[test]
fn all_string_and_binary_length_forms_and_chunk_endings() {
    for len in [
        0, 1, 15, 16, 31, 32, 255, 256, 1023, 1024, 32768, 32769, 65535, 65536,
    ] {
        let data = vec![b'a'; len];
        for string in [true, false] {
            let mut writer = Writer::new();
            let expected = if string {
                writer.write_str(std::str::from_utf8(&data).unwrap());
                Token::String(Cow::Borrowed(data.as_slice()))
            } else {
                writer.write_binary(&data);
                Token::Binary(Cow::Borrowed(data.as_slice()))
            };
            let mut reader = Reader::new(writer.as_bytes());
            assert_eq!(reader.next_token().unwrap(), expected);
            assert!(reader.is_empty());
            if let Ok(len) = u16::try_from(len) {
                let mut full = vec![if string { b'S' } else { b'B' }];
                full.extend(len.to_be_bytes());
                full.extend(&data);
                assert_eq!(Reader::new(&full).next_token().unwrap(), expected);
            }
        }
    }
    for (wire, expected) in [
        (
            &b"R\0\0R\0\x01a\x01b"[..],
            Token::String(Cow::Borrowed(&b"ab"[..])),
        ),
        (
            b"R\0\x01a\x30\x01b",
            Token::String(Cow::Borrowed(&b"ab"[..])),
        ),
        (
            b"A\0\0A\0\x01a\x21b",
            Token::Binary(Cow::Borrowed(&b"ab"[..])),
        ),
        (
            b"A\0\x01a\x34\x01b",
            Token::Binary(Cow::Borrowed(&b"ab"[..])),
        ),
    ] {
        assert_eq!(Reader::new(wire).next_token().unwrap(), expected);
    }
}

#[test]
fn list_forms_and_shared_type_table() {
    for wire in [
        &b"U\x01t\x90Z"[..],
        b"V\x01t\x91\x90",
        b"W\x90Z",
        b"X\x91\x90",
        b"\x71\x01t\x90",
        b"\x79\x90",
    ] {
        let mut reader = Reader::new(wire);
        reader.skip().unwrap();
        assert!(reader.is_empty());
    }
    for len in [0, 1, 2, 3, 4, 5, 6, 7, 8, 47, 48, 1024] {
        for typed in [true, false] {
            let mut writer = Writer::new();
            writer.begin_list(len, typed.then_some("t"));
            for _ in 0..len {
                writer.write_null();
            }
            assert_eq!(
                writer.as_bytes()[0],
                match (typed, len) {
                    (true, 0..=7) => 0x70 + len as u8,
                    (false, 0..=7) => 0x78 + len as u8,
                    (true, _) => b'V',
                    (false, _) => b'X',
                }
            );
            let mut reader = Reader::new(writer.as_bytes());
            assert_eq!(
                reader.next_token().unwrap(),
                Token::List {
                    type_name: typed.then_some(0),
                    len: Some(len),
                }
            );
            if typed {
                assert_eq!(reader.type_name(0), "t");
            }
            for _ in 0..len {
                assert_eq!(reader.next_token().unwrap(), Token::Null);
            }
            assert!(reader.is_empty());
        }
    }
    let wire = b"M\x01tZ\x70\x90U\x90ZV\x90\x90M\x90Z";
    let mut reader = Reader::new(wire);
    for _ in 0..5 {
        reader.skip().unwrap();
    }
    assert!(reader.is_empty());
    assert_eq!(reader.type_name(0), "t");
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
fn errors_report_the_kind_and_failing_byte() {
    for (wire, kind, offset, message) in [
        (
            &b"I\0"[..],
            ErrorKind::UnexpectedEof,
            2,
            "unexpected end of input",
        ),
        (
            b"Z",
            ErrorKind::UnexpectedTag(b'Z'),
            0,
            "unexpected tag 0x5a",
        ),
        (
            b"X\x80",
            ErrorKind::InvalidLength,
            1,
            "length exceeds the remaining input",
        ),
        (
            b"\x01\xff",
            ErrorKind::InvalidString,
            1,
            "invalid string data",
        ),
        (
            b"\x60",
            ErrorKind::UnknownClass,
            0,
            "reference to an undefined class",
        ),
        (
            b"U\x90",
            ErrorKind::UnknownType,
            1,
            "reference to an undefined type",
        ),
        (
            b"Q\x90",
            ErrorKind::UnknownRef,
            0,
            "reference to an unread value",
        ),
    ] {
        let mut bytes = vec![b'N'];
        bytes.extend_from_slice(wire);
        let mut reader = Reader::new(&bytes);
        assert_eq!(reader.next_token().unwrap(), Token::Null);
        let error = reader.next_token().unwrap_err();
        assert_eq!(error.kind(), kind);
        assert_eq!(error.offset(), offset + 1);
        assert_eq!(
            error.to_string(),
            format!("{message} at byte {}", offset + 1)
        );
    }
    let bytes = vec![0x79; MAX_SKIP_DEPTH + 1];
    let error = Reader::new(&bytes).skip().unwrap_err();
    assert_eq!(error.kind(), ErrorKind::DepthLimit);
    assert_eq!(error.offset(), MAX_SKIP_DEPTH);
    assert_eq!(
        error.to_string(),
        format!("values nest too deeply at byte {MAX_SKIP_DEPTH}")
    );
}

#[test]
fn error_display_propagates_output_failures() {
    use std::fmt::{self, Write};

    struct FailingOutput;
    impl Write for FailingOutput {
        fn write_str(&mut self, _: &str) -> fmt::Result {
            Err(fmt::Error)
        }
    }

    let error = Reader::new(b"Z").next_token().unwrap_err();
    assert!(write!(FailingOutput, "{error}").is_err());
}

#[test]
fn malformed_metadata_and_chunk_headers_report_their_position() {
    for (wire, kind, offset) in [
        (&b"U"[..], ErrorKind::UnexpectedEof, 1),
        (b"QN", ErrorKind::UnexpectedTag(b'N'), 1),
        (b"CN", ErrorKind::UnexpectedTag(b'N'), 1),
        (b"C\x01X\x91N", ErrorKind::UnexpectedTag(b'N'), 4),
        (b"R\0\x01aN", ErrorKind::UnexpectedTag(b'N'), 4),
        (b"A\0\x01aN", ErrorKind::UnexpectedTag(b'N'), 4),
        (b"R\0\x01a", ErrorKind::UnexpectedEof, 4),
        (b"A\0\x01a", ErrorKind::UnexpectedEof, 4),
        (b"W", ErrorKind::UnexpectedEof, 1),
        (b"H\x90", ErrorKind::UnexpectedEof, 2),
    ] {
        let error = Reader::new(wire).skip().unwrap_err();
        assert_eq!((error.kind(), error.offset()), (kind, offset), "{wire:x?}");
    }
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
fn two_byte_unicode_boundaries_round_trip() {
    let value = "\u{80}é\u{7ff}";
    let units: Vec<_> = value.encode_utf16().collect();
    assert_eq!(text::cesu8_to_utf16(value.as_bytes()), Some(units.clone()));
    let mut bytes = Vec::new();
    text::push_cesu8(&mut bytes, &units);
    assert_eq!(bytes, value.as_bytes());
    assert_eq!(text::cesu8_to_utf8(&bytes).as_deref(), Some(value));
    let mut writer = Writer::new();
    writer.write_str(value);
    assert_eq!(writer.as_bytes()[0], 3);
    assert_eq!(
        Reader::new(writer.as_bytes()).next_token().unwrap(),
        Token::String(bytes.into())
    );
}

#[test]
fn hostile_counts_indexes_and_truncations_return_errors() {
    for wire in [
        &b"X\x80"[..],
        b"X\x49\x7f\xff\xff\xff",
        b"C\x01X\x49\x7f\xff\xff\xff",
        b"Q\x80",
        b"Q\x90",
        b"\x79Q\x91",
        b"O\x49\x7f\xff\xff\xff",
        b"\x70\x90",
        b"M\x80Z",
        b"H\x90Z",
        b"R\0\x01a",
        b"A\0\x01a",
        b"S\xff\xffx",
        b"B\xff\xffx",
        b"J\0",
        b"K\0",
        b"D\0",
        b"I\0",
        b"L\0",
    ] {
        assert!(Reader::new(wire).skip().is_err(), "{wire:x?}");
    }
    assert_eq!(
        Reader::new(b"\x79Q\x91").skip().unwrap_err().kind(),
        ErrorKind::UnknownRef
    );
}

#[test]
fn cesu8_validation_rejects_overlong_truncated_and_out_of_range_scalars() {
    for wire in [
        &b"\x80"[..],
        b"\xc0\x80",
        b"\xc1\xbf",
        b"\xc2",
        b"\xc2a",
        b"\xe0\x80\x80",
        b"\xe0\x9f\xbf",
        b"\xe4\xb8",
        b"\xe4x\x80",
        b"\xe4\xb8x",
        b"\xf0x\x80\x80",
        b"\xf0\x9fx\x80",
        b"\xf0\x9f\x90x",
        b"\xf0\x80\x80\x80",
        b"\xf0\x9f\x90",
        b"\xf4\x90\x80\x80",
        b"\xf5\x80\x80\x80",
        b"\xff",
    ] {
        assert!(text::cesu8_to_utf16(wire).is_none(), "{wire:x?}");
    }
    assert_eq!(
        text::cesu8_to_utf16(b"\xed\xa0\x80\xed\xb0\x80"),
        Some(vec![0xd800, 0xdc00])
    );
    assert_eq!(
        text::cesu8_to_utf16(b"\xed\xa0\x80x\xed\xb0\x80"),
        Some(vec![0xd800, 0x78, 0xdc00])
    );
}

/// Class, field, and type names are Java identifiers, which cannot hold an
/// unpaired surrogate.
#[test]
fn lone_surrogate_metadata_is_rejected() {
    assert!(Reader::new(b"\x70\x01\xed\xa0\x80").skip().is_err());
    assert!(
        Reader::new(b"C\x01X\x91\x01\xed\xa0\x80\x60\x90")
            .skip()
            .is_err()
    );
}
