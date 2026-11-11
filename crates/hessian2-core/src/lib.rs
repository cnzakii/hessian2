//! Hessian 2.0 serialization primitives.
//!
//! [`Reader`] turns a byte stream into [`Token`]s and [`Writer`] turns calls
//! into bytes. Both keep the per-stream tables Hessian shares across values:
//! class definitions, type names, and reference numbers. Building values from
//! tokens, and choosing tokens for values, belongs to the caller.

#![forbid(unsafe_code)]

mod error;
mod reader;
pub mod text;
mod writer;

pub use error::{Error, ErrorKind};
pub use reader::{ClassDef, MAX_SKIP_DEPTH, Reader, Token};
pub use writer::Writer;

/// Compiles and runs the README's example as a doctest.
#[doc = include_str!("../README.md")]
#[cfg(doctest)]
pub struct ReadmeDoctests;

#[cfg(test)]
mod tests {
    use super::*;
    use std::borrow::Cow;

    fn written(write: impl FnOnce(&mut Writer)) -> Vec<u8> {
        let mut writer = Writer::new();
        write(&mut writer);
        writer.into_bytes()
    }

    fn tokens(bytes: &[u8]) -> Vec<Token<'_>> {
        let mut reader = Reader::new(bytes);
        let mut out = Vec::new();
        while !reader.is_empty() {
            out.push(reader.next_token().unwrap());
        }
        out
    }

    fn error(bytes: &[u8]) -> ErrorKind {
        let mut reader = Reader::new(bytes);
        loop {
            match reader.next_token() {
                Ok(_) if reader.is_empty() => panic!("{bytes:x?} decoded"),
                Ok(_) => {}
                Err(error) => return error.kind(),
            }
        }
    }

    // Examples from the Hessian 2.0 serialization specification.
    #[test]
    fn ints_use_the_specification_forms() {
        for (value, bytes) in [
            (0, &[0x90][..]),
            (-16, &[0x80]),
            (47, &[0xBF]),
            (48, &[0xC8, 0x30]),
            (-2048, &[0xC0, 0x00]),
            (2047, &[0xCF, 0xFF]),
            (-262_144, &[0xD0, 0x00, 0x00]),
            (262_143, &[0xD7, 0xFF, 0xFF]),
            (262_144, &[b'I', 0x00, 0x04, 0x00, 0x00]),
        ] {
            assert_eq!(written(|w| w.write_int(value)), bytes, "{value}");
            assert_eq!(tokens(bytes), [Token::Int(value)]);
        }
    }

    #[test]
    fn longs_use_the_specification_forms() {
        for (value, bytes) in [
            (0, &[0xE0][..]),
            (-8, &[0xD8]),
            (15, &[0xEF]),
            (-2048, &[0xF0, 0x00]),
            (2047, &[0xFF, 0xFF]),
            (-262_144, &[0x38, 0x00, 0x00]),
            (262_143, &[0x3F, 0xFF, 0xFF]),
            (262_144, &[b'Y', 0x00, 0x04, 0x00, 0x00]),
            (1 << 31, &[b'L', 0, 0, 0, 0, 0x80, 0, 0, 0]),
        ] {
            assert_eq!(written(|w| w.write_long(value)), bytes, "{value}");
            assert_eq!(tokens(bytes), [Token::Long(value)]);
        }
    }

    #[test]
    fn doubles_match_java_compact_forms() {
        for (value, bytes) in [
            (0.0, &[0x5B][..]),
            (1.0, &[0x5C]),
            (-128.0, &[0x5D, 0x80]),
            (127.0, &[0x5D, 0x7F]),
            (-32768.0, &[0x5E, 0x80, 0x00]),
            (12.25, &[0x5F, 0x00, 0x00, 0x2F, 0xDA]),
            (0.1, &[0x5F, 0x00, 0x00, 0x00, 0x64]),
        ] {
            assert_eq!(written(|w| w.write_double(value)), bytes, "{value}");
            assert_eq!(tokens(bytes), [Token::Double(value)]);
        }
        let third = written(|w| w.write_double(1.0 / 3.0));
        assert_eq!(third[0], b'D');
        assert_eq!(tokens(&third), [Token::Double(1.0 / 3.0)]);
    }

    #[test]
    fn dates_use_minutes_when_exact() {
        let millis = 894_621_091_000;
        let full = [b'J', 0x00, 0x00, 0x00, 0xD0, 0x4B, 0x92, 0x84, 0xB8];
        assert_eq!(written(|w| w.write_date(millis)), full);
        assert_eq!(tokens(&full), [Token::Date(millis)]);
        let minutes = [b'K', 0x00, 0xE3, 0x83, 0x8F];
        assert_eq!(written(|w| w.write_date(894_621_060_000)), minutes);
        assert_eq!(tokens(&minutes), [Token::Date(894_621_060_000)]);
    }

    #[test]
    fn strings_use_short_medium_and_chunked_forms() {
        assert_eq!(written(|w| w.write_str("")), [0x00]);
        assert_eq!(written(|w| w.write_str("hello")), b"\x05hello");
        let medium = "a".repeat(1023);
        assert_eq!(&written(|w| w.write_str(&medium))[..2], [0x33, 0xFF]);
        let long = "a".repeat(0x8001);
        let bytes = written(|w| w.write_str(&long));
        assert_eq!(&bytes[..3], [b'R', 0x80, 0x00]);
        assert_eq!(&bytes[3 + 0x8000..3 + 0x8000 + 1], [0x01]);
        assert_eq!(
            tokens(&bytes),
            [Token::String(Cow::Owned(long.into_bytes()))]
        );
    }

    #[test]
    fn supplementary_characters_are_written_as_surrogates() {
        let bytes = written(|w| w.write_str("🐸"));
        assert_eq!(bytes, [0x02, 0xED, 0xA0, 0xBD, 0xED, 0xB0, 0xB8]);
        let mut reader = Reader::new(b"\x02\xF0\x9F\x90\xB8");
        assert_eq!(
            reader.next_token().unwrap(),
            Token::String(Cow::Borrowed("🐸".as_bytes()))
        );
    }

    #[test]
    fn string_chunks_never_split_a_surrogate_pair() {
        let text = format!("{}🐸", "a".repeat(0x7FFF));
        let bytes = written(|w| w.write_str(&text));
        assert_eq!(&bytes[..3], [b'R', 0x7F, 0xFF]);
        let Token::String(raw) = &tokens(&bytes)[0] else {
            panic!()
        };
        assert_eq!(
            text::cesu8_to_utf16(raw).unwrap(),
            text.encode_utf16().collect::<Vec<_>>()
        );
    }

    #[test]
    fn binary_round_trips_across_chunks() {
        for len in [0, 15, 16, 1023, 1024, 0x8000, 0x8001, 70_000] {
            let data: Vec<u8> = (0..len).map(|i| i as u8).collect();
            let bytes = written(|w| w.write_binary(&data));
            assert_eq!(
                tokens(&bytes),
                [Token::Binary(Cow::Owned(data.clone()))],
                "{len}"
            );
        }
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
    fn malformed_streams_are_rejected() {
        assert_eq!(error(b""), ErrorKind::UnexpectedEof);
        assert_eq!(error(b"I\x00\x00"), ErrorKind::UnexpectedEof);
        assert_eq!(error(b"Z"), ErrorKind::UnexpectedTag(b'Z'));
        assert_eq!(error(b"\x60"), ErrorKind::UnknownClass);
        assert_eq!(error(b"Q\x90"), ErrorKind::UnknownRef);
        assert_eq!(error(b"U\x91Z"), ErrorKind::UnknownType);
        assert_eq!(error(b"X\xCF\xFF"), ErrorKind::InvalidLength);
        assert_eq!(error(b"C\x03Car\xCF\xFF"), ErrorKind::InvalidLength);
        assert_eq!(error(b"\x03ab"), ErrorKind::UnexpectedEof);
        assert_eq!(error(b"\x01\xF0\x9F\x90\xB8"), ErrorKind::InvalidString);
        assert_eq!(error(b"C\x01\xC3\x28\x90"), ErrorKind::InvalidString);
    }

    #[test]
    fn skip_stops_at_the_depth_limit() {
        let bytes = vec![0x79; MAX_SKIP_DEPTH + 1];
        assert_eq!(
            Reader::new(&bytes).skip().unwrap_err().kind(),
            ErrorKind::DepthLimit
        );
    }
}
