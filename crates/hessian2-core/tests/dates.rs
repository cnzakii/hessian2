//! Dates wire contracts.

use hessian2_core::{Reader, Token, Writer};

#[test]
fn dates_use_minutes_only_when_exact_and_in_range() {
    for (millis, bytes) in [
        (
            -9_223_372_036_854_775_808_i64,
            &b"J\x80\x00\x00\x00\x00\x00\x00\x00"[..],
        ),
        (
            -128_849_018_940_000_i64,
            &b"J\xff\xff\x8a\xcf\xff\xff\x15\xa0"[..],
        ),
        (-128_849_018_880_000_i64, &b"K\x80\x00\x00\x00"[..]),
        (-60_001_i64, &b"J\xff\xff\xff\xff\xff\xff\x15\x9f"[..]),
        (-60_000_i64, &b"K\xff\xff\xff\xff"[..]),
        (-1_i64, &b"J\xff\xff\xff\xff\xff\xff\xff\xff"[..]),
        (0_i64, &b"K\x00\x00\x00\x00"[..]),
        (1_i64, &b"J\x00\x00\x00\x00\x00\x00\x00\x01"[..]),
        (60_000_i64, &b"K\x00\x00\x00\x01"[..]),
        (128_849_018_820_000_i64, &b"K\x7f\xff\xff\xff"[..]),
        (128_849_018_880_000_i64, &b"J\x00\x00u0\x00\x00\x00\x00"[..]),
        (
            9_223_372_036_854_775_807_i64,
            &b"J\x7f\xff\xff\xff\xff\xff\xff\xff"[..],
        ),
        (894_621_091_000_i64, &b"J\x00\x00\x00\xd0K\x92\x84\xb8"[..]),
        (894_621_060_000_i64, &b"K\x00\xe3\x83\x8f"[..]),
    ] {
        let mut writer = Writer::new();

        writer.write_date(millis);

        assert_eq!(writer.as_bytes(), bytes, "{millis}");
        assert_eq!(
            Reader::new(bytes).next_token().unwrap(),
            Token::Date(millis)
        );
    }
}

#[test]
fn dates_accept_full_milliseconds_even_when_minutes_would_fit() {
    for (bytes, millis) in [
        (&b"J\0\0\0\0\0\0\0\0"[..], 0),
        (b"J\xff\xff\xff\xff\xff\xff\x15\xa0", -60_000),
        (b"J\0\0\0\0\0\0\xea\x60", 60_000),
    ] {
        assert_eq!(
            Reader::new(bytes).next_token().unwrap(),
            Token::Date(millis)
        );
    }
}
