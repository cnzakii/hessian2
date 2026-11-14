//! Mappings between JDK classes and Python standard library types.
//!
//! Each [`Builtin`] is the object form Caucho 4.0.66 or hessian-lite 4.0.5
//! writes for a JDK value, with the Python value it decodes to and, for most, the Python type that
//! encodes to it.

use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{
    PyBytes, PyDate, PyDateTime, PyDelta, PyDict, PyFloat, PyInt, PyList, PyString, PyTime, PyType,
    PyTzInfo,
};

use crate::{DecodeError, EncodeError, time};

pub const DECIMAL: &str = "java.math.BigDecimal";
pub const UUID: &str = "java.util.UUID";

/// The most fields any built-in form is decoded from.
pub const MAX_FIELDS: usize = 4;

const MICROS_PER_DAY: i128 = 86_400_000_000;

/// A JDK value with a fixed object form.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Builtin {
    /// `java.math.BigDecimal` and `decimal.Decimal`: `{value}` holding
    /// `toString()`.
    Decimal,
    /// `java.util.UUID` and `uuid.UUID`: hessian-lite writes `{value}`, and
    /// Caucho the private fields `mostSigBits` and `leastSigBits`.
    Uuid,
    /// `java.math.BigInteger` and an `int` outside the 64-bit range: the JDK's
    /// private fields, of which `signum` and `mag` hold the value.
    BigInteger,
    /// `java.time.LocalDate` and `datetime.date`.
    LocalDate,
    /// `java.time.LocalTime` and a naive `datetime.time`.
    LocalTime,
    /// `java.time.LocalDateTime` and a naive `datetime.datetime`.
    LocalDateTime,
    /// `java.time.Duration` and `datetime.timedelta`.
    Duration,
    /// `java.time.Instant`, decoded as an aware UTC `datetime`.
    Instant,
    /// `java.time.ZoneOffset`, decoded as a fixed-offset `datetime.timezone`.
    ZoneOffset,
    /// `java.time.OffsetDateTime`, decoded as an aware `datetime`.
    OffsetDateTime,
    /// `java.time.ZonedDateTime`, decoded as an aware `datetime` in its
    /// `zoneinfo` zone, or at its offset when that zone is unavailable.
    ZonedDateTime,
    /// Caucho's boxes for a `Byte` or `Short` written as `Object`, decoded as
    /// `int`.
    SmallInt,
    /// Caucho's box for a `Float` written as `Object`, decoded as `float`.
    Float,
}

impl Builtin {
    pub const ALL: [Self; 13] = [
        Self::Decimal,
        Self::Uuid,
        Self::BigInteger,
        Self::LocalDate,
        Self::LocalTime,
        Self::LocalDateTime,
        Self::Duration,
        Self::Instant,
        Self::ZoneOffset,
        Self::OffsetDateTime,
        Self::ZonedDateTime,
        Self::SmallInt,
        Self::Float,
    ];

