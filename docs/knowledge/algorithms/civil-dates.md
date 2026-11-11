---
title: Civil dates from day counts
description: Converting between days since 1970-01-01 and proleptic Gregorian year, month, and day without tables or loops, and how Hessian dates map to Python and Java calendars.
topics: [algorithms, dates, calendar]
checked_at: 2026-11-11
---

# Civil Dates From Day Counts

Hessian dates are UTC milliseconds or minutes since 1970-01-01 (specification
§4.3). Turning them into calendar fields needs a day-count conversion.

## Algorithm

Literature: Howard Hinnant,
[chrono-Compatible Low-Level Date Algorithms](https://howardhinnant.github.io/date_algorithms.html),
functions `days_from_civil` and `civil_from_days`.

- Years are shifted to start on March 1, so February 29 is the last day of the
  shifted year and month lengths follow the `(153 * m + 2) / 5` pattern.
- Days are split into 400-year eras of 146097 days; within an era the year is
  `(doe - doe/1460 + doe/36524 - doe/146096) / 365`.
- Both directions use only integer arithmetic, no tables or loops, and are
  exact over the whole proleptic Gregorian calendar when the era division
  floors (the paper handles negative values explicitly; Rust's `div_euclid`
  does the same).

## Calendar Ranges

- Official guidance: Python's `datetime` uses the proleptic Gregorian calendar
  for years 1 through 9999 (`datetime.MINYEAR`, `datetime.MAXYEAR`), with
  microsecond resolution.
- Official guidance: Java's `java.util.Date` is an instant in milliseconds;
  `java.util.GregorianCalendar` switches to the Julian calendar before
  1582-10-15 by default, so Java's legacy calendar fields for earlier instants
  differ from the proleptic Gregorian fields of the same milliseconds.
- A 64-bit millisecond count spans about ±292 million years, far beyond
  Python's range.
