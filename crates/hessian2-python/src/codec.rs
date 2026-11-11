//! The codec: its registrations and the calls that encode and decode with
//! them.

use std::collections::{HashMap, HashSet};
use std::sync::RwLock;

use pyo3::PyTraverseError;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::gc::PyVisit;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{
    PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyNone, PyString, PyTuple, PyType,
};

use crate::address::AddressMap;
use crate::builtins::Builtin;
use crate::decode::Decoder;
use crate::encode::Encoder;

/// What decoding does with an object whose class has no registration.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Unknown {
    Raise,
    Object,
    Dict,
}

pub enum Default {
    Required,
    Value(Py<PyAny>),
    Factory(Py<PyAny>),
}

pub struct Field {
    pub attr: Py<PyString>,
    pub wire: String,
    pub default: Default,
}

pub enum Kind {
    /// A class whose attributes are the object's fields.
    Class {
        fields: Vec<Field>,
        /// Set attributes with `object.__setattr__`, past a frozen class's own.
        bypass_setattr: bool,
        /// The method run after the fields are set, and its arguments: the
        /// defaults of a dataclass's `InitVar`s.
        post_init: Option<(Py<PyString>, Py<PyTuple>)>,
    },
    /// A `NamedTuple` or `collections.namedtuple` class, whose items are the
    /// object's fields.
    Tuple { fields: Vec<Field> },
    /// An `enum.Enum`, sent as an object with one `name` field.
    Enum,
    /// A JDK value mapped to a Python standard library type.
    Value(Builtin),
    /// A type converted to and from a field dictionary.
    Converter {
        encode: Py<PyAny>,
        decode: Py<PyAny>,
    },
}

/// One registered class name and the Python type it maps to.
#[pyclass(frozen)]
pub struct Entry {
    pub name: String,
    pub ty: Py<PyType>,
    pub kind: Kind,
}

impl Entry {
    /// Returns what the name maps to, for error messages.
    fn describe(&self, py: Python<'_>) -> PyResult<String> {
        Ok(match self.kind {
            Kind::Value(_) => "a built-in mapping".to_owned(),
            _ => self.ty.bind(py).qualname()?.to_string(),
        })
    }

    pub fn wire_fields(&self) -> Vec<&str> {
        match &self.kind {
            Kind::Class { fields, .. } | Kind::Tuple { fields } => {
                fields.iter().map(|f| f.wire.as_str()).collect()
            }
            Kind::Enum => vec!["name"],
            Kind::Value(builtin) => builtin.wire_fields().to_vec(),
            Kind::Converter { .. } => Vec::new(),
        }
    }
}

#[pymethods]
impl Entry {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.ty)?;
        match &self.kind {
            Kind::Class {
                fields, post_init, ..
            } => {
                visit_fields(fields, &visit)?;
                if let Some((name, args)) = post_init {
                    visit.call(name)?;
                    visit.call(args)?;
                }
            }
            Kind::Tuple { fields } => visit_fields(fields, &visit)?,
            Kind::Converter { encode, decode } => {
                visit.call(encode)?;
                visit.call(decode)?;
            }
            Kind::Enum | Kind::Value(_) => {}
        }
        Ok(())
    }
}

fn visit_fields(fields: &[Field], visit: &PyVisit<'_>) -> Result<(), PyTraverseError> {
    for field in fields {
        visit.call(&field.attr)?;
        match &field.default {
            Default::Value(value) => visit.call(value)?,
            Default::Factory(factory) => visit.call(factory)?,
            Default::Required => {}
        }
    }
    Ok(())
}

/// An immutable snapshot of a codec's registrations; registering replaces it.
#[derive(Default)]
#[pyclass(frozen)]
pub struct Registry {
    pub by_name: HashMap<String, Py<Entry>>,
    /// Keyed by the address of the registered type, which `Entry::ty` keeps
    /// alive.
    pub by_type: AddressMap<Py<Entry>>,
    /// Whether explicit converters can override the encoder's native fast paths.
    pub natives_overridden: bool,
}

impl Registry {
    fn clone_ref(&self, py: Python<'_>) -> Self {
        Self {
            by_name: self
                .by_name
                .iter()
                .map(|(name, entry)| (name.clone(), entry.clone_ref(py)))
                .collect(),
            by_type: self
                .by_type
                .iter()
                .map(|(&ty, entry)| (ty, entry.clone_ref(py)))
                .collect(),
            natives_overridden: self.natives_overridden,
        }
    }
}

