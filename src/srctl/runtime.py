"""Start and stop the embedded controller alongside the Mininet lab."""

from __future__ import annotations

import sys
import threading
from typing import Any

from .control import ControlError, ControlServer, default_socket_path
from .models import ControllerError
from .service import SaltController
from .southbound import LiveSouthbound


class ControllerRuntime:
    """Own the controller instance and its control socket for one lab run."""

    def __init__(
        self,
        net: Any,
        topology: Any,
        socket_path: str | None = None,
        reconcile_interval: float = 5.0,
    ) -> None:
        self.socket_path = socket_path or default_socket_path()
        self.reconcile_interval = reconcile_interval
        self.controller = SaltController(topology, LiveSouthbound(net))
        self.control = ControlServer(self.controller, self.socket_path)
        self._stop = threading.Event()
        self._reconcile_thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            self.control.start()
        except (OSError, ControlError) as exc:
            print(f"controller: cannot create {self.socket_path}: {exc}", file=sys.stderr)
        else:
            print(f"controller: control socket {self.socket_path}")

        try:
            self.controller.start()
        except ControllerError as exc:
            print(f"controller: cannot load YAML policies: {exc}", file=sys.stderr)

        self._reconcile_thread = threading.Thread(
            target=self._reconcile_loop, name="srctl-reconcile", daemon=True
        )
        self._reconcile_thread.start()

    def _reconcile_loop(self) -> None:
        last = ""
        while not self._stop.wait(self.reconcile_interval):
            result = self.controller.reconcile()
            status = self.controller.status()
            policy_errors: dict[str, str] = status["policy_errors"]
            interesting = [
                action for action in result.actions if action.kind != "noop"
            ]
            signature = repr(
                (
                    [
                        (action.kind, action.ingress, action.prefix)
                        for action in interesting
                    ],
                    list(result.errors),
                    policy_errors,
                )
            )
            if signature != last and (
                interesting or result.errors or policy_errors
            ):
                for action in interesting:
                    print(
                        f"controller: {action.kind} {action.prefix} on "
                        f"{action.ingress} ({action.detail})"
                    )
                for error in result.errors:
                    print(f"controller: reconcile error: {error}", file=sys.stderr)
                for policy_id, error in policy_errors.items():
                    print(
                        f"controller: policy {policy_id}: {error}",
                        file=sys.stderr,
                    )
                last = signature

    def stop(self) -> None:
        self._stop.set()
        if self._reconcile_thread is not None:
            self._reconcile_thread.join(timeout=5)
            self._reconcile_thread = None
        self.control.stop()