    /// Returns the class names that decode as this value; the first is the
    /// one written.
    pub fn names(self) -> &'static [&'static str] {
        match self {
            Self::Decimal => &[DECIMAL],
            Self::Uuid => &[UUID],
            Self::BigInteger => &["java.math.BigInteger"],
            Self::LocalDate => &["com.alibaba.com.caucho.hessian.io.java8.LocalDateHandle"],
            Self::LocalTime => &["com.alibaba.com.caucho.hessian.io.java8.LocalTimeHandle"],
            Self::LocalDateTime => &["com.alibaba.com.caucho.hessian.io.java8.LocalDateTimeHandle"],
            Self::Duration => &["com.alibaba.com.caucho.hessian.io.java8.DurationHandle"],
            Self::Instant => &["com.alibaba.com.caucho.hessian.io.java8.InstantHandle"],
            Self::ZoneOffset => &["com.alibaba.com.caucho.hessian.io.java8.ZoneOffsetHandle"],
            Self::OffsetDateTime => {
                &["com.alibaba.com.caucho.hessian.io.java8.OffsetDateTimeHandle"]
            }
            Self::ZonedDateTime => &["com.alibaba.com.caucho.hessian.io.java8.ZonedDateTimeHandle"],
            Self::SmallInt => &[
                "com.caucho.hessian.io.ByteHandle",
                "com.caucho.hessian.io.ShortHandle",
                "com.alibaba.com.caucho.hessian.io.ByteHandle",
                "com.alibaba.com.caucho.hessian.io.ShortHandle",
            ],
            Self::Float => &[
                "com.caucho.hessian.io.FloatHandle",
                "com.alibaba.com.caucho.hessian.io.FloatHandle",
            ],
        }
    }

    /// Returns the fields written, in the order Java writes them.
    pub fn wire_fields(self) -> &'static [&'static str] {
        match self {
            Self::Decimal | Self::Uuid => &["value"],
            // Caucho's order; hessian-lite writes the same fields reversed.
            Self::BigInteger => &[
                "signum",
                "bitCountPlusOne",
                "bitLengthPlusOne",
                "lowestSetBitPlusTwo",
                "firstNonzeroIntNumPlusTwo",
                "mag",
            ],
            Self::LocalDate => &["day", "month", "year"],
            Self::LocalTime => &["nano", "second", "minute", "hour"],
            Self::LocalDateTime => &["time", "date"],
            Self::Duration | Self::Instant => &["nanos", "seconds"],
            Self::ZoneOffset => &["seconds"],
            Self::OffsetDateTime => &["offset", "dateTime"],
            Self::ZonedDateTime => &["offset", "dateTime", "zoneId"],
            Self::SmallInt | Self::Float => &["_value"],
        }
    }

    /// Returns the fields [`Builtin::build`] receives, in order; any other
    /// field is read and ignored.
    pub fn read_fields(self) -> &'static [&'static str] {
        match self {
            Self::Decimal => &["value"],
            Self::Uuid => &["value", "mostSigBits", "leastSigBits"],
            Self::BigInteger => &["signum", "mag"],
            Self::LocalDate => &["year", "month", "day"],
            Self::LocalTime => &["hour", "minute", "second", "nano"],
            Self::LocalDateTime => &["date", "time"],
            Self::Duration | Self::Instant => &["seconds", "nanos"],
            Self::ZoneOffset => &["seconds"],
            Self::OffsetDateTime => &["dateTime", "offset"],
            Self::ZonedDateTime => &["dateTime", "offset", "zoneId"],
            Self::SmallInt | Self::Float => &["_value"],
        }
    }

    /// Returns the Python type whose exact instances encode as this value.
    ///
    /// A naive `datetime` encodes as [`Builtin::LocalDateTime`] and a large
    /// `int` as [`Builtin::BigInteger`], but neither type maps here because
    /// other instances of it encode differently.
    pub fn python_type(self, py: Python<'_>) -> PyResult<Option<Bound<'_, PyType>>> {
        Ok(match self {
            Self::Decimal => Some(api(py)?.decimal.bind(py).clone()),
            Self::Uuid => Some(api(py)?.uuid.bind(py).clone()),
            Self::LocalDate => Some(py.get_type::<PyDate>()),
            Self::LocalTime => Some(py.get_type::<PyTime>()),
            Self::Duration => Some(py.get_type::<PyDelta>()),
            _ => None,
        })
    }

    /// Returns the class of the values [`Builtin::build`] returns, so a
    /// position can be checked before one is built.
    pub fn decoded_type(self, py: Python<'_>) -> PyResult<Bound<'_, PyType>> {
        Ok(match self {
            Self::Decimal => api(py)?.decimal.bind(py).clone(),
            Self::Uuid => api(py)?.uuid.bind(py).clone(),
            Self::BigInteger | Self::SmallInt => py.get_type::<pyo3::types::PyInt>(),
            Self::Float => py.get_type::<pyo3::types::PyFloat>(),
            Self::LocalDate => py.get_type::<PyDate>(),
            Self::LocalTime => py.get_type::<PyTime>(),
            Self::Duration => py.get_type::<PyDelta>(),
            Self::LocalDateTime | Self::Instant | Self::OffsetDateTime | Self::ZonedDateTime => {
                py.get_type::<pyo3::types::PyDateTime>()
            }
            Self::ZoneOffset => py
                .import(intern!(py, "datetime"))?
                .getattr(intern!(py, "timezone"))?
                .cast_into()?,
        })
    }

    /// Builds the Python value from the fields named by
    /// [`Builtin::read_fields`], each `None` when the object lacks it.
    ///
    /// # Errors
    ///
    /// Raises `DecodeError` when a field is missing, has the wrong type, or
    /// holds a value Python cannot represent.
    pub fn build<'py>(
        self,
        py: Python<'py>,
        class: &str,
        values: [Option<Bound<'py, PyAny>>; MAX_FIELDS],
    ) -> PyResult<Bound<'py, PyAny>> {
        let fields = Fields {
            class,
            names: self.read_fields(),
            values,
        };
        match self {
            Self::Decimal => decimal_from(py, &fields.string(0)?),
            Self::Uuid => match (fields.present(1), fields.present(2)) {
                (true, true) if !fields.present(0) => {
                    uuid_from_halves(py, fields.int(1)?, fields.int(2)?)
                }
                _ => uuid_from_text(py, &fields.string(0)?),
            },
            Self::BigInteger => big_integer(py, &fields),
            Self::LocalDate => fields.range(PyDate::new(
                py,
                fields.int(0)?,
                fields.int(1)?,
                fields.int(2)?,
            )),
            Self::LocalTime => {
                let nano: u32 = fields.int(3)?;
                fields.range(PyTime::new(
                    py,
                    fields.int(0)?,
                    fields.int(1)?,
                    fields.int(2)?,
                    nano / 1000,
                    None,
                ))
            }
            Self::LocalDateTime => {
                let date = fields.exact::<PyDate>(0)?;
                let time = fields.exact::<PyTime>(1)?;
                time::combine(py, &date, &time, None).map(Bound::into_any)
            }
            Self::Duration => {
                let micros = fields.micros()?;
                let days = i32::try_from(micros.div_euclid(MICROS_PER_DAY))
                    .map_err(|_| fields.out_of_range())?;
                let rest = micros.rem_euclid(MICROS_PER_DAY) as i64;
                fields.range(PyDelta::new(
                    py,
                    days,
                    (rest / 1_000_000) as i32,
                    (rest % 1_000_000) as i32,
                    false,
                ))
            }
            Self::Instant => {
                let micros = fields.micros()?;
                let micros = i64::try_from(micros).map_err(|_| fields.out_of_range())?;
                time::from_micros(py, micros)
                    .map_err(|_| fields.out_of_range())
                    .map(Bound::into_any)
            }
            Self::ZoneOffset => {
                let delta = PyDelta::new(py, 0, fields.int(0)?, 0, true)?;
                fields.range(PyTzInfo::fixed_offset(py, delta))
            }
            Self::OffsetDateTime | Self::ZonedDateTime => {
                let local = fields.exact::<PyDateTime>(0)?;
                // A `datetime.timezone`, which subclasses tzinfo.
                let offset = fields
                    .get(1)?
                    .cast::<PyTzInfo>()
                    .map_err(|_| fields.wrong_type(1, "a tzinfo"))?
                    .clone();
                let at_offset = time::combine(py, &local, &local, Some(&offset))?;
                if self == Self::OffsetDateTime {
                    return Ok(at_offset.into_any());
                }
                let Some(zone) = zone(py, &fields.string(2)?) else {
                    return Ok(at_offset.into_any());
                };
                // Java's `ZonedDateTime.ofLocal` keeps the local time and the
                // offset when the zone allows that offset there; placing the
                // local time in the zone directly also stays clear of the UTC
                // year range at the edges.
                let wanted = at_offset.call_method0(intern!(py, "utcoffset"))?;
                for fold in [0, 1] {
                    let kwargs = PyDict::new(py);
                    kwargs.set_item(intern!(py, "tzinfo"), &zone)?;
                    kwargs.set_item(intern!(py, "fold"), fold)?;
                    let zoned = local.call_method(intern!(py, "replace"), (), Some(&kwargs))?;
                    if zoned.call_method0(intern!(py, "utcoffset"))?.eq(&wanted)? {
                        return Ok(zoned);
                    }
                }
                // The zone's rules differ from the writer's: keep the instant.
                at_offset
                    .call_method1(intern!(py, "astimezone"), (zone,))
                    .map_err(|_| fields.out_of_range())
            }
            Self::SmallInt => Ok(fields.exact::<PyInt>(0)?.into_any()),
            Self::Float => {
                let value = fields.get(0)?;
                if value.is_instance_of::<PyFloat>() || value.is_instance_of::<PyInt>() {
                    Ok(PyFloat::new(py, value.extract()?).into_any())
                } else {
                    Err(fields.wrong_type(0, "a number"))
                }
            }
        }
    }
}

