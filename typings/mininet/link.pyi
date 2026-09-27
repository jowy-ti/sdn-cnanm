from typing import Any

class Intf:
    name: str
    link: Any

class Link:
    intf1: Intf
    intf2: Intf
    def __init__(self, node1: Any, node2: Any, **params: Any) -> None: ...

class TCIntf(Intf): ...
class TCLink(Link): ...
