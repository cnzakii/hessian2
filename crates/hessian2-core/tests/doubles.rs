//! Doubles wire contracts.

use hessian2_core::{Reader, Token, Writer};

#[test]
fn doubles_use_java_compact_forms_at_every_boundary() {
    for (value, bytes) in [
        (0.0, &b"\x5b"[..]),
        (1.0, b"\x5c"),
        (-128.0, b"\x5d\x80"),
        (127.0, b"\x5d\x7f"),
        (-129.0, b"\x5e\xff\x7f"),
        (128.0, b"\x5e\0\x80"),
        (-32768.0, b"\x5e\x80\0"),
        (32767.0, b"\x5e\x7f\xff"),
        (12.25, b"\x5f\0\0\x2f\xda"),
        (0.1, b"\x5f\0\0\0\x64"),
        (-32769.0, b"\x5f\xfe\x0b\xfc\x18"),
        (-32767.0, b"\x5e\x80\x01"),
        (32766.0, b"\x5e\x7f\xfe"),
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
}

#[test]
fn thousandths_decode_with_javas_multiplication_rounding() {
    for (bytes, bits) in [
        (&b"\x5f\x80\0\0\0"[..], 0xc140_624d_d2f1_a9fc),
        (b"\x5f\xff\xff\xff\xf7", 0xbf82_6e97_8d4f_df3c),
        (b"\x5f\xff\xff\xff\xff", 0xbf50_624d_d2f1_a9fc),
        (b"\x5f\0\0\0\0", 0),
        (b"\x5f\0\0\0\x01", 0x3f50_624d_d2f1_a9fc),
        (b"\x5f\0\0\0\x09", 0x3f82_6e97_8d4f_df3c),
        (b"\x5f\x7f\xff\xff\xff", 0x4140_624d_d2d0_e560),
        (b"D\x80\0\0\0\0\0\0\0", 0x8000_0000_0000_0000),
        (b"D\x7f\xf0\0\0\0\0\0\0", 0x7ff0_0000_0000_0000),
    ] {
        let Token::Double(value) = Reader::new(bytes).next_token().unwrap() else {
            panic!("expected double")
        };

        assert_eq!(value.to_bits(), bits, "{bytes:x?}");
    }
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
fn a_non_thousandth_double_uses_all_eight_payload_bytes() {
    let mut writer = Writer::new();

    writer.write_double(1.0 / 3.0);

    assert_eq!(writer.as_bytes(), b"D\x3f\xd5\x55\x55\x55\x55\x55\x55");
    assert_eq!(
        Reader::new(writer.as_bytes()).next_token().unwrap(),
        Token::Double(1.0 / 3.0)
    );
}
