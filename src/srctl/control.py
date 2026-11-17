"""Unix-domain-socket control channel between ``srctl`` and the controller."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
from collections.abc import Mapping
from typing import Any, cast

from .models import (
    ApplyResult,
    ConflictError,
    ControllerError,
    NotFoundError,
    PolicyView,
    ReconcileResult,
)
from .service import SaltController
from .southbound import SouthboundError

_MAX_REQUEST = 4 * 1024 * 1024
_TIMEOUT = 30.0


class ControlError(RuntimeError):
    """A control-socket request failed."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def default_socket_path() -> str:
    """Return the per-user socket path used by both server and client."""
    override = os.environ.get("SRCTL_SOCKET")
    if override:
        return override
    uid = os.getuid()
    if os.geteuid() == 0:
        sudo_uid = os.environ.get("SUDO_UID")
        if sudo_uid:
            try:
                uid = int(sudo_uid)
            except ValueError:
                pass
    return f"/tmp/srctl-{uid}.sock"


class ControlServer:
    """Serve controller methods over a Unix stream socket."""

    def __init__(self, controller: SaltController, path: str | None = None) -> None:
        self.controller = controller
        self.path = path or default_socket_path()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._owns_socket = False

    def start(self) -> None:
        self._remove_stale_socket()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.path)
        self._owns_socket = True
        self._set_permissions()
        server.listen(8)
        server.settimeout(0.5)
        self._socket = server
        self._thread = threading.Thread(
            target=self._serve, name="srctl-control", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._owns_socket:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            self._owns_socket = False

    def _remove_stale_socket(self) -> None:
        try:
            mode = os.stat(self.path).st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(mode):
            raise ControlError(
                "invalid", f"{self.path} exists and is not a Unix socket"
            )
        os.unlink(self.path)

    def _set_permissions(self) -> None:
        if os.geteuid() == 0:
            sudo_uid = os.environ.get("SUDO_UID")
            if sudo_uid:
                try:
                    uid = int(sudo_uid)
                except ValueError:
                    uid = -1
                gid = -1
                sudo_gid = os.environ.get("SUDO_GID")
                if sudo_gid:
                    try:
                        gid = int(sudo_gid)
                    except ValueError:
                        gid = -1
                try:
                    os.chown(self.path, uid, gid)
                except OSError:
                    pass
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            server = self._socket
            if server is None:
                break
            try:
                connection, _address = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(_TIMEOUT)
                try:
                    request = _read_json(connection)
                except ControlError as exc:
                    _write_json(connection, _error(str(exc), exc.kind))
                    continue
                response = self._handle(request)
                try:
                    _write_json(connection, response)
                except OSError:
                    pass

    def _handle(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            return _error("request must be a JSON object", "invalid")
        mapping = cast("Mapping[str, Any]", request)
        method = mapping.get("method")
        params = mapping.get("params", {})
        if not isinstance(method, str):
            return _error("missing 'method'", "invalid")
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            return _error("'params' must be an object", "invalid")
        param_map = cast("Mapping[str, Any]", params)
        try:
            result = self._dispatch(method, dict(param_map))
        except (NotFoundError, ConflictError, ControllerError) as exc:
            kind = (
                "not_found"
                if isinstance(exc, NotFoundError)
                else "conflict"
                if isinstance(exc, ConflictError)
                else "invalid"
            )
            return _error(str(exc), kind)
        except SouthboundError as exc:
            return _error(str(exc), "dataplane")
        except Exception as exc:  # noqa: BLE001 - control boundary
            return _error(str(exc), "internal")
        return {"ok": True, "result": result}

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            return {"status": "ok"}
        if method == "status":
            return self.controller.status()
        if method == "topology":
            return self.controller.topology_dict()
        if method == "policies.list":
            return [_policy_dict(view) for view in self.controller.list_policies()]
        if method == "policies.apply":
            return _apply_dict(
                self.controller.apply_document(
                    _content(params),
                    prune=_prune(params),
                    dry_run=False,
                )
            )
        if method == "policies.diff":
            return _apply_dict(
                self.controller.apply_document(
                    _content(params),
                    prune=_prune(params),
                    dry_run=True,
                )
            )
        if method == "reconcile":
            return _result_dict(self.controller.reconcile())
        if method == "paths.compute":
            return self.controller.path(
                _str_param(params, "source"),
                _str_param(params, "destination"),
                _list_param(params, "via"),
                _list_param(params, "avoid"),
            )
        raise ControlError(f"unknown method {method!r}", "invalid")


class ControlClient:
    """Client for the controller's Unix socket."""

    def __init__(self, path: str | None = None, timeout: float = _TIMEOUT) -> None:
        self.path = path or default_socket_path()
        self.timeout = timeout

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        request = json.dumps(
            {"method": method, "params": dict(params or {})}
        ).encode() + b"\n"
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(self.timeout)
        try:
            client.connect(self.path)
            client.sendall(request)
            response = _read_json(client)
        except (OSError, TimeoutError) as exc:
            raise ControlError(
                "unavailable",
                f"cannot reach the controller at {self.path}: {exc}",
            ) from exc
        finally:
            client.close()
        if not isinstance(response, Mapping):
            raise ControlError("invalid", "malformed controller response")
        mapping = cast("Mapping[str, Any]", response)
        if not mapping.get("ok"):
            error = mapping.get("error", {})
            if isinstance(error, Mapping):
                error_map = cast("Mapping[str, Any]", error)
                message = str(error_map.get("message", "request failed"))
                kind = str(error_map.get("kind", "invalid"))
            else:
                message = "request failed"
                kind = "invalid"
            raise ControlError(kind, message)
        return mapping.get("result")


def _read_json(connection: socket.socket) -> Any:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = connection.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > _MAX_REQUEST:
            raise ControlError("request too large", "invalid")
    if not data:
        raise ControlError("empty request", "invalid")
    try:
        return json.loads(data.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlError(f"invalid JSON: {exc}", "invalid") from exc


def _write_json(connection: socket.socket, payload: Mapping[str, Any]) -> None:
    connection.sendall(json.dumps(payload).encode() + b"\n")


def _error(message: str, kind: str) -> dict[str, Any]:
    return {"ok": False, "error": {"kind": kind, "message": message}}


def _content(params: Mapping[str, Any]) -> str:
    content = params.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ControlError("'content' must be a non-empty policy document", "invalid")
    return content


def _prune(params: Mapping[str, Any]) -> bool:
    prune = params.get("prune", False)
    if not isinstance(prune, bool):
        raise ControlError("'prune' must be a boolean", "invalid")
    return prune


def _str_param(params: Mapping[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ControlError(f"'{key}' must be a non-empty string", "invalid")
    return value


def _list_param(params: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = params.get(key, [])
    if not isinstance(value, list):
        raise ControlError(f"'{key}' must be a list", "invalid")
    items: list[str] = []
    for item in cast("list[Any]", value):
        if not isinstance(item, str) or not item:
            raise ControlError(f"'{key}' must contain strings", "invalid")
        items.append(item)
    return tuple(items)


def _policy_dict(view: PolicyView) -> dict[str, Any]:
    return {
        "id": view.id,
        "ingress": view.ingress,
        "destination": view.destination,
        "segments": list(view.segments),
        "mode": view.mode,
        "metric": view.metric,
        "origin": view.origin,
        "status": view.status,
        "error": view.error,
    }


def _result_dict(result: ReconcileResult) -> dict[str, Any]:
    return {
        "actions": [
            {
                "kind": action.kind,
                "ingress": action.ingress,
                "prefix": action.prefix,
                "detail": action.detail,
            }
            for action in result.actions
        ],
        "errors": list(result.errors),
    }


def _apply_dict(result: ApplyResult) -> dict[str, Any]:
    payload = _result_dict(
        ReconcileResult(actions=result.actions, errors=result.errors)
    )
    payload["applied"] = list(result.applied)
    payload["removed"] = list(result.removed)
    payload["policy_errors"] = dict(result.policy_errors)
    return payload
