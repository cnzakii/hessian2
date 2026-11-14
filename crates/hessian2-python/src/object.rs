//! Python values for Hessian forms with no Python type of their own: an
//! object of an unregistered class, and a value with its Java type.

use pyo3::PyTraverseError;
use pyo3::basic::CompareOp;
use pyo3::exceptions::PyTypeError;
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
        let (CompareOp::Eq | CompareOp::Ne, Ok(other)) = (op, other.cast::<HessianObject>()) else {
            return Ok(py.NotImplemented());
        };
        let other = other.get();
        let equal = self
            .class_name
            .bind(py)
            .as_any()
            .eq(other.class_name.bind(py))?
            && self.fields.bind(py).as_any().eq(other.fields.bind(py))?;
        let result = if matches!(op, CompareOp::Eq) {
            equal
        } else {
            !equal
        };
        Ok(result.into_pyobject(py)?.to_owned().into_any().unbind())
    }
}

/// A value with the annotation it encodes under, for a position without one.
///
/// Encoding writes ``value`` as it writes a value annotated ``type``, as in
/// a field or ``dumps(value, type=type)``, so a dict value or list item can
/// be a Java ``long`` or a typed array. Decoding never returns one, and a
/// codec cannot register a converter for it. Instances are equal when their
/// values and annotations are.
///
/// Args:
///     value: The value to encode, which is not itself a ``Typed``.
///     type: Its annotation, such as ``hessian2.Long`` or
///         ``tuple[str, ...]``.
///
/// Attributes:
///     value (Any): The value.
///     type (Any): The annotation.
#[pyclass(name = "Typed", module = "hessian2", frozen)]
pub struct Typed {
    #[pyo3(get)]
    pub value: Py<PyAny>,
    #[pyo3(get, name = "type")]
    pub ty: Py<PyAny>,
    /// `ty` with the namespace a field's annotation was compiled in, which
    /// encoding compiles instead, for one from `as_object`.
    pub scope: Option<Py<PyAny>>,
}

#[pymethods]
impl Typed {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.value)?;
        visit.call(&self.ty)?;
        visit.call(&self.scope)
    }

    #[new]
    fn new(value: Bound<'_, PyAny>, r#type: Bound<'_, PyAny>) -> PyResult<Self> {
        // One annotation per value; a chain would also nest past any depth
        // limit when freed, as this class has no trashcan.
        if value.is_instance_of::<Typed>() {
            return Err(PyTypeError::new_err("a Typed value cannot be Typed again"));
        }
        if r#type.is_instance_of::<Typed>() {
            return Err(PyTypeError::new_err("a Typed is not an annotation"));
        }
        Ok(Self {
            value: value.unbind(),
            ty: r#type.unbind(),
            scope: None,
        })
    }

    #[classattr]
    fn __match_args__() -> (&'static str, &'static str) {
        ("value", "type")
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        Ok(format!(
            "Typed({}, {})",
            self.value.bind(py).repr()?,
            self.ty.bind(py).repr()?
        ))
    }

    fn __richcmp__(
        &self,
        other: &Bound<'_, PyAny>,
        op: CompareOp,
        py: Python<'_>,
    ) -> PyResult<Py<PyAny>> {
        // Ordering compares nothing, so it runs no `__eq__` of the contents.
        let (CompareOp::Eq | CompareOp::Ne, Ok(other)) = (op, other.cast::<Typed>()) else {
            return Ok(py.NotImplemented());
        };
        let other = other.get();
        let equal = self.value.bind(py).eq(other.value.bind(py))?
            && self.ty.bind(py).eq(other.ty.bind(py))?;
        let result = if matches!(op, CompareOp::Eq) {
            equal
        } else {
            !equal
        };
        Ok(result.into_pyobject(py)?.to_owned().into_any().unbind())
    }
}
