"""Southbound drivers: program SRv6 encapsulation routes on the lab nodes."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from typing import Any, Protocol

from .models import DesiredRoute, RouteState


class SouthboundError(RuntimeError):
    """The data plane rejected a controller command."""


class Southbound(Protocol):
    """Operations the controller needs from the data plane."""

    def dest_prefixes(self, node: str) -> tuple[str, ...]: ...

    def egress_dev(self, ingress: str, first_sid: str) -> str: ...

    def list_encap_routes(self, ingress: str) -> tuple[RouteState, ...]: ...

    def replace(self, ingress: str, route: DesiredRoute) -> None: ...

    def delete(self, ingress: str, route: RouteState) -> None: ...


_ROUTE_RE = re.compile(
    r"^(?P<prefix>\S+)\s+encap\s+seg6\s+mode\s+(?P<mode>\S+)\s+"
    r"segs\s+(?P<count>\d+)\s+\[\s*(?P<segs>[^\]]*?)\s*\]\s+"
    r"dev\s+(?P<dev>\S+)\s+metric\s+(?P<metric>\d+)"
)
_DEV_RE = re.compile(r"\bdev\s+(\S+)")


def _segments(text: str) -> tuple[str, ...]:
    return tuple(part for part in text.split() if part)


def _command(
    action: str, prefix: str, segments: tuple[str, ...], mode: str, metric: int, dev: str
) -> str:
    return (
        f"ip -6 route {action} {prefix} encap seg6 mode {mode} "
        f"segs {','.join(segments)} metric {metric} dev {dev}"
    )


class LiveSouthbound:
    """Drive the real lab through Mininet node handles."""

    def __init__(self, net: Any) -> None:
        self._net = net

    def _node(self, name: str) -> Any:
        try:
            return self._net[name]
        except KeyError as exc:
            raise SouthboundError(f"unknown lab node {name!r}") from exc

    def _run(self, node: Any, command: str) -> tuple[str, str, int]:
        out, err, code = node.pexec(shlex.split(command))
        return str(out), str(err), int(code)

    def dest_prefixes(self, node: str) -> tuple[str, ...]:
        prefixes: list[str] = []
        for intf in self._node(node).intfList():
            try:
                addresses = intf.ip6s(exclude_lls=True, exclude_lbs=True)
            except AttributeError:
                continue
            for address in addresses:
                prefix = str(address.network.with_prefixlen)
                if prefix not in prefixes:
                    prefixes.append(prefix)
        return tuple(prefixes)

    def egress_dev(self, ingress: str, first_sid: str) -> str:
        node = self._node(ingress)
        out, err, code = self._run(node, f"ip -6 route get {first_sid}")
        if code != 0:
            raise SouthboundError(
                f"cannot resolve a route to {first_sid} on {ingress}: {err.strip()}"
            )
        match = _DEV_RE.search(out)
        if match is None:
            raise SouthboundError(
                f"no egress device for {first_sid} on {ingress}: {out.strip()}"
            )
        return match.group(1)

    def list_encap_routes(self, ingress: str) -> tuple[RouteState, ...]:
        node = self._node(ingress)
        out, err, code = self._run(node, "ip -6 route show")
        if code != 0:
            raise SouthboundError(f"cannot list routes on {ingress}: {err.strip()}")
        routes: list[RouteState] = []
        for line in out.splitlines():
            match = _ROUTE_RE.search(line)
            if match is None:
                continue
            routes.append(
                RouteState(
                    prefix=match.group("prefix"),
                    segments=_segments(match.group("segs")),
                    mode=match.group("mode"),
                    metric=int(match.group("metric")),
                    dev=match.group("dev"),
                )
            )
        return tuple(routes)

    def replace(self, ingress: str, route: DesiredRoute) -> None:
        command = _command(
            "replace",
            route.prefix,
            route.segments,
            route.mode,
            route.metric,
            route.dev,
        )
        _out, err, code = self._run(self._node(ingress), command)
        if code != 0:
            raise SouthboundError(
                f"cannot install policy {route.policy_id} on {ingress}: "
                f"{err.strip() or command}"
            )

    def delete(self, ingress: str, route: RouteState) -> None:
        command = _command(
            "del",
            route.prefix,
            route.segments,
            route.mode,
            route.metric,
            route.dev,
        )
        _out, err, code = self._run(self._node(ingress), command)
        if code != 0:
            raise SouthboundError(
                f"cannot remove route {route.prefix} on {ingress}: "
                f"{err.strip() or command}"
            )


class DryRunSouthbound:
    """In-memory data plane used by tests and the CLI dry-run mode."""

    def __init__(
        self,
        prefixes: Mapping[str, tuple[str, ...]] | None = None,
        devs: Mapping[tuple[str, str], str] | None = None,
    ) -> None:
        self.prefixes = dict(prefixes or {})
        self.devs = dict(devs or {})
        self.routes: dict[tuple[str, str], RouteState] = {}
        self.commands: list[str] = []

    def dest_prefixes(self, node: str) -> tuple[str, ...]:
        return self.prefixes.get(node, ())

    def egress_dev(self, ingress: str, first_sid: str) -> str:
        return self.devs.get((ingress, first_sid), "eth0")

    def list_encap_routes(self, ingress: str) -> tuple[RouteState, ...]:
        return tuple(
            route
            for (route_ingress, _prefix), route in self.routes.items()
            if route_ingress == ingress
        )

    def replace(self, ingress: str, route: DesiredRoute) -> None:
        self.commands.append(
            _command("replace", route.prefix, route.segments, route.mode, route.metric, route.dev)
        )
        self.routes[(ingress, route.prefix)] = RouteState(
            prefix=route.prefix,
            segments=route.segments,
            mode=route.mode,
            metric=route.metric,
            dev=route.dev,
        )

    def delete(self, ingress: str, route: RouteState) -> None:
        self.commands.append(
            _command("del", route.prefix, route.segments, route.mode, route.metric, route.dev)
        )
        self.routes.pop((ingress, route.prefix), None)
