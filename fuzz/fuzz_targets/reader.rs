#![no_main]

use hessian2_core::{Reader, Token, text};
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    let mut reader = Reader::new(data);
    while !reader.is_empty() {
        let before = reader.offset();
        if reader.end().unwrap_or(false) {
            continue;
        }
        match reader.next_token() {
            Ok(Token::Object { class }) => {
                let _ = reader.class(class);
            }
            Ok(
                Token::List {
                    type_name: Some(index),
                    ..
                }
                | Token::Map {
                    type_name: Some(index),
                },
            ) => {
                let _ = reader.type_name(index);
            }
            Ok(Token::String(bytes)) => {
                let _ = text::cesu8_to_utf16(&bytes);
            }
            Ok(_) => {}
            Err(_) => break,
        }
        assert!(reader.offset() > before && reader.offset() <= data.len());
    }

    let mut reader = Reader::new(data);
    while !reader.is_empty() {
        let before = reader.offset();
        if reader.skip().is_err() {
            break;
        }
        assert!(reader.offset() > before && reader.offset() <= data.len());
    }

    let reference = text::cesu8_to_utf16(data).and_then(|units| String::from_utf16(&units).ok());
    assert_eq!(text::cesu8_to_utf8(data).as_deref(), reference.as_deref());
    assert_eq!(
        text::contains_surrogate(data),
        data.windows(2)
            .any(|pair| pair[0] == 0xED && pair[1] & 0xE0 == 0xA0)
    );
    if let Ok(string) = std::str::from_utf8(data) {
        let mut direct = Vec::new();
        text::push_cesu8_str(&mut direct, string);
        let mut reference = Vec::new();
        text::push_cesu8(&mut reference, &string.encode_utf16().collect::<Vec<_>>());
        assert_eq!(direct, reference);
    }

    if let Some(units) = text::cesu8_to_utf16(data) {
        let mut encoded = Vec::new();
        text::push_cesu8(&mut encoded, &units);
        assert_eq!(
            text::cesu8_to_utf16(&encoded).as_deref(),
            Some(units.as_slice())
        );
        assert_eq!(text::utf16_span(&encoded, units.len()), Some(encoded.len()));
    }
    let units = data.first().copied().unwrap_or(0) as usize;
    if let Some(end) = text::utf16_span(data, units) {
        assert!(end <= data.len());
    }
});