/// The decoded fields of one built-in object, for error messages that name
/// the class and field.
struct Fields<'a, 'py> {
    class: &'a str,
    names: &'static [&'static str],
    values: [Option<Bound<'py, PyAny>>; MAX_FIELDS],
}

impl<'py> Fields<'_, 'py> {
    fn present(&self, index: usize) -> bool {
        self.values[index].as_ref().is_some_and(|v| !v.is_none())
    }

    fn get(&self, index: usize) -> PyResult<&Bound<'py, PyAny>> {
        self.values[index].as_ref().ok_or_else(|| {
            DecodeError::new_err(format!(
                "{} is missing field {:?}",
                self.class, self.names[index]
            ))
        })
    }

    fn wrong_type(&self, index: usize, expected: &str) -> PyErr {
        DecodeError::new_err(format!(
            "field {:?} of {} must be {expected}",
            self.names[index], self.class
        ))
    }

    fn out_of_range(&self) -> PyErr {
        DecodeError::new_err(format!(
            "{} is outside the range Python supports",
            self.class
        ))
    }

    /// Maps the `ValueError` or `OverflowError` a constructor raises for an
    /// unrepresentable value to `DecodeError`.
    fn range<T>(&self, result: PyResult<Bound<'py, T>>) -> PyResult<Bound<'py, PyAny>> {
        result.map(Bound::into_any).map_err(|_| self.out_of_range())
    }

    /// Only an exact type, so no peer-built object's methods run.
    fn exact<T: pyo3::PyTypeInfo>(&self, index: usize) -> PyResult<Bound<'py, T>> {
        let value = self.get(index)?;
        if value.is_exact_instance_of::<T>() {
            Ok(value.clone().cast_into::<T>()?)
        } else {
            let name = T::type_object(value.py()).name()?;
            Err(self.wrong_type(index, &format!("a {name}")))
        }
    }

    fn string(&self, index: usize) -> PyResult<Bound<'py, PyString>> {
        self.exact::<PyString>(index)
    }

    fn int<T: for<'a> FromPyObject<'a, 'py>>(&self, index: usize) -> PyResult<T> {
        self.exact::<PyInt>(index)?
            .extract()
            .map_err(|_| self.out_of_range())
    }

    /// The microseconds in a `seconds` and `nanos` pair, rounding toward
    /// negative infinity as `Duration` and `Instant` do.
    fn micros(&self) -> PyResult<i128> {
        let seconds: i64 = self.int(0)?;
        let nanos: i64 = self.int(1)?;
        Ok(i128::from(seconds) * 1_000_000 + i128::from(nanos.div_euclid(1000)))
    }
}

