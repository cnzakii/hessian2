use std::cell::Cell;
use std::collections::HashMap;

use hessian2_core::Writer;
use pyo3::exceptions::{PyOverflowError, PyRecursionError};
use pyo3::prelude::*;
use pyo3::types::{
    PyBool, PyByteArray, PyBytes, PyDate, PyDateAccess, PyDateTime, PyDelta, PyDeltaAccess, PyDict,
    PyFloat, PyFrozenSet, PyInt, PyList, PyMemoryView, PySet, PyString, PyTime, PyTimeAccess,
    PyTuple, PyTzInfoAccess,
};

use crate::address::AddressMap;
use crate::builtins::{self, Builtin};
use crate::codec::{Entry, Kind, Registry};
use crate::object::HessianObject;
use crate::{EncodeError, time};

/// Writes Python values as one Hessian stream, keeping the stream's class
/// definitions and reference numbering across the values in it.
pub struct Encoder<'a, 'py> {
    py: Python<'py>,
    writer: Writer,
    registry: &'a Registry,
    /// Reference numbers of containers already written, by object address.
    refs: AddressMap<usize>,
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
}

thread_local! {
    /// The reference table of this thread's last encoding, emptied, so the
    /// next one reuses its allocation instead of rehashing as it grows.
    static SPARE_REFS: Cell<AddressMap<usize>> = Cell::default();
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
    /// Returns an encoder for a new stream that maps types through `registry`.
    pub fn new(py: Python<'py>, registry: &'a Registry, max_depth: usize) -> Self {
        Self {
            py,
            writer: Writer::new(),
            registry,
            refs: SPARE_REFS.take(),
            alive: Vec::new(),
            classes: AddressMap::default(),
            shapes: HashMap::new(),
            builtins: [None; Builtin::ALL.len()],
            open_tuples: Vec::new(),
            max_depth,
            depth: 0,
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
    /// Raises `EncodeError` for a value with no Hessian form, `RecursionError`
    /// past `max_depth`, and propagates errors raised by user code it runs,
    /// such as property getters and converters.
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
            self.list(obj, None)
        } else if obj.is_exact_instance_of::<PyDict>() {
            self.map(obj)
        } else if let Some(entry) = self
            .registry
            .by_type
            .get(&(obj.get_type().as_ptr() as usize))
        {
            self.registered(obj, entry.get())
        } else {
            self.other(obj)
        }
    }

    #[inline(never)]
    fn other(&mut self, obj: &Bound<'py, PyAny>) -> PyResult<()> {
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
            self.list(obj, None)?;
        } else if obj.is_instance_of::<PySet>() || obj.is_instance_of::<PyFrozenSet>() {
            // The type Java writes for a HashSet, so Java reads a Set back.
            self.list(obj, Some("java.util.HashSet"))?;
        } else if obj.is_instance_of::<PyDict>() {
            self.map(obj)?;
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
        let address = obj.as_ptr() as usize;
        if let Some(&index) = self.refs.get(&address) {
            if self.open_tuples.contains(&index) {
                return Err(EncodeError::new_err(format!(
                    "{} contains itself; a tuple is built after its items, so it cannot",
                    type_name(obj)
                )));
            }
            self.writer.write_ref(index);
            return Ok(true);
        }
        let index = start(self)?;
        self.refs.insert(address, index);
        self.alive.push(obj.clone());
        Ok(false)
    }

    /// Runs `write` one container level deeper, failing past `max_depth`.
    fn nested(&mut self, write: impl FnOnce(&mut Self) -> PyResult<()>) -> PyResult<()> {
        if self.depth >= self.max_depth {
            return Err(PyRecursionError::new_err(format!(
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
    fn list(&mut self, obj: &Bound<'py, PyAny>, java_type: Option<&str>) -> PyResult<()> {
        let len = obj.len()?;
        if self.track(obj, |this| Ok(this.writer.begin_list(len, java_type)))? {
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
                this.value(&item?)?;
                written += 1;
            }
            if written != len {
                return Err(changed());
            }
            Ok(())
        })
    }

    #[inline(never)]
    fn map(&mut self, obj: &Bound<'py, PyAny>) -> PyResult<()> {
        let dict = obj.cast::<PyDict>()?;
        if self.track(dict.as_any(), |this| Ok(this.writer.begin_map(None)))? {
            return Ok(());
        }
        // A copy, because encoding a value can run code that changes the
        // dict, and PyO3's iterator panics on that.
        let items = dict.copy()?;
        self.nested(|this| {
            for (key, value) in items.iter() {
                this.value(&key)?;
                this.value(&value)?;
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
    fn registered(&mut self, obj: &Bound<'py, PyAny>, entry: &'a Entry) -> PyResult<()> {
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
            Kind::Class { fields, .. } => fields
                .iter()
                .try_for_each(|field| this.value(&obj.getattr(field.attr.bind(this.py))?)),
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
                this.open_tuples.push(this.refs[&(obj.as_ptr() as usize)]);
                let result = items.iter().try_for_each(|item| this.value(&item));
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
                Builtin::BigInteger => {
                    let (signum, mag) = builtins::big_integer_parts(obj.cast::<PyInt>()?)?;
                    this.writer.write_int(signum);
                    // Java's lazily computed caches; zero means not yet computed.
                    (0..4).for_each(|_| this.writer.write_int(0));
                    this.nested(|this| {
                        let w = &mut this.writer;
                        w.begin_list(mag.len(), Some("[int"));
                        mag.into_iter().for_each(|word| w.write_int(word));
                        Ok(())
                    })?;
                }
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
                Builtin::LocalDateTime => {
                    // Java writes the date and time as objects of their own.
                    let value = obj.cast::<PyDateTime>()?;
                    this.nested(|this| {
                        // Java defines each class just before its first object.
                        let time = this.builtin_class(Builtin::LocalTime);
                        this.writer.begin_object(time);
                        this.time_fields(value);
                        let date = this.builtin_class(Builtin::LocalDate);
                        this.writer.begin_object(date);
                        this.date_fields(value);
                        Ok(())
                    })?;
                }
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
                .call1((obj,))?
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
