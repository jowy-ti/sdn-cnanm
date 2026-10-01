from collections.abc import Callable, Sequence
from typing import Literal, overload

from .link import Intf, Link
from .node import Controller, Host, Switch
from .topo import Topo

class Mininet:
    hosts: list[Host]
    switches: list[Switch]
    controllers: list[Controller]
    links: list[Link]
    def __init__(
        self,
        topo: Topo | None = None,
        switch: type[Switch] = ...,
        host: type[Host] = ...,
        controller: type[Controller] | Callable[[str], Controller] | None = ...,
        link: type[Link] = ...,
        intf: type[Intf] = ...,
        build: bool = ...,
        xterms: bool = ...,
        cleanup: bool = ...,
        ipBase: str = ...,
        inNamespace: bool = ...,
        autoSetMacs: bool = ...,
        autoStaticArp: bool = ...,
        autoPinCpus: bool = ...,
        listenPort: int | None = ...,
        waitConnected: bool = ...,
    ) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def pingAll(self, timeout: float | None = None) -> float | Literal[0]: ...
    def ping(
        self, hosts: Sequence[Host] | None = None, timeout: str | None = None
    ) -> float | None: ...
    def iperf(
        self,
        hosts: Sequence[Host] | None = None,
        l4Type: str = "TCP",
        udpBw: str = "10M",
        fmt: str | None = None,
        seconds: int = 5,
        port: int = 5001,
    ) -> tuple[str, str]: ...
    @overload
    def get(self, name: str) -> Host: ...
    @overload
    def get(self, name: str, arg2: str, *args: str) -> list[Host]: ...
