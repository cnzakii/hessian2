use std::cell::Cell;
use std::collections::HashMap;

use hessian2_core::Writer;
use pyo3::exceptions::PyOverflowError;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{
    PyBool, PyByteArray, PyBytes, PyDate, PyDateAccess, PyDateTime, PyDelta, PyDeltaAccess, PyDict,
    PyFloat, PyFrozenSet, PyInt, PyList, PyMemoryView, PySet, PyString, PyTime, PyTimeAccess,
    PyTuple, PyTzInfoAccess,
};

use crate::address::AddressMap;
use crate::builtins::{self, Builtin};
use crate::codec::{Entry, Kind, Registry};
use crate::hint::Hint;
use crate::object::HessianObject;
use crate::{EncodeError, encode_user_error, time};

/// Writes Python values as one Hessian stream, keeping the stream's class
/// definitions and reference numbering across the values in it.
pub struct Encoder<'a, 'py> {
    py: Python<'py>,
    writer: Writer,
    registry: &'a Registry,
    /// Reference numbers of containers already written, by object address,
    /// with the fingerprint of the hint a container was written under, or 0.
    refs: AddressMap<(usize, u64)>,
    /// Further versions of a container seen again under other hints, by
    /// hint fingerprint.
    versions: AddressMap<Vec<(u64, usize)>>,
    /// Keeps every value in `refs` alive so no address is reused while
    /// encoding, as `pickle` does for its memo.
    alive: Vec<Bound<'py, PyAny>>,
    /// Class indexes of registered classes, by entry address.
    classes: AddressMap<usize>,
    /// Class indexes of definitions given as a name and field names.
    shapes: HashMap<(String, Vec<String>), usize>,
    /// Class indexes of built-in forms, by `Builtin` discriminant.
    builtins: [Option<usize>; Builtin::ALL.len()],
    /// Reference numbers of the registered tuples being written, which a
    /// reader cannot refer to until their items are read.
    open_tuples: Vec<usize>,
    max_depth: usize,
    depth: usize,
    /// The codec, whose `_declared` method makes entries for classes
    /// declared with `hessian2.java_class` but not registered.
    codec: Bound<'py, PyAny>,
    /// Those entries by type address; an entry keeps its type alive, so
    /// the address cannot be reused while it is cached.
    declared: AddressMap<Py<Entry>>,
}

/// The hints for the items of a list, tuple, or set.
#[derive(Clone, Copy)]
enum ItemHints<'h> {
    None,
    Each(&'h Hint),
    At(&'h [Option<Hint>]),
}

impl<'h> ItemHints<'h> {
    fn get(self, index: usize) -> Option<&'h Hint> {
        match self {
            Self::None => None,
            Self::Each(hint) => Some(hint),
            Self::At(hints) => hints.get(index).and_then(Option::as_ref),
        }
    }
}

/// The hints for the keys and values of a dict.
#[derive(Clone, Copy)]
enum MapHints<'h> {
    None,
    Pair(Option<&'h Hint>, Option<&'h Hint>),
    Keys(&'h HashMap<String, Hint>),
}

/// The class name a `java.util.HashSet` is written with, so Java reads a
/// `Set` back.
const HASH_SET: &str = "java.util.HashSet";

thread_local! {
    /// The reference table of this thread's last encoding, emptied, so the
    /// next one reuses its allocation instead of rehashing as it grows.
    static SPARE_REFS: Cell<AddressMap<(usize, u64)>> = Cell::default();
}

/// Tables larger than this are freed rather than kept for reuse.
const MAX_SPARE_REFS: usize = 1 << 16;

fn type_name(obj: &Bound<'_, PyAny>) -> String {
    obj.get_type()
        .qualname()
        .map_or_else(|_| "?".to_owned(), |name| name.to_string())
}

impl Drop for Encoder<'_, '_> {
    fn drop(&mut self) {
        let mut refs = std::mem::take(&mut self.refs);
        if refs.capacity() <= MAX_SPARE_REFS {
            refs.clear();
            SPARE_REFS.set(refs);
        }
    }
}

