//! Decoding types: what a decoded value must be, compiled from type
//! annotations by the Python package.

use std::collections::HashMap;
use std::sync::Arc;

use crate::address::{AddressMap, AddressSet};
use pyo3::PyTraverseError;
use pyo3::exceptions::PyValueError;
use pyo3::gc::PyVisit;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::PyTzInfoAccess;
use pyo3::types::{
    PyBool, PyDate, PyDateTime, PyDict, PyFloat, PyFrozenSet, PyInt, PyList, PySet, PyString,
    PyTuple, PyType,
};

/// The container a Hessian list decodes into.
#[derive(Clone, Copy, Debug)]
pub enum Items {
    List,
    Tuple,
    Set,
    FrozenSet,
}

/// The types of a generic class's fields for one choice of its type
/// parameters, such as `item: Address` for `Box[Address]`, by attribute.
pub type Fields = HashMap<String, Type>;

/// What a decoded value must be, and how containers are built.
#[derive(Debug)]
pub enum Type {
    /// Any value, decoded as without a type.
    Any,
    /// `None` only.
    Null,
    /// An instance of a class: a scalar, a JDK value, or a declared,
    /// registered, or converted class, with its field types where its type
    /// parameters are given.
    Is(Py<PyType>, Option<Arc<Fields>>),
    /// A `datetime` that has a UTC offset, or one that has none, as a
    /// `Date` or `LocalDateTime` annotation requires.
    DateTime(bool),
    /// A list, tuple, or set of items of one type.
    Items(Items, Box<Type>),
    /// A tuple with one type per position.
    Tuple(Vec<Type>),
    /// A dict with typed keys and values.
    Map(Box<Type>, Box<Type>),
    /// A dict with values typed by string key, and the keys it must have,
    /// as a `TypedDict` declares them.
    Keys(HashMap<String, Type>, Vec<String>),
    /// A value of any of the members.
    Union(Vec<Type>),
    /// The type of an annotation that contains this position, such as
    /// `Node[int] | None` within `Node[int]`, compiled when a value reaches
    /// it. Never a union member, so selecting a member needs no lookup.
    Ref(Py<PyAny>),
}

impl Type {
    /// Builds a type from the nested tuples `hessian2._type_plan` returns;
    /// `None` means [`Type::Any`].
    pub fn parse(plan: &Bound<'_, PyAny>) -> PyResult<Self> {
        if plan.is_none() {
            return Ok(Self::Any);
        }
        if let Ok(tag) = plan.cast::<PyString>() {
            return match tag.to_str()? {
                "none" => Ok(Self::Null),
                other => Err(unknown(other)),
            };
        }
        let plan = plan.cast::<PyTuple>()?;
        let members = |index: usize| -> PyResult<Vec<Self>> {
            plan.get_item(index)?
                .try_iter()?
                .map(|item| Self::parse(&item?))
                .collect()
        };
        let boxed = |index: usize| Ok::<_, PyErr>(Box::new(Self::parse(&plan.get_item(index)?)?));
        let by_key = |index: usize| -> PyResult<HashMap<String, Self>> {
            let mut types = HashMap::new();
            for (key, value) in plan.get_item(index)?.cast::<PyDict>()?.iter() {
                types.insert(
                    key.cast::<PyString>()?.to_str()?.to_owned(),
                    Self::parse(&value)?,
                );
            }
            Ok(types)
        };
        Ok(match plan.get_item(0)?.cast::<PyString>()?.to_str()? {
            "is" => {
                let fields = if plan.len() > 2 {
                    Some(Arc::new(by_key(2)?))
                } else {
                    None
                };
                Self::Is(plan.get_item(1)?.cast_into::<PyType>()?.unbind(), fields)
            }
            "items" => {
                let items = match plan.get_item(1)?.cast::<PyString>()?.to_str()? {
                    "list" => Items::List,
                    "tuple" => Items::Tuple,
                    "set" => Items::Set,
                    "frozenset" => Items::FrozenSet,
                    other => return Err(unknown(other)),
                };
                Self::Items(items, boxed(2)?)
            }
            "datetime" => Self::DateTime(plan.get_item(1)?.extract()?),
            "tuple" => Self::Tuple(members(1)?),
            "map" => Self::Map(boxed(1)?, boxed(2)?),
            "keys" => Self::Keys(by_key(1)?, plan.get_item(2)?.extract()?),
            "union" => Self::Union(members(1)?),
            "ref" => Self::Ref(plan.get_item(1)?.unbind()),
            other => return Err(unknown(other)),
        })
    }

