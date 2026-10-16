"""Minimal hand-written stub for PyYAML.

PyYAML ships no ``py.typed`` marker, so strict mode would flag every use of
``yaml.safe_load`` as an untyped import.  Only the handful of entry points the
lab uses are declared here.
"""

from typing import Any


class YAMLError(Exception): ...


def safe_load(stream: Any, **kwargs: Any) -> Any: ...
def safe_load_all(stream: Any, **kwargs: Any) -> Any: ...
def dump(data: Any, **kwargs: Any) -> str: ...
