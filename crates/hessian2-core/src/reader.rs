use std::borrow::Cow;

use crate::error::{Error, ErrorKind};
use crate::text;

/// Values the reader skips may nest at most this deep.
pub const MAX_SKIP_DEPTH: usize = 1024;

/// A class definition sent once per stream: the type name and field names
/// that later objects fill in order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClassDef {
    pub name: String,
    pub fields: Vec<String>,
}

/// One step of a Hessian stream.
///
/// `List`, `Map`, and `Object` start a value that later `Ref` tokens may point
/// back to; they are numbered in the order they start.
#[derive(Debug, Clone, PartialEq)]
pub enum Token<'a> {
    Null,
    Bool(bool),
    Int(i32),
    Long(i64),
    Double(f64),
    /// Milliseconds since the Unix epoch, UTC.
    Date(i64),
    /// String bytes as sent: UTF-8 where surrogates may appear as
    /// three-byte sequences.
    String(Cow<'a, [u8]>),
    Binary(Cow<'a, [u8]>),
    /// A list of `len` values, or of values up to [`Reader::end`] when `len`
    /// is `None`. `type_name` indexes [`Reader::type_name`].
    List {
        type_name: Option<usize>,
        len: Option<usize>,
    },
    /// Key and value pairs up to [`Reader::end`].
    Map {
        type_name: Option<usize>,
    },
    /// One value per field of [`Reader::class`]`(class)`, in order.
    Object {
        class: usize,
    },
    /// The value started by the `n`-th `List`, `Map`, or `Object` token.
    Ref(usize),
}

/// A reader's position and stream tables, detached from its input so that
/// reading the same input can resume later, as Java's `Hessian2Input`
/// continues across `readObject` calls.
#[derive(Debug, Default)]
pub struct ReaderState {
    pos: usize,
    classes: Vec<ClassDef>,
    types: Vec<String>,
    started: usize,
}

impl ReaderState {
    /// Returns the offset of the next unread byte.
    pub fn offset(&self) -> usize {
        self.pos
    }
}

/// A pull reader over one Hessian 2.0 byte stream.
///
/// Class definitions, type names, and reference numbering persist across
/// values, so several values written to one stream are read with one reader.
#[derive(Debug)]
pub struct Reader<'a> {
    input: &'a [u8],
    pos: usize,
    classes: Vec<ClassDef>,
    types: Vec<String>,
    started: usize,
}

impl<'a> Reader<'a> {
    pub fn new(input: &'a [u8]) -> Self {
        Self {
            input,
            pos: 0,
            classes: Vec::new(),
            types: Vec::new(),
            started: 0,
        }
    }

    /// Returns a reader over `input` that continues where `state` left off.
    ///
    /// `state` must come from [`Reader::into_state`] on a reader over the
    /// same input; for other input, reading fails or reads nonsense.
    pub fn resume(input: &'a [u8], state: ReaderState) -> Self {
        Self {
            input,
            pos: state.pos,
            classes: state.classes,
            types: state.types,
            started: state.started,
        }
    }

    /// Returns the reader's position and stream tables, for
    /// [`Reader::resume`].
    pub fn into_state(self) -> ReaderState {
        ReaderState {
            pos: self.pos,
            classes: self.classes,
            types: self.types,
            started: self.started,
        }
    }

    /// Returns the offset of the next unread byte.
    pub fn offset(&self) -> usize {
        self.pos
    }

    /// Returns whether every input byte has been read.
    pub fn is_empty(&self) -> bool {
        self.pos == self.input.len()
    }

    /// Returns the class definition a [`Token::Object`] names.
    ///
    /// # Panics
    ///
    /// Panics if `index` did not come from a token of this reader.
    pub fn class(&self, index: usize) -> &ClassDef {
        &self.classes[index]
    }

    /// Returns the type name a [`Token::List`] or [`Token::Map`] names.
    ///
    /// # Panics
    ///
    /// Panics if `index` did not come from a token of this reader.
    pub fn type_name(&self, index: usize) -> &str {
        &self.types[index]
    }