impl<'a, 'py> Encoder<'a, 'py> {
    /// Returns an encoder for a new stream that maps types through
    /// `registry`, and through `codec` for declared classes it lacks.
    pub fn new(
        py: Python<'py>,
        registry: &'a Registry,
        max_depth: usize,
        codec: &Bound<'py, PyAny>,
    ) -> Self {
        Self {
            py,
            writer: Writer::new(),
            registry,
            refs: SPARE_REFS.take(),
            versions: AddressMap::default(),
            alive: Vec::new(),
            classes: AddressMap::default(),
            shapes: HashMap::new(),
            builtins: [None; Builtin::ALL.len()],
            open_tuples: Vec::new(),
            max_depth,
            depth: 0,
            codec: codec.clone(),
            declared: AddressMap::default(),
        }
    }

    /// Returns the bytes written.
    pub fn finish(&self) -> &[u8] {
        self.writer.as_bytes()
    }

    /// Writes one value.
    ///
    /// # Errors
    ///
    /// Raises `EncodeError` for a value with no Hessian form and past
    /// `max_depth`. A `ValueError` or `TypeError` from user code it runs,
    /// such as property getters and converters, becomes `EncodeError`;
    /// other errors from that code propagate.
    #[inline(never)]
    pub fn value(&mut self, obj: &Bound<'py, PyAny>) -> PyResult<()> {
        if self.registry.natives_overridden
            && let Some(entry) = self
                .registry
                .by_type
                .get(&(obj.get_type().as_ptr() as usize))
            && matches!(entry.get().kind, Kind::Converter { .. })
        {
            return self.registered(obj, entry.get());
        }
        // The common exact types first; everything else is out of line so the
        // recursive frame stays small.
        if obj.is_none() {
            self.writer.write_null();
            Ok(())
        } else if obj.is_exact_instance_of::<PyString>() {
            self.string(obj)
        } else if obj.is_exact_instance_of::<PyBool>() {
            self.writer.write_bool(obj.is(PyBool::new(self.py, true)));
            Ok(())
        } else if obj.is_exact_instance_of::<PyInt>() {
            self.int(obj)
        } else if obj.is_exact_instance_of::<PyFloat>() {
            self.float(obj)
        } else if obj.is_exact_instance_of::<PyList>() {
            self.list(obj, None, None, ItemHints::None)
        } else if obj.is_exact_instance_of::<PyDict>() {
            self.map(obj, None, MapHints::None)
        } else if let Some(entry) = self
            .registry
            .by_type
            .get(&(obj.get_type().as_ptr() as usize))
        {
            self.registered(obj, entry.get())
        } else if let Some(entry) = self.declared.get(&(obj.get_type().as_ptr() as usize)) {
            let entry = entry.clone_ref(self.py);
            self.registered(obj, entry.get())
        } else {
            self.other(obj)
        }
    }

    /// Writes one value as `hint` says, or as [`Encoder::value`] does where
    /// the hint does not apply to it.
    ///
    /// # Errors
    ///
    /// As for [`Encoder::value`], and `EncodeError` for a value the hinted
    /// Java type cannot hold.
    pub fn hinted(&mut self, obj: &Bound<'py, PyAny>, hint: Option<&Hint>) -> PyResult<()> {
        match hint {
            None => self.value(obj),
            Some(hint) => self.with_hint(obj, hint),
        }
    }

    /// Returns whether `hint` describes a value like `obj`: an `int` for an
    /// integer tag, a list for item hints, and so on, subclasses included, as
    /// the encoder writes them the same way.
    fn applies(obj: &Bound<'py, PyAny>, hint: &Hint) -> bool {
        match hint {
            Hint::Long | Hint::BigInteger => {
                obj.is_instance_of::<PyInt>() && !obj.is_instance_of::<PyBool>()
            }
            Hint::Instant | Hint::OffsetDateTime | Hint::ZonedDateTime => {
                obj.is_instance_of::<PyDateTime>()
            }
            Hint::Items(_) => sequence(obj).is_some(),
            Hint::Tuple(items) => {
                (obj.is_instance_of::<PyList>() || obj.is_instance_of::<PyTuple>())
                    && obj.len().is_ok_and(|len| len == items.len())
            }
            Hint::Map(..) | Hint::Keys(_) => obj.is_instance_of::<PyDict>(),
            Hint::Union(members) => members.iter().any(|member| Self::applies(obj, member)),
        }
    }

