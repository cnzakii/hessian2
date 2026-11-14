use std::borrow::Cow;
use std::collections::HashMap;
use std::rc::Rc;

use hessian2_core::{Reader, ReaderState, Token, text};
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{
    PyBool, PyBytes, PyDateTime, PyDict, PyFloat, PyFrozenSet, PyList, PySet, PyString, PyTuple,
    PyType,
};

use crate::address::{AddressMap, AddressSet};
use crate::builtins::{self, Builtin};
use crate::codec::{Default, Entry, Field, Kind, Registry, Unknown, declared_entry};
use crate::object::HessianObject;
use crate::typed::{self, Fields, Items, Type, TypeObject};
use crate::{DecodeError, decode_user_error, time};

mod annotated;

/// How objects of one class definition are built, decided once per stream.
enum Plan {
    Class {
        entry: Py<Entry>,
        /// The registered field each wire field fills, or `None` to ignore it.
        slots: Vec<Option<usize>>,
        /// The registered fields no wire field fills, which take defaults.
        missing: Vec<usize>,
    },
    Enum {
        entry: Py<Entry>,
        name_slot: Option<usize>,
    },
    /// A JDK value; each slot is the wire field holding the corresponding
    /// field of [`Builtin::read_fields`].
    Value {
        builtin: Builtin,
        class: String,
        slots: [Option<usize>; builtins::MAX_FIELDS],
    },
    Converter {
        entry: Py<Entry>,
        keys: Vec<Py<PyString>>,
    },
    Unknown {
        name: Py<PyString>,
        keys: Vec<Py<PyString>>,
        /// Always a `HessianObject`, whatever `unknown_class` says.
        raw: bool,
    },
}

/// What an object's class definition is decoded as.
#[derive(Clone, Copy)]
enum As<'e> {
    /// The class registered under its name, if any.
    Registry,
    /// The class an annotation names.
    Entry(&'e Py<Entry>),
    /// A `HessianObject`.
    Raw,
    /// A JDK value registered under its name, or else a `HessianObject`:
    /// plain data that runs no class's code.
    Plain,
}

impl As<'_> {
    /// Returns a number that tells apart what plans are made for.
    fn key(self) -> usize {
        match self {
            Self::Registry => 0,
            Self::Raw => 1,
            Self::Plain => 2,
            Self::Entry(entry) => entry.as_ptr() as usize,
        }
    }
}

/// What a [`Decoder`] keeps between calls over one stream: the reader's
/// position and tables, the values numbered for back-references, and the
/// back-reference checks already made, with the types they name kept alive
/// so their addresses stay theirs.
#[derive(Default)]
pub struct Saved {
    reader: ReaderState,
    refs: Vec<Option<Py<PyAny>>>,
    checked: AddressSet,
    types: AddressMap<Py<TypeObject>>,
    expected: AddressMap<Option<Py<Entry>>>,
    /// The types earlier calls decoded their values as, by address.
    roots: AddressMap<Py<TypeObject>>,
}

impl Saved {
    /// Returns the offset of the next undecoded byte.
    pub fn offset(&self) -> usize {
        self.reader.offset()
    }

    /// Visits the Python objects kept, for an owner's `__traverse__`.
    pub fn traverse(&self, visit: &pyo3::gc::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        self.refs
            .iter()
            .flatten()
            .try_for_each(|value| visit.call(value))?;
        self.types
            .values()
            .chain(self.roots.values())
            .try_for_each(|ty| visit.call(ty))?;
        self.expected
            .values()
            .flatten()
            .try_for_each(|entry| visit.call(entry))
    }
}

