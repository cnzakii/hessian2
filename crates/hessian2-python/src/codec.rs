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
use crate::hint::{self, Hint};

/// What decoding does with an object whose class has no registration.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Unknown {
    Raise,
    Object,
    Dict,
}

impl Unknown {
    fn parse(value: &str) -> PyResult<Self> {
        match value {
            "raise" => Ok(Self::Raise),
            "hessian_object" => Ok(Self::Object),
            "dict" => Ok(Self::Dict),
            other => Err(PyValueError::new_err(format!(
                "unknown_class must be 'raise', 'hessian_object', or 'dict', not {other:?}"
            ))),
        }
    }
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
    /// How the field's values are written, from its annotation.
    pub hint: Option<Hint>,
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

    /// Registers every entry, each replacing what it conflicts with only
    /// when its flag says so, or none of them: the registry is swapped
    /// atomically against the snapshot the batch was checked on.
    ///
    /// Entries come from the `_*_entry` builders. An entry no codec
    /// registers is how a class declared with `hessian2.java_class` encodes:
    /// the Python package caches it on the declaration, and its name does
    /// not decode.
    fn publish(&self, py: Python<'_>, entries: &[(Py<Entry>, bool)]) -> PyResult<()> {
        loop {
            let previous = self.snapshot(py)?;
            let mut next = previous.get().clone_ref(py);
            for (entry, replace) in entries {
                let new = entry.get();
                let type_key = new.ty.as_ptr() as usize;
                // Checked against `next`, so entries in one batch cannot conflict either.
                if !replace {
                    if let Some(existing) = next.by_name.get(&new.name)
                        && !existing.get().ty.is(&new.ty)
                    {
                        return Err(PyValueError::new_err(format!(
                            "{} is already registered to {}; pass replace=True to replace it",
                            PyString::new(py, &new.name).repr()?,
                            existing.get().describe(py)?,
                        )));
                    }
                    if let Some(existing) = next.by_type.get(&type_key)
                        && existing.get().name != new.name
                    {
                        return Err(PyValueError::new_err(format!(
                            "{} is already registered as {}; pass replace=True to move it",
                            new.ty.bind(py).qualname()?,
                            PyString::new(py, &existing.get().name).repr()?,
                        )));
                    }
                }
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
            }
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
    #[pyo3(signature = (*, unknown_class = "raise", max_depth = 256))]
    fn new(py: Python<'_>, unknown_class: &str, max_depth: usize) -> PyResult<Self> {
        let unknown = Unknown::parse(unknown_class)?;
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
            unknown,
            max_depth,
            registry: RwLock::new(Some(Py::new(py, registry)?)),
        };
        Ok(codec)
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
    ///     type: An annotation for ``obj``, such as ``list[hessian2.Long]``,
    ///         whose tags choose the Java types its values are written as.
    ///
    /// Returns:
    ///     The stream.
    ///
    /// Raises:
    ///     EncodeError: If a value has no Hessian form under this codec or
    ///         values nest deeper than ``max_depth``; also wrapping, as its
    ///         ``__cause__``, a ``ValueError`` or ``TypeError`` raised by a
    ///         converter or a field's getter.
    #[pyo3(signature = (obj, *, r#type = None))]
    fn dumps(
        slf: &Bound<'_, Self>,
        obj: &Bound<'_, PyAny>,
        r#type: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Py<PyBytes>> {
        let py = slf.py();
        let this = slf.get();
        let hint = match r#type {
            Some(annotation) => hint::lookup(slf.as_any(), annotation)?,
            None => None,
        };
        let registry = this.snapshot(py)?;
        let mut encoder = Encoder::new(py, registry.get(), this.max_depth, slf.as_any());
        encoder.hinted(obj, hint.as_ref().and_then(|h| h.get().0.as_ref()))?;
        Ok(PyBytes::new(py, encoder.finish()).unbind())
    }

    /// Encode several values as one stream.
    ///
    /// The values share class definitions and references, as values written
    /// to one Java ``Hessian2Output`` do.
    ///
    /// Args:
    ///     values: An iterable of the values to encode.
    ///     types: An annotation for each value, or ``None`` for a value
    ///         without one, as for ``dumps``.
    ///
    /// Returns:
    ///     The stream.
    ///
    /// Raises:
    ///     EncodeError: As for ``dumps``.
    ///     ValueError: If ``types`` and ``values`` differ in length.
    #[pyo3(signature = (values, *, types = None))]
    fn dumps_many(
        slf: &Bound<'_, Self>,
        values: &Bound<'_, PyAny>,
        types: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Py<PyBytes>> {
        let py = slf.py();
        let this = slf.get();
        let hints = match types {
            Some(types) => Some(
                types
                    .try_iter()?
                    .map(|annotation| hint::lookup(slf.as_any(), &annotation?))
                    .collect::<PyResult<Vec<_>>>()?,
            ),
            None => None,
        };
        let registry = this.snapshot(py)?;
        let mut encoder = Encoder::new(py, registry.get(), this.max_depth, slf.as_any());
        let mut count = 0;
        for value in values.try_iter()? {
            let hint = match &hints {
                Some(hints) => match hints.get(count) {
                    Some(hint) => hint.as_ref(),
                    None => return Err(length_mismatch(hints.len())),
                },
                None => None,
            };
            encoder.hinted(&value?, hint.and_then(|h| h.get().0.as_ref()))?;
            count += 1;
        }
        if let Some(hints) = hints
            && hints.len() != count
        {
            return Err(length_mismatch(hints.len()));
        }
        Ok(PyBytes::new(py, encoder.finish()).unbind())
    }

    /// Decode a stream that holds exactly one value.
    ///
    /// Args:
    ///     data: The stream, as ``bytes`` or another buffer.
    ///     unknown_class: Overrides the codec's ``unknown_class`` for this
    ///         call.
    ///
    /// Returns:
    ///     The value.
    ///
    /// Raises:
    ///     DecodeError: If ``data`` is not one valid value under this codec
    ///         or holds more than one; also wrapping, as its ``__cause__``, a
    ///         ``ValueError`` or ``TypeError`` raised by a converter,
    ///         post-init method, or default factory.
    #[pyo3(signature = (data, *, unknown_class = None))]
    fn loads(
        &self,
        py: Python<'_>,
        data: &Bound<'_, PyAny>,
        unknown_class: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let unknown = self.unknown_for(unknown_class)?;
        let registry = self.snapshot(py)?;
        with_buffer(data, |bytes| {
            let mut decoder = Decoder::new(py, bytes, registry.get(), unknown, self.max_depth);
            let value = decoder.value()?;
            decoder.finish()?;
            Ok(value.unbind())
        })
    }

    /// Decode every value in a stream.
    ///
    /// Args:
    ///     data: The stream, as ``bytes`` or another buffer.
    ///     unknown_class: Overrides the codec's ``unknown_class`` for this
    ///         call.
    ///
    /// Returns:
    ///     The values, in order.
    ///
    /// Raises:
    ///     DecodeError: As for ``loads``.
    #[pyo3(signature = (data, *, unknown_class = None))]
    fn loads_many(
        &self,
        py: Python<'_>,
        data: &Bound<'_, PyAny>,
        unknown_class: Option<&str>,
    ) -> PyResult<Py<PyList>> {
        let unknown = self.unknown_for(unknown_class)?;
        let registry = self.snapshot(py)?;
        with_buffer(data, |bytes| {
            let mut decoder = Decoder::new(py, bytes, registry.get(), unknown, self.max_depth);
            let values = PyList::empty(py);
            while !decoder.is_empty() {
                values.append(decoder.value()?)?;
            }
            Ok(values.unbind())
        })
    }

    /// Returns the name a type is registered under for decoding, or `None`
    /// for a built-in mapping, a type whose name another registration has
    /// since taken, or no entry.
    fn _registered_name(&self, py: Python<'_>, ty: &Bound<'_, PyType>) -> PyResult<Option<String>> {
        let registry = self.snapshot(py)?;
        let registry = registry.get();
        Ok(registry
            .by_type
            .get(&(ty.as_ptr() as usize))
            .filter(|entry| {
                !matches!(entry.get().kind, Kind::Value(_))
                    && registry
                        .by_name
                        .get(&entry.get().name)
                        .is_some_and(|named| named.is(*entry))
            })
            .map(|entry| entry.get().name.clone()))
    }

    /// Returns the entry for a dataclass or attrs class.
    #[staticmethod]
    fn _class_entry(
        py: Python<'_>,
        name: String,
        cls: Bound<'_, PyType>,
        fields: Vec<FieldSpec<'_>>,
        post_init: Option<(Bound<'_, PyString>, Bound<'_, PyTuple>)>,
    ) -> PyResult<Py<Entry>> {
        let fields = to_fields(py, fields)?;
        let setattr = intern!(py, "__setattr__");
        let bypass_setattr = !cls
            .getattr(setattr)?
            .is(py.get_type::<pyo3::PyAny>().getattr(setattr)?);
        let post_init = post_init.map(|(name, args)| (name.unbind(), args.unbind()));
        Py::new(
            py,
            Entry {
                name,
                ty: cls.unbind(),
                kind: Kind::Class {
                    fields,
                    bypass_setattr,
                    post_init,
                },
            },
        )
    }

    /// Returns the entry for a named tuple.
    #[staticmethod]
    fn _tuple_entry(
        py: Python<'_>,
        name: String,
        cls: Bound<'_, PyType>,
        fields: Vec<FieldSpec<'_>>,
    ) -> PyResult<Py<Entry>> {
        let fields = to_fields(py, fields)?;
        let kind = Kind::Tuple { fields };
        Py::new(
            py,
            Entry {
                name,
                ty: cls.unbind(),
                kind,
            },
        )
    }

    /// Returns the entry for an Enum, sent by member name.
    #[staticmethod]
    fn _enum_entry(py: Python<'_>, name: String, cls: Bound<'_, PyType>) -> PyResult<Py<Entry>> {
        let kind = Kind::Enum;
        Py::new(
            py,
            Entry {
                name,
                ty: cls.unbind(),
                kind,
            },
        )
    }

    /// Returns the entry for a `hessian2.Converter`.
    #[staticmethod]
    fn _converter_entry(
        py: Python<'_>,
        name: String,
        ty: Bound<'_, PyType>,
        encode: Py<PyAny>,
        decode: Py<PyAny>,
    ) -> PyResult<Py<Entry>> {
        let kind = Kind::Converter { encode, decode };
        Py::new(
            py,
            Entry {
                name,
                ty: ty.unbind(),
                kind,
            },
        )
    }

    /// Registers entries with their `replace` flags, all or none.
    fn _publish(&self, py: Python<'_>, entries: Vec<(Py<Entry>, bool)>) -> PyResult<()> {
        self.publish(py, &entries)
    }
}

impl Codec {
    fn unknown_for(&self, unknown_class: Option<&str>) -> PyResult<Unknown> {
        unknown_class.map_or(Ok(self.unknown), Unknown::parse)
    }
}

fn length_mismatch(types: usize) -> PyErr {
    PyValueError::new_err(format!(
        "types has {types} annotations, but values has a different number of items"
    ))
}

/// A field as the Python package describes it: attribute name, wire name,
/// default kind (0 required, 1 value, 2 factory), default, and encoding hint
/// plan.
type FieldSpec<'py> = (
    Bound<'py, PyString>,
    String,
    u8,
    Py<PyAny>,
    Bound<'py, PyAny>,
);

fn to_fields(py: Python<'_>, specs: Vec<FieldSpec<'_>>) -> PyResult<Vec<Field>> {
    let mut names = HashSet::with_capacity(specs.len());
    let mut fields = Vec::with_capacity(specs.len());
    for (attr, wire, kind, default, plan) in specs {
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
            hint: Hint::parse(&plan)?,
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
