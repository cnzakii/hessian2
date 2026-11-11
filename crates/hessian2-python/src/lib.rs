//! Python extension module for `hessian2`.

#![forbid(unsafe_code)]

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyModule;

mod address;
mod builtins;
mod codec;
mod decode;
mod encode;
mod object;
mod time;

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

#[pymodule(gil_used = false)]
fn _core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = module.py();
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add("DecodeError", py.get_type::<DecodeError>())?;
    module.add("EncodeError", py.get_type::<EncodeError>())?;
    module.add_class::<codec::Codec>()?;
    module.add_class::<object::HessianObject>()?;
    Ok(())
}