/// Builds Python values from one Hessian stream, keeping the stream's
/// reference numbering and class plans across the values in it.
pub struct Decoder<'a, 'py> {
    py: Python<'py>,
    reader: Reader<'a>,
    registry: &'a Registry,
    unknown: Unknown,
    max_depth: usize,
    /// Values numbered for back-references; `None` while one is being built
    /// from its fields.
    refs: Vec<Option<Bound<'py, PyAny>>>,
    /// The plan for each class definition, with the [`As::key`] it was
    /// made for.
    plans: Vec<Option<(usize, Rc<Plan>)>>,
    /// The codec, whose `_declared` method makes entries for classes
    /// declared with `hessian2.java_class` but not registered.
    codec: &'a Bound<'py, PyAny>,
    /// The entry each annotated class decodes with, by type address.
    expected: AddressMap<Option<Py<Entry>>>,
    /// Nonzero while reading values kept only as plain data: fields a class
    /// ignores, and the fields of JDK values and enums. Objects there decode
    /// as `HessianObject`, so peer input builds no class it then discards.
    plain: usize,
    /// Where an error happened, innermost segment first, such as `.name`.
    path: Vec<String>,
    /// The (value, type) pairs back-references were found to fit. Values
    /// that application code, such as a post-init method, changes are not
    /// checked again: the peer's bytes are the boundary, not the
    /// application's own code.
    checked: AddressSet,
    /// The types of the annotations [`Type::Ref`]s name.
    types: typed::Refs<'py>,
    /// The types values of the stream are decoded as, by address, for
    /// [`Decoder::keep`].
    roots: AddressMap<Py<TypeObject>>,
    /// The index of each entry's fields by wire name, by entry address, for
    /// maps decoded as classes. Entries outlive the decoder.
    wire: AddressMap<Rc<HashMap<String, usize>>>,
    depth: usize,
}

/// Returns the value for a field an object lacks: the field's default, or
/// `DecodeError` when it has none.
fn default<'py>(py: Python<'py>, entry: &Entry, field: &Field) -> PyResult<Bound<'py, PyAny>> {
    match &field.default {
        Default::Required => Err(DecodeError::new_err(format!(
            "{:?} is missing field {:?}",
            entry.name, field.wire
        ))),
        Default::Value(value) => Ok(value.bind(py).clone()),
        Default::Factory(factory) => factory
            .bind(py)
            .call0()
            .map_err(|error| decode_user_error(py, error)),
    }
}

fn decode_error(error: hessian2_core::Error) -> PyErr {
    DecodeError::new_err(error.to_string())
}

/// Returns the error for a value that does not match its annotation.
#[cold]
#[inline(never)]
fn mismatch(py: Python<'_>, ty: &Type, got: &str) -> PyErr {
    DecodeError::new_err(format!("expected {}, got {got}", ty.describe(py)))
}

/// Returns the error for an instance that does not hold the field types a
/// back-reference to it expected while it was being filled.
#[cold]
#[inline(never)]
fn unfilled_mismatch(ty: &Bound<'_, PyType>) -> PyErr {
    DecodeError::new_err(format!(
        "a back-reference to this {} expects other field types",
        ty.qualname().map_or_else(|_| "?".into(), |n| n.to_string())
    ))
}

/// Returns the member of an enum entry's class named `name`.
fn enum_member<'py>(
    py: Python<'py>,
    entry: &Entry,
    name: Option<Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>> {
    // Only an exact str, so no peer-built object's methods run in the
    // lookup or the error message.
    let name = name
        .filter(|name| name.is_exact_instance_of::<PyString>())
        .ok_or_else(|| DecodeError::new_err(format!("{:?} needs a string name", entry.name)))?;
    entry
        .ty
        .bind(py)
        .getattr(pyo3::intern!(py, "__members__"))?
        .get_item(&name)
        .map_err(|_| {
            DecodeError::new_err(format!(
                "{:?} has no member {}",
                entry.name,
                name.repr().map_or_else(|_| "?".into(), |r| r.to_string())
            ))
        })
}

/// Returns the type `generic` gives `field`, or its own.
#[inline(never)]
fn field_type<'t>(py: Python<'_>, field: &'t Field, generic: &'t Fields) -> PyResult<&'t Type> {
    Ok(generic
        .get(field.attr.bind(py).to_str()?)
        .unwrap_or(&field.ty))
}

/// Where the fields of an object being built come from.
#[derive(Clone, Copy)]
enum Fill<'p> {
    /// An object's values in its class definition's order: the field each
    /// fills, if any, and the fields the definition lacks.
    Object(&'p [Option<usize>], &'p [usize]),
    /// A map's entries, matched to fields by wire name.
    Map,
}