fn big_integer<'py>(py: Python<'py>, fields: &Fields<'_, 'py>) -> PyResult<Bound<'py, PyAny>> {
    let signum: i32 = fields.int(0)?;
    let mag = fields.exact::<PyList>(1)?;
    let mut bytes = Vec::with_capacity(mag.len() * 4);
    for word in mag.iter() {
        let word: i32 = word
            .cast_into::<PyInt>()
            .map_err(|_| fields.wrong_type(1, "a list of int"))?
            .extract()
            .map_err(|_| fields.out_of_range())?;
        bytes.extend_from_slice(&word.to_be_bytes());
    }
    let magnitude = py
        .get_type::<PyInt>()
        .call_method1(intern!(py, "from_bytes"), (PyBytes::new(py, &bytes), "big"))?;
    match signum {
        1 => Ok(magnitude),
        0 => Ok(0i32.into_pyobject(py)?.into_any()),
        -1 => magnitude.neg(),
        _ => Err(fields.wrong_type(0, "-1, 0, or 1")),
    }
}

/// Returns the signum and the big-endian 32-bit words of `value`'s magnitude
/// without leading zero words, as `BigInteger` stores them.
pub fn big_integer_parts(value: &Bound<'_, PyInt>) -> PyResult<(i32, Vec<i32>)> {
    let py = value.py();
    // int's own `__pos__` returns an exact int, so a subclass's overrides of
    // the methods below cannot change the number written.
    let value = if value.is_exact_instance_of::<PyInt>() {
        value.clone().into_any()
    } else {
        py.get_type::<PyInt>()
            .getattr(intern!(py, "__pos__"))?
            .call1((value,))?
    };
    let negative = value.lt(0)?;
    let magnitude = if negative { value.neg()? } else { value };
    let bits: usize = magnitude
        .call_method0(intern!(py, "bit_length"))?
        .extract()?;
    let words = bits.div_ceil(32);
    let bytes = magnitude.call_method1(intern!(py, "to_bytes"), (words * 4, "big"))?;
    let mag = bytes
        .cast::<PyBytes>()?
        .as_bytes()
        .as_chunks::<4>()
        .0
        .iter()
        .map(|word| i32::from_be_bytes(*word))
        .collect();
    let signum = if words == 0 {
        0
    } else if negative {
        -1
    } else {
        1
    };
    Ok((signum, mag))
}

