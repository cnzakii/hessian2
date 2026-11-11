//! Conversion between Hessian dates and `datetime` objects.
//!
//! A Hessian date is milliseconds since the Unix epoch, as `java.util.Date`
//! holds; it decodes as an aware UTC `datetime`.

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{
    PyDateAccess, PyDateTime, PyDelta, PyDeltaAccess, PyTimeAccess, PyTzInfo, PyTzInfoAccess,
};

use crate::DecodeError;

const MICROS_PER_DAY: i64 = 86_400_000_000;

/// Returns the days from 1970-01-01 to a proleptic Gregorian date, by Howard
/// Hinnant's `days_from_civil`
/// (<https://howardhinnant.github.io/date_algorithms.html#days_from_civil>).
fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = year.div_euclid(400);
    let year_of_era = year - era * 400;
    let day_of_year = (153 * (month + if month > 2 { -3 } else { 9 }) + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    era * 146_097 + day_of_era - 719_468
}

/// Returns the proleptic Gregorian date `days` after 1970-01-01 (Howard
/// Hinnant's `civil_from_days`).
fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let days = days + 719_468;
    let era = days.div_euclid(146_097);
    let day_of_era = days - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_index = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_index + 2) / 5 + 1;
    let month = if month_index < 10 {
        month_index + 3
    } else {
        month_index - 9
    };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

/// Returns an aware UTC `datetime` for milliseconds since the epoch.
///
/// # Errors
///
/// Raises `DecodeError` outside the years 1 to 9999 that `datetime` covers.
pub fn from_millis(py: Python<'_>, millis: i64) -> PyResult<Bound<'_, PyAny>> {
    from_micros(
        py,
        i64::try_from(i128::from(millis) * 1000).unwrap_or(i64::MAX),
    )
    .map(Bound::into_any)
    .map_err(|_| DecodeError::new_err(format!("date {millis} ms is outside the datetime range")))
}

/// Returns an aware UTC `datetime` for microseconds since the epoch.
///
/// # Errors
///
/// Raises `ValueError` outside the years 1 to 9999.
pub fn from_micros(py: Python<'_>, micros: i64) -> PyResult<Bound<'_, PyDateTime>> {
    let days = micros.div_euclid(MICROS_PER_DAY);
    let rest = micros.rem_euclid(MICROS_PER_DAY);
    let (year, month, day) = civil_from_days(days);
    if !(1..=9999).contains(&year) {
        return Err(pyo3::exceptions::PyValueError::new_err("year out of range"));
    }
    let seconds = rest / 1_000_000;
    PyDateTime::new(
        py,
        year as i32,
        month as u8,
        day as u8,
        (seconds / 3600) as u8,
        (seconds / 60 % 60) as u8,
        (seconds % 60) as u8,
        (rest % 1_000_000) as u32,
        Some(&PyTzInfo::utc(py)?.to_owned()),
    )
}

/// Returns the `datetime` of the date fields of `date` and the time fields of
/// `time`, in `tzinfo`.
pub fn combine<'py>(
    py: Python<'py>,
    date: &impl PyDateAccess,
    time: &impl PyTimeAccess,
    tzinfo: Option<&Bound<'py, PyTzInfo>>,
) -> PyResult<Bound<'py, PyDateTime>> {
    PyDateTime::new(
        py,
        date.get_year(),
        date.get_month(),
        date.get_day(),
        time.get_hour(),
        time.get_minute(),
        time.get_second(),
        time.get_microsecond(),
        tzinfo,
    )
}

/// Returns the microseconds from the epoch to a `datetime`'s fields read as
/// UTC, ignoring its `tzinfo`.
fn local_micros(value: &Bound<'_, PyDateTime>) -> i64 {
    days_from_civil(
        i64::from(value.get_year()),
        i64::from(value.get_month()),
        i64::from(value.get_day()),
    ) * MICROS_PER_DAY
        + (i64::from(value.get_hour()) * 3600
            + i64::from(value.get_minute()) * 60
            + i64::from(value.get_second()))
            * 1_000_000
        + i64::from(value.get_microsecond())
}

/// Returns the offset `tzinfo.utcoffset(arg)` gives, in microseconds, or
/// `None` when it gives `None`.
///
/// The `tzinfo` is asked directly, as `datetime.timestamp` and `astimezone`
/// do, so a `datetime` subclass overriding `utcoffset` does not change it.
///
/// # Errors
///
/// Propagates an error from `utcoffset`, and raises `TypeError` or
/// `ValueError` when it returns something other than a `timedelta` strictly
/// within a day, as `datetime.utcoffset` does.
pub fn utc_offset<'py>(
    tzinfo: &Bound<'py, PyTzInfo>,
    arg: Bound<'py, PyAny>,
) -> PyResult<Option<i64>> {
    let py = tzinfo.py();
    let offset = tzinfo.call_method1(intern!(py, "utcoffset"), (arg,))?;
    if offset.is_none() {
        return Ok(None);
    }
    let offset = offset.cast_into::<PyDelta>().map_err(|error| {
        PyTypeError::new_err(format!("utcoffset() must return a timedelta: {error}"))
    })?;
    let micros = i64::from(offset.get_days()) * MICROS_PER_DAY
        + i64::from(offset.get_seconds()) * 1_000_000
        + i64::from(offset.get_microseconds());
    if micros.abs() >= MICROS_PER_DAY {
        return Err(PyValueError::new_err(
            "utcoffset() must be strictly between -timedelta(hours=24) and timedelta(hours=24)",
        ));
    }
    Ok(Some(micros))
}

/// Returns milliseconds since the epoch for an aware `datetime`, dropping
/// sub-millisecond precision, or `None` for a naive one.
///
/// # Errors
///
/// Propagates an error from [`utc_offset`].
pub fn to_millis(value: &Bound<'_, PyDateTime>) -> PyResult<Option<i64>> {
    let py = value.py();
    let Some(tzinfo) = value.get_tzinfo() else {
        return Ok(None);
    };
    let local = local_micros(value);
    // datetime.UTC needs no call into Python.
    if tzinfo.is(PyTzInfo::utc(py)?) {
        return Ok(Some(local.div_euclid(1000)));
    }
    Ok(utc_offset(&tzinfo, value.clone().into_any())?
        .map(|offset| (local - offset).div_euclid(1000)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn civil_conversion_round_trips() {
        assert_eq!(days_from_civil(1970, 1, 1), 0);
        assert_eq!(days_from_civil(2000, 3, 1), 11_017);
        assert_eq!(days_from_civil(1, 1, 1), -719_162);
        assert_eq!(civil_from_days(11_016), (2000, 2, 29));
        assert_eq!(civil_from_days(2_932_896), (9999, 12, 31));
        for days in [-719_162, -1, 0, 1, 11_016, 11_017, 2_932_896] {
            let (y, m, d) = civil_from_days(days);
            assert_eq!(days_from_civil(y, m, d), days);
        }
    }
}