/// Returns the indexes of the fields not `present`.
fn absent(present: &[bool]) -> Vec<usize> {
    present
        .iter()
        .enumerate()
        .filter_map(|(index, &present)| (!present).then_some(index))
        .collect()
}

fn type_name(value: &Bound<'_, PyAny>) -> String {
    if value.is_none() {
        return "None".to_owned();
    }
    value
        .get_type()
        .qualname()
        .map_or_else(|_| "?".to_owned(), |name| name.to_string())
}

/// Returns the `str` of string bytes, which may hold surrogates as three-byte
/// sequences, as Java writes them.
fn string<'py>(py: Python<'py>, bytes: &[u8]) -> PyResult<Bound<'py, PyAny>> {
    let invalid = || DecodeError::new_err("invalid string data");
    // Without surrogates the bytes should be UTF-8, which CPython validates
    // once while building the string.
    if !text::contains_surrogate(bytes) {
        return PyString::from_bytes(py, bytes)
            .map(Bound::into_any)
            .map_err(|_| invalid());
    }
    if let Some(text) = text::cesu8_to_utf8(bytes) {
        return Ok(PyString::new(py, &text).into_any());
    }
    // Unpaired surrogates are valid in Java and Python strings but not in Rust.
    let units = text::cesu8_to_utf16(bytes).ok_or_else(invalid)?;
    let raw: Vec<u8> = units.iter().flat_map(|unit| unit.to_le_bytes()).collect();
    PyBytes::new(py, &raw).call_method1("decode", ("utf-16-le", "surrogatepass"))
}

/// Returns the value of a token that starts no container.
// Out of line so the recursive `Decoder::value` frame stays small.
#[inline(never)]
fn scalar<'py>(py: Python<'py>, token: Token<'_>) -> PyResult<Bound<'py, PyAny>> {
    Ok(match token {
        Token::Null => py.None().into_bound(py),
        Token::Bool(value) => PyBool::new(py, value).to_owned().into_any(),
        Token::Int(value) => value.into_pyobject(py)?.into_any(),
        Token::Long(value) => value.into_pyobject(py)?.into_any(),
        Token::Double(value) => PyFloat::new(py, value).into_any(),
        Token::Date(millis) => time::from_millis(py, millis)?,
        Token::String(bytes) => string(py, &bytes)?,
        Token::Binary(bytes) => PyBytes::new(py, &bytes).into_any(),
        Token::List { .. } | Token::Map { .. } | Token::Object { .. } | Token::Ref(_) => {
            unreachable!("containers and refs are handled by Decoder::value")
        }
    })
}

#[cold]
#[inline(never)]
fn key_error(py: Python<'_>, key: &Bound<'_, PyAny>, error: PyErr) -> PyErr {
    if error.is_instance_of::<PyTypeError>(py) {
        DecodeError::new_err(format!(
            "unhashable map key of type {}",
            key.get_type()
                .name()
                .map_or_else(|_| "?".into(), |n| n.to_string())
        ))
    } else {
        error
    }
}

/// Returns the path segment for the value under a map key: `["name"]` for
/// a string or number key, `[...]` for others, whose repr could run peer
/// built code.
#[cold]
fn key_segment(key: &Bound<'_, PyAny>) -> String {
    let shown = (key.is_exact_instance_of::<PyString>()
        || key.is_exact_instance_of::<pyo3::types::PyInt>())
    .then(|| key.repr().ok())
    .flatten();
    shown.map_or_else(|| "[...]".to_owned(), |repr| format!("[{repr}]"))
}

#[cold]
#[inline(never)]
fn set_error(py: Python<'_>, error: PyErr) -> PyErr {
    if error.is_instance_of::<PyTypeError>(py) {
        DecodeError::new_err(format!("unhashable set item: {}", error.value(py)))
    } else {
        error
    }
}

impl<'a, 'py> Decoder<'a, 'py> {
    /// Returns a decoder over a whole stream, `input`, that maps classes
    /// through `registry`.
    pub fn new(
        py: Python<'py>,
        input: &'a [u8],
        registry: &'a Registry,
        unknown: Unknown,
        max_depth: usize,
        codec: &'a Bound<'py, PyAny>,
    ) -> Self {
        Self {
            py,
            reader: Reader::new(input),
            registry,
            unknown,
            max_depth,
            refs: Vec::new(),
            plans: Vec::new(),
            codec,
            expected: AddressMap::default(),
            plain: 0,
            path: Vec::new(),
            checked: AddressSet::default(),
            types: typed::Refs::new(codec),
            roots: AddressMap::default(),
            wire: AddressMap::default(),
            depth: 0,
        }
    }

