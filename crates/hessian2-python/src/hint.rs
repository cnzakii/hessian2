//! Encoding hints: the Java type a value should be written as where Python
//! values alone cannot tell, compiled from type annotations by the Python
//! package.

use std::collections::HashMap;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyDict, PyString, PyTuple};

/// How to write a value, beyond what its Python type says.
#[derive(Debug)]
pub enum Hint {
    /// An `int` as a Hessian long, which Java reads back as `Long`.
    Long,
    /// An `int` as `java.math.BigInteger`, whatever its size.
    BigInteger,
    /// An aware `datetime` as `java.time.Instant`.
    Instant,
    /// An aware `datetime` as `java.time.OffsetDateTime`.
    OffsetDateTime,
    /// An aware `datetime` as `java.time.ZonedDateTime`.
    ZonedDateTime,
    /// Each item of a list, tuple, or set.
    Items(Box<Hint>),
    /// The items of a fixed-length tuple, by position.
    Tuple(Vec<Option<Hint>>),
    /// The keys and values of a dict.
    Map(Option<Box<Hint>>, Option<Box<Hint>>),
    /// The values of a dict, by string key, as a `TypedDict` declares them.
    Keys(HashMap<String, Hint>),
    /// The first member that applies to the value.
    Union(Vec<Hint>),
}

/// Mixes `n` into `h`, as rustc-hash 2 (<https://github.com/rust-lang/rustc-hash>) does.
fn mix(h: u64, n: u64) -> u64 {
    (h.rotate_left(5) ^ n).wrapping_mul(0xf135_7aea_2e62_a9c5)
}

impl Hint {
    /// Returns a hash of the hint's structure, never 0: equal hints have
    /// equal fingerprints, and different ones collide with chance 2^-63.
    pub fn fingerprint(&self) -> u64 {
        let of = |hint: Option<&Self>| hint.map_or(0, Self::fingerprint);
        let h = match self {
            Self::Long => mix(0, 1),
            Self::BigInteger => mix(0, 2),
            Self::Instant => mix(0, 3),
            Self::OffsetDateTime => mix(0, 4),
            Self::ZonedDateTime => mix(0, 5),
            Self::Items(item) => mix(6, item.fingerprint()),
            Self::Tuple(items) => items
                .iter()
                .fold(mix(0, 7), |h, item| mix(h, of(item.as_ref()))),
            Self::Map(key, value) => mix(mix(8, of(key.as_deref())), of(value.as_deref())),
            // A sum, as the map's order is arbitrary.
            Self::Keys(keys) => mix(
                9,
                keys.iter().fold(0u64, |sum, (key, hint)| {
                    let key = key.bytes().fold(0, |h, byte| mix(h, u64::from(byte)));
                    sum.wrapping_add(mix(key, hint.fingerprint()))
                }),
            ),
            Self::Union(members) => members
                .iter()
                .fold(mix(0, 10), |h, member| mix(h, member.fingerprint())),
        };
        h | 1
    }