    /// Returns the type as an annotation would spell it, for error messages.
    pub fn describe(&self, py: Python<'_>) -> String {
        let all =
            |types: &[Self]| -> Vec<String> { types.iter().map(|t| t.describe(py)).collect() };
        match self {
            Self::Any => "Any".to_owned(),
            Self::Null => "None".to_owned(),
            Self::DateTime(aware) => datetime_kind(*aware).to_owned(),
            Self::Is(ty, _) => ty
                .bind(py)
                .qualname()
                .map_or_else(|_| "?".to_owned(), |name| name.to_string()),
            Self::Items(items, item) => {
                let name = match items {
                    Items::List => "list",
                    Items::Tuple => "tuple",
                    Items::Set => "set",
                    Items::FrozenSet => "frozenset",
                };
                match (items, &**item) {
                    (_, Self::Any) => name.to_owned(),
                    (Items::Tuple, item) => format!("tuple[{}, ...]", item.describe(py)),
                    (_, item) => format!("{name}[{}]", item.describe(py)),
                }
            }
            Self::Tuple(items) => format!("tuple[{}]", all(items).join(", ")),
            Self::Map(..) | Self::Keys(..) => "dict".to_owned(),
            Self::Union(members) => all(members).join(" | "),
            Self::Ref(annotation) => annotation.bind(py).to_string(),
        }
    }

    /// Returns whether `None` is a value of the type.
    pub fn allows_null(&self) -> bool {
        self.members()
            .iter()
            .any(|member| matches!(member, Self::Null))
    }

    /// Returns the type, or the first member of a union, that `pick` selects.
    pub fn member(&self, pick: impl Fn(&Self) -> bool) -> Option<&Self> {
        self.members().iter().find(|member| pick(member))
    }

    /// Returns the classes the type, or the members of a union, name, with
    /// their field types where given.
    pub fn classes(&self) -> impl Iterator<Item = (&Py<PyType>, Option<&Fields>)> {
        self.members().iter().filter_map(|member| match member {
            Self::Is(ty, fields) => Some((ty, fields.as_deref())),
            _ => None,
        })
    }

    /// Returns whether the type, or a member of a union, is a
    /// [`Type::DateTime`] that `value` fits.
    pub fn holds_datetime(&self, value: &Bound<'_, PyAny>) -> bool {
        let aware = aware_datetime(value);
        aware.is_some()
            && self
                .members()
                .iter()
                .any(|member| matches!(member, Self::DateTime(a) if Some(*a) == aware))
    }

    /// Returns whether the type, or a member of a union, is a
    /// [`Type::DateTime`].
    pub fn takes_datetimes(&self) -> bool {
        self.members()
            .iter()
            .any(|member| matches!(member, Self::DateTime(_)))
    }

    fn members(&self) -> &[Self] {
        match self {
            Self::Union(members) => members,
            other => std::slice::from_ref(other),
        }
    }

    /// Returns whether a value already decoded, as a back-reference is,
    /// fits the type, checking its contents as well.
    ///
    /// `checked` holds the (value, type) pairs found to fit before, which
    /// fit again without a walk; pairs this check finds are added when the
    /// value fits. A value the check reaches
    /// again, through a cycle, counts as fitting on the second visit.
    /// Instances of classes are checked by class, and by the fields that
    /// type arguments give: their own fields were checked when they were
    /// built. An instance still being filled has its fields checked once
    /// filled, by [`Refs::filled`].
    pub fn holds(
        &self,
        value: &Bound<'_, PyAny>,
        checked: &mut AddressSet,
        refs: &mut Refs<'_>,
    ) -> bool {
        let mut seen = AddressSet::default();
        let fits = self.holds_in(value, &mut seen, checked, refs);
        if fits {
            checked.extend(seen);
        }
        fits
    }

