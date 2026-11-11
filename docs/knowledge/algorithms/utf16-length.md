---
title: UTF-16 length of UTF-8 and CESU-8 bytes
description: Counting UTF-16 code units from UTF-8 or CESU-8 bytes, byte at a time and eight bytes at a time, and finding where a Hessian string with a given unit count ends.
topics: [algorithms, utf-8, cesu-8, utf-16, swar]
checked_at: 2026-11-11
---

# UTF-16 Length Of UTF-8 And CESU-8 Bytes

Hessian string lengths count UTF-16 units (specification §4.12), while the
payload is UTF-8 or, from Java writers, CESU-8. A reader therefore has to scan
the bytes to know where a string ends.

## Formula

Literature, from two canonical implementations:

- simdutf, `include/simdutf/scalar/utf8.h:470-480` at commit `63e5998`
  (`utf16_length_from_utf8`): add one for every byte that is not a
  continuation byte (`int8_t(b) > -65`, that is, not `10xxxxxx`), and one more
  for every byte `>= 0xF0`.
- str_indices, `src/utf16.rs:70` at commit `5e897b0`:
  `((b & 0xC0) != 0x80) + ((b & 0xF0) == 0xF0)`.

So: **units = non-continuation bytes + four-byte lead bytes.** Every character
has exactly one non-continuation byte, and only four-byte sequences encode
characters outside the BMP, which take two UTF-16 units.

Methodological synthesis: the formula also holds for CESU-8. CESU-8 writes each
surrogate as its own three-byte sequence with one lead byte, so a supplementary
character contributes two lead bytes and no four-byte lead, which is again two
units. Unpaired surrogates (three bytes, one lead) count one unit, matching
their UTF-16 length.

## Eight Bytes At A Time

str_indices processes machine words: for each byte it masks with `0xC0` and
compares to `0x80` using a SWAR byte comparison (`byte_chunk.rs:122-127`), and
accumulates per-byte counts before summing (`utf16.rs:77-98`).

An equivalent form reads both conditions off the high bit of each byte of a
64-bit word `w` (methodological synthesis; `H = 0x8080808080808080`):

- `w << k` moves bit `7 - k` of every byte to bit 7 of the same byte; bits that
  cross into the next byte land below bit 7 and are discarded by `& H`.
- Continuation bytes (bit 7 set, bit 6 clear): `w & !(w << 1) & H`.
- Four-byte leads (bits 7, 6, 5, 4 set): `w & (w << 1) & (w << 2) & (w << 3) & H`.
- Units in the word: `8 - popcount(continuation) + popcount(four_byte_leads)`.

## Finding The End Of A Hessian String

A Hessian string is followed by more Hessian data, so the scan must stop at the
right byte rather than at the end of the buffer (methodological synthesis):

- A word contributes at most 16 units (eight four-byte leads count two each).
  Counting whole words while more than 16 units remain cannot pass the target.
  str_indices bounds its fast path the same way, by the remaining budget
  (`utf16.rs:79`).
- The rest is counted byte by byte. When the count reaches the target, the
  string ends after the last character's full sequence, computed from its lead
  byte. Stopping at the first non-continuation byte instead would absorb the
  bytes that follow: many Hessian tags, such as compact ints `0x80`-`0xBF`,
  have the continuation bit pattern `10xxxxxx`.
- A target crossed in the middle of a four-byte sequence (one unit left, a
  four-byte character next) means the length and the bytes disagree.