    /// Builds a hint from the nested tuples `hessian2._plan` returns; `None`
    /// means no hint.
    pub fn parse(plan: &Bound<'_, PyAny>) -> PyResult<Option<Self>> {
        if plan.is_none() {
            return Ok(None);
        }
        if let Ok(tag) = plan.cast::<PyString>() {
            return Ok(Some(match tag.to_str()? {
                "long" => Self::Long,
                "big_integer" => Self::BigInteger,
                "instant" => Self::Instant,
                "offset_datetime" => Self::OffsetDateTime,
                "zoned_datetime" => Self::ZonedDateTime,
                other => return Err(PyValueError::new_err(format!("unknown hint {other:?}"))),
            }));
        }
        let plan = plan.cast::<PyTuple>()?;
        let kind = plan.get_item(0)?;
        let boxed = |index: usize| -> PyResult<Option<Box<Self>>> {
            Ok(Self::parse(&plan.get_item(index)?)?.map(Box::new))
        };
        let members = |index: usize| -> PyResult<Vec<Option<Self>>> {
            plan.get_item(index)?
                .try_iter()?
                .map(|item| Self::parse(&item?))
                .collect()
        };
        Ok(Some(match kind.cast::<PyString>()?.to_str()? {
            "items" => match boxed(1)? {
                Some(item) => Self::Items(item),
                None => return Ok(None),
            },
            "tuple" => Self::Tuple(members(1)?),
            "map" => Self::Map(boxed(1)?, boxed(2)?),
            "keys" => {
                let keys = plan.get_item(1)?;
                let mut hints = HashMap::new();
                for (key, value) in keys.cast::<PyDict>()?.iter() {
                    if let Some(hint) = Self::parse(&value)? {
                        hints.insert(key.cast::<PyString>()?.to_str()?.to_owned(), hint);
                    }
                }
                Self::Keys(hints)
            }
            "union" => Self::Union(members(1)?.into_iter().flatten().collect()),
            other => return Err(PyValueError::new_err(format!("unknown hint {other:?}"))),
        }))
    }
}

/// A compiled hint, cached per annotation so each call only looks it up.
#[pyclass(frozen, name = "_Hint", module = "hessian2._core")]
pub struct HintObject(pub Option<Hint>);

#[pymethods]
impl HintObject {
    #[new]
    fn new(plan: &Bound<'_, PyAny>) -> PyResult<Self> {
        Ok(Self(Hint::parse(plan)?))
    }
}

/// Annotations compiled so far, shared by every codec: a hint depends only
/// on the annotation.
static HINTS: PyOnceLock<Py<PyDict>> = PyOnceLock::new();

/// More cached annotations than this are discarded before adding one, so
/// annotations built at run time cannot grow the cache without bound.
const MAX_HINTS: usize = 4096;

/// Returns the hint for the annotation `annotation`, compiling it with the
/// codec's `_hint` method on first use.
///
/// # Errors
///
/// Propagates errors from `_hint`, such as an annotation it cannot resolve.
pub fn lookup<'py>(
    codec: &Bound<'py, PyAny>,
    annotation: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, HintObject>>> {
    let py = codec.py();
    if annotation.is_none() {
        return Ok(None);
    }
    let cache = HINTS.get_or_init(py, || PyDict::new(py).unbind()).bind(py);
    // An unhashable annotation, such as one with dict metadata, compiles each time.
    let cached = cache.get_item(annotation).ok();
    if let Some(Some(hint)) = cached {
        return Ok(Some(hint.cast_into::<HintObject>()?));
    }
    let hint = codec
        .call_method1(pyo3::intern!(py, "_hint"), (annotation,))?
        .cast_into::<HintObject>()?;
    if cached.is_some() {
        if cache.len() >= MAX_HINTS {
            cache.clear();
        }
        cache.set_item(annotation, &hint)?;
    }
    Ok(Some(hint))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fingerprints_follow_structure() {
        let items = |hint| Hint::Items(Box::new(hint));
        assert_eq!(
            items(Hint::Long).fingerprint(),
            items(Hint::Long).fingerprint()
        );
        assert_ne!(
            items(Hint::Long).fingerprint(),
            items(Hint::BigInteger).fingerprint()
        );
        assert_ne!(items(Hint::Long).fingerprint(), Hint::Long.fingerprint());
        let keys = |a, b| Hint::Keys(HashMap::from([("a".to_owned(), a), ("b".to_owned(), b)]));
        // Each map iterates in its own random order.
        assert_eq!(
            keys(Hint::Long, Hint::BigInteger).fingerprint(),
            keys(Hint::Long, Hint::BigInteger).fingerprint()
        );
        assert_ne!(
            keys(Hint::Long, Hint::BigInteger).fingerprint(),
            keys(Hint::BigInteger, Hint::Long).fingerprint()
        );
        assert_ne!(Hint::Long.fingerprint(), 0);
    }
}
