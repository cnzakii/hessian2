"""Check an installed release without importing the source checkout."""

from __future__ import annotations

import dataclasses
import datetime as dt
import sys
import sysconfig
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import hessian2


def main() -> None:
    assert hessian2.__version__ == sys.argv[1]
    assert Path(hessian2.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        assert not sys._is_gil_enabled()

    @hessian2.java_class("example.Address")
    @dataclasses.dataclass
    class Address:
        city: str

    @hessian2.java_class("example.Order")
    @dataclasses.dataclass
    class Order:
        home_address: Address
        delivery_address: Address
        amount: Decimal
        order_id: UUID
        created_at: dt.datetime

    codec = hessian2.Codec()
    codec.register(Order)

    address = Address("Hangzhou")
    order = Order(
        address,
        address,
        Decimal("19.90"),
        UUID("123e4567-e89b-12d3-a456-426614174000"),
        dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    received = codec.loads(codec.dumps(order))
    assert received == order
    assert received.home_address is received.delivery_address
    cyclic: list[object] = []
    cyclic.append(cyclic)
    decoded = hessian2.loads(hessian2.dumps(cyclic))
    assert decoded[0] is decoded
    assert hessian2.loads(hessian2.dumps("中文😀\ud800")) == "中文😀\ud800"
    print(f"Validated hessian2 {hessian2.__version__} from {hessian2.__file__}")


if __name__ == "__main__":
    main()
