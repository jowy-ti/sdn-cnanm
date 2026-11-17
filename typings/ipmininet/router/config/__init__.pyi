from typing import Any


class RouterConfig:
    daemons: list[Any]

    def __init__(self, node: Any, *args: Any, **kwargs: Any) -> None: ...


class OSPF: ...


class OSPF6: ...