    /// Returns whether `obj` takes hints: an instance of a registered or
    /// declared type keeps its own form.
    fn plain(&mut self, obj: &Bound<'py, PyAny>) -> PyResult<bool> {
        let key = obj.get_type().as_ptr() as usize;
        if self.registry.by_type.contains_key(&key) {
            return Ok(false);
        }
        let exact = obj.is_exact_instance_of::<PyInt>()
            || obj.is_exact_instance_of::<PyList>()
            || obj.is_exact_instance_of::<PyTuple>()
            || obj.is_exact_instance_of::<PyDict>()
            || obj.is_exact_instance_of::<PyDateTime>();
        Ok(exact || self.declared_entry(obj)?.is_none())
    }

    #[inline(never)]
    fn with_hint(&mut self, obj: &Bound<'py, PyAny>, hint: &Hint) -> PyResult<()> {
        if !Self::applies(obj, hint) || !self.plain(obj)? {
            return self.value(obj);
        }
        match hint {
            Hint::Long => {
                let value: i64 = obj.extract().map_err(|_| {
                    EncodeError::new_err(format!(
                        "{} does not fit in a Java long",
                        obj.repr().map_or_else(|_| "?".into(), |r| r.to_string())
                    ))
                })?;
                self.writer.write_long(value);
                Ok(())
            }
            Hint::BigInteger => self.big_integer(obj),
            Hint::Instant | Hint::OffsetDateTime | Hint::ZonedDateTime => {
                self.java_time(obj.cast::<PyDateTime>()?, hint)
            }
            Hint::Items(item) => {
                let java_type = sequence(obj).flatten();
                self.list(obj, java_type, Some(hint), ItemHints::Each(item))
            }
            Hint::Tuple(items) => self.list(obj, None, Some(hint), ItemHints::At(items)),
            Hint::Map(key, value) => self.map(
                obj,
                Some(hint),
                MapHints::Pair(key.as_deref(), value.as_deref()),
            ),
            Hint::Keys(keys) => self.map(obj, Some(hint), MapHints::Keys(keys)),
            // Checked again: a value's own code, such as `__len__`, can
            // change its answer between the two checks.
            Hint::Union(members) => {
                match members.iter().find(|member| Self::applies(obj, member)) {
                    Some(member) => self.with_hint(obj, member),
                    None => self.value(obj),
                }
            }
        }
    }

    #[inline(never)]
    fn other(&mut self, obj: &Bound<'py, PyAny>) -> PyResult<()> {
        // Before the str, int, and tuple checks, as registered types come
        // first: a declared StrEnum or named tuple is an object.
        if let Some(entry) = self.declared_entry(obj)? {
            return self.registered(obj, entry.get());
        }
        if obj.is_instance_of::<PyString>() {
            self.string(obj)?;
        } else if obj.is_instance_of::<PyInt>() {
            self.int(obj)?;
        } else if obj.is_instance_of::<PyFloat>() {
            self.float(obj)?;
        } else if let Ok(bytes) = obj.cast::<PyBytes>() {
            self.writer.write_binary(bytes.as_bytes());
        } else if obj.is_instance_of::<PyByteArray>() || obj.is_instance_of::<PyMemoryView>() {
            let bytes = PyMemoryView::from(obj)?.call_method0("tobytes")?;
            self.writer
                .write_binary(bytes.cast::<PyBytes>()?.as_bytes());
        } else if obj.is_instance_of::<PyList>() || obj.is_instance_of::<PyTuple>() {
            self.list(obj, None, None, ItemHints::None)?;
        } else if obj.is_instance_of::<PySet>() || obj.is_instance_of::<PyFrozenSet>() {
            self.list(obj, Some(HASH_SET), None, ItemHints::None)?;
        } else if obj.is_instance_of::<PyDict>() {
            self.map(obj, None, MapHints::None)?;
        } else if let Ok(object) = obj.cast::<HessianObject>() {
            self.generic_object(object)?;
        } else if let Ok(value) = obj.cast::<PyDateTime>() {
            match time::to_millis(value)? {
                Some(millis) => self.writer.write_date(millis),
                None => self.builtin(obj, Builtin::LocalDateTime)?,
            }
        } else if obj.is_instance_of::<PyDate>() {
            self.builtin(obj, Builtin::LocalDate)?;
        } else if obj.is_instance_of::<PyTime>() {
            self.builtin(obj, Builtin::LocalTime)?;
        } else if obj.is_instance_of::<PyDelta>() {
            self.builtin(obj, Builtin::Duration)?;
        } else {
            return Err(EncodeError::new_err(format!(
                "{} has no Hessian form; register it with the codec",
                type_name(obj)
            )));
        }
        Ok(())
    }

