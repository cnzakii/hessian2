//! Python extension module for `hessian2`.

#![forbid(unsafe_code)]

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::type_object::PyTypeInfo;
use pyo3::types::PyModule;

mod address;
mod builtins;
mod codec;
mod decode;
mod encode;
mod hint;
mod object;
mod reader;
mod time;
mod typed;

pyo3::create_exception!(
    hessian2,
    DecodeError,
    PyValueError,
    "The input is not a valid Hessian stream for this codec."
);
pyo3::create_exception!(
    hessian2,
    EncodeError,
    PyTypeError,
    "A value has no Hessian form under this codec."
);

/// Returns an error raised by user code that encoding or decoding ran, such
/// as a converter or `__post_init__`: a `ValueError` or `TypeError` becomes
/// `wrapper`'s exception with the original as its cause, as msgspec treats
/// errors from its hooks; others, such as a bug's `AttributeError`, are
/// returned unchanged.
fn from_user<E: PyTypeInfo>(py: Python<'_>, error: PyErr, wrapper: fn(String) -> PyErr) -> PyErr {
    // Only the operation's own error passes through: an EncodeError from a
    // decode callback is still a decode failure.
    if error.is_instance_of::<E>(py)
        || !(error.is_instance_of::<PyValueError>(py) || error.is_instance_of::<PyTypeError>(py))
    {
        return error;
    }
    let wrapped = wrapper(error.to_string());
    wrapped.set_cause(py, Some(error));
    wrapped
}

/// [`from_user`] for decoding: invalid input as user code judged it.
pub fn decode_user_error(py: Python<'_>, error: PyErr) -> PyErr {
    from_user::<DecodeError>(py, error, DecodeError::new_err)
}

/// [`from_user`] for encoding: a value user code could not convert.
pub fn encode_user_error(py: Python<'_>, error: PyErr) -> PyErr {
    from_user::<EncodeError>(py, error, EncodeError::new_err)
}

#[pymodule(gil_used = false)]
fn _core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = module.py();
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add("DecodeError", py.get_type::<DecodeError>())?;
    module.add("EncodeError", py.get_type::<EncodeError>())?;
    module.add_class::<codec::Codec>()?;
    module.add_class::<object::HessianObject>()?;
    module.add_class::<object::Typed>()?;
    module.add_class::<hint::HintObject>()?;
    module.add_class::<typed::TypeObject>()?;
    module.add_class::<reader::Reader>()?;
    Ok(())
}