    fn holds_in(
        &self,
        value: &Bound<'_, PyAny>,
        seen: &mut AddressSet,
        checked: &AddressSet,
        refs: &mut Refs<'_>,
    ) -> bool {
        let pair = (value.as_ptr() as usize, std::ptr::from_ref(self) as usize);
        if checked.contains(&pair) || !seen.insert(pair) {
            return true;
        }
        let all = |items: &Bound<'_, PyAny>, ty: &Self, seen: &mut AddressSet, refs: &mut Refs| {
            items.try_iter().is_ok_and(|iter| {
                iter.flatten()
                    .all(|item| ty.holds_in(&item, seen, checked, refs))
            })
        };
        match self {
            Self::Any => true,
            Self::Null => value.is_none(),
            Self::DateTime(aware) => aware_datetime(value) == Some(*aware),
            Self::Is(ty, fields) => {
                accepts(ty.bind(value.py()), value)
                    && match fields {
                        Some(fields) if refs.defer(value, fields) => true,
                        _ => fields_hold(value, fields.as_deref(), seen, checked, refs),
                    }
            }
            Self::Items(items, item) => {
                let exact = match items {
                    Items::List => value.is_exact_instance_of::<PyList>(),
                    Items::Tuple => value.is_exact_instance_of::<PyTuple>(),
                    Items::Set => value.is_exact_instance_of::<PySet>(),
                    Items::FrozenSet => value.is_exact_instance_of::<PyFrozenSet>(),
                };
                exact && all(value, item, seen, refs)
            }
            Self::Tuple(types) => value.cast::<PyTuple>().is_ok_and(|tuple| {
                value.is_exact_instance_of::<PyTuple>()
                    && tuple.len() == types.len()
                    && tuple
                        .iter()
                        .zip(types)
                        .all(|(item, ty)| ty.holds_in(&item, seen, checked, refs))
            }),
            Self::Map(key, item) => value.cast::<PyDict>().is_ok_and(|dict| {
                value.is_exact_instance_of::<PyDict>()
                    && dict.iter().all(|(k, v)| {
                        key.holds_in(&k, seen, checked, refs)
                            && item.holds_in(&v, seen, checked, refs)
                    })
            }),
            Self::Keys(keys, required) => value.cast::<PyDict>().is_ok_and(|dict| {
                value.is_exact_instance_of::<PyDict>()
                    && required
                        .iter()
                        .all(|key| dict.contains(key.as_str()).unwrap_or(false))
                    && dict.iter().all(|(k, v)| {
                        let ty = k
                            .cast::<PyString>()
                            .ok()
                            .and_then(|k| keys.get(k.to_str().ok()?));
                        ty.is_none_or(|ty| ty.holds_in(&v, seen, checked, refs))
                    })
            }),
            Self::Union(members) => members
                .iter()
                .any(|member| member.holds_in(value, seen, checked, refs)),
            Self::Ref(annotation) => refs
                .resolve(annotation)
                .is_ok_and(|ty| ty.get().0.holds_in(value, seen, checked, refs)),
        }
    }

    /// Visits the classes the type refers to, for an owner's `__traverse__`.
    pub fn traverse(&self, visit: &PyVisit<'_>) -> Result<(), PyTraverseError> {
        match self {
            Self::Any | Self::Null | Self::DateTime(_) => Ok(()),
            Self::Is(ty, fields) => {
                visit.call(ty)?;
                fields
                    .iter()
                    .flat_map(|fields| fields.values())
                    .try_for_each(|t| t.traverse(visit))
            }
            Self::Items(_, item) => item.traverse(visit),
            Self::Map(key, value) => {
                key.traverse(visit)?;
                value.traverse(visit)
            }
            Self::Tuple(types) | Self::Union(types) => {
                types.iter().try_for_each(|t| t.traverse(visit))
            }
            Self::Keys(keys, _) => keys.values().try_for_each(|t| t.traverse(visit)),
            Self::Ref(annotation) => visit.call(annotation),
        }
    }
}

