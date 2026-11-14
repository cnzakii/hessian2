//! UTF-16 lengths and CESU-8 transcoding for Hessian strings.
//!
//! Hessian counts string lengths in UTF-16 code units. Java writes each
//! surrogate of a supplementary character as its own three-byte sequence
//! (CESU-8) and rejects four-byte UTF-8. Reading accepts both forms; a
//! four-byte sequence counts as two units.

use std::borrow::Cow;

const HIGH_BITS: u64 = 0x8080_8080_8080_8080;

/// Returns the UTF-16 units contributed by eight bytes, and whether a
/// four-byte sequence starts among them: every non-continuation byte starts
/// a unit, and a four-byte lead adds the second unit of its surrogate pair.
#[inline]
fn word_units(word: u64) -> (usize, bool) {
    let continuation = word & !(word << 1) & HIGH_BITS;
    let four_byte_lead = word & (word << 1) & (word << 2) & (word << 3) & HIGH_BITS;
    (
        8 - continuation.count_ones() as usize + four_byte_lead.count_ones() as usize,
        four_byte_lead != 0,
    )
}

#[inline]
fn byte_units(byte: u8) -> usize {
    usize::from(byte & 0xC0 != 0x80) + usize::from(byte >= 0xF0)
}

#[inline]
fn sequence_len(lead: u8) -> usize {
    match lead {
        0x00..=0x7F => 1,
        0xC0..=0xDF => 2,
        0xE0..=0xEF => 3,
        _ => 4,
    }
}

/// Returns how many leading bytes of `bytes` hold exactly `units` UTF-16 code
/// units, or `None` when the input ends first or the count passes `units`
/// inside a four-byte sequence.
///
/// Whole 8-byte words are counted without per-byte branches while the target
/// is out of reach, following the chunked counting in `str_indices`
/// (<https://github.com/cessen/str_indices>).
pub fn utf16_span(bytes: &[u8], units: usize) -> Option<usize> {
    // In ASCII every byte is one unit; `is_ascii` checks a word at a time.
    if bytes.get(..units).is_some_and(<[u8]>::is_ascii) {
        return Some(units);
    }
    let mut pos = 0;
    let mut count = 0;
    // A word adds at most 16 units, so whole words cannot reach the target
    // while more than 16 remain; the final lead is always found byte-wise.
    while units - count > 16 && pos + 8 <= bytes.len() {
        let word = u64::from_le_bytes(bytes[pos..pos + 8].try_into().unwrap());
        count += word_units(word).0;
        pos += 8;
    }
    let mut lead = 0;
    while count < units {
        let byte = *bytes.get(pos)?;
        if byte & 0xC0 != 0x80 {
            count += byte_units(byte);
            lead = pos;
        }
        pos += 1;
    }
    if count != units {
        return None;
    }
    let end = lead + sequence_len(bytes[lead]);
    (end <= bytes.len()).then_some(end)
}

/// The longest prefix of a string that fits in a string chunk.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Prefix {
    /// The prefix length in bytes, always on a character boundary.
    pub len: usize,
    /// The prefix length in UTF-16 code units.
    pub units: usize,
    /// Whether the prefix holds a supplementary character, which CESU-8
    /// writes differently from UTF-8.
    pub supplementary: bool,
}

/// Returns the longest prefix of whole characters of `text` that holds at
/// most `limit` UTF-16 code units.
///
/// Stopping before a character that would pass `limit` is Java's rule for
/// chunks: a chunk never ends between the two halves of a surrogate pair.
pub(crate) fn utf16_prefix(text: &str, limit: usize) -> Prefix {
    let bytes = text.as_bytes();
    let head = &bytes[..bytes.len().min(limit)];
    if head.is_ascii() {
        return Prefix {
            len: head.len(),
            units: head.len(),
            supplementary: false,
        };
    }
    let mut prefix = Prefix {
        len: 0,
        units: 0,
        supplementary: false,
    };
    // A word adds at most 16 units, so whole words cannot pass the limit
    // while more than 16 remain.
    while limit - prefix.units > 16 && prefix.len + 8 <= bytes.len() {
        let word = u64::from_le_bytes(bytes[prefix.len..prefix.len + 8].try_into().unwrap());
        let (units, supplementary) = word_units(word);
        prefix.units += units;
        prefix.supplementary |= supplementary;
        prefix.len += 8;
    }
    // Byte by byte: the next lead byte either fits or ends the prefix.
    while let Some(&byte) = bytes.get(prefix.len) {
        if byte & 0xC0 != 0x80 {
            let units = byte_units(byte);
            if prefix.units + units > limit {
                break;
            }
            prefix.units += units;
            prefix.supplementary |= byte >= 0xF0;
        }
        prefix.len += 1;
    }
    prefix
}

