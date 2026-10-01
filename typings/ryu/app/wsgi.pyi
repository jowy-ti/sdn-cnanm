from collections.abc import Callable, Mapping, Sequence
from typing import Any

class Request:
    body: bytes
    json: Any

class Response:
    def __init__(
        self,
        status: str = ...,
        text: str = ...,
        body: str | bytes = ...,
        content_type: str = ...,
        **kwargs: Any,
    ) -> None: ...

def route(
    name: str,
    path: str,
    methods: Sequence[str] | None = ...,
    requirements: Mapping[str, str] | None = ...,
) -> Callable[[Callable[..., Response]], Callable[..., Response]]: ...

class ControllerBase:
    def __init__(
        self, req: Request, link: Any, data: dict[str, Any], **config: Any
    ) -> None: ...

class WSGIApplication:
    def __init__(self, **config: Any) -> None: ...
    def register(
        self, controller: type[ControllerBase], data: dict[str, Any] | None = ...
    ) -> None: ...