#[pymethods]
impl Registry {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        // Each map owns a Python reference, even when both name the same entry.
        for entry in self.by_name.values().chain(self.by_type.values()) {
            visit.call(entry)?;
        }
        Ok(())
    }
}

/// The Rust base of `hessian2.Codec`, which adds registration.
#[pyclass(name = "_Codec", module = "hessian2._core", subclass, frozen)]
pub struct Codec {
    camel: bool,
    unknown: Unknown,
    max_depth: usize,
    registry: RwLock<Option<Py<Registry>>>,
}

impl Codec {
    fn snapshot(&self, py: Python<'_>) -> PyResult<Py<Registry>> {
        self.registry
            .read()
            .unwrap_or_else(|poison| poison.into_inner())
            .as_ref()
            .map(|registry| registry.clone_ref(py))
            .ok_or_else(|| PyRuntimeError::new_err("codec has been cleared by garbage collection"))
    }

    /// Replaces a registration atomically against the snapshot it was built from.
    fn insert(&self, py: Python<'_>, entry: Entry, replace: bool) -> PyResult<()> {
        let type_key = entry.ty.as_ptr() as usize;
        let entry = Py::new(py, entry)?;
        loop {
            let previous = self.snapshot(py)?;
            let registry = previous.get();
            let new = entry.get();
            if !replace {
                if let Some(existing) = registry.by_name.get(&new.name)
                    && !existing.get().ty.is(&new.ty)
                {
                    return Err(PyValueError::new_err(format!(
                        "{} is already registered to {}; pass replace=True to replace it",
                        PyString::new(py, &new.name).repr()?,
                        existing.get().describe(py)?,
                    )));
                }
                if let Some(existing) = registry.by_type.get(&type_key)
                    && existing.get().name != new.name
                {
                    return Err(PyValueError::new_err(format!(
                        "{} is already registered as {}; pass replace=True to move it",
                        new.ty.bind(py).qualname()?,
                        PyString::new(py, &existing.get().name).repr()?,
                    )));
                }
            }
            let mut next = registry.clone_ref(py);
            if let Some(replaced) = next.by_name.remove(&new.name) {
                let old_type = replaced.get().ty.as_ptr() as usize;
                // An old decode alias must not remove the type's newer encoding.
                if next
                    .by_type
                    .get(&old_type)
                    .is_some_and(|current| current.is(&replaced))
                {
                    next.by_type.remove(&old_type);
                }
            }
            next.by_type.insert(type_key, entry.clone_ref(py));
            next.by_name.insert(new.name.clone(), entry.clone_ref(py));
            next.natives_overridden = [
                py.get_type::<PyNone>(),
                py.get_type::<PyString>(),
                py.get_type::<PyBool>(),
                py.get_type::<PyInt>(),
                py.get_type::<PyFloat>(),
                py.get_type::<PyList>(),
                py.get_type::<PyDict>(),
            ]
            .iter()
            .any(|ty| {
                next.by_type
                    .get(&(ty.as_ptr() as usize))
                    .is_some_and(|entry| matches!(entry.get().kind, Kind::Converter { .. }))
            });
            // Allocation may run GC; build the GC-visible snapshot without the lock.
            let next = Py::new(py, next)?;
            let mut guard = self
                .registry
                .write()
                .unwrap_or_else(|poison| poison.into_inner());
            if guard.as_ref().is_some_and(|current| current.is(&previous)) {
                let retired = guard.replace(next);
                // Finalizers can reenter the codec, so release references unlocked.
                drop(guard);
                drop(retired);
                return Ok(());
            }
            drop(guard);
        }
    }
}

