//! Strings wire contracts.

use hessian2_core::{Reader, Token, Writer, text};
use std::borrow::Cow;

#[test]
fn strings_use_literal_headers_at_every_length_boundary() {
    for (len, expected) in [
        (0, [b"\x00".as_slice(), b""].concat()),
        (1, [b"\x01".as_slice(), b"a"].concat()),
        (15, [b"\x0f".as_slice(), &b"a".repeat(15)].concat()),
        (16, [b"\x10".as_slice(), &b"a".repeat(16)].concat()),
        (31, [b"\x1f".as_slice(), &b"a".repeat(31)].concat()),
        (32, [b"0 ".as_slice(), &b"a".repeat(32)].concat()),
        (255, [b"0\xff".as_slice(), &b"a".repeat(255)].concat()),
        (256, [b"1\x00".as_slice(), &b"a".repeat(256)].concat()),
        (1023, [b"3\xff".as_slice(), &b"a".repeat(1023)].concat()),
        (1024, [b"S\x04\x00".as_slice(), &b"a".repeat(1024)].concat()),
        (
            32767,
            [b"S\x7f\xff".as_slice(), &b"a".repeat(32767)].concat(),
        ),
        (
            32768,
            [b"S\x80\x00".as_slice(), &b"a".repeat(32768)].concat(),
        ),
        (
            32769,
            [b"R\x80\x00".as_slice(), &b"a".repeat(32768), b"\x01", b"a"].concat(),
        ),
        (
            65535,
            [
                b"R\x80\x00".as_slice(),
                &b"a".repeat(32768),
                b"S\x7f\xff",
                &b"a".repeat(32767),
            ]
            .concat(),
        ),
        (
            65536,
            [
                b"R\x80\x00".as_slice(),
                &b"a".repeat(32768),
                b"S\x80\x00",
                &b"a".repeat(32768),
            ]
            .concat(),
        ),
    ] {
        let data = vec![b'a'; len];
        let mut writer = Writer::new();

        writer.write_str(std::str::from_utf8(&data).unwrap());

        assert_eq!(writer.as_bytes(), expected, "{len}");
        let mut reader = Reader::new(&expected);
        assert_eq!(
            reader.next_token().unwrap(),
            Token::String(Cow::Borrowed(&data))
        );
        assert!(reader.is_empty());
    }
}

#[test]
fn strings_accept_full_length_final_chunks() {
    for (len, header) in [
        (0, b"S\x00\x00"),
        (1, b"S\x00\x01"),
        (15, b"S\x00\x0f"),
        (16, b"S\x00\x10"),
        (31, b"S\x00\x1f"),
        (32, b"S\x00 "),
        (255, b"S\x00\xff"),
        (256, b"S\x01\x00"),
        (1023, b"S\x03\xff"),
        (1024, b"S\x04\x00"),
        (32768, b"S\x80\x00"),
        (65535, b"S\xff\xff"),
    ] {
        let data = vec![b'a'; len];
        let bytes = [header.as_slice(), &data].concat();

        assert_eq!(
            Reader::new(&bytes).next_token().unwrap(),
            Token::String(Cow::Borrowed(&data))
        );
    }
}

#[test]
fn strings_join_empty_and_nonfinal_chunks_with_compact_endings() {
    for wire in [&b"R\x00\x00R\x00\x01a\x01b"[..], b"R\x00\x01a0\x01b"] {
        assert_eq!(
            Reader::new(wire).next_token().unwrap(),
            Token::String(Cow::Borrowed(b"ab"))
        );
    }
}

#[test]
fn supplementary_characters_are_written_as_surrogate_pairs() {
    let mut writer = Writer::new();

    writer.write_str("🐸");

    assert_eq!(writer.as_bytes(), b"\x02\xed\xa0\xbd\xed\xb0\xb8");
}

#[test]
fn strings_accept_four_byte_utf8_as_two_utf16_units() {
    assert_eq!(
        Reader::new(b"\x02\xf0\x9f\x90\xb8").next_token().unwrap(),
        Token::String(Cow::Borrowed("🐸".as_bytes()))
    );
}

