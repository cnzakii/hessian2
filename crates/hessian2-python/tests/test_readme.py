"""Run the Python examples in the README, so it stays true."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

README = Path(__file__).parents[3] / "README.md"
EXAMPLES = re.findall(
    r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.DOTALL
)


OUTPUTS = [
    "['customer']\n",
    "alice\nHangzhou\nTrue\n19.90 Decimal\n"
    "123e4567-e89b-12d3-a456-426614174000 UUID\n2026-01-01T12:00:00+00:00\n",
    "header ready True\n",
    "5 [7, 8] 2026-01-01T00:00:00+00:00\n[7, 8]\n",
    "",
    "NewPoint(x=3, y=4)\n",
    "1 9.5\n",
    "com.example.User\nHangzhou\nTrue\n",
]


@pytest.mark.parametrize(
    ("source", "output"),
    list(zip(EXAMPLES, OUTPUTS, strict=True)),
    ids=range(len(EXAMPLES)),
)
def test_readme_example(
    source: str, output: str, capsys: pytest.CaptureFixture[str]
) -> None:
    if "pydantic" in source and importlib.util.find_spec("pydantic") is None:
        pytest.skip("pydantic is not installed")
    # dataclasses look classes up through sys.modules, so borrow this module.
    # dont_inherit: the examples run without this file's future import, as a
    # reader's script would, so their annotations are not strings.
    code = compile(source, str(README), "exec", dont_inherit=True)
    exec(code, {"__name__": __name__})
    assert capsys.readouterr().out == output
