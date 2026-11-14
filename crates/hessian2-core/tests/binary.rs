//! Binary wire contracts.

use hessian2_core::{Reader, Token, Writer};
use std::borrow::Cow;

#[test]
fn binary_use_literal_headers_at_every_length_boundary() {
    for (len, expected) in [
        (0, [b" ".as_slice(), b""].concat()),
        (1, [b"!".as_slice(), b"a"].concat()),
        (15, [b"/".as_slice(), &b"a".repeat(15)].concat()),
        (16, [b"4\x10".as_slice(), &b"a".repeat(16)].concat()),
        (31, [b"4\x1f".as_slice(), &b"a".repeat(31)].concat()),
        (32, [b"4 ".as_slice(), &b"a".repeat(32)].concat()),
        (255, [b"4\xff".as_slice(), &b"a".repeat(255)].concat()),
        (256, [b"5\x00".as_slice(), &b"a".repeat(256)].concat()),
        (1023, [b"7\xff".as_slice(), &b"a".repeat(1023)].concat()),
        (1024, [b"B\x04\x00".as_slice(), &b"a".repeat(1024)].concat()),
        (
            32767,
            [b"B\x7f\xff".as_slice(), &b"a".repeat(32767)].concat(),
        ),
        (
            32768,
            [b"B\x80\x00".as_slice(), &b"a".repeat(32768)].concat(),
        ),
        (
            32769,
            [b"A\x80\x00".as_slice(), &b"a".repeat(32768), b"!", b"a"].concat(),
        ),
        (
            65535,
            [
                b"A\x80\x00".as_slice(),
                &b"a".repeat(32768),
                b"B\x7f\xff",
                &b"a".repeat(32767),
            ]
            .concat(),
        ),
        (
            65536,
            [
                b"A\x80\x00".as_slice(),
                &b"a".repeat(32768),
                b"B\x80\x00",
                &b"a".repeat(32768),
            ]
            .concat(),
        ),
        (
            70000,
            [
                b"A\x80\x00".as_slice(),
                &b"a".repeat(32768),
                b"A\x80\x00",
                &b"a".repeat(32768),
                b"B\x11p",
                &b"a".repeat(4464),
            ]
            .concat(),
        ),
    ] {
        let data = vec![b'a'; len];
        let mut writer = Writer::new();

        writer.write_binary(&data);

        assert_eq!(writer.as_bytes(), expected, "{len}");
        let mut reader = Reader::new(&expected);
        assert_eq!(
            reader.next_token().unwrap(),
            Token::Binary(Cow::Borrowed(&data))
        );
        assert!(reader.is_empty());
    }
}

#[test]
fn binary_accept_full_length_final_chunks() {
    for (len, header) in [
        (0, b"B\x00\x00"),
        (1, b"B\x00\x01"),
        (15, b"B\x00\x0f"),
        (16, b"B\x00\x10"),
        (31, b"B\x00\x1f"),
        (32, b"B\x00 "),
        (255, b"B\x00\xff"),
        (256, b"B\x01\x00"),
        (1023, b"B\x03\xff"),
        (1024, b"B\x04\x00"),
        (32768, b"B\x80\x00"),
        (65535, b"B\xff\xff"),
    ] {
        let data = vec![b'a'; len];
        let bytes = [header.as_slice(), &data].concat();

        assert_eq!(
            Reader::new(&bytes).next_token().unwrap(),
            Token::Binary(Cow::Borrowed(&data))
        );
    }
}

#[test]
fn binary_join_empty_and_nonfinal_chunks_with_compact_endings() {
    for wire in [&b"A\x00\x00A\x00\x01a!b"[..], b"A\x00\x01a4\x01b"] {
        assert_eq!(
            Reader::new(wire).next_token().unwrap(),
            Token::Binary(Cow::Borrowed(b"ab"))
        );
    }
}