#[pymethods]
impl Codec {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        // Never block GC on a registration from another thread.
        if let Ok(registry) = self.registry.try_read() {
            visit.call(&*registry)?;
        }
        Ok(())
    }

    fn __clear__(&self) {
        let retired = self
            .registry
            .write()
            .unwrap_or_else(|poison| poison.into_inner())
            .take();
        drop(retired);
    }

    #[new]
    #[pyo3(signature = (*, field_naming = None, unknown_class = "raise", max_depth = 256))]
    fn new(
        py: Python<'_>,
        field_naming: Option<&str>,
        unknown_class: &str,
        max_depth: usize,
    ) -> PyResult<Self> {
        let camel = match field_naming {
            None => false,
            Some("camelCase") => true,
            Some(other) => {
                return Err(PyValueError::new_err(format!(
                    "field_naming must be None or 'camelCase', not {other:?}"
                )));
            }
        };
        let unknown = match unknown_class {
            "raise" => Unknown::Raise,
            "hessian_object" => Unknown::Object,
            "dict" => Unknown::Dict,
            other => {
                return Err(PyValueError::new_err(format!(
                    "unknown_class must be 'raise', 'hessian_object', or 'dict', not {other:?}"
                )));
            }
        };
        if max_depth == 0 {
            return Err(PyValueError::new_err("max_depth must be at least 1"));
        }
        let mut registry = Registry::default();
        let object = py.get_type::<pyo3::PyAny>();
        for builtin in Builtin::ALL {
            let python_type = builtin.python_type(py)?;
            for (index, name) in builtin.names().iter().enumerate() {
                let entry = Py::new(
                    py,
                    Entry {
                        name: (*name).to_owned(),
                        // Never used as a key for a type that only decodes.
                        ty: python_type.as_ref().unwrap_or(&object).clone().unbind(),
                        kind: Kind::Value(builtin),
                    },
                )?;
                if index == 0
                    && let Some(ty) = &python_type
                {
                    registry
                        .by_type
                        .insert(ty.as_ptr() as usize, entry.clone_ref(py));
                }
                registry.by_name.insert(entry.get().name.clone(), entry);
            }
        }
        let codec = Self {
            camel,
            unknown,
            max_depth,
            registry: RwLock::new(Some(Py::new(py, registry)?)),
        };
        Ok(codec)
    }

    /// How field names are sent: ``None`` or ``"camelCase"``.
    #[getter]
    fn field_naming(&self) -> Option<&'static str> {
        self.camel.then_some("camelCase")
    }

    /// How deeply values may nest.
    #[getter]
    fn max_depth(&self) -> usize {
        self.max_depth
    }

    /// What decoding does with an object of an unregistered class.
    #[getter]
    fn unknown_class(&self) -> &'static str {
        match self.unknown {
            Unknown::Raise => "raise",
            Unknown::Object => "hessian_object",
            Unknown::Dict => "dict",
        }
    }

    /// Encode one value as a Hessian stream.
    ///
    /// Args:
    ///     obj: The value to encode.
    ///
    /// Returns:
    ///     The stream.
    ///
    /// Raises:
    ///     EncodeError: If a value has no Hessian form under this codec.
    ///     RecursionError: If values nest deeper than ``max_depth``.
    fn dumps(&self, py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<Py<PyBytes>> {
        let registry = self.snapshot(py)?;
        let mut encoder = Encoder::new(py, registry.get(), self.max_depth);
        encoder.value(obj)?;
        Ok(PyBytes::new(py, encoder.finish()).unbind())
    }

    /// Encode several values as one stream.
    ///
    /// The values share class definitions and references, as values written
    /// to one Java ``Hessian2Output`` do.
    ///
    /// Args:
    ///     values: An iterable of the values to encode.
    ///
    /// Returns:
    ///     The stream.
    ///
    /// Raises:
    ///     EncodeError: If a value has no Hessian form under this codec.
    ///     RecursionError: If values nest deeper than ``max_depth``.
    fn dumps_many(&self, py: Python<'_>, values: &Bound<'_, PyAny>) -> PyResult<Py<PyBytes>> {
        let registry = self.snapshot(py)?;
        let mut encoder = Encoder::new(py, registry.get(), self.max_depth);
        for value in values.try_iter()? {
            encoder.value(&value?)?;
        }
        Ok(PyBytes::new(py, encoder.finish()).unbind())
    }

    /// Decode a stream that holds exactly one value.
    ///
    /// Args:
    ///     data: The stream, as ``bytes`` or another buffer.
    ///
    /// Returns:
    ///     The value.
    ///
    /// Raises:
    ///     DecodeError: If ``data`` is not one valid value under this codec,
    ///         or holds more than one.
    fn loads(&self, py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let registry = self.snapshot(py)?;
        with_buffer(data, |bytes| {
            let mut decoder = Decoder::new(py, bytes, registry.get(), self.unknown, self.max_depth);
            let value = decoder.value()?;
            decoder.finish()?;
            Ok(value.unbind())
        })
    }

    /// Decode every value in a stream.
    ///
    /// Args:
    ///     data: The stream, as ``bytes`` or another buffer.
    ///
    /// Returns:
    ///     The values, in order.
    ///
    /// Raises:
    ///     DecodeError: If ``data`` is not a sequence of valid values under
    ///         this codec.
    fn loads_many(&self, py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<Py<PyList>> {
        let registry = self.snapshot(py)?;
        with_buffer(data, |bytes| {
            let mut decoder = Decoder::new(py, bytes, registry.get(), self.unknown, self.max_depth);
            let values = PyList::empty(py);
            while !decoder.is_empty() {
                values.append(decoder.value()?)?;
            }
            Ok(values.unbind())
        })
    }

    fn _register_class(
        &self,
        py: Python<'_>,
        name: String,
        cls: Bound<'_, PyType>,
        fields: Vec<FieldSpec<'_>>,
        post_init: Option<(Bound<'_, PyString>, Bound<'_, PyTuple>)>,
        replace: bool,
    ) -> PyResult<()> {
        let fields = to_fields(py, fields)?;
        let setattr = intern!(py, "__setattr__");
        let bypass_setattr = !cls
            .getattr(setattr)?
            .is(py.get_type::<pyo3::PyAny>().getattr(setattr)?);
        self.insert(
            py,
            Entry {
                name,
                ty: cls.unbind(),
                kind: Kind::Class {
                    fields,
                    bypass_setattr,
                    post_init: post_init.map(|(name, args)| (name.unbind(), args.unbind())),
                },
            },
            replace,
        )
    }

    fn _register_tuple(
        &self,
        py: Python<'_>,
        name: String,
        cls: Bound<'_, PyType>,
        fields: Vec<FieldSpec<'_>>,
        replace: bool,
    ) -> PyResult<()> {
        let fields = to_fields(py, fields)?;
        self.insert(
            py,
            Entry {
                name,
                ty: cls.unbind(),
                kind: Kind::Tuple { fields },
            },
            replace,
        )
    }

    fn _register_enum(
        &self,
        py: Python<'_>,
        name: String,
        cls: Bound<'_, PyType>,
        replace: bool,
    ) -> PyResult<()> {
        self.insert(
            py,
            Entry {
                name,
                ty: cls.unbind(),
                kind: Kind::Enum,
            },
            replace,
        )
    }

    fn _register_converter(
        &self,
        py: Python<'_>,
        name: String,
        ty: Bound<'_, PyType>,
        encode: Py<PyAny>,
        decode: Py<PyAny>,
        replace: bool,
    ) -> PyResult<()> {
        self.insert(
            py,
            Entry {
                name,
                ty: ty.unbind(),
                kind: Kind::Converter { encode, decode },
            },
            replace,
        )
    }
}

/// A field as the Python package describes it: attribute name, wire name,
/// default kind (0 required, 1 value, 2 factory), and default.
type FieldSpec<'py> = (Bound<'py, PyString>, String, u8, Py<PyAny>);

