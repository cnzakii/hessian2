"""Public contracts for malformed."""

from __future__ import annotations

import re

import hessian2
import pytest
from hessian2 import DecodeError


def test_trailing_data() -> None:
    with pytest.raises(DecodeError, match=re.escape("trailing")):
        hessian2.loads(b"\x90\x90")


def test_decode_error_forwards_the_core_byte_offset():
    with pytest.raises(
        DecodeError, match=re.escape("unexpected end of input at byte 2")
    ):
        hessian2.loads(b"I\0")