    /// Reads the next token, applying any class definitions before it.
    ///
    /// # Errors
    ///
    /// Returns an error for malformed or truncated input; the reader should
    /// not be used after one.
    // Out of line so recursive callers don't carry its locals in every frame.
    #[inline(never)]
    pub fn next_token(&mut self) -> Result<Token<'a>, Error> {
        loop {
            let start = self.pos;
            let tag = self.byte()?;
            let token = match tag {
                b'N' => Token::Null,
                b'T' => Token::Bool(true),
                b'F' => Token::Bool(false),
                0x80..=0xBF | 0xC0..=0xCF | 0xD0..=0xD7 | b'I' => Token::Int(self.int_body(tag)?),
                0xD8..=0xEF => Token::Long(i64::from(tag) - 0xE0),
                0xF0..=0xFF => {
                    Token::Long(((i64::from(tag) - 0xF8) << 8) | i64::from(self.byte()?))
                }
                0x38..=0x3F => {
                    let [b1, b0] = self.array()?;
                    Token::Long(
                        ((i64::from(tag) - 0x3C) << 16) | i64::from(u16::from_be_bytes([b1, b0])),
                    )
                }
                b'Y' => Token::Long(i64::from(i32::from_be_bytes(self.array()?))),
                b'L' => Token::Long(i64::from_be_bytes(self.array()?)),
                0x5B => Token::Double(0.0),
                0x5C => Token::Double(1.0),
                0x5D => Token::Double(f64::from(self.byte()? as i8)),
                0x5E => Token::Double(f64::from(i16::from_be_bytes(self.array()?))),
                0x5F => Token::Double(0.001 * f64::from(i32::from_be_bytes(self.array()?))),
                b'D' => Token::Double(f64::from_be_bytes(self.array()?)),
                b'J' => Token::Date(i64::from_be_bytes(self.array()?)),
                b'K' => Token::Date(i64::from(i32::from_be_bytes(self.array()?)) * 60_000),
                0x00..=0x1F | 0x30..=0x33 | b'S' | b'R' => Token::String(self.string_body(tag)?),
                0x20..=0x2F | 0x34..=0x37 | b'B' | b'A' => Token::Binary(self.binary_body(tag)?),
                b'U' => {
                    let type_name = self.type_ref()?;
                    self.list(Some(type_name), None)
                }
                b'V' => {
                    let type_name = self.type_ref()?;
                    let len = self.length()?;
                    self.list(Some(type_name), Some(len))
                }
                b'W' => self.list(None, None),
                b'X' => {
                    let len = self.length()?;
                    self.list(None, Some(len))
                }
                0x70..=0x77 => {
                    let type_name = self.type_ref()?;
                    self.list(Some(type_name), Some(usize::from(tag - 0x70)))
                }
                0x78..=0x7F => self.list(None, Some(usize::from(tag - 0x78))),
                b'M' => {
                    let type_name = self.type_ref()?;
                    self.started += 1;
                    Token::Map {
                        type_name: Some(type_name),
                    }
                }
                b'H' => {
                    self.started += 1;
                    Token::Map { type_name: None }
                }
                b'C' => {
                    self.class_def()?;
                    continue;
                }
                b'O' | 0x60..=0x6F => {
                    let class = if tag == b'O' {
                        self.index()?
                    } else {
                        usize::from(tag - 0x60)
                    };
                    if class >= self.classes.len() {
                        return Err(Error::new(ErrorKind::UnknownClass, start));
                    }
                    self.started += 1;
                    Token::Object { class }
                }
                b'Q' => {
                    let index = self.index()?;
                    if index >= self.started {
                        return Err(Error::new(ErrorKind::UnknownRef, start));
                    }
                    Token::Ref(index)
                }
                _ => return Err(Error::new(ErrorKind::UnexpectedTag(tag), start)),
            };
            return Ok(token);
        }
    }

    /// Consumes the `Z` that closes a variable-length list or a map and
    /// returns `true`, or returns `false` when another value follows.
    pub fn end(&mut self) -> Result<bool, Error> {
        match self.input.get(self.pos) {
            Some(b'Z') => {
                self.pos += 1;
                Ok(true)
            }
            Some(_) => Ok(false),
            None => Err(self.eof()),
        }
    }

    /// Reads and discards one complete value.
    ///
    /// # Errors
    ///
    /// Returns an error for malformed or truncated input, or for values
    /// nested deeper than [`MAX_SKIP_DEPTH`].
    pub fn skip(&mut self) -> Result<(), Error> {
        self.skip_nested(0)
    }

    fn skip_nested(&mut self, depth: usize) -> Result<(), Error> {
        if depth >= MAX_SKIP_DEPTH {
            return Err(Error::new(ErrorKind::DepthLimit, self.pos));
        }
        match self.next_token()? {
            Token::List { len: Some(len), .. } => {
                for _ in 0..len {
                    self.skip_nested(depth + 1)?;
                }
            }
            Token::List { len: None, .. } => {
                while !self.end()? {
                    self.skip_nested(depth + 1)?;
                }
            }
            Token::Map { .. } => {
                while !self.end()? {
                    self.skip_nested(depth + 1)?;
                    self.skip_nested(depth + 1)?;
                }
            }
            Token::Object { class } => {
                for _ in 0..self.classes[class].fields.len() {
                    self.skip_nested(depth + 1)?;
                }
            }
            _ => {}
        }
        Ok(())
    }

    fn eof(&self) -> Error {
        Error::new(ErrorKind::UnexpectedEof, self.input.len())
    }

    fn byte(&mut self) -> Result<u8, Error> {
        let byte = *self.input.get(self.pos).ok_or_else(|| self.eof())?;
        self.pos += 1;
        Ok(byte)
    }

    fn take(&mut self, len: usize) -> Result<&'a [u8], Error> {
        let input: &'a [u8] = self.input;
        let bytes = input
            .get(self.pos..self.pos + len)
            .ok_or_else(|| self.eof())?;
        self.pos += len;
        Ok(bytes)
    }

    fn array<const N: usize>(&mut self) -> Result<[u8; N], Error> {
        Ok(self.take(N)?.try_into().unwrap())
    }

    fn int_body(&mut self, tag: u8) -> Result<i32, Error> {
        Ok(match tag {
            0x80..=0xBF => i32::from(tag) - 0x90,
            0xC0..=0xCF => ((i32::from(tag) - 0xC8) << 8) | i32::from(self.byte()?),
            0xD0..=0xD7 => {
                let [b1, b0] = self.array()?;
                ((i32::from(tag) - 0xD4) << 16) | i32::from(u16::from_be_bytes([b1, b0]))
            }
            _ => i32::from_be_bytes(self.array()?),
        })
    }

    /// Reads an int where the grammar requires one.
    fn int(&mut self) -> Result<i32, Error> {
        let start = self.pos;
        match self.byte()? {
            tag @ (0x80..=0xD7 | b'I') => self.int_body(tag),
            tag => Err(Error::new(ErrorKind::UnexpectedTag(tag), start)),
        }
    }

    fn index(&mut self) -> Result<usize, Error> {
        let start = self.pos;
        usize::try_from(self.int()?).map_err(|_| Error::new(ErrorKind::InvalidLength, start))
    }

    /// Reads a count of values; each value takes at least one byte.
    fn length(&mut self) -> Result<usize, Error> {
        let start = self.pos;
        let len = self.index()?;
        if len > self.input.len() - self.pos {
            return Err(Error::new(ErrorKind::InvalidLength, start));
        }
        Ok(len)
    }

    fn list(&mut self, type_name: Option<usize>, len: Option<usize>) -> Token<'a> {
        self.started += 1;
        Token::List { type_name, len }
    }

    fn type_ref(&mut self) -> Result<usize, Error> {
        let start = self.pos;
        match self.input.get(self.pos) {
            Some(0x00..=0x1F | 0x30..=0x33 | b'S' | b'R') => {
                let name = self.string()?;
                self.types.push(name);
                Ok(self.types.len() - 1)
            }
            Some(_) => {
                let index = self.index()?;
                if index >= self.types.len() {
                    return Err(Error::new(ErrorKind::UnknownType, start));
                }
                Ok(index)
            }
            None => Err(self.eof()),
        }
    }

    fn class_def(&mut self) -> Result<(), Error> {
        let name = self.string()?;
        let count = self.length()?;
        let fields = (0..count)
            .map(|_| self.string())
            .collect::<Result<_, _>>()?;
        self.classes.push(ClassDef { name, fields });
        Ok(())
    }

    /// Reads a class, field, or type name, which the grammar requires to be
    /// a string.
    ///
    /// Unpaired surrogates fail as [`ErrorKind::InvalidString`]: Java
    /// identifiers cannot hold them, and Rust text cannot either.
    fn string(&mut self) -> Result<String, Error> {
        let start = self.pos;
        let tag = self.byte()?;
        let bytes = match tag {
            0x00..=0x1F | 0x30..=0x33 | b'S' | b'R' => self.string_body(tag)?,
            _ => return Err(Error::new(ErrorKind::UnexpectedTag(tag), start)),
        };
        text::cesu8_to_utf8(&bytes)
            .map(Cow::into_owned)
            .ok_or_else(|| Error::new(ErrorKind::InvalidString, start))
    }

    /// Returns `(final, units)` for a string chunk header.
    fn string_chunk(&mut self, tag: u8) -> Result<(bool, usize), Error> {
        Ok(match tag {
            0x00..=0x1F => (true, usize::from(tag)),
            0x30..=0x33 => (
                true,
                (usize::from(tag - 0x30) << 8) | usize::from(self.byte()?),
            ),
            b'S' => (true, usize::from(u16::from_be_bytes(self.array()?))),
            b'R' => (false, usize::from(u16::from_be_bytes(self.array()?))),
            _ => return Err(Error::new(ErrorKind::UnexpectedTag(tag), self.pos - 1)),
        })
    }

    fn string_units(&mut self, units: usize) -> Result<&'a [u8], Error> {
        let rest = &self.input[self.pos..];
        match text::utf16_span(rest, units) {
            Some(len) => self.take(len),
            None if rest.len() < units => Err(self.eof()),
            None => Err(Error::new(ErrorKind::InvalidString, self.pos)),
        }
    }

    fn string_body(&mut self, tag: u8) -> Result<Cow<'a, [u8]>, Error> {
        let (mut last, units) = self.string_chunk(tag)?;
        let first = self.string_units(units)?;
        if last {
            return Ok(Cow::Borrowed(first));
        }
        let mut joined = first.to_vec();
        while !last {
            let tag = self.byte()?;
            let (chunk_last, units) = self.string_chunk(tag)?;
            joined.extend_from_slice(self.string_units(units)?);
            last = chunk_last;
        }
        Ok(Cow::Owned(joined))
    }

    /// Returns `(final, len)` for a binary chunk header.
    fn binary_chunk(&mut self, tag: u8) -> Result<(bool, usize), Error> {
        Ok(match tag {
            0x20..=0x2F => (true, usize::from(tag - 0x20)),
            0x34..=0x37 => (
                true,
                (usize::from(tag - 0x34) << 8) | usize::from(self.byte()?),
            ),
            b'B' => (true, usize::from(u16::from_be_bytes(self.array()?))),
            b'A' => (false, usize::from(u16::from_be_bytes(self.array()?))),
            _ => return Err(Error::new(ErrorKind::UnexpectedTag(tag), self.pos - 1)),
        })
    }

    fn binary_body(&mut self, tag: u8) -> Result<Cow<'a, [u8]>, Error> {
        let (mut last, len) = self.binary_chunk(tag)?;
        let first = self.take(len)?;
        if last {
            return Ok(Cow::Borrowed(first));
        }
        let mut joined = first.to_vec();
        while !last {
            let tag = self.byte()?;
            let (chunk_last, len) = self.binary_chunk(tag)?;
            joined.extend_from_slice(self.take(len)?);
            last = chunk_last;
        }
        Ok(Cow::Owned(joined))
    }
}
