use std::rc::Rc;

use hessian2_core::{Reader, Token, text};
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyList, PyString, PyTuple};

use crate::builtins::{self, Builtin};
use crate::codec::{Default, Entry, Field, Kind, Registry, Unknown};
use crate::object::HessianObject;
use crate::{DecodeError, time};

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
    },
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
    plans: Vec<Option<Rc<Plan>>>,
    /// Nonzero while reading fields a registered class ignores, where an
    /// unregistered class decodes as `HessianObject` instead of failing.
    ignored: usize,
    /// Nonzero while reading a map key, where lists decode as tuples so they
    /// can be hashed, as msgpack and cbor2 do.
    key: usize,
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
        Default::Factory(factory) => factory.bind(py).call0(),
    }
}

fn decode_error(error: hessian2_core::Error) -> PyErr {
    DecodeError::new_err(error.to_string())
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

impl<'a, 'py> Decoder<'a, 'py> {
    /// Returns a decoder over `input` that maps classes through `registry`.
    pub fn new(
        py: Python<'py>,
        input: &'a [u8],
        registry: &'a Registry,
        unknown: Unknown,
        max_depth: usize,
    ) -> Self {
        Self {
            py,
            reader: Reader::new(input),
            registry,
            unknown,
            max_depth,
            refs: Vec::new(),
            plans: Vec::new(),
            ignored: 0,
            key: 0,
            depth: 0,
        }
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
            Token::Object { class } => self.object(class),
            Token::Ref(index) => self.refs[index].clone().ok_or_else(|| {
                DecodeError::new_err("reference to a value that is still being decoded")
            }),
            token => scalar(self.py, token),
        }
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
        let slot = self.refs.len();
        self.refs.push(Some(list.clone().into_any()));
        match len {
            Some(len) => {
                for _ in 0..len {
                    list.append(self.value()?)?;
                }
            }
            None => {
                while !self.reader.end().map_err(decode_error)? {
                    list.append(self.value()?)?;
                }
            }
        }
        self.depth -= 1;
        if self.key > 0 {
            let tuple = list.to_tuple().into_any();
            self.refs[slot] = Some(tuple.clone());
            return Ok(tuple);
        }
        Ok(list.into_any())
    }

    #[inline(never)]
    fn map(&mut self) -> PyResult<Bound<'py, PyAny>> {
        self.enter()?;
        let dict = PyDict::new(self.py);
        self.refs.push(Some(dict.clone().into_any()));
        while !self.reader.end().map_err(decode_error)? {
            self.key += 1;
            let key = self.value();
            self.key -= 1;
            let key = key?;
            let value = self.value()?;
            dict.set_item(&key, value)
                .map_err(|error| key_error(self.py, &key, error))?;
        }
        self.depth -= 1;
        Ok(dict.into_any())
    }

    #[inline(never)]
    /// Returns how objects of a class definition are built, deciding on first
    /// use.
    fn plan(&mut self, class: usize) -> PyResult<Rc<Plan>> {
        if let Some(Some(plan)) = self.plans.get(class) {
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
        let plan = Rc::new(match self.registry.by_name.get(&def.name) {
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
            },
        });
        if self.plans.len() <= class {
            self.plans.resize(class + 1, None);
        }
        self.plans[class] = Some(plan.clone());
        Ok(plan)
    }

    /// Decodes a field the registered class has no attribute for, and drops
    /// it.
    fn ignored_value(&mut self) -> PyResult<()> {
        self.ignored += 1;
        let result = self.value();
        self.ignored -= 1;
        result.map(drop)
    }

    #[inline(never)]
    fn object(&mut self, class: usize) -> PyResult<Bound<'py, PyAny>> {
        self.enter()?;
        let plan = self.plan(class)?;
        let object = match &*plan {
            Plan::Class {
                entry,
                slots,
                missing,
            } => match entry.get().kind {
                Kind::Tuple { .. } => self.tuple_object(entry.get(), slots),
                _ => self.class_object(entry.get(), slots, missing),
            },
            Plan::Enum { entry, name_slot } => {
                let entry = entry.get();
                let fields = self.reader.class(class).fields.len();
                self.placeholder_object(fields, &[*name_slot], |py, [name]| {
                    // Only an exact str, so no peer-built object's methods run
                    // in the lookup or the error message.
                    let name = name
                        .filter(|name| name.is_exact_instance_of::<PyString>())
                        .ok_or_else(|| {
                            DecodeError::new_err(format!("{:?} needs a string name", entry.name))
                        })?;
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
            Plan::Converter { entry, keys } => self.converter_object(entry.get(), keys),
            Plan::Unknown { name, keys } => self.unknown_object(name, keys),
        }?;
        self.depth -= 1;
        Ok(object)
    }

    /// Builds an instance of a registered class without calling `__init__`,
    /// as `pickle` does, then runs its post-init method.
    fn class_object(
        &mut self,
        entry: &Entry,
        slots: &[Option<usize>],
        missing: &[usize],
    ) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let Kind::Class {
            fields,
            bypass_setattr,
            post_init,
        } = &entry.kind
        else {
            unreachable!()
        };
        let api = builtins::api(py)?;
        let instance = api.object_new.bind(py).call1((entry.ty.bind(py),))?;
        self.refs.push(Some(instance.clone()));
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
        for &target in slots {
            match target {
                Some(index) => set(&fields[index].attr, self.value()?)?,
                None => self.ignored_value()?,
            }
        }
        for &index in missing {
            let field = &fields[index];
            set(&field.attr, default(py, entry, field)?)?;
        }
        if let Some((name, args)) = post_init {
            instance.call_method1(name.bind(py), args.bind(py))?;
        }
        Ok(instance)
    }

    /// Builds a named tuple, which exists only once all its items are read.
    fn tuple_object(
        &mut self,
        entry: &Entry,
        slots: &[Option<usize>],
    ) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let Kind::Tuple { fields } = &entry.kind else {
            unreachable!()
        };
        let slot = self.refs.len();
        self.refs.push(None);
        let mut values: Vec<Option<Bound<'py, PyAny>>> = (0..fields.len()).map(|_| None).collect();
        for &target in slots {
            match target {
                Some(index) => values[index] = Some(self.value()?),
                None => self.ignored_value()?,
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
            match slots.iter().position(|&s| s == Some(index)) {
                Some(at) => values[at] = Some(self.value()?),
                None => self.ignored_value()?,
            }
        }
        let value = build(self.py, values)?;
        self.refs[slot] = Some(value.clone());
        Ok(value)
    }

    /// Builds a value with a `Converter`, which receives the fields as a dict.
    fn converter_object(
        &mut self,
        entry: &Entry,
        keys: &[Py<PyString>],
    ) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let Kind::Converter { decode, .. } = &entry.kind else {
            unreachable!()
        };
        let slot = self.refs.len();
        self.refs.push(None);
        let fields = PyDict::new(py);
        for key in keys {
            fields.set_item(key.bind(py), self.value()?)?;
        }
        let value = decode.bind(py).call1((fields,))?;
        self.refs[slot] = Some(value.clone());
        Ok(value)
    }

    /// Builds an object of an unregistered class as `unknown_class` says.
    fn unknown_object(
        &mut self,
        name: &Py<PyString>,
        keys: &[Py<PyString>],
    ) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let unknown = match self.unknown {
            Unknown::Raise if self.ignored > 0 => Unknown::Object,
            unknown => unknown,
        };
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