    #[inline(never)]
    fn float(&mut self, obj: &Bound<'py, PyAny>) -> PyResult<()> {
        self.writer.write_double(obj.extract()?);
        Ok(())
    }

    #[inline(never)]
    fn string(&mut self, obj: &Bound<'py, PyAny>) -> PyResult<()> {
        let text = obj.cast::<PyString>()?;
        match text.to_str() {
            Ok(text) => self.writer.write_str(text),
            Err(_) => {
                // Unpaired surrogates have no UTF-8 form; write their UTF-16 units.
                let raw = text.call_method1("encode", ("utf-16-le", "surrogatepass"))?;
                let units: Vec<u16> = raw
                    .cast::<PyBytes>()?
                    .as_bytes()
                    .as_chunks::<2>()
                    .0
                    .iter()
                    .map(|pair| u16::from_le_bytes(*pair))
                    .collect();
                self.writer.write_utf16(&units);
            }
        }
        Ok(())
    }

    #[inline(never)]
    fn int(&mut self, obj: &Bound<'py, PyAny>) -> PyResult<()> {
        match obj.extract::<i64>() {
            Ok(value) => match i32::try_from(value) {
                Ok(int) => self.writer.write_int(int),
                Err(_) => self.writer.write_long(value),
            },
            Err(error) if error.is_instance_of::<PyOverflowError>(self.py) => {
                return self.builtin(obj, Builtin::BigInteger);
            }
            Err(error) => return Err(error),
        }
        Ok(())
    }

    /// Writes a back-reference when `obj` was written before; otherwise
    /// remembers `obj` under the reference number `start` returns. Work a
    /// new value needs before it starts belongs in `start`, so references
    /// skip it.
    ///
    /// # Errors
    ///
    /// Raises `EncodeError` for a reference to a registered tuple from
    /// inside its own items, which no reader can rebuild, and propagates
    /// errors from `start`.
    fn track(
        &mut self,
        obj: &Bound<'py, PyAny>,
        start: impl FnOnce(&mut Self) -> PyResult<usize>,
    ) -> PyResult<bool> {
        self.track_hinted(obj, None, start)
    }

    /// [`track`](Self::track) for a container written under `own`, the hint
    /// for it at this position.
    ///
    /// Java reads one object as one type, so a container seen again under
    /// other tags is written again rather than referred to, as pydantic and
    /// Jackson write each position by its own annotation. No hint means no
    /// constraint, so it refers to the first write. Hints are finite, so
    /// copying a container that contains itself ends where they run out.
    fn track_hinted(
        &mut self,
        obj: &Bound<'py, PyAny>,
        own: Option<&Hint>,
        start: impl FnOnce(&mut Self) -> PyResult<usize>,
    ) -> PyResult<bool> {
        let address = obj.as_ptr() as usize;
        let fingerprint = own.map_or(0, Hint::fingerprint);
        if let Some(&(index, first)) = self.refs.get(&address) {
            if self.open_tuples.contains(&index) {
                return Err(EncodeError::new_err(format!(
                    "{} contains itself; a tuple is built after its items, so it cannot",
                    type_name(obj)
                )));
            }
            let version = if fingerprint == 0 || fingerprint == first {
                Some(index)
            } else {
                self.versions.get(&address).and_then(|versions| {
                    versions
                        .iter()
                        .find(|&&(hint, _)| hint == fingerprint)
                        .map(|&(_, index)| index)
                })
            };
            if let Some(index) = version {
                self.writer.write_ref(index);
                return Ok(true);
            }
            let index = start(self)?;
            self.versions
                .entry(address)
                .or_default()
                .push((fingerprint, index));
            return Ok(false);
        }
        let index = start(self)?;
        self.refs.insert(address, (index, fingerprint));
        self.alive.push(obj.clone());
        Ok(false)
    }

