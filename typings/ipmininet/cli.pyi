from typing import Any

from .ipnet import IPNet


class IPCLI:
    def __init__(self, net: IPNet, stdin: Any = ..., script: Any = ...) -> None: ...