/// Appends `text` as CESU-8: its UTF-8, except that each supplementary
/// character becomes the three-byte forms of its two surrogates.
pub fn push_cesu8_str(out: &mut Vec<u8>, text: &str) {
    let bytes = text.as_bytes();
    out.reserve(bytes.len() + bytes.len() / 2);
    let mut start = 0;
    while let Some(offset) = bytes[start..].iter().position(|&byte| byte >= 0xF0) {
        let mut lead = start + offset;
        out.extend_from_slice(&bytes[start..lead]);
        // Supplementary characters often come in runs, as in emoji.
        while bytes.get(lead).is_some_and(|&byte| byte >= 0xF0) {
            let scalar = (u32::from(bytes[lead] & 0x07) << 18)
                | (u32::from(bytes[lead + 1] & 0x3F) << 12)
                | (u32::from(bytes[lead + 2] & 0x3F) << 6)
                | u32::from(bytes[lead + 3] & 0x3F);
            let offset = scalar - 0x1_0000;
            let (high, low) = (0xD800 | (offset >> 10), 0xDC00 | (offset & 0x3FF));
            out.extend_from_slice(&[
                0xED,
                0x80 | ((high >> 6) & 0x3F) as u8,
                0x80 | (high & 0x3F) as u8,
                0xED,
                0x80 | ((low >> 6) & 0x3F) as u8,
                0x80 | (low & 0x3F) as u8,
            ]);
            lead += 4;
        }
        start = lead;
    }
    out.extend_from_slice(&bytes[start..]);
}

/// Returns whether `bytes` hold the three-byte form of a surrogate, which
/// valid UTF-8 never does and Java writes for supplementary characters.
pub fn contains_surrogate(bytes: &[u8]) -> bool {
    // Surrogates are `ED A0..BF ..`. Other text with `ED` leads, such as
    // Hangul, is common, so the pairs are checked in blocks without
    // branches, which the compiler vectorizes.
    let Some(seconds) = bytes.get(1..) else {
        return false;
    };
    bytes.contains(&0xED)
        && bytes
            .chunks(64)
            .zip(seconds.chunks(64))
            .any(|(leads, seconds)| {
                leads
                    .iter()
                    .zip(seconds)
                    .fold(false, |found, (&lead, &second)| {
                        found | ((lead == 0xED) & (second & 0xE0 == 0xA0))
                    })
            })
}

/// Returns the character a CESU-8 surrogate pair encodes.
fn surrogate_pair(bytes: &[u8]) -> Option<char> {
    let [
        0xED,
        high @ 0xA0..=0xAF,
        high_tail @ 0x80..=0xBF,
        0xED,
        low @ 0xB0..=0xBF,
        low_tail @ 0x80..=0xBF,
    ] = *bytes
    else {
        return None;
    };
    let high = (u32::from(high & 0x0F) << 6) | u32::from(high_tail & 0x3F);
    let low = (u32::from(low & 0x0F) << 6) | u32::from(low_tail & 0x3F);
    char::from_u32(0x1_0000 + (high << 10) + low)
}

