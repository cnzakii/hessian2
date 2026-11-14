//! `Codec.reader`: decoding a stream one value at a time, as Java's
//! `Hessian2Input` reads with `readObject` and `pickle.Unpickler` with
//! `load`.

use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use pyo3::PyTraverseError;
use pyo3::exceptions::{PyEOFError, PyRuntimeError};
use pyo3::gc::PyVisit;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::DecodeError;
use crate::codec::{Registry, Unknown};
use crate::decode::{Decoder, Saved};
use crate::typed;

/// Decodes the values of one Hessian stream in order, keeping the stream's
/// class definitions and references between them.
#[pyclass(frozen, name = "Reader", module = "hessian2._core")]
pub struct Reader {
    codec: Py<PyAny>,
    data: Py<PyBytes>,
    registry: Py<Registry>,
    unknown: Unknown,
    max_depth: usize,
    /// The decoder between values; `None` once a value failed to decode,
    /// which leaves the stream position unknown.
    state: Mutex<Option<Saved>>,
    /// The offset after the last value decoded.
    offset: AtomicUsize,
}

impl Reader {
    pub fn new(
        codec: Py<PyAny>,
        data: Py<PyBytes>,
        registry: Py<Registry>,
        unknown: Unknown,
        max_depth: usize,
    ) -> Self {
        Self {
            codec,
            data,
            registry,
            unknown,
            max_depth,
            state: Mutex::new(Some(Saved::default())),
            offset: AtomicUsize::new(0),
        }
    }
}

#[pymethods]
impl Reader {
    /// Decode the next value.
    ///
    /// Args:
    ///     type: An annotation the value must match, as for ``Codec.loads``.
    ///     unknown_class: Overrides the codec's ``unknown_class`` for this
    ///         value.
    ///
    /// Returns:
    ///     The value.
    ///
    /// Raises:
    ///     EOFError: If every value has been read.
    ///     DecodeError: As for ``Codec.loads``. The reader cannot continue
    ///         after one, and later calls raise it again.
    ///     TypeError: As for ``Codec.loads``.
    ///     RuntimeError: If another thread, or code this decode runs, is
    ///         using the reader.
    #[pyo3(signature = (*, r#type = None, unknown_class = None))]
    fn load(
        &self,
        py: Python<'_>,
        r#type: Option<&Bound<'_, PyAny>>,
        unknown_class: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        // Held before any annotation code runs, which could call load again.
        let mut state = self
            .state
            .try_lock()
            .map_err(|_| PyRuntimeError::new_err("the reader is already decoding a value"))?;
        let codec = self.codec.bind(py);
        let ty = r#type
            .map(|annotation| typed::lookup(codec, annotation))
            .transpose()?;
        let unknown = unknown_class.map_or(Ok(self.unknown), Unknown::parse)?;
        let data = self.data.bind(py).as_bytes();
        let saved = match state.take() {
            Some(saved) if saved.offset() == data.len() => {
                *state = Some(saved);
                return Err(PyEOFError::new_err("no more values in the stream"));
            }
            Some(saved) => saved,
            None => {
                return Err(DecodeError::new_err(
                    "an earlier value failed to decode, so the reader cannot continue",
                ));
            }
        };
        let registry = self.registry.get();
        let mut decoder =
            Decoder::resume(py, data, saved, registry, unknown, self.max_depth, codec);
        let value = match &ty {
            Some(ty) => {
                decoder.keep(ty);
                decoder.typed(&ty.get().0)
            }
            None => decoder.value(),
        };
        match value {
            Ok(value) => {
                let saved = decoder.suspend();
                self.offset.store(saved.offset(), Ordering::Relaxed);
                *state = Some(saved);
                Ok(value.unbind())
            }
            Err(error) => Err(decoder.locate(error)),
        }
    }

    /// Return the offset of the next undecoded byte.
    fn tell(&self) -> usize {
        self.offset.load(Ordering::Relaxed)
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.codec)?;
        visit.call(&self.data)?;
        visit.call(&self.registry)?;
        // Never block GC on a decode in progress.
        if let Ok(state) = self.state.try_lock()
            && let Some(saved) = &*state
        {
            saved.traverse(&visit)?;
        }
        Ok(())
    }

    fn __clear__(&self) {
        let retired = self.state.lock().map(|mut state| state.take());
        drop(retired);
    }
}
