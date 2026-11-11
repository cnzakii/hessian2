use std::fmt;

/// Why a Hessian stream could not be read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum ErrorKind {
    /// The input ended inside a value.
    UnexpectedEof,
    /// A byte that starts no value allowed at this position.
    UnexpectedTag(u8),
    /// A length or count larger than the remaining input can hold.
    InvalidLength,
    /// String bytes that are neither UTF-8 nor CESU-8, or that do not end on
    /// the declared UTF-16 length.
    InvalidString,
    /// An object refers to a class definition the stream has not sent.
    UnknownClass,
    /// A type reference to a type name the stream has not sent.
    UnknownType,
    /// A back-reference to a value the stream has not started.
    UnknownRef,
    /// Values nest deeper than the reader allows.
    DepthLimit,
}

/// A malformed or truncated Hessian stream, with the byte offset where reading
/// stopped.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    kind: ErrorKind,
    offset: usize,
}

impl Error {
    pub(crate) fn new(kind: ErrorKind, offset: usize) -> Self {
        Self { kind, offset }
    }

    /// Returns why reading failed.
    pub fn kind(&self) -> ErrorKind {
        self.kind
    }

    /// Returns the input offset at which reading failed.
    pub fn offset(&self) -> usize {
        self.offset
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.kind {
            ErrorKind::UnexpectedEof => f.write_str("unexpected end of input"),
            ErrorKind::UnexpectedTag(tag) => write!(f, "unexpected tag 0x{tag:02x}"),
            ErrorKind::InvalidLength => f.write_str("length exceeds the remaining input"),
            ErrorKind::InvalidString => f.write_str("invalid string data"),
            ErrorKind::UnknownClass => f.write_str("reference to an undefined class"),
            ErrorKind::UnknownType => f.write_str("reference to an undefined type"),
            ErrorKind::UnknownRef => f.write_str("reference to an unread value"),
            ErrorKind::DepthLimit => f.write_str("values nest too deeply"),
        }?;
        write!(f, " at byte {}", self.offset)
    }
}

impl std::error::Error for Error {}