    /// Returns a decoder over `input` that continues from `saved`, as
    /// [`Decoder::new`] otherwise.
    pub fn resume(
        py: Python<'py>,
        input: &'a [u8],
        saved: Saved,
        registry: &'a Registry,
        unknown: Unknown,
        max_depth: usize,
        codec: &'a Bound<'py, PyAny>,
    ) -> Self {
        Self {
            reader: Reader::resume(input, saved.reader),
            refs: saved
                .refs
                .into_iter()
                .map(|value| value.map(|value| value.into_bound(py)))
                .collect(),
            checked: saved.checked,
            types: typed::Refs::resume(codec, saved.types),
            expected: saved.expected,
            roots: saved.roots,
            ..Self::new(py, input, registry, unknown, max_depth, codec)
        }
    }

    /// Keeps `ty`, which a value of the stream is decoded as, alive while
    /// the checks against it are kept.
    pub fn keep(&mut self, ty: &Bound<'py, TypeObject>) {
        self.roots.insert(ty.as_ptr() as usize, ty.clone().unbind());
    }

    /// Returns what a later [`Decoder::resume`] continues from.
    pub fn suspend(self) -> Saved {
        Saved {
            reader: self.reader.into_state(),
            refs: self
                .refs
                .into_iter()
                .map(|value| value.map(Bound::unbind))
                .collect(),
            checked: self.checked,
            types: self.types.suspend(),
            expected: self.expected,
            roots: self.roots,
        }
    }

    /// Adds where in the value an error happened, such as `- at $.items[0]`,
    /// to a `DecodeError` that one of the values in it raised.
    pub fn locate(&mut self, error: PyErr) -> PyErr {
        let py = self.py;
        if self.path.is_empty() || !error.is_instance_of::<DecodeError>(py) {
            return error;
        }
        let path: String = self.path.drain(..).rev().collect();
        let located = DecodeError::new_err(format!("{} - at `${path}`", error.value(py)));
        located.set_cause(py, error.cause(py));
        located
    }

    /// Records a segment of where an error happened, innermost first.
    #[cold]
    #[inline(never)]
    pub fn at(&mut self, error: PyErr, segment: impl FnOnce() -> String) -> PyErr {
        self.path.push(segment());
        error
    }

    /// Returns whether every input byte has been decoded.
    pub fn is_empty(&self) -> bool {
        self.reader.is_empty()
    }

    /// Raises `DecodeError` when input remains after the decoded values.
    pub fn finish(&self) -> PyResult<()> {
        if self.reader.is_empty() {
            Ok(())
        } else {
            Err(DecodeError::new_err(format!(
                "trailing data at byte {}",
                self.reader.offset()
            )))
        }
    }

