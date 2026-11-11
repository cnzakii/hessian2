use std::collections::HashMap;

use crate::text;

/// The largest string or binary chunk; Java splits strings at the same size.
const CHUNK: usize = 0x8000;

/// A writer for one Hessian 2.0 byte stream.
///
/// Each method picks the form Java's `Hessian2Output` picks for the same
/// value, so Java peers and this writer produce the same bytes; long binaries
/// are the exception, chunked as [`Writer::write_binary`] says. Type names,
/// class indexes, and reference numbering persist across values.
///
/// # Panics
///
/// Methods that write a count panic when it exceeds `i32::MAX`.
#[derive(Debug, Default)]
pub struct Writer {
    out: Vec<u8>,
    types: HashMap<String, usize>,
    classes: usize,
    started: usize,
}

impl Writer {
    /// Returns a writer for a new stream.
    pub fn new() -> Self {
        Self::default()
    }

    /// Returns the bytes written so far.
    pub fn as_bytes(&self) -> &[u8] {
        &self.out
    }

    /// Returns the bytes written, ending the stream.
    pub fn into_bytes(self) -> Vec<u8> {
        self.out
    }

    /// Writes `null`.
    pub fn write_null(&mut self) {
        self.out.push(b'N');
    }

    /// Writes a boolean.
    pub fn write_bool(&mut self, value: bool) {
        self.out.push(if value { b'T' } else { b'F' });
    }

    /// Writes a 32-bit int in its shortest form.
    pub fn write_int(&mut self, value: i32) {
        match value {
            -0x10..=0x2F => self.out.push((0x90 + value) as u8),
            -0x800..=0x7FF => self
                .out
                .extend_from_slice(&[(0xC8 + (value >> 8)) as u8, value as u8]),
            -0x4_0000..=0x3_FFFF => self.out.extend_from_slice(&[
                (0xD4 + (value >> 16)) as u8,
                (value >> 8) as u8,
                value as u8,
            ]),
            _ => {
                self.out.push(b'I');
                self.out.extend_from_slice(&value.to_be_bytes());
            }
        }
    }

    /// Writes a 64-bit long in its shortest form; a reader sees a long even
    /// when the value would fit an int.
    pub fn write_long(&mut self, value: i64) {
        match value {
            -0x8..=0xF => self.out.push((0xE0 + value) as u8),
            -0x800..=0x7FF => self
                .out
                .extend_from_slice(&[(0xF8 + (value >> 8)) as u8, value as u8]),
            -0x4_0000..=0x3_FFFF => self.out.extend_from_slice(&[
                (0x3C + (value >> 16)) as u8,
                (value >> 8) as u8,
                value as u8,
            ]),
            _ => match i32::try_from(value) {
                Ok(int) => {
                    self.out.push(b'Y');
                    self.out.extend_from_slice(&int.to_be_bytes());
                }
                Err(_) => {
                    self.out.push(b'L');
                    self.out.extend_from_slice(&value.to_be_bytes());
                }
            },
        }
    }

    /// Writes a double in the form Java's `Hessian2Output.writeDouble` picks:
    /// negative zero becomes `0x5b` and loses its sign, and every NaN is
    /// written as the canonical one, as `Double.doubleToLongBits` returns.
    pub fn write_double(&mut self, value: f64) {
        let int = value as i32;
        if f64::from(int) == value {
            match int {
                0 => return self.out.push(0x5B),
                1 => return self.out.push(0x5C),
                -0x80..=0x7F => return self.out.extend_from_slice(&[0x5D, int as u8]),
                -0x8000..=0x7FFF => {
                    self.out.push(0x5E);
                    return self.out.extend_from_slice(&(int as i16).to_be_bytes());
                }
                _ => {}
            }
        }
        let mills = (value * 1000.0) as i32;
        if 0.001 * f64::from(mills) == value {
            self.out.push(0x5F);
            self.out.extend_from_slice(&mills.to_be_bytes());
        } else {
            let value = if value.is_nan() { f64::NAN } else { value };
            self.out.push(b'D');
            self.out.extend_from_slice(&value.to_be_bytes());
        }
    }

    /// Writes a date as milliseconds since the Unix epoch, UTC, in minutes
    /// when that is exact, as Java does.
    pub fn write_date(&mut self, millis: i64) {
        if millis % 60_000 == 0
            && let Ok(minutes) = i32::try_from(millis / 60_000)
        {
            self.out.push(b'K');
            self.out.extend_from_slice(&minutes.to_be_bytes());
        } else {
            self.out.push(b'J');
            self.out.extend_from_slice(&millis.to_be_bytes());
        }
    }

    /// Writes text, encoding supplementary characters as CESU-8 surrogate
    /// pairs as Java does.
    pub fn write_str(&mut self, value: &str) {
        let mut rest = value;
        loop {
            let prefix = text::utf16_prefix(rest, CHUNK);
            let (chunk, tail) = rest.split_at(prefix.len);
            let last = tail.is_empty();
            self.string_header(prefix.units, last);
            // Without supplementary characters, UTF-8 is already CESU-8.
            if prefix.supplementary {
                text::push_cesu8_str(&mut self.out, chunk);
            } else {
                self.out.extend_from_slice(chunk.as_bytes());
            }
            if last {
                return;
            }
            rest = tail;
        }
    }