/// Returns whether the fields of `value` hold the types `fields` gives them.
fn fields_hold(
    value: &Bound<'_, PyAny>,
    fields: Option<&Fields>,
    seen: &mut AddressSet,
    checked: &AddressSet,
    refs: &mut Refs<'_>,
) -> bool {
    fields
        .iter()
        .flat_map(|fields| fields.iter())
        .all(|(attr, ty)| {
            value
                .getattr(attr.as_str())
                .is_ok_and(|field| ty.holds_in(&field, seen, checked, refs))
        })
}

/// Returns whether a value built by application code, such as a
/// converter's result, is an instance of `ty` whose fields hold the types
/// `fields` gives them.
pub fn instance_holds(
    ty: &Bound<'_, PyType>,
    fields: Option<&Fields>,
    value: &Bound<'_, PyAny>,
    refs: &mut Refs<'_>,
) -> bool {
    accepts(ty, value)
        && fields_hold(
            value,
            fields,
            &mut AddressSet::default(),
            &AddressSet::default(),
            refs,
        )
}

/// Returns whether `value` is an aware `datetime`, one with a UTC offset,
/// or `None` when it is no `datetime`.
pub fn aware_datetime(value: &Bound<'_, PyAny>) -> Option<bool> {
    let value = value.cast::<PyDateTime>().ok()?;
    let Some(tzinfo) = value.get_tzinfo() else {
        return Some(false);
    };
    Some(crate::time::utc_offset(&tzinfo, value.clone().into_any()).is_ok_and(|o| o.is_some()))
}

/// Returns how a message names a datetime that is `aware` or not.
pub fn datetime_kind(aware: bool) -> &'static str {
    if aware {
        "aware datetime"
    } else {
        "naive datetime"
    }
}

fn unknown(kind: &str) -> PyErr {
    PyValueError::new_err(format!("unknown type plan {kind:?}"))
}

/// Returns whether a decoded scalar or object is an instance of `ty`, as
/// msgspec's strict mode judges it: a `bool` is not an `int`, and a
/// `datetime` is not a `date`.
pub fn accepts(ty: &Bound<'_, PyType>, value: &Bound<'_, PyAny>) -> bool {
    let py = ty.py();
    if ty.is(py.get_type::<PyInt>()) {
        return value.is_instance_of::<PyInt>() && !value.is_instance_of::<PyBool>();
    }
    if ty.is(py.get_type::<PyDate>()) {
        return value.is_instance_of::<PyDate>() && !value.is_instance_of::<PyDateTime>();
    }
    value.is_instance(ty).unwrap_or(false)
}

/// Returns whether values of class `found` fit where `ty` is expected, by
/// the rules of [`accepts`] and [`convert`], without building one.
pub fn admits(ty: &Bound<'_, PyType>, found: &Bound<'_, PyType>) -> bool {
    let py = ty.py();
    let int = py.get_type::<PyInt>();
    let is_int = found.is_subclass(&int).unwrap_or(false)
        && !found.is_subclass(&py.get_type::<PyBool>()).unwrap_or(false);
    if ty.is(&int) {
        return is_int;
    }
    if ty.is(py.get_type::<PyFloat>()) && is_int {
        return true;
    }
    if ty.is(py.get_type::<PyDate>()) {
        return found.is_subclass(ty).unwrap_or(false)
            && !found
                .is_subclass(&py.get_type::<PyDateTime>())
                .unwrap_or(false);
    }
    found.is_subclass(ty).unwrap_or(false)
}

/// Returns `value` as an instance of `ty`, converting an `int` where `ty`
/// is `float` as msgspec does, or `None` when it is not one.
pub fn convert<'py>(
    ty: &Bound<'py, PyType>,
    value: Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let py = ty.py();
    if ty.is(py.get_type::<PyFloat>())
        && value.is_instance_of::<PyInt>()
        && !value.is_instance_of::<PyBool>()
    {
        // An int beyond float's range fails here, as float(int) does.
        return ty.call1((value,)).map(Some).map_err(|error| {
            crate::DecodeError::new_err(format!("cannot decode as float: {error}"))
        });
    }
    Ok(accepts(ty, &value).then_some(value))
}

/// A compiled type, cached per annotation so each call only looks it up.
#[pyclass(frozen, name = "_Type", module = "hessian2._core")]
pub struct TypeObject(pub Type);

