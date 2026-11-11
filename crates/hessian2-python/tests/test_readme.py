"""Run the Python examples in the README, so it stays true."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

README = Path(__file__).parents[3] / "README.md"
EXAMPLES = re.findall(r"```python\n(.*?)```", README.read_text(), re.DOTALL)


OUTPUTS = [
    "alice\nHangzhou\nTrue\n",
    "['customer']\n",
    "header ready True\n",
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
    exec(compile(source, str(README), "exec"), {"__name__": __name__})
    assert capsys.readouterr().out == output