fn to_fields(py: Python<'_>, specs: Vec<FieldSpec<'_>>) -> PyResult<Vec<Field>> {
    let mut names = HashSet::with_capacity(specs.len());
    let mut fields = Vec::with_capacity(specs.len());
    for (attr, wire, kind, default) in specs {
        if !names.insert(wire.clone()) {
            return Err(PyValueError::new_err(format!(
                "multiple Python fields map to {}; choose distinct field names",
                PyString::new(py, &wire).repr()?,
            )));
        }
        fields.push(Field {
            // Interned: attribute lookups compare interned names by pointer.
            attr: PyString::intern(py, attr.to_str()?).unbind(),
            wire,
            default: match kind {
                0 => Default::Required,
                1 => Default::Value(default),
                _ => Default::Factory(default),
            },
        });
    }
    Ok(fields)
}

/// Runs `consume` over the bytes of a buffer-protocol object.
///
/// Immutable `bytes` are borrowed directly. Other buffers are copied first
/// because another thread may mutate them while they are read.
fn with_buffer<R>(
    value: &Bound<'_, PyAny>,
    consume: impl FnOnce(&[u8]) -> PyResult<R>,
) -> PyResult<R> {
    if let Ok(bytes) = value.cast::<PyBytes>() {
        return consume(bytes.as_bytes());
    }
    let view = pyo3::types::PyMemoryView::from(value)?;
    let bytes = view.call_method0("tobytes")?.cast_into::<PyBytes>()?;
    consume(bytes.as_bytes())
}