#[pymethods]
impl TypeObject {
    #[new]
    fn new(plan: &Bound<'_, PyAny>) -> PyResult<Self> {
        Ok(Self(Type::parse(plan)?))
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        self.0.traverse(&visit)
    }
}

/// Annotations compiled so far, shared by every codec: a type depends only
/// on the annotation.
static TYPES: PyOnceLock<Py<PyDict>> = PyOnceLock::new();

/// Returns the type for the annotation `annotation`, compiling it with the
/// codec's `_type` method on first use.
///
/// # Errors
///
/// Propagates errors from `_type`, such as an annotation it cannot resolve.
pub fn lookup<'py>(
    codec: &Bound<'py, PyAny>,
    annotation: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, TypeObject>> {
    crate::hint::cached(
        &TYPES,
        codec,
        annotation,
        pyo3::intern!(codec.py(), "_type"),
    )
}

/// What back-reference checks share in one decode: the types
/// [`Type::Ref`] annotations resolve to, kept alive so the type addresses
/// the checks remember stay theirs, and the instances being filled.
pub struct Refs<'py> {
    codec: Bound<'py, PyAny>,
    types: AddressMap<Bound<'py, TypeObject>>,
    /// The field types each instance being filled must also hold once
    /// filled, by its address: a back-reference to it from within, such as
    /// a child's `parent`, cannot check fields not yet read.
    filling: AddressMap<Vec<Arc<Fields>>>,
}

impl<'py> Refs<'py> {
    pub fn new(codec: &Bound<'py, PyAny>) -> Self {
        Self {
            codec: codec.clone(),
            types: AddressMap::default(),
            filling: AddressMap::default(),
        }
    }

    /// Returns the [`Refs::new`] that resolves `types` as an earlier
    /// decode of the stream, [`Refs::suspend`], did.
    pub fn resume(codec: &Bound<'py, PyAny>, types: AddressMap<Py<TypeObject>>) -> Self {
        let py = codec.py();
        Self {
            types: types
                .into_iter()
                .map(|(key, ty)| (key, ty.into_bound(py)))
                .collect(),
            ..Self::new(codec)
        }
    }

    /// Returns the resolved types, for a later [`Refs::resume`]. Nothing is
    /// being filled between successful decodes.
    pub fn suspend(self) -> AddressMap<Py<TypeObject>> {
        self.types
            .into_iter()
            .map(|(key, ty)| (key, ty.unbind()))
            .collect()
    }

    /// Marks `instance` as being filled, until [`Refs::filled`].
    pub fn fill(&mut self, instance: &Bound<'_, PyAny>) {
        self.filling.insert(instance.as_ptr() as usize, Vec::new());
    }

    /// Returns whether `value` is being filled, recording that it must hold
    /// `fields` once filled if so.
    fn defer(&mut self, value: &Bound<'_, PyAny>, fields: &Arc<Fields>) -> bool {
        self.filling
            .get_mut(&(value.as_ptr() as usize))
            .map(|deferred| deferred.push(fields.clone()))
            .is_some()
    }

    /// Returns whether `instance`, now filled, holds the field types
    /// back-references to it expected while it was being filled.
    pub fn filled(&mut self, instance: &Bound<'_, PyAny>, checked: &mut AddressSet) -> bool {
        let deferred = self
            .filling
            .remove(&(instance.as_ptr() as usize))
            .unwrap_or_default();
        deferred.iter().all(|fields| {
            let mut seen = AddressSet::default();
            let fits = fields_hold(instance, Some(fields), &mut seen, checked, self);
            if fits {
                checked.extend(seen);
            }
            fits
        })
    }

    /// Returns the type of a [`Type::Ref`]'s annotation.
    ///
    /// # Errors
    ///
    /// As for [`lookup`].
    pub fn resolve(&mut self, annotation: &Py<PyAny>) -> PyResult<Bound<'py, TypeObject>> {
        // The annotation lives as long as the type holding the reference.
        let key = annotation.as_ptr() as usize;
        if let Some(ty) = self.types.get(&key) {
            return Ok(ty.clone());
        }
        let ty = lookup(&self.codec, annotation.bind(self.codec.py()))?;
        self.types.insert(key, ty.clone());
        Ok(ty)
    }
}