    /// Returns the entry for an instance of a class declared with
    /// `hessian2.java_class` but not registered, making it on first use, or
    /// `None` when the class has no declaration of its own.
    #[inline(never)]
    fn declared_entry(&mut self, obj: &Bound<'py, PyAny>) -> PyResult<Option<Py<Entry>>> {
        let py = self.py;
        let ty = obj.get_type();
        let key = ty.as_ptr() as usize;
        if let Some(known) = self.declared.get(&key) {
            return Ok(Some(known.clone_ref(py)));
        }
        // Built-in types cannot hold a declaration; skip the lookup for them.
        if obj.is_exact_instance_of::<PyTuple>()
            || obj.is_exact_instance_of::<PyBytes>()
            || obj.is_exact_instance_of::<PyDateTime>()
            || obj.is_exact_instance_of::<PyDate>()
            || obj.is_exact_instance_of::<PyTime>()
            || obj.is_exact_instance_of::<PyDelta>()
            || obj.is_exact_instance_of::<PySet>()
            || obj.is_exact_instance_of::<PyFrozenSet>()
            || obj.is_exact_instance_of::<PyByteArray>()
        {
            return Ok(None);
        }
        // An attribute lookup hits CPython's per-type cache and allocates
        // nothing when absent, so undeclared classes need no cache of ours.
        let Some(found) = ty.getattr_opt(intern!(py, "__hessian2__"))? else {
            return Ok(None);
        };
        // Declared on the class itself: a subclass does not inherit it.
        let own = ty
            .getattr(intern!(py, "__dict__"))?
            .call_method1(intern!(py, "get"), (intern!(py, "__hessian2__"),))?;
        if !own.is(&found) {
            return Ok(None);
        }
        // Built once per class and cached on its declaration.
        let mut entry = own.getattr(intern!(py, "entry"))?;
        if entry.is_none() {
            // Such as annotations that do not resolve: the class cannot encode.
            entry = self
                .codec
                .call_method1(intern!(py, "_declared"), (&ty,))
                .map_err(|error| encode_user_error(py, error))?;
        }
        let entry = entry.cast_into::<Entry>()?.unbind();
        self.declared.insert(key, entry.clone_ref(py));
        Ok(Some(entry))
    }

    /// Runs `write` one container level deeper, failing past `max_depth`.
    fn nested(&mut self, write: impl FnOnce(&mut Self) -> PyResult<()>) -> PyResult<()> {
        if self.depth >= self.max_depth {
            return Err(EncodeError::new_err(format!(
                "values nest deeper than max_depth={} while encoding",
                self.max_depth
            )));
        }
        self.depth += 1;
        let result = write(self);
        self.depth -= 1;
        result
    }

    #[inline(never)]
    fn list(
        &mut self,
        obj: &Bound<'py, PyAny>,
        java_type: Option<&str>,
        own: Option<&Hint>,
        hints: ItemHints<'_>,
    ) -> PyResult<()> {
        let len = obj.len()?;
        if self.track_hinted(obj, own, |this| Ok(this.writer.begin_list(len, java_type)))? {
            return Ok(());
        }
        self.nested(|this| {
            let changed =
                || EncodeError::new_err(format!("{} changed size while encoding", type_name(obj)));
            let mut written = 0;
            for item in obj.try_iter()? {
                // Stop at the first extra item: code run by encoding an item
                // could otherwise keep appending forever.
                if written == len {
                    return Err(changed());
                }
                this.hinted(&item?, hints.get(written))?;
                written += 1;
            }
            if written != len {
                return Err(changed());
            }
            Ok(())
        })
    }

    #[inline(never)]
    fn map(
        &mut self,
        obj: &Bound<'py, PyAny>,
        own: Option<&Hint>,
        hints: MapHints<'_>,
    ) -> PyResult<()> {
        let dict = obj.cast::<PyDict>()?;
        if self.track_hinted(dict.as_any(), own, |this| Ok(this.writer.begin_map(None)))? {
            return Ok(());
        }
        // A copy, because encoding a value can run code that changes the
        // dict, and PyO3's iterator panics on that.
        let items = dict.copy()?;
        self.nested(|this| {
            // The common case without hints skips choosing them per item.
            if matches!(hints, MapHints::None) {
                for (key, value) in items.iter() {
                    this.value(&key)?;
                    this.value(&value)?;
                }
                return Ok(());
            }
            for (key, value) in items.iter() {
                let (key_hint, value_hint) = match hints {
                    MapHints::None => (None, None),
                    MapHints::Pair(key, value) => (key, value),
                    MapHints::Keys(keys) => (
                        None,
                        key.cast::<PyString>()
                            .ok()
                            .and_then(|key| key.to_str().ok())
                            .and_then(|key| keys.get(key)),
                    ),
                };
                this.hinted(&key, key_hint)?;
                this.hinted(&value, value_hint)?;
            }
            Ok(())
        })?;
        self.writer.end_map();
        Ok(())
    }

