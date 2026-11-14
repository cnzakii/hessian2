//! Decoding by annotation: each value is checked against a [`Type`], and the
//! classes the type names are built, as `loads(..., type=)` decodes.

#[allow(clippy::wildcard_imports)]
use super::*;

impl<'py> Decoder<'_, 'py> {
    /// Decodes the next value as an instance of `ty`.
    ///
    /// # Errors
    ///
    /// As for [`Decoder::value`], and `DecodeError` when the value does not
    /// match `ty`.
    #[inline(never)]
    pub fn typed(&mut self, ty: &Type) -> PyResult<Bound<'py, PyAny>> {
        match ty {
            Type::Any => return self.value(),
            Type::Ref(annotation) => return self.typed_ref(annotation),
            _ => {}
        }
        let py = self.py;
        match self.reader.next_token().map_err(decode_error)? {
            Token::Ref(index) => self.typed_reference(index, ty),
            Token::Null if ty.allows_null() => Ok(py.None().into_bound(py)),
            Token::List { len, .. } => {
                match ty.member(|t| matches!(t, Type::Items(..) | Type::Tuple(_))) {
                    Some(Type::Items(items, item)) => self.typed_list(len, *items, item),
                    Some(Type::Tuple(types)) => self.fixed_tuple(len, types),
                    _ => Err(mismatch(py, ty, "a list")),
                }
            }
            Token::Map { .. } => match ty.member(|t| matches!(t, Type::Map(..) | Type::Keys(..))) {
                Some(member) => self.typed_map(member),
                None => self.map_object(ty),
            },
            Token::Object { class } => self.typed_object(class, ty),
            token => {
                let value = scalar(py, token)?;
                self.instance(ty, value)
            }
        }
    }

    /// Decodes the next value as the type a [`Type::Ref`] names.
    #[inline(never)]
    fn typed_ref(&mut self, annotation: &Py<PyAny>) -> PyResult<Bound<'py, PyAny>> {
        let ty = self.resolve(annotation)?;
        self.typed(&ty.get().0)
    }

    /// [`Decoder::typed`] for an item, value, or field, resolving a
    /// [`Type::Ref`] in the caller's frame: a type that contains itself
    /// reaches one at every level, and the stack must hold `max_depth`
    /// levels.
    #[inline(always)]
    pub(super) fn typed_in(&mut self, ty: &Type) -> PyResult<Bound<'py, PyAny>> {
        let Type::Ref(annotation) = ty else {
            return self.typed(ty);
        };
        let resolved = self.resolve(annotation)?;
        self.typed(&resolved.get().0)
    }

    #[inline(never)]
    fn resolve(&mut self, annotation: &Py<PyAny>) -> PyResult<Bound<'py, TypeObject>> {
        self.types.resolve(annotation)
    }

    /// Returns the value a back-reference points to, if it fits `ty`.
    #[inline(never)]
    fn typed_reference(&mut self, index: usize, ty: &Type) -> PyResult<Bound<'py, PyAny>> {
        let value = self.reference(index)?;
        if ty.holds(&value, &mut self.checked, &mut self.types) {
            return Ok(value);
        }
        let got = format!(
            "a back-reference to a {} that does not match",
            type_name(&value)
        );
        Err(mismatch(self.py, ty, &got))
    }

    #[inline(never)]
    /// Returns `value` as a class of `ty`: unchanged when any member
    /// accepts it, so the order of a union's members does not matter, else
    /// converted.
    fn instance(&mut self, ty: &Type, value: Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        // The common case: a str for str, an int for int.
        let exact = value.get_type();
        if ty.classes().any(|(class, _)| exact.is(class.bind(py)))
            || ty
                .classes()
                .any(|(class, _)| typed::accepts(class.bind(py), &value))
            || ty.holds_datetime(&value)
        {
            return Ok(value);
        }
        let mut missing = None;
        for (class, _) in ty.classes() {
            if let Some(value) = typed::convert(class.bind(py), value.clone())? {
                return Ok(value);
            }
            match self.enum_by_value(class.bind(py), &value) {
                Ok(Some(member)) => return Ok(member),
                Ok(None) => {}
                // Another enum of the union may have the value.
                Err(error) => drop(missing.get_or_insert(error)),
            }
        }
        if let Some(error) = missing {
            return Err(error);
        }
        let got = match typed::aware_datetime(&value) {
            Some(aware) if ty.takes_datetimes() => typed::datetime_kind(aware).to_owned(),
            _ => type_name(&value),
        };
        Err(mismatch(py, ty, &got))
    }

    /// Decodes an object whose class the wire names, if that class is
    /// trusted at this position and fits `ty`.
    ///
    /// A class is trusted when `ty` names it or the codec registers it, so
    /// both a union of classes and a base class with registered subclasses
    /// select by the name on the wire. A JDK value is built only when its
    /// Python type fits, and a `HessianObject` in `ty` keeps any other
    /// object raw.
    #[inline(never)]
    fn typed_object(&mut self, class: usize, ty: &Type) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let mut unnamed = None;
        for (expected, fields) in ty.classes() {
            let expected = expected.bind(py);
            match self.expected_entry(expected)? {
                Some(entry) if entry.get().name == self.reader.class(class).name => {
                    let value = self.object(class, As::Entry(&entry), fields)?;
                    // A converter's from_fields may return anything; the
                    // annotation still decides what fits here.
                    if matches!(entry.get().kind, Kind::Converter { .. })
                        && !typed::instance_holds(expected, fields, &value, &mut self.types)
                    {
                        return Err(converted_mismatch(py, ty, &value, entry.get()));
                    }
                    return Ok(value);
                }
                Some(_) => {}
                None => unnamed = unnamed.or(Some(expected.clone())),
            }
        }
        self.untrusted_object(class, ty, unnamed)
    }

    /// Decodes an object whose class `ty` does not name: a registered class
    /// that fits `ty`, a JDK value, or a raw `HessianObject`, or fails.
    /// Out of line so the recursion through `typed_object` stays shallow.
    #[inline(never)]
    fn untrusted_object(
        &mut self,
        class: usize,
        ty: &Type,
        unnamed: Option<Bound<'py, PyType>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let name = &self.reader.class(class).name;
        if let Some(entry) = self.registry.by_name.get(name) {
            let found = match &entry.get().kind {
                Kind::Value(builtin) => builtin.decoded_type(py)?,
                _ => entry.get().ty.bind(py).clone(),
            };
            // A subclass keeps the type arguments of the position it fills.
            let fitting = ty
                .classes()
                .find(|(expected, _)| typed::admits(expected.bind(py), &found))
                .map(|(_, fields)| fields)
                .or_else(|| {
                    // java.time values decode as datetimes, checked once built.
                    (ty.takes_datetimes()
                        && found
                            .is_subclass(&py.get_type::<PyDateTime>())
                            .unwrap_or(false))
                    .then_some(None)
                });
            if let Some(fields) = fitting {
                let value = self.object(class, As::Registry, fields)?;
                return self.instance(ty, value);
            }
        }
        if ty
            .classes()
            .any(|(expected, _)| expected.bind(py).is(py.get_type::<HessianObject>()))
        {
            return self.object(class, As::Raw, None);
        }
        let got = format!("an object of class {}", PyString::new(py, name).repr()?);
        Err(mismatch(py, ty, &no_name_hint(self.codec, got, unnamed)?))
    }

    /// Decodes a map where `ty` expects a class, as Java's
    /// `Hessian2Input.readObject(Class)` does: the one record class `ty`
    /// names is filled by field name. Nothing in the map, such as a
    /// `"class"` key or a type name, chooses the class.
    #[inline(never)]
    fn map_object(&mut self, ty: &Type) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let mut found = None;
        let mut unnamed = None;
        for (expected, fields) in ty.classes() {
            let expected = expected.bind(py);
            match self.expected_entry(expected)? {
                Some(entry)
                    if matches!(
                        entry.get().kind,
                        Kind::Class { .. }
                            | Kind::Tuple { .. }
                            | Kind::Converter { .. }
                            | Kind::Enum
                    ) =>
                {
                    if found.is_some() {
                        return Err(mismatch(py, ty, "a map, which names no class to choose"));
                    }
                    found = Some((entry, expected.clone(), fields));
                }
                Some(_) => {}
                None => unnamed = unnamed.or(Some(expected.clone())),
            }
        }
        let Some((entry, expected, fields)) = found else {
            return Err(mismatch(
                py,
                ty,
                &no_name_hint(self.codec, "a map".to_owned(), unnamed)?,
            ));
        };
        let entry = entry.get();
        self.enter()?;
        let value = match &entry.kind {
            Kind::Enum => self.enum_map(entry)?,
            Kind::Tuple { .. } => self.tuple_object(entry, Fill::Map, fields)?,
            Kind::Class { .. } => self.class_object(entry, Fill::Map, fields)?,
            _ => {
                let value = self.converter_object(entry, None)?;
                if !typed::instance_holds(&expected, fields, &value, &mut self.types) {
                    return Err(converted_mismatch(py, ty, &value, entry));
                }
                value
            }
        };
        self.depth -= 1;
        Ok(value)
    }

    /// Decodes a map as the enum member its `"name"` key names, ignoring
    /// other keys, as Java's `EnumDeserializer.readMap` does
    /// (https://github.com/apache/dubbo-hessian-lite/blob/master/hessian-lite/src/main/java/com/alibaba/com/caucho/hessian/io/EnumDeserializer.java).
    fn enum_map(&mut self, entry: &Entry) -> PyResult<Bound<'py, PyAny>> {
        let slot = self.refs.len();
        self.refs.push(None);
        let mut name = None;
        while !self.reader.end().map_err(decode_error)? {
            let key = self.plain_value()?;
            let value = self.plain_value()?;
            if key.is_exact_instance_of::<PyString>() && key.cast::<PyString>()?.to_str()? == "name"
            {
                name = Some(value);
            }
        }
        let member = enum_member(self.py, entry, name)?;
        self.refs[slot] = Some(member.clone());
        Ok(member)
    }

    /// Returns the member of `class` whose value is `value`, when `class`
    /// is an enum this codec writes as its values: a `StrEnum` or `IntEnum`
    /// with no Java class name, as msgspec decodes enums by value.
    fn enum_by_value(
        &mut self,
        class: &Bound<'py, PyType>,
        value: &Bound<'py, PyAny>,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        let py = self.py;
        let exact = value.get_type();
        let scalar =
            exact.is(py.get_type::<PyString>()) || exact.is(py.get_type::<pyo3::types::PyInt>());
        if !scalar
            || !class.is_subclass(builtins::api(py)?.enum_type.bind(py))?
            || !class.is_subclass(&exact)?
            || self.expected_entry(class)?.is_some()
        {
            return Ok(None);
        }
        class.call1((value,)).map(Some).map_err(|_| {
            let shown = value.repr().map_or_else(|_| "?".into(), |r| r.to_string());
            DecodeError::new_err(format!(
                "{} has no member with value {shown}",
                class
                    .qualname()
                    .map_or_else(|_| "?".into(), |n| n.to_string())
            ))
        })
    }

    /// Returns the entry an annotated class decodes with: its registration,
    /// or else its own declaration.
    #[inline(never)]
    fn expected_entry(&mut self, ty: &Bound<'py, PyType>) -> PyResult<Option<Py<Entry>>> {
        let py = self.py;
        let key = ty.as_ptr() as usize;
        if let Some(found) = self.expected.get(&key) {
            return Ok(found.as_ref().map(|entry| entry.clone_ref(py)));
        }
        let found = match self.registry.by_type.get(&key) {
            Some(entry) => Some(entry.clone_ref(py)),
            None => declared_entry(self.codec, ty)?.map(Bound::unbind),
        };
        self.expected
            .insert(key, found.as_ref().map(|entry| entry.clone_ref(py)));
        Ok(found)
    }

    #[inline(never)]
    fn typed_list(
        &mut self,
        len: Option<usize>,
        items: Items,
        item: &Type,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.enter()?;
        let py = self.py;
        let list = PyList::empty(py);
        let slot = self.refs.len();
        // A tuple or set exists only once its items are read.
        self.refs
            .push(matches!(items, Items::List).then(|| list.clone().into_any()));
        let mut index = 0;
        while match len {
            Some(len) => index < len,
            None => !self.reader.end().map_err(decode_error)?,
        } {
            match self.typed_in(item) {
                Ok(value) => list.append(value)?,
                Err(error) => return Err(self.at(error, || format!("[{index}]"))),
            }
            index += 1;
        }
        self.depth -= 1;
        let value = match items {
            Items::List => return Ok(list.into_any()),
            Items::Tuple => list.to_tuple().into_any(),
            Items::Set => PySet::new(py, &list)
                .map_err(|error| set_error(py, error))?
                .into_any(),
            Items::FrozenSet => PyFrozenSet::new(py, &list)
                .map_err(|error| set_error(py, error))?
                .into_any(),
        };
        self.refs[slot] = Some(value.clone());
        Ok(value)
    }

    #[inline(never)]
    fn fixed_tuple(&mut self, len: Option<usize>, types: &[Type]) -> PyResult<Bound<'py, PyAny>> {
        let wrong = |got: &str| {
            DecodeError::new_err(format!(
                "expected a tuple of {} items, got a list of {got}",
                types.len()
            ))
        };
        if let Some(len) = len
            && len != types.len()
        {
            return Err(wrong(&len.to_string()));
        }
        self.enter()?;
        let slot = self.refs.len();
        self.refs.push(None);
        let mut values = Vec::with_capacity(types.len());
        for (index, ty) in types.iter().enumerate() {
            if len.is_none() && self.reader.end().map_err(decode_error)? {
                return Err(wrong("fewer"));
            }
            match self.typed_in(ty) {
                Ok(value) => values.push(value),
                Err(error) => return Err(self.at(error, || format!("[{index}]"))),
            }
        }
        if len.is_none() && !self.reader.end().map_err(decode_error)? {
            return Err(wrong("more"));
        }
        self.depth -= 1;
        let tuple = PyTuple::new(self.py, values)?.into_any();
        self.refs[slot] = Some(tuple.clone());
        Ok(tuple)
    }

    /// Decodes a map as a dict whose keys and values `ty`, a [`Type::Map`]
    /// or [`Type::Keys`], types.
    #[inline(never)]
    fn typed_map(&mut self, ty: &Type) -> PyResult<Bound<'py, PyAny>> {
        self.enter()?;
        let py = self.py;
        let dict = PyDict::new(py);
        self.refs.push(Some(dict.clone().into_any()));
        while !self.reader.end().map_err(decode_error)? {
            let key = match ty {
                Type::Map(key, _) if !matches!(**key, Type::Any) => self.typed(key)?,
                // A TypedDict declares str keys only; any other is plain data.
                Type::Keys(..) => {
                    self.plain += 1;
                    let key = self.map_key();
                    self.plain -= 1;
                    key?
                }
                _ => self.map_key()?,
            };
            let value_type = match ty {
                Type::Map(_, value) => Some(&**value),
                Type::Keys(keys, _) => key
                    .cast::<PyString>()
                    .ok()
                    .and_then(|key| keys.get(key.to_str().ok()?)),
                _ => None,
            };
            // A key a TypedDict does not declare keeps plain data, as a
            // field a class lacks does.
            let value = match value_type {
                Some(value_type) => self.typed_in(value_type),
                None => self.plain_value(),
            };
            let value = match value {
                Ok(value) => value,
                Err(error) => return Err(self.at(error, || key_segment(&key))),
            };
            dict.set_item(&key, value)
                .map_err(|error| key_error(py, &key, error))?;
        }
        if let Type::Keys(_, required) = ty
            && let Some(missing) = required
                .iter()
                .find(|key| !dict.contains(key.as_str()).unwrap_or(false))
        {
            let missing = PyString::new(py, missing).repr()?;
            return Err(mismatch(py, ty, &format!("a map without key {missing}")));
        }
        self.depth -= 1;
        Ok(dict.into_any())
    }
}

/// Returns `got`, explaining why `unnamed`, a class an annotation names,
/// took nothing: it has no Java class name.
fn no_name_hint(
    codec: &Bound<'_, PyAny>,
    mut got: String,
    unnamed: Option<Bound<'_, PyType>>,
) -> PyResult<String> {
    if let Some(unnamed) = unnamed
        && let Some(advice) = crate::codec::advice(codec, &unnamed)?
    {
        got.push_str(&format!(
            "; {} has no Java class name: {advice}",
            unnamed.qualname()?
        ));
    }
    Ok(got)
}

/// Returns the error for a converter's result that does not fit `ty`.
#[cold]
#[inline(never)]
fn converted_mismatch(py: Python<'_>, ty: &Type, value: &Bound<'_, PyAny>, entry: &Entry) -> PyErr {
    let got = format!(
        "{} from the converter for {:?}",
        type_name(value),
        entry.name
    );
    mismatch(py, ty, &got)
}