#[test]
fn string_chunks_never_split_a_surrogate_pair() {
    let value = "a".repeat(32767) + "🐸";
    let expected = [
        b"R\x7f\xff".as_slice(),
        &b"a".repeat(32767),
        b"\x02\xed\xa0\xbd\xed\xb0\xb8",
    ]
    .concat();
    let mut writer = Writer::new();

    writer.write_str(&value);

    assert_eq!(writer.as_bytes(), expected);
    let Token::String(raw) = Reader::new(&expected).next_token().unwrap() else {
        panic!("expected string")
    };
    assert_eq!(text::cesu8_to_utf8(&raw).as_deref(), Some(value.as_str()));
}

#[test]
fn utf16_writing_preserves_lone_surrogates() {
    let mut writer = Writer::new();

    writer.write_utf16(&[0xd800, 0x78, 0xdc00]);

    assert_eq!(writer.as_bytes(), b"\x03\xed\xa0\x80x\xed\xb0\x80");
}

#[test]
fn two_byte_unicode_boundaries_preserve_utf16_units() {
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

fn samples() -> Vec<String> {
    let mut texts = vec![String::new(), "a".into(), "🐸".into()];
    for pad in 0..8 {
        let pad = "x".repeat(pad);
        for body in ["Hel🐸lo こん🐸にち🐸🐸は é", "中文", "한국어", "🐸😀"]
        {
            texts.push(pad.clone() + &body.repeat(13));
        }
    }
    texts
}

fn cesu(text: &str) -> Vec<u8> {
    let mut out = Vec::new();
    text::push_cesu8(&mut out, &text.encode_utf16().collect::<Vec<_>>());
    out
}

#[test]
fn utf16_spans_stop_at_the_declared_code_unit_boundary() {
    for (bytes, units, expected) in [
        (&b"a\xc3\xa9\xf0\x9f\x90\xb8\x90"[..], 0, Some(0)),
        (b"a\xc3\xa9\xf0\x9f\x90\xb8\x90", 1, Some(1)),
        (b"a\xc3\xa9\xf0\x9f\x90\xb8\x90", 2, Some(3)),
        (b"a\xc3\xa9\xf0\x9f\x90\xb8\x90", 3, None),
        (b"a\xc3\xa9\xf0\x9f\x90\xb8\x90", 4, Some(7)),
        (b"\xc3\xa9\x90", 1, Some(2)),
        (b"\xed\xa0\xbd\xed\xb0\xb8", 1, Some(3)),
        (b"\xed\xa0\xbd\xed\xb0\xb8", 2, Some(6)),
        (b"\xff\xff\xff\xff", 17, None),
        (b"\xff\xff\xff\xff", 128, None),
    ] {
        assert_eq!(
            text::utf16_span(bytes, units),
            expected,
            "{bytes:x?} {units}"
        );
    }
}

#[test]
fn utf16_spans_count_mixed_ascii_cjk_and_supplementary_characters() {
    for (bytes, boundaries) in [
        (
            &b"a\xc3\xa9\xe4\xb8\xad\xed\xa0\xbd\xed\xb0\xb8"[..],
            [Some(0), Some(1), Some(3), Some(6), Some(9), Some(12), None],
        ),
        (
            &b"a\xc3\xa9\xe4\xb8\xad\xf0\x9f\x90\xb8"[..],
            [Some(0), Some(1), Some(3), Some(6), None, Some(10), None],
        ),
    ] {
        for (units, expected) in boundaries.into_iter().enumerate() {
            assert_eq!(text::utf16_span(bytes, units), expected);
        }
    }
}

#[test]
fn writer_matches_utf16_chunks_at_length_boundaries() {
    for padding in [0, 1, 7] {
        for size in [31, 32, 1023, 1024, 32767, 32768, 32769] {
            for body in ["a", "中", "한", "😀", "é中😀"] {
                let value = "x".repeat(padding) + &body.repeat(size);
                let mut direct = Writer::new();
                direct.write_str(&value);
                let mut reference = Writer::new();
                reference.write_utf16(&value.encode_utf16().collect::<Vec<_>>());
                assert_eq!(
                    direct.as_bytes(),
                    reference.as_bytes(),
                    "{padding} {size} {body}"
                );
            }
        }
    }
}

#[test]
fn cesu8_str_matches_the_utf16_encoding() {
    for text in samples() {
        let mut out = Vec::new();
        text::push_cesu8_str(&mut out, &text);
        assert_eq!(out, cesu(&text), "{text:?}");
    }
}

#[test]
fn cesu8_to_utf8_joins_surrogate_pairs_and_borrows_utf8() {
    for text in samples() {
        assert!(
            matches!(text::cesu8_to_utf8(text.as_bytes()), Some(Cow::Borrowed(t)) if t == text)
        );
        assert_eq!(
            text::cesu8_to_utf8(&cesu(&text)).as_deref(),
            Some(text.as_str())
        );
    }
    let mut lone = Vec::new();
    text::push_cesu8(&mut lone, &[0x61, 0xD800, 0x62]);
    assert_eq!(text::cesu8_to_utf8(&lone), None);
    text::push_cesu8(&mut lone, &[0xDC00]);
    assert_eq!(text::cesu8_to_utf8(&lone), None);
    assert_eq!(text::cesu8_to_utf8(&[0x61, 0xFF]), None);
}

#[test]
fn surrogates_are_found_at_every_offset_and_hangul_is_not_one() {
    for at in 0..130 {
        let mut bytes = "한".repeat(50).into_bytes();
        assert!(!text::contains_surrogate(&bytes));
        bytes.splice(at..at, cesu("🐸"));
        assert!(text::contains_surrogate(&bytes), "{at}");
    }
    assert!(!text::contains_surrogate(&[0xED]));
    assert!(!text::contains_surrogate(&[]));
}

#[test]
fn cesu8_round_trips_lone_and_paired_surrogates() {
    let units = [0x61, 0xD83D, 0xDC38, 0xDC00, 0xD800, 0x4E2D];
    let mut bytes = Vec::new();
    text::push_cesu8(&mut bytes, &units);
    assert_eq!(text::cesu8_to_utf16(&bytes).unwrap(), units);
}

#[test]
fn cesu8_accepts_four_byte_utf8() {
    assert_eq!(
        text::cesu8_to_utf16("🐸".as_bytes()).unwrap(),
        [0xD83D, 0xDC38]
    );
}

#[test]
fn long_mixed_strings_keep_the_next_token_at_every_word_alignment() {
    for body in [&b"Hel\xf0\x9f\x90\xb8lo world! \xe3\x81\x93\xe3\x82\x93\xf0\x9f\x90\xb8\xe3\x81\xab\xe3\x81\xa1\xf0\x9f\x90\xb8\xf0\x9f\x90\xb8\xe3\x81\xaf! "[..], b"Hel\xed\xa0\xbd\xed\xb0\xb8lo world! \xe3\x81\x93\xe3\x82\x93\xed\xa0\xbd\xed\xb0\xb8\xe3\x81\xab\xe3\x81\xa1\xed\xa0\xbd\xed\xb0\xb8\xed\xa0\xbd\xed\xb0\xb8\xe3\x81\xaf! "] {
        for (padding, header) in [b"S\x00\xc4", b"S\x00\xc5", b"S\x00\xc6", b"S\x00\xc7", b"S\x00\xc8", b"S\x00\xc9", b"S\x00\xca", b"S\x00\xcb"].into_iter().enumerate() {
            let data = [vec![b'a'; padding], body.repeat(7)].concat();
            let wire = [header.as_slice(), &data, b"\x90"].concat();
            let mut reader = Reader::new(&wire);

            assert_eq!(reader.next_token().unwrap(), Token::String(Cow::Borrowed(&data)));
            assert_eq!(reader.next_token().unwrap(), Token::Int(0));
            assert!(reader.is_empty());
        }
    }
}