/// Returns the text of UTF-8 in which supplementary characters may arrive as
/// CESU-8 surrogate pairs, borrowing input that is already UTF-8.
///
/// Returns `None` for malformed input and for unpaired surrogates, which
/// Rust text cannot hold.
pub fn cesu8_to_utf8(bytes: &[u8]) -> Option<Cow<'_, str>> {
    if bytes.is_empty() {
        return Some(Cow::Borrowed(""));
    }
    let mut out = String::new();
    let mut rest = bytes;
    // Each pass copies the valid UTF-8 up to the next surrogate pair, as the
    // standard validator finds it, then joins the pairs found there.
    while let Some(chunk) = rest.utf8_chunks().next() {
        if chunk.invalid().is_empty() {
            if rest.len() == bytes.len() {
                return Some(Cow::Borrowed(chunk.valid()));
            }
            out.push_str(chunk.valid());
            break;
        }
        if out.is_empty() {
            out.reserve(bytes.len());
        }
        out.push_str(chunk.valid());
        rest = &rest[chunk.valid().len()..];
        // Supplementary characters often come in runs, as in emoji.
        let start = rest.len();
        while let Some(scalar) = rest.get(..6).and_then(surrogate_pair) {
            out.push(scalar);
            rest = &rest[6..];
        }
        if rest.len() == start {
            return None;
        }
    }
    Some(Cow::Owned(out))
}

/// Decodes UTF-8 that may encode surrogates as three-byte sequences into
/// UTF-16 code units. Returns `None` for malformed or overlong input.
pub fn cesu8_to_utf16(bytes: &[u8]) -> Option<Vec<u16>> {
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let lead = bytes[i];
        if lead < 0x80 {
            out.push(u16::from(lead));
            i += 1;
            continue;
        }
        let tail = |k: usize| -> Option<u32> {
            let byte = *bytes.get(i + k)?;
            (byte & 0xC0 == 0x80).then_some(u32::from(byte & 0x3F))
        };
        let lead_bits = u32::from(lead);
        match lead {
            0xC2..=0xDF => {
                out.push((((lead_bits & 0x1F) << 6) | tail(1)?) as u16);
                i += 2;
            }
            0xE0..=0xEF => {
                let unit = ((lead_bits & 0x0F) << 12) | (tail(1)? << 6) | tail(2)?;
                if unit < 0x800 {
                    return None;
                }
                out.push(unit as u16);
                i += 3;
            }
            0xF0..=0xF4 => {
                let scalar =
                    ((lead_bits & 0x07) << 18) | (tail(1)? << 12) | (tail(2)? << 6) | tail(3)?;
                if !(0x1_0000..=0x10_FFFF).contains(&scalar) {
                    return None;
                }
                let offset = scalar - 0x1_0000;
                out.push(0xD800 | (offset >> 10) as u16);
                out.push(0xDC00 | (offset & 0x3FF) as u16);
                i += 4;
            }
            _ => return None,
        }
    }
    Some(out)
}

/// Appends UTF-16 code units as CESU-8, one sequence per unit.
pub fn push_cesu8(out: &mut Vec<u8>, units: &[u16]) {
    for &unit in units {
        match unit {
            0..=0x7F => out.push(unit as u8),
            0x80..=0x7FF => {
                out.extend_from_slice(&[0xC0 | (unit >> 6) as u8, 0x80 | (unit & 0x3F) as u8])
            }
            _ => out.extend_from_slice(&[
                0xE0 | (unit >> 12) as u8,
                0x80 | ((unit >> 6) & 0x3F) as u8,
                0x80 | (unit & 0x3F) as u8,
            ]),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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

    #[test]
    fn prefix_is_the_longest_whole_character_prefix_within_the_limit() {
        for text in samples() {
            for limit in [0, 1, 2, 3, 7, 16, 17, 40, 1000] {
                let mut expected = Prefix {
                    len: 0,
                    units: 0,
                    supplementary: false,
                };
                for c in text.chars() {
                    if expected.units + c.len_utf16() > limit {
                        break;
                    }
                    expected.len += c.len_utf8();
                    expected.units += c.len_utf16();
                    expected.supplementary |= c.len_utf16() == 2;
                }
                assert_eq!(utf16_prefix(&text, limit), expected, "{text:?} {limit}");
            }
        }
    }
}