    /// Returns the class index for a name and field names, defining it on
    /// first use.
    fn shape_class(&mut self, name: &str, fields: Vec<String>) -> usize {
        let key = (name.to_owned(), fields);
        if let Some(&class) = self.shapes.get(&key) {
            return class;
        }
        let class = self.writer.define_class(name, &key.1);
        self.shapes.insert(key, class);
        class
    }

    /// Writes `obj` as an object of class `name` whose fields come from the
    /// dict `fields` returns, called only when `obj` is not a reference.
    #[inline(never)]
    fn fields_object(
        &mut self,
        obj: &Bound<'py, PyAny>,
        name: &str,
        fields: impl FnOnce() -> PyResult<Bound<'py, PyDict>>,
    ) -> PyResult<()> {
        let mut values = Vec::new();
        let started = |this: &mut Self| {
            let fields = fields()?;
            let mut names = Vec::with_capacity(fields.len());
            values.reserve(fields.len());
            for (key, value) in fields.iter() {
                let key = key.cast::<PyString>().map_err(|_| {
                    EncodeError::new_err(format!("field names of {name:?} must be str"))
                })?;
                names.push(key.to_str()?.to_owned());
                values.push(value);
            }
            let class = this.shape_class(name, names);
            Ok(this.writer.begin_object(class))
        };
        if self.track(obj, started)? {
            return Ok(());
        }
        self.nested(|this| values.iter().try_for_each(|value| this.value(value)))
    }

    fn generic_object(&mut self, object: &Bound<'py, HessianObject>) -> PyResult<()> {
        let py = self.py;
        let inner = object.get();
        let name = inner.class_name.bind(py).to_str()?;
        self.fields_object(object.as_any(), name, || Ok(inner.fields.bind(py).clone()))
    }

    #[inline(never)]
    /// Writes an instance of a registered type.
    fn registered(&mut self, obj: &Bound<'py, PyAny>, entry: &Entry) -> PyResult<()> {
        match &entry.kind {
            Kind::Converter { encode, .. } => return self.converted(obj, entry, encode),
            Kind::Value(builtin) => return self.builtin(obj, *builtin),
            _ => {}
        }
        let class = match self.classes.get(&(entry as *const Entry as usize)) {
            Some(&class) => class,
            None => self.define_entry(entry),
        };
        if self.track(obj, |this| Ok(this.writer.begin_object(class)))? {
            return Ok(());
        }
        self.nested(|this| match &entry.kind {
            Kind::Class { fields, .. } => fields.iter().try_for_each(|field| {
                let value = obj
                    .getattr(field.attr.bind(this.py))
                    .map_err(|error| encode_user_error(this.py, error))?;
                this.hinted(&value, field.hint.as_ref())
            }),
            Kind::Tuple { fields } => {
                let items = obj.cast::<PyTuple>()?;
                if items.len() != fields.len() {
                    return Err(EncodeError::new_err(format!(
                        "{} has {} items, not {}",
                        type_name(obj),
                        items.len(),
                        fields.len()
                    )));
                }
                this.open_tuples.push(this.refs[&(obj.as_ptr() as usize)].0);
                let result = items
                    .iter()
                    .zip(fields)
                    .try_for_each(|(item, field)| this.hinted(&item, field.hint.as_ref()));
                this.open_tuples.pop();
                result
            }
            Kind::Enum => this.value(&obj.getattr(pyo3::intern!(this.py, "name"))?),
            Kind::Value(_) | Kind::Converter { .. } => unreachable!("handled above"),
        })
    }

    #[cold]
    #[inline(never)]
    fn define_entry(&mut self, entry: &Entry) -> usize {
        let class = self.writer.define_class(&entry.name, &entry.wire_fields());
        self.classes.insert(entry as *const Entry as usize, class);
        class
    }

