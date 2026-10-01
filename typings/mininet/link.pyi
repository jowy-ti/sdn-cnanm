from typing import Any

from .node import Node

class Intf:
    name: str
    node: Node
    link: Any

class Link:
    intf1: Intf
    intf2: Intf
    node1: Node
    node2: Node
    def __init__(self, node1: Node, node2: Node, **params: Any) -> None: ...

class TCIntf(Intf): ...
class TCLink(Link): ...