/// The `zoneinfo` zone named by a Java zone ID, if Python knows it.
fn zone<'py>(py: Python<'py>, id: &Bound<'py, PyString>) -> Option<Bound<'py, PyAny>> {
    api(py).ok()?.zone_info.bind(py).call1((id,)).ok()
}

/// Python objects decoding and encoding call, looked up once per process.
pub struct Api {
    pub object_new: Py<PyAny>,
    pub tuple_new: Py<PyAny>,
    pub object_setattr: Py<PyAny>,
    pub enum_type: Py<PyType>,
    decimal: Py<PyType>,
    uuid: Py<PyType>,
    uuid_unknown: Py<PyAny>,
    pub zone_info: Py<PyAny>,
}

/// Returns the shared [`Api`], importing its modules on first use.
pub fn api(py: Python<'_>) -> PyResult<&Api> {
    static API: PyOnceLock<Api> = PyOnceLock::new();
    API.get_or_try_init(py, || {
        let object = py.import("builtins")?.getattr("object")?;
        let uuid = py.import("uuid")?;
        Ok::<_, PyErr>(Api {
            object_new: object.getattr("__new__")?.unbind(),
            tuple_new: py
                .get_type::<pyo3::types::PyTuple>()
                .getattr("__new__")?
                .unbind(),
            object_setattr: object.getattr("__setattr__")?.unbind(),
            enum_type: py.import("enum")?.getattr("Enum")?.cast_into()?.unbind(),
            decimal: py
                .import("decimal")?
                .getattr("Decimal")?
                .cast_into()?
                .unbind(),
            uuid: uuid.getattr("UUID")?.cast_into()?.unbind(),
            uuid_unknown: uuid.getattr("SafeUUID")?.getattr("unknown")?.unbind(),
            zone_info: py.import("zoneinfo")?.getattr("ZoneInfo")?.unbind(),
        })
    })
}

/// Returns `str(value)` for a finite `Decimal`, which `new BigDecimal(String)`
/// parses.
///
/// # Errors
///
/// Raises `EncodeError` for NaN and infinities, which `BigDecimal` lacks.
pub fn decimal_text(value: &Bound<'_, PyAny>) -> PyResult<String> {
    let text = value.str()?.to_str()?.to_owned();
    // Non-finite values print as NaN, sNaN, or Infinity, with an optional sign.
    if matches!(
        text.trim_start_matches('-').as_bytes().first(),
        Some(b'N' | b's' | b'I')
    ) {
        return Err(EncodeError::new_err(format!(
            "Decimal('{text}') has no {DECIMAL} form"
        )));
    }
    Ok(text)
}

/// Returns whether `text` matches what `BigDecimal.toString` writes and
/// `new BigDecimal(String)` reads: `[+-]digits[.digits][E[+-]digits]`, where
/// one side of the point may be empty.
fn is_decimal(text: &[u8]) -> bool {
    let digits = |from: usize| {
        from + text[from..]
            .iter()
            .take_while(|b| b.is_ascii_digit())
            .count()
    };
    let mut at = usize::from(matches!(text.first(), Some(b'+' | b'-')));
    let int_end = digits(at);
    let mut count = int_end - at;
    at = int_end;
    if text.get(at) == Some(&b'.') {
        let frac_end = digits(at + 1);
        count += frac_end - at - 1;
        at = frac_end;
    }
    if count == 0 {
        return false;
    }
    if matches!(text.get(at), Some(b'e' | b'E')) {
        at += 1 + usize::from(matches!(text.get(at + 1), Some(b'+' | b'-')));
        let exp_end = digits(at);
        if exp_end == at {
            return false;
        }
        at = exp_end;
    }
    at == text.len()
}