    /// Writes `obj` in the object form of `builtin`.
    #[inline(never)]
    fn builtin(&mut self, obj: &Bound<'py, PyAny>, builtin: Builtin) -> PyResult<()> {
        let class = self.builtin_class(builtin);
        if self.track(obj, |this| Ok(this.writer.begin_object(class)))? {
            return Ok(());
        }
        self.nested(|this| {
            match builtin {
                Builtin::Decimal => this.writer.write_str(&builtins::decimal_text(obj)?),
                Builtin::Uuid => this.writer.write_str(&builtins::uuid_text(obj)?),
                Builtin::BigInteger => this.big_integer_fields(obj)?,
                Builtin::LocalDate => this.date_fields(obj.cast::<PyDate>()?),
                Builtin::LocalTime => {
                    let time = obj.cast::<PyTime>()?;
                    // A time is naive when its tzinfo gives no offset, as a
                    // region zone does without a date.
                    let offset = match time.get_tzinfo() {
                        Some(tzinfo) => {
                            time::utc_offset(&tzinfo, this.py.None().into_bound(this.py))?
                        }
                        None => None,
                    };
                    if offset.is_some() {
                        return Err(EncodeError::new_err(
                            "an aware time has no Java form; use a naive time",
                        ));
                    }
                    this.time_fields(time);
                }
                Builtin::LocalDateTime => this.local_datetime_fields(obj.cast::<PyDateTime>()?)?,
                Builtin::Duration => {
                    let delta = obj.cast::<PyDelta>()?;
                    this.writer.write_int(delta.get_microseconds() * 1000);
                    this.writer.write_long(
                        i64::from(delta.get_days()) * 86_400 + i64::from(delta.get_seconds()),
                    );
                }
                _ => unreachable!("{builtin:?} has no Python type that encodes to it"),
            }
            Ok(())
        })
    }

    /// Writes an `int` as `java.math.BigInteger`, which Java writes as an
    /// object even for a small value.
    fn big_integer(&mut self, obj: &Bound<'py, PyAny>) -> PyResult<()> {
        // Not tracked: a small int is a shared object, which may also appear
        // elsewhere in the stream as a plain int or a long.
        let class = self.builtin_class(Builtin::BigInteger);
        self.writer.begin_object(class);
        self.nested(|this| this.big_integer_fields(obj))
    }

    /// Writes the fields of a `BigInteger`, in Caucho's order.
    fn big_integer_fields(&mut self, obj: &Bound<'py, PyAny>) -> PyResult<()> {
        let (signum, mag) = builtins::big_integer_parts(obj.cast::<PyInt>()?)?;
        self.writer.write_int(signum);
        // Java's lazily computed caches; zero means not yet computed.
        (0..4).for_each(|_| self.writer.write_int(0));
        self.nested(|this| {
            let w = &mut this.writer;
            w.begin_list(mag.len(), Some("[int"));
            mag.into_iter().for_each(|word| w.write_int(word));
            Ok(())
        })
    }

