from collections.abc import Sequence
from typing import Any

from .ipnet import IPNet


class LocalSIDTable:
    num: int

    def __init__(self, node: Any, matching: Any = ...) -> None: ...
    def clean(self) -> None: ...


class SRv6Encap:
    ENCAP: str
    INLINE: str

    def __init__(
        self,
        net: IPNet,
        node: str,
        to: str = ...,
        through: Sequence[str] = ...,
        mode: str = ...,
        cost: int = ...,
    ) -> None: ...


class SRv6EndDX6Function:
    def __init__(
        self,
        net: IPNet,
        node: str,
        to: str = ...,
        nexthop: Any = ...,
        cost: int = ...,
        table: LocalSIDTable | None = ...,
    ) -> None: ...