/// Returns the `Decimal` for a `BigDecimal.toString` form.
///
/// # Errors
///
/// Raises `DecodeError` when `text` is not in that form, rather than letting
/// `Decimal` accept forms Java never writes, such as `NaN`.
fn decimal_from<'py>(py: Python<'py>, text: &Bound<'py, PyString>) -> PyResult<Bound<'py, PyAny>> {
    if !is_decimal(text.to_str()?.as_bytes()) {
        return Err(DecodeError::new_err(format!(
            "invalid {DECIMAL} {}",
            text.repr()?
        )));
    }
    api(py)?.decimal.bind(py).call1((text,))
}

/// Returns the 36-character form `UUID.toString` writes for a `uuid.UUID`.
pub fn uuid_text(value: &Bound<'_, PyAny>) -> PyResult<String> {
    let n: u128 = value.getattr(intern!(value.py(), "int"))?.extract()?;
    Ok(format!(
        "{:08x}-{:04x}-{:04x}-{:04x}-{:012x}",
        n >> 96,
        (n >> 80) & 0xffff,
        (n >> 64) & 0xffff,
        (n >> 48) & 0xffff,
        n & 0xffff_ffff_ffff
    ))
}

/// Parses the 36-character form `UUID.toString` writes, in either case.
fn parse_uuid(text: &[u8]) -> Option<u128> {
    if text.len() != 36 {
        return None;
    }
    let mut n = 0u128;
    for (index, &byte) in text.iter().enumerate() {
        if matches!(index, 8 | 13 | 18 | 23) {
            if byte != b'-' {
                return None;
            }
            continue;
        }
        n = n << 4 | u128::from(char::from(byte).to_digit(16)? as u8);
    }
    Some(n)
}

/// Returns the `uuid.UUID` for hessian-lite's `value` form.
///
/// # Errors
///
/// Raises `DecodeError` when `text` is not the form `UUID.toString` writes.
fn uuid_from_text<'py>(
    py: Python<'py>,
    text: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyAny>> {
    match parse_uuid(text.to_str()?.as_bytes()) {
        Some(n) => uuid_from_int(py, n),
        None => Err(DecodeError::new_err(format!(
            "invalid {UUID} {}",
            text.repr()?
        ))),
    }
}

/// Returns the `uuid.UUID` for Caucho's form, which holds `UUID`'s two private
/// `long` fields.
fn uuid_from_halves(py: Python<'_>, most: i64, least: i64) -> PyResult<Bound<'_, PyAny>> {
    uuid_from_int(py, u128::from(most as u64) << 64 | u128::from(least as u64))
}

/// Returns a `uuid.UUID` built without the parsing in `UUID.__init__`, as
/// `UUID._from_int` does on Python 3.14.
fn uuid_from_int(py: Python<'_>, n: u128) -> PyResult<Bound<'_, PyAny>> {
    let api = api(py)?;
    let uuid = api.object_new.bind(py).call1((api.uuid.bind(py),))?;
    let setattr = api.object_setattr.bind(py);
    setattr.call1((&uuid, "int", n))?;
    setattr.call1((&uuid, "is_safe", api.uuid_unknown.bind(py)))?;
    Ok(uuid)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decimal_grammar() {
        for good in ["0", "-1", "+1.5", "1.", ".5", "1E+3", "1.23e-7", "-0E-8"] {
            assert!(is_decimal(good.as_bytes()), "{good}");
        }
        for bad in [
            "", "-", ".", "1e", "1E+", "NaN", "Infinity", " 1", "1_000", "1.2.3", "0x1",
        ] {
            assert!(!is_decimal(bad.as_bytes()), "{bad}");
        }
    }

    #[test]
    fn uuid_parsing() {
        let text = b"123e4567-E89B-12d3-a456-426614174000";
        assert_eq!(
            parse_uuid(text),
            Some(0x123e4567_e89b_12d3_a456_426614174000)
        );
        assert_eq!(parse_uuid(b"123e4567e89b12d3a456426614174000"), None);
        assert_eq!(parse_uuid(b"123e4567-e89b-12d3-a456-42661417400g"), None);
        assert_eq!(parse_uuid(b"123e4567-e89b-12d3-a456_426614174000"), None);
    }
}