    /// Writes an aware `datetime` in hessian-lite's handle form for the
    /// `java.time` class `hint` names.
    ///
    /// # Errors
    ///
    /// Raises `EncodeError` for a naive `datetime`, an offset `ZoneOffset`
    /// cannot hold, and, for `ZonedDateTime`, a `tzinfo` without a zone ID.
    #[inline(never)]
    fn java_time(&mut self, value: &Bound<'py, PyDateTime>, hint: &Hint) -> PyResult<()> {
        let (builtin, java) = match hint {
            Hint::Instant => (Builtin::Instant, "java.time.Instant"),
            Hint::OffsetDateTime => (Builtin::OffsetDateTime, "java.time.OffsetDateTime"),
            Hint::ZonedDateTime => (Builtin::ZonedDateTime, "java.time.ZonedDateTime"),
            _ => unreachable!("with_hint passes only java.time hints"),
        };
        let naive = || {
            EncodeError::new_err(format!(
                "a naive datetime has no {java} form; give it a tzinfo"
            ))
        };
        let tzinfo = value.get_tzinfo().ok_or_else(naive)?;
        let offset = time::utc_offset(&tzinfo, value.clone().into_any())?.ok_or_else(naive)?;
        let class = self.builtin_class(builtin);
        // Java writes a new handle object for each value, so none is tracked.
        self.writer.begin_object(class);
        if builtin == Builtin::Instant {
            let micros = time::local_micros(value) - offset;
            return self.nested(|this| {
                this.writer
                    .write_int((micros.rem_euclid(1_000_000) * 1000) as i32);
                this.writer.write_long(micros.div_euclid(1_000_000));
                Ok(())
            });
        }
        // java.time.ZoneOffset holds whole seconds within 18 hours.
        if offset % 1_000_000 != 0 || offset.abs() > 18 * 3_600_000_000 {
            return Err(EncodeError::new_err(format!(
                "{java} needs a UTC offset of whole seconds within 18 hours"
            )));
        }
        let seconds = (offset / 1_000_000) as i32;
        let zone = match builtin {
            Builtin::ZonedDateTime => Some(time::zone_id(&tzinfo, seconds)?),
            _ => None,
        };
        self.nested(|this| {
            let zone_offset = this.builtin_class(Builtin::ZoneOffset);
            this.writer.begin_object(zone_offset);
            this.nested(|this| {
                this.writer.write_int(seconds);
                Ok(())
            })?;
            let local = this.builtin_class(Builtin::LocalDateTime);
            this.writer.begin_object(local);
            this.nested(|this| this.local_datetime_fields(value))?;
            if let Some(zone) = zone {
                this.writer.write_str(&zone);
            }
            Ok(())
        })
    }

    /// Writes the fields of a `LocalDateTimeHandle`: a time and a date,
    /// which Java writes as objects of their own.
    fn local_datetime_fields(&mut self, value: &Bound<'py, PyDateTime>) -> PyResult<()> {
        self.nested(|this| {
            // Java defines each class just before its first object.
            let time = this.builtin_class(Builtin::LocalTime);
            this.writer.begin_object(time);
            this.time_fields(value);
            let date = this.builtin_class(Builtin::LocalDate);
            this.writer.begin_object(date);
            this.date_fields(value);
            Ok(())
        })
    }

    /// Returns the class index of a built-in form, defining it on first use.
    fn builtin_class(&mut self, builtin: Builtin) -> usize {
        let slot = &mut self.builtins[builtin as usize];
        *slot.get_or_insert_with(|| {
            self.writer
                .define_class(builtin.names()[0], builtin.wire_fields())
        })
    }

    /// Writes the fields of a `LocalDateHandle`.
    fn date_fields(&mut self, date: &impl PyDateAccess) {
        self.writer.write_int(i32::from(date.get_day()));
        self.writer.write_int(i32::from(date.get_month()));
        self.writer.write_int(date.get_year());
    }

    /// Writes the fields of a `LocalTimeHandle`.
    fn time_fields(&mut self, time: &impl PyTimeAccess) {
        self.writer.write_int(time.get_microsecond() as i32 * 1000);
        self.writer.write_int(i32::from(time.get_second()));
        self.writer.write_int(i32::from(time.get_minute()));
        self.writer.write_int(i32::from(time.get_hour()));
    }

    /// Writes a value through its `Converter`.
    #[inline(never)]
    fn converted(
        &mut self,
        obj: &Bound<'py, PyAny>,
        entry: &Entry,
        encode: &Py<PyAny>,
    ) -> PyResult<()> {
        let py = self.py;
        let fields = || {
            encode
                .bind(py)
                .call1((obj,))
                .map_err(|error| encode_user_error(py, error))?
                .cast_into::<PyDict>()
                .map_err(|_| {
                    EncodeError::new_err(format!(
                        "converter for {:?} must return a dict",
                        entry.name
                    ))
                })
        };
        self.fields_object(obj, &entry.name, fields)
    }
}

/// Returns, for a list, tuple, or set that hints apply to, the class name to
/// write it with: `None` for a list or tuple, Java's `HashSet` for a set.
fn sequence(obj: &Bound<'_, PyAny>) -> Option<Option<&'static str>> {
    if obj.is_instance_of::<PyList>() || obj.is_instance_of::<PyTuple>() {
        Some(None)
    } else if obj.is_instance_of::<PySet>() || obj.is_instance_of::<PyFrozenSet>() {
        Some(Some(HASH_SET))
    } else {
        None
    }
}
