//! Malformed wire contracts.

use hessian2_core::{ErrorKind, Reader, Token, text};

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

#[test]
fn malformed_values_report_their_kind_and_offset() {
    for (wire, kind, offset) in [
        (&b""[..], ErrorKind::UnexpectedEof, 0),
        (&b"I\x00\x00"[..], ErrorKind::UnexpectedEof, 3),
        (&b"Z"[..], ErrorKind::UnexpectedTag(b'Z'), 0),
        (&b"`"[..], ErrorKind::UnknownClass, 0),
        (&b"Q\x90"[..], ErrorKind::UnknownRef, 0),
        (&b"U\x91Z"[..], ErrorKind::UnknownType, 1),
        (&b"X\xcf\xff"[..], ErrorKind::InvalidLength, 1),
        (&b"C\x03Car\xcf\xff"[..], ErrorKind::InvalidLength, 5),
        (&b"\x03ab"[..], ErrorKind::UnexpectedEof, 3),
        (&b"\x01\xf0\x9f\x90\xb8"[..], ErrorKind::InvalidString, 1),
        (&b"C\x01\xc3(\x90"[..], ErrorKind::InvalidString, 1),
        (&b"X\x80"[..], ErrorKind::InvalidLength, 1),
        (&b"XI\x7f\xff\xff\xff"[..], ErrorKind::InvalidLength, 1),
        (&b"C\x01XI\x7f\xff\xff\xff"[..], ErrorKind::InvalidLength, 3),
        (&b"Q\x80"[..], ErrorKind::InvalidLength, 1),
        (&b"yQ\x91"[..], ErrorKind::UnknownRef, 1),
        (&b"OI\x7f\xff\xff\xff"[..], ErrorKind::UnknownClass, 0),
        (&b"p\x90"[..], ErrorKind::UnknownType, 1),
        (&b"M\x80Z"[..], ErrorKind::InvalidLength, 1),
        (&b"H\x90Z"[..], ErrorKind::UnexpectedTag(b'Z'), 2),
        (&b"S\xff\xffx"[..], ErrorKind::UnexpectedEof, 4),
        (&b"B\xff\xffx"[..], ErrorKind::UnexpectedEof, 4),
        (&b"J\x00"[..], ErrorKind::UnexpectedEof, 2),
        (&b"K\x00"[..], ErrorKind::UnexpectedEof, 2),
        (&b"D\x00"[..], ErrorKind::UnexpectedEof, 2),
        (&b"L\x00"[..], ErrorKind::UnexpectedEof, 2),
        (&b"\x01\xc2"[..], ErrorKind::InvalidString, 1),
        (&b"\x01\xe4\xb8"[..], ErrorKind::InvalidString, 1),
        (&b"p\x01\xed\xa0\x80"[..], ErrorKind::InvalidString, 1),
        (&b"M\x01\xed\xa0\x80Z"[..], ErrorKind::InvalidString, 1),
        (
            &b"C\x01X\x91\x01\xed\xa0\x80`\x90"[..],
            ErrorKind::InvalidString,
            4,
        ),
        (&b"C\x01\xed\xa0\x80\x90`"[..], ErrorKind::InvalidString, 1),
    ] {
        let error = Reader::new(wire).skip().unwrap_err();

        assert_eq!((error.kind(), error.offset()), (kind, offset), "{wire:x?}");
    }
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
}

#[test]
fn reserved_and_unknown_tags_report_the_tag_and_offset() {
    for tag in [0x40, 0x45, 0x47, 0x50, 0x5a] {
        // 0x60..=0x6f are object tags, handled by the class-index tests.
        let bytes = [b'N', tag];
        let mut reader = Reader::new(&bytes);
        reader.skip().unwrap();
        let error = reader.next_token().unwrap_err();

        assert_eq!(
            (error.kind(), error.offset()),
            (ErrorKind::UnexpectedTag(tag), 1)
        );
    }
}

#[test]
fn every_truncation_reports_the_expected_kind_and_offset() {
    use ErrorKind::{InvalidLength, InvalidString, UnexpectedEof};

    for (wire, expected) in [
        (
            &b"I\x7f\xff\xff\xff"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (UnexpectedEof, 3),
                (UnexpectedEof, 4),
            ][..],
        ),
        (
            &b"L\x7f\xff\xff\xff\xff\xff\xff\xff"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (UnexpectedEof, 3),
                (UnexpectedEof, 4),
                (UnexpectedEof, 5),
                (UnexpectedEof, 6),
                (UnexpectedEof, 7),
                (UnexpectedEof, 8),
            ][..],
        ),
        (
            &b"D?\xd5UUUUUU"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (UnexpectedEof, 3),
                (UnexpectedEof, 4),
                (UnexpectedEof, 5),
                (UnexpectedEof, 6),
                (UnexpectedEof, 7),
                (UnexpectedEof, 8),
            ][..],
        ),
        (
            &b"J\x00\x00\x00\xd0K\x92\x84\xb8"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (UnexpectedEof, 3),
                (UnexpectedEof, 4),
                (UnexpectedEof, 5),
                (UnexpectedEof, 6),
                (UnexpectedEof, 7),
                (UnexpectedEof, 8),
            ][..],
        ),
        (
            &b"K\x00\xe3\x83\x8f"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (UnexpectedEof, 3),
                (UnexpectedEof, 4),
            ][..],
        ),
        (
            &b"R\x00\x01a\x01b"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (UnexpectedEof, 3),
                (UnexpectedEof, 4),
                (UnexpectedEof, 5),
            ][..],
        ),
        (
            &b"A\x00\x01a!b"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (UnexpectedEof, 3),
                (UnexpectedEof, 4),
                (UnexpectedEof, 5),
            ][..],
        ),
        (
            &b"\x02\xed\xa0\xbd\xed\xb0\xb8"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (InvalidString, 1),
                (InvalidString, 1),
                (InvalidString, 1),
                (InvalidString, 1),
            ][..],
        ),
        (
            &b"V\x01t\x92\x91N"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (UnexpectedEof, 3),
                (InvalidLength, 3),
                (InvalidLength, 3),
            ][..],
        ),
        (
            &b"W\x91NZ"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (UnexpectedEof, 3),
            ][..],
        ),
        (
            &b"H\x01kyQ\x90Z"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (UnexpectedEof, 3),
                (UnexpectedEof, 4),
                (UnexpectedEof, 5),
                (UnexpectedEof, 6),
            ][..],
        ),
        (
            &b"C\x01X\x91\x01f`yQ\x90"[..],
            &[
                (UnexpectedEof, 0),
                (UnexpectedEof, 1),
                (UnexpectedEof, 2),
                (UnexpectedEof, 3),
                (InvalidLength, 3),
                (UnexpectedEof, 5),
                (UnexpectedEof, 6),
                (UnexpectedEof, 7),
                (UnexpectedEof, 8),
                (UnexpectedEof, 9),
            ][..],
        ),
    ] {
        let mut complete = Reader::new(wire);
        complete.skip().unwrap();
        assert!(complete.is_empty());
        assert_eq!(expected.len(), wire.len(), "every byte boundary has a row");

        for (end, &(kind, offset)) in expected.iter().enumerate() {
            let error = Reader::new(&wire[..end]).skip().unwrap_err();

            assert_eq!(
                (error.kind(), error.offset()),
                (kind, offset),
                "{wire:x?} cut {end}"
            );
        }
    }
}
