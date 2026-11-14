//! Hessian 2.0 serialization primitives.
//!
//! [`Reader`] turns a byte stream into [`Token`]s and [`Writer`] turns calls
//! into bytes. Both keep the per-stream tables Hessian shares across values:
//! class definitions, type names, and reference numbers. Building values from
//! tokens, and choosing tokens for values, belongs to the caller.

#![forbid(unsafe_code)]

mod error;
mod reader;
pub mod text;
mod writer;

pub use error::{Error, ErrorKind};
pub use reader::{ClassDef, MAX_SKIP_DEPTH, Reader, ReaderState, Token};
pub use writer::Writer;

/// Compiles and runs the README's example as a doctest.
#[doc = include_str!("../README.md")]
#[cfg(doctest)]
pub struct ReadmeDoctests;