    /// Decodes the next value.
    ///
    /// # Errors
    ///
    /// Raises `DecodeError` for malformed input or a value this codec cannot
    /// map, and propagates errors raised by registered classes' code.
    pub fn value(&mut self) -> PyResult<Bound<'py, PyAny>> {
        match self.reader.next_token().map_err(decode_error)? {
            Token::List { len, .. } => self.list(len),
            Token::Map { .. } => self.map(),
            Token::Object { class } => {
                let mapped = if self.plain > 0 {
                    As::Plain
                } else {
                    As::Registry
                };
                self.object(class, mapped, None)
            }
            Token::Ref(index) => self.reference(index),
            token => scalar(self.py, token),
        }
    }

    /// Decodes the next value as plain data, building no class.
    fn plain_value(&mut self) -> PyResult<Bound<'py, PyAny>> {
        self.plain += 1;
        let value = self.value();
        self.plain -= 1;
        value
    }

    /// Returns the value back-reference `index` points to.
    fn reference(&self, index: usize) -> PyResult<Bound<'py, PyAny>> {
        self.refs[index]
            .clone()
            .ok_or_else(|| DecodeError::new_err("reference to a value that is still being decoded"))
    }

    /// Reads a map's entries up to the next whose key is the wire name of
    /// one of `fields`, reading the others as plain data, and returns that
    /// field's index, or `None` at the end of the map.
    fn map_field(&mut self, fields: &[Field], entry: &Entry) -> PyResult<Option<usize>> {
        let index = self.wire_index(fields, entry);
        while !self.reader.end().map_err(decode_error)? {
            let key = self.plain_value()?;
            let found = key
                .cast::<PyString>()
                .ok()
                .and_then(|key| index.get(key.to_str().ok()?).copied());
            match found {
                Some(found) => return Ok(Some(found)),
                None => drop(self.plain_value()?),
            }
        }
        Ok(None)
    }

    /// Returns the index of each of an entry's fields by wire name.
    fn wire_index(&mut self, fields: &[Field], entry: &Entry) -> Rc<HashMap<String, usize>> {
        let key = std::ptr::from_ref(entry) as usize;
        self.wire
            .entry(key)
            .or_insert_with(|| {
                // Reversed, so the first of fields sharing a name wins.
                Rc::new(
                    fields
                        .iter()
                        .enumerate()
                        .rev()
                        .map(|(index, field)| (field.wire.clone(), index))
                        .collect(),
                )
            })
            .clone()
    }

    /// Decodes a map key no annotation types, one whose hash a peer cannot
    /// make collide with many others: a dict of colliding keys takes time
    /// quadratic in their number. Strings, bytes, dates, and enum members
    /// hash randomly, and 64-bit ints at most eight to one value; other
    /// keys need an annotation that names their type, as msgpack-python's
    /// `strict_map_key` requires
    /// (https://github.com/msgpack/msgpack-python/blob/v1.1.0/README.md).
    fn map_key(&mut self) -> PyResult<Bound<'py, PyAny>> {
        let key = self.value()?;
        let py = self.py;
        let fits = if key.is_exact_instance_of::<pyo3::types::PyInt>() {
            key.extract::<i64>().is_ok()
        } else {
            key.is_none()
                || key.is_exact_instance_of::<PyString>()
                || key.is_exact_instance_of::<PyBytes>()
                || key.is_exact_instance_of::<PyBool>()
                || key.is_exact_instance_of::<PyFloat>()
                || key.is_instance_of::<pyo3::types::PyDate>()
                || key.is_instance_of::<pyo3::types::PyTime>()
                || key.is_instance(builtins::api(py)?.enum_type.bind(py))?
        };
        if fits {
            return Ok(key);
        }
        let name = key
            .get_type()
            .qualname()
            .map_or_else(|_| "?".into(), |n| n.to_string());
        // A list key decodes as a tuple where an annotation says so.
        let annotation = if name == "list" { "tuple[...]" } else { &name };
        Err(DecodeError::new_err(format!(
            "a map key of type {name} needs an annotation that names its type, \
             such as dict[{annotation}, ...]: a peer can make such keys collide"
        )))
    }

    /// Enters one container level, failing past `max_depth`.
    fn enter(&mut self) -> PyResult<()> {
        if self.depth >= self.max_depth {
            return Err(self.too_deep());
        }
        self.depth += 1;
        Ok(())
    }

    #[cold]
    #[inline(never)]
    fn too_deep(&self) -> PyErr {
        DecodeError::new_err(format!(
            "values nest deeper than max_depth={} at byte {}",
            self.max_depth,
            self.reader.offset()
        ))
    }

    #[inline(never)]
    fn list(&mut self, len: Option<usize>) -> PyResult<Bound<'py, PyAny>> {
        self.enter()?;
        let list = PyList::empty(self.py);
        self.refs.push(Some(list.clone().into_any()));
        let mut index = 0;
        while match len {
            Some(len) => index < len,
            None => !self.reader.end().map_err(decode_error)?,
        } {
            match self.value() {
                Ok(value) => list.append(value)?,
                Err(error) => return Err(self.at(error, || format!("[{index}]"))),
            }
            index += 1;
        }
        self.depth -= 1;
        Ok(list.into_any())
    }

    #[inline(never)]
    fn map(&mut self) -> PyResult<Bound<'py, PyAny>> {
        self.enter()?;
        let dict = PyDict::new(self.py);
        self.refs.push(Some(dict.clone().into_any()));
        while !self.reader.end().map_err(decode_error)? {
            let key = self.map_key()?;
            let value = match self.value() {
                Ok(value) => value,
                Err(error) => return Err(self.at(error, || key_segment(&key))),
            };
            dict.set_item(&key, value)
                .map_err(|error| key_error(self.py, &key, error))?;
        }
        self.depth -= 1;
        Ok(dict.into_any())
    }

    #[inline(never)]
    /// Returns how objects of a class definition are built as `mapped`,
    /// deciding on first use.
    fn plan(&mut self, class: usize, mapped: As<'_>) -> PyResult<Rc<Plan>> {
        if let Some(Some((key, plan))) = self.plans.get(class)
            && *key == mapped.key()
        {
            return Ok(plan.clone());
        }
        let py = self.py;
        let def = self.reader.class(class);
        // Not interned: peer-chosen names would stay alive for the process,
        // and forever on free-threaded builds, where interned strings are
        // immortal.
        let keys = || {
            def.fields
                .iter()
                .map(|f| PyString::new(py, f).unbind())
                .collect()
        };
        let position = |name: &str| def.fields.iter().position(|f| f == name);
        let entry = match mapped {
            As::Registry => self.registry.by_name.get(&def.name),
            As::Entry(entry) => Some(entry),
            As::Raw => None,
            As::Plain => self
                .registry
                .by_name
                .get(&def.name)
                .filter(|entry| matches!(entry.get().kind, Kind::Value(_))),
        };
        let plan = Rc::new(match entry {
            Some(entry) => match &entry.get().kind {
                Kind::Class { fields, .. } | Kind::Tuple { fields } => {
                    // The common case needs neither an index nor name lookups.
                    let slots: Vec<_> = if def
                        .fields
                        .iter()
                        .map(String::as_str)
                        .eq(fields.iter().map(|field| field.wire.as_str()))
                    {
                        (0..fields.len()).map(Some).collect()
                    } else {
                        // Sorting once avoids quadratic scans of wide classes;
                        // ties keep the first registered field, as linear lookup did.
                        let mut names: Vec<_> = fields
                            .iter()
                            .enumerate()
                            .map(|(index, field)| (field.wire.as_str(), index))
                            .collect();
                        names.sort_unstable();
                        def.fields
                            .iter()
                            .map(|wire| {
                                let at = names.partition_point(|&(name, _)| name < wire.as_str());
                                names
                                    .get(at)
                                    .filter(|&&(name, _)| name == wire.as_str())
                                    .map(|&(_, index)| index)
                            })
                            .collect()
                    };
                    let mut present = vec![false; fields.len()];
                    for &index in slots.iter().flatten() {
                        present[index] = true;
                    }
                    let missing = present
                        .into_iter()
                        .enumerate()
                        .filter_map(|(index, present)| (!present).then_some(index))
                        .collect();
                    Plan::Class {
                        entry: entry.clone_ref(py),
                        slots,
                        missing,
                    }
                }
                Kind::Enum => Plan::Enum {
                    entry: entry.clone_ref(py),
                    name_slot: position("name"),
                },
                Kind::Value(builtin) => {
                    let mut slots = [None; builtins::MAX_FIELDS];
                    for (slot, name) in slots.iter_mut().zip(builtin.read_fields()) {
                        *slot = position(name);
                    }
                    Plan::Value {
                        builtin: *builtin,
                        class: def.name.clone(),
                        slots,
                    }
                }
                Kind::Converter { .. } => Plan::Converter {
                    entry: entry.clone_ref(py),
                    keys: keys(),
                },
            },
            None => Plan::Unknown {
                name: PyString::new(py, &def.name).unbind(),
                keys: keys(),
                raw: matches!(mapped, As::Raw | As::Plain),
            },
        });
        if self.plans.len() <= class {
            self.plans.resize(class + 1, None);
        }
        // A class definition decodes one way in almost every stream, so one
        // plan per definition is kept.
        self.plans[class] = Some((mapped.key(), plan.clone()));
        Ok(plan)
    }

    /// Decodes an object of class definition `class` as `mapped`; `fields`
    /// types the fields of a generic class, in place of its own annotations.
    #[inline(never)]
    fn object(
        &mut self,
        class: usize,
        mapped: As<'_>,
        fields: Option<&Fields>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.enter()?;
        let plan = self.plan(class, mapped)?;
        let object = match &*plan {
            Plan::Class {
                entry,
                slots,
                missing,
            } => match entry.get().kind {
                Kind::Tuple { .. } => {
                    self.tuple_object(entry.get(), Fill::Object(slots, missing), fields)
                }
                _ => self.class_object(entry.get(), Fill::Object(slots, missing), fields),
            },
            Plan::Enum { entry, name_slot } => {
                let entry = entry.get();
                let fields = self.reader.class(class).fields.len();
                self.placeholder_object(fields, &[*name_slot], |py, [name]| {
                    enum_member(py, entry, name)
                })
            }
            Plan::Value {
                builtin,
                class: name,
                slots,
            } => {
                let fields = self.reader.class(class).fields.len();
                self.placeholder_object(fields, slots, |py, values| builtin.build(py, name, values))
            }
            Plan::Converter { entry, keys } => self.converter_object(entry.get(), Some(keys)),
            Plan::Unknown { name, keys, raw } => self.unknown_object(name, keys, *raw),
        }?;
        self.depth -= 1;
        Ok(object)
    }

    /// Builds an instance of a class without calling `__init__`, sets its
    /// fields as their annotations, or `generic`, type them, then runs its
    /// post-init method.
    #[inline(never)]
    fn class_object(
        &mut self,
        entry: &Entry,
        fill: Fill<'_>,
        generic: Option<&Fields>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let Kind::Class {
            fields,
            bypass_setattr,
            new,
            post_init,
        } = &entry.kind
        else {
            unreachable!()
        };
        let api = builtins::api(py)?;
        let instance = new.bind(py).call1((entry.ty.bind(py),))?;
        self.refs.push(Some(instance.clone()));
        self.types.fill(&instance);
        let set = |name: &Py<PyString>, value: Bound<'py, PyAny>| -> PyResult<()> {
            if *bypass_setattr {
                api.object_setattr
                    .bind(py)
                    .call1((&instance, name.bind(py), value))
                    .map(drop)
            } else {
                instance.setattr(name.bind(py), value)
            }
        };
        let missing = match fill {
            Fill::Object(slots, missing) => {
                for &target in slots {
                    match target {
                        Some(index) => {
                            let field = &fields[index];
                            let value = self.field(field, generic)?;
                            set(&field.attr, value)?;
                        }
                        None => drop(self.plain_value()?),
                    }
                }
                Cow::Borrowed(missing)
            }
            Fill::Map => {
                let mut present = vec![false; fields.len()];
                while let Some(index) = self.map_field(fields, entry)? {
                    let field = &fields[index];
                    let value = self.field(field, generic)?;
                    set(&field.attr, value)?;
                    present[index] = true;
                }
                Cow::Owned(absent(&present))
            }
        };
        for &index in missing.iter() {
            let field = &fields[index];
            set(&field.attr, default(py, entry, field)?)?;
        }
        if !self.types.filled(&instance, &mut self.checked) {
            return Err(unfilled_mismatch(entry.ty.bind(py)));
        }
        if let Some((name, args)) = post_init {
            instance
                .call_method1(name.bind(py), args.bind(py))
                .map_err(|error| decode_user_error(py, error))?;
        }
        Ok(instance)
    }

    /// Decodes the value of `field`, typed by `generic` where it gives one.
    #[inline(always)]
    fn field(&mut self, field: &Field, generic: Option<&Fields>) -> PyResult<Bound<'py, PyAny>> {
        let ty = match generic {
            Some(generic) => field_type(self.py, field, generic)?,
            None => &field.ty,
        };
        match self.typed_in(ty) {
            Ok(value) => Ok(value),
            Err(error) => Err(self.at(error, || format!(".{}", field.attr))),
        }
    }

    /// Builds a named tuple, which exists only once all its items are read.
    #[inline(never)]
    fn tuple_object(
        &mut self,
        entry: &Entry,
        fill: Fill<'_>,
        generic: Option<&Fields>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let Kind::Tuple { fields } = &entry.kind else {
            unreachable!()
        };
        let slot = self.refs.len();
        self.refs.push(None);
        let mut values: Vec<Option<Bound<'py, PyAny>>> = (0..fields.len()).map(|_| None).collect();
        match fill {
            Fill::Object(slots, _) => {
                for &target in slots {
                    match target {
                        Some(index) => values[index] = Some(self.field(&fields[index], generic)?),
                        None => drop(self.plain_value()?),
                    }
                }
            }
            Fill::Map => {
                while let Some(index) = self.map_field(fields, entry)? {
                    values[index] = Some(self.field(&fields[index], generic)?);
                }
            }
        }
        let items = values
            .into_iter()
            .zip(fields)
            .map(|(value, field)| value.map_or_else(|| default(py, entry, field), Ok))
            .collect::<PyResult<Vec<_>>>()?;
        // tuple.__new__ skips the class's own __new__, as class_object skips
        // __init__.
        let value = builtins::api(py)?
            .tuple_new
            .bind(py)
            .call1((entry.ty.bind(py), PyTuple::new(py, items)?))?;
        self.refs[slot] = Some(value.clone());
        Ok(value)
    }

    /// Builds a value that exists only once its fields are read: reserves its
    /// reference number, reads the wire fields named by `slots`, ignores the
    /// rest, and passes the read ones to `build`.
    #[inline(never)]
    fn placeholder_object<const N: usize>(
        &mut self,
        fields: usize,
        slots: &[Option<usize>; N],
        build: impl FnOnce(Python<'py>, [Option<Bound<'py, PyAny>>; N]) -> PyResult<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let slot = self.refs.len();
        self.refs.push(None);
        let mut values = [const { None }; N];
        for index in 0..fields {
            let value = self.plain_value()?;
            if let Some(at) = slots.iter().position(|&s| s == Some(index)) {
                values[at] = Some(value);
            }
        }
        let value = build(self.py, values)?;
        self.refs[slot] = Some(value.clone());
        Ok(value)
    }

    /// Builds a value with a `Converter`, which receives the fields as a dict.
    #[inline(never)]
    fn converter_object(
        &mut self,
        entry: &Entry,
        keys: Option<&[Py<PyString>]>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let Kind::Converter { decode, .. } = &entry.kind else {
            unreachable!()
        };
        let slot = self.refs.len();
        self.refs.push(None);
        let fields = PyDict::new(py);
        match keys {
            Some(keys) => {
                for key in keys {
                    fields.set_item(key.bind(py), self.value()?)?;
                }
            }
            // A map's entries, keys as plain data, as a TypedDict's are.
            None => {
                while !self.reader.end().map_err(decode_error)? {
                    self.plain += 1;
                    let key = self.map_key();
                    self.plain -= 1;
                    let key = key?;
                    let value = self.value()?;
                    fields
                        .set_item(&key, value)
                        .map_err(|error| key_error(py, &key, error))?;
                }
            }
        }
        let value = decode
            .bind(py)
            .call1((fields,))
            .map_err(|error| decode_user_error(py, error))?;
        self.refs[slot] = Some(value.clone());
        Ok(value)
    }

    /// Builds an object of an unregistered class as `unknown_class` says, or
    /// as a `HessianObject` when `raw`.
    #[inline(never)]
    fn unknown_object(
        &mut self,
        name: &Py<PyString>,
        keys: &[Py<PyString>],
        raw: bool,
    ) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let unknown = if raw { Unknown::Object } else { self.unknown };
        let fields = PyDict::new(py);
        let value = match unknown {
            Unknown::Raise => {
                return Err(DecodeError::new_err(format!(
                    "class {} is not registered",
                    name.bind(py).repr()?
                )));
            }
            Unknown::Object => Bound::new(
                py,
                HessianObject {
                    class_name: name.clone_ref(py),
                    fields: fields.clone().unbind(),
                },
            )?
            .into_any(),
            Unknown::Dict => fields.clone().into_any(),
        };
        self.refs.push(Some(value.clone()));
        for key in keys {
            fields.set_item(key.bind(py), self.value()?)?;
        }
        Ok(value)
    }
}
