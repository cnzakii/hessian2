//! The Python value of an object whose class has no registration.

use pyo3::PyTraverseError;
use pyo3::basic::CompareOp;
use pyo3::gc::PyVisit;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString};

/// An object of a Java class with no registration.
///
/// Decoding returns one for an unregistered class when ``unknown_class`` is
/// ``"hessian_object"``, and inside fields a registered class ignores. Encoding
/// writes its class name and fields, so it also sends an object of a class
/// without a Python type. Instances are equal when their names and fields
/// are.
///
/// Args:
///     class_name: The Java class name, such as ``"com.example.User"``.
///     fields: Field values by field name, in the order they are sent.
///
/// Attributes:
///     class_name (str): The class name the peer sent, such as
///         ``"com.example.User"``.
///     fields (dict[str, Any]): Field values by field name.
#[pyclass(name = "HessianObject", module = "hessian2", frozen)]
pub struct HessianObject {
    #[pyo3(get)]
    pub class_name: Py<PyString>,
    #[pyo3(get)]
    pub fields: Py<PyDict>,
}

#[pymethods]
impl HessianObject {
    /// Reports the fields to the garbage collector, which can then free
    /// cycles through them, such as an object among its own fields.
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.class_name)?;
        visit.call(&self.fields)
    }

    #[new]
    #[pyo3(signature = (class_name, fields = None))]
    fn new(
        py: Python<'_>,
        class_name: Bound<'_, PyString>,
        fields: Option<Bound<'_, PyDict>>,
    ) -> Self {
        Self {
            class_name: class_name.unbind(),
            fields: fields.unwrap_or_else(|| PyDict::new(py)).unbind(),
        }
    }

    #[classattr]
    fn __match_args__() -> (&'static str, &'static str) {
        ("class_name", "fields")
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        Ok(format!(
            "HessianObject({}, {})",
            self.class_name.bind(py).repr()?,
            self.fields.bind(py).repr()?
        ))
    }

    fn __richcmp__(
        &self,
        other: &Bound<'_, PyAny>,
        op: CompareOp,
        py: Python<'_>,
    ) -> PyResult<Py<PyAny>> {
        let Ok(other) = other.cast::<HessianObject>() else {
            return Ok(py.NotImplemented());
        };
        let other = other.get();
        let equal = self
            .class_name
            .bind(py)
            .as_any()
            .eq(other.class_name.bind(py))?
            && self.fields.bind(py).as_any().eq(other.fields.bind(py))?;
        match op {
            CompareOp::Eq => Ok(equal.into_pyobject(py)?.to_owned().into_any().unbind()),
            CompareOp::Ne => Ok((!equal).into_pyobject(py)?.to_owned().into_any().unbind()),
            _ => Ok(py.NotImplemented()),
        }
    }
}