    /// Writes UTF-16 code units, which may include unpaired surrogates.
    pub fn write_utf16(&mut self, mut units: &[u16]) {
        while units.len() > CHUNK {
            let mut len = CHUNK;
            // A chunk never ends between the two halves of a surrogate pair.
            if (0xD800..0xDC00).contains(&units[len - 1]) {
                len -= 1;
            }
            self.string_header(len, false);
            text::push_cesu8(&mut self.out, &units[..len]);
            units = &units[len..];
        }
        self.string_header(units.len(), true);
        text::push_cesu8(&mut self.out, units);
    }

    fn string_header(&mut self, units: usize, last: bool) {
        if !last {
            self.out.push(b'R');
            self.out.extend_from_slice(&(units as u16).to_be_bytes());
        } else if units <= 0x1F {
            self.out.push(units as u8);
        } else if units <= 0x3FF {
            self.out
                .extend_from_slice(&[0x30 + (units >> 8) as u8, units as u8]);
        } else {
            self.out.push(b'S');
            self.out.extend_from_slice(&(units as u16).to_be_bytes());
        }
    }

    /// Writes bytes in chunks of up to 32768 bytes. Java chunks wherever its
    /// 8 KiB output buffer fills, which depends on everything written before,
    /// so long binaries match Java's content but not its bytes.
    pub fn write_binary(&mut self, mut value: &[u8]) {
        while value.len() > CHUNK {
            self.out.push(b'A');
            self.out.extend_from_slice(&(CHUNK as u16).to_be_bytes());
            self.out.extend_from_slice(&value[..CHUNK]);
            value = &value[CHUNK..];
        }
        let len = value.len();
        if len <= 0xF {
            self.out.push(0x20 + len as u8);
        } else if len <= 0x3FF {
            self.out
                .extend_from_slice(&[0x34 + (len >> 8) as u8, len as u8]);
        } else {
            self.out.push(b'B');
            self.out.extend_from_slice(&(len as u16).to_be_bytes());
        }
        self.out.extend_from_slice(value);
    }

    /// Starts a list of `len` values, named by `type_name` if given, and
    /// returns its reference number.
    pub fn begin_list(&mut self, len: usize, type_name: Option<&str>) -> usize {
        match (type_name, len) {
            (Some(name), 0..=7) => {
                self.out.push(0x70 + len as u8);
                self.write_type(name);
            }
            (Some(name), _) => {
                self.out.push(b'V');
                self.write_type(name);
                self.write_count(len);
            }
            (None, 0..=7) => self.out.push(0x78 + len as u8),
            (None, _) => {
                self.out.push(b'X');
                self.write_count(len);
            }
        }
        self.start()
    }

    /// Starts a map, named by `type_name` if given, whose key and value pairs
    /// end with [`Writer::end_map`], and returns its reference number.
    pub fn begin_map(&mut self, type_name: Option<&str>) -> usize {
        match type_name {
            Some(name) => {
                self.out.push(b'M');
                self.write_type(name);
            }
            None => self.out.push(b'H'),
        }
        self.start()
    }

    /// Ends the map most recently started.
    pub fn end_map(&mut self) {
        self.out.push(b'Z');
    }

    /// Writes a class definition and returns the index objects use to name it.
    pub fn define_class<S: AsRef<str>>(&mut self, name: &str, fields: &[S]) -> usize {
        self.out.push(b'C');
        self.write_str(name);
        self.write_count(fields.len());
        for field in fields {
            self.write_str(field.as_ref());
        }
        self.classes += 1;
        self.classes - 1
    }

    /// Starts an object of a defined class, whose field values follow in
    /// definition order, and returns its reference number.
    ///
    /// # Panics
    ///
    /// Panics if `class` was not returned by [`Writer::define_class`].
    pub fn begin_object(&mut self, class: usize) -> usize {
        assert!(class < self.classes, "undefined class {class}");
        if class <= 0xF {
            self.out.push(0x60 + class as u8);
        } else {
            self.out.push(b'O');
            self.write_count(class);
        }
        self.start()
    }

    /// Writes a reference to a value started earlier in this stream.
    ///
    /// # Panics
    ///
    /// Panics if no value with that reference number was started.
    pub fn write_ref(&mut self, index: usize) {
        assert!(index < self.started, "unknown reference {index}");
        self.out.push(b'Q');
        self.write_count(index);
    }

    fn start(&mut self) -> usize {
        self.started += 1;
        self.started - 1
    }

    fn write_count(&mut self, count: usize) {
        self.write_int(i32::try_from(count).expect("count exceeds i32::MAX"));
    }

    fn write_type(&mut self, name: &str) {
        match self.types.get(name) {
            Some(&index) => self.write_count(index),
            None => {
                self.types.insert(name.to_owned(), self.types.len());
                self.write_str(name);
            }
        }
    }
}
