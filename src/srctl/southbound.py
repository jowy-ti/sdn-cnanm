"""Southbound drivers: program SRv6 encapsulation routes on the lab nodes."""

from __future__ import annotations

import socket
import threading
from collections.abc import Mapping
from ipaddress import IPv6Address, IPv6Interface
from typing import Any, Protocol, cast

from pyroute2 import IPRoute
from pyroute2.netlink.exceptions import NetlinkError

from .models import DesiredRoute, RouteState

_SEG6_ENCAP_TYPE = 5


class SouthboundError(RuntimeError):
    """The data plane rejected a controller command."""


class Southbound(Protocol):
    """Operations the controller needs from the data plane."""

    def dest_prefixes(self, node: str) -> tuple[str, ...]: ...

    def egress_dev(self, ingress: str, first_sid: str) -> str: ...

    def list_encap_routes(self, ingress: str) -> tuple[RouteState, ...]: ...

    def replace(self, ingress: str, route: DesiredRoute) -> None: ...

    def delete(self, ingress: str, route: RouteState) -> None: ...

    def close(self) -> None: ...


class LiveSouthbound:
    """Drive the real lab through pyroute2 netlink sockets."""

    def __init__(self, net: Any) -> None:
        self._net = net
        # pyroute2's synchronous API is not thread-safe (it owns an asyncio
        # loop), and reconcile/API calls can run on different threads.  Keep
        # one IPRoute per node per thread and remember them for cleanup.
        self._local = threading.local()
        self._all: list[Any] = []
        self._lock = threading.Lock()

    def _ipr_for(self, node: str) -> Any:
        cache = getattr(self._local, "cache", None)
        if cache is None:
            cache = {}
            self._local.cache = cache
        node_cache = cast("dict[str, Any]", cache)
        cached = node_cache.get(node)
        if cached is not None:
            return cached
        try:
            lab_node = self._net[node]
        except KeyError as exc:
            raise SouthboundError(f"unknown lab node {node!r}") from exc
        pid = getattr(lab_node, "pid", None)
        if not isinstance(pid, int):
            raise SouthboundError(
                f"node {node!r} has no PID (has the lab started?)"
            )
        try:
            ipr = IPRoute(netns=f"/proc/{pid}/ns/net")
        except (OSError, NetlinkError) as exc:
            raise SouthboundError(
                f"cannot open netlink socket in {node!r}: {exc}"
            ) from exc
        cache[node] = ipr
        with self._lock:
            self._all.append(ipr)
        return ipr

    def close(self) -> None:
        with self._lock:
            instances = list(self._all)
            self._all.clear()
        for ipr in instances:
            try:
                ipr.close()
            except OSError:
                pass
        self._local = threading.local()

    def dest_prefixes(self, node: str) -> tuple[str, ...]:
        ipr = self._ipr_for(node)
        links = _link_names(ipr)
        prefixes: list[str] = []
        for address in ipr.get_addr(family=socket.AF_INET6):
            value = address.get_attr("IFA_ADDRESS")
            prefixlen = address.get("prefixlen")
            if not isinstance(value, str) or not isinstance(prefixlen, int):
                continue
            try:
                ip = IPv6Address(value)
            except ValueError:
                continue
            if ip.is_link_local or ip.is_loopback:
                continue
            if links.get(address.get("index")) is None:
                continue
            try:
                prefix = IPv6Interface(f"{value}/{prefixlen}").network.with_prefixlen
            except ValueError:
                continue
            if prefix not in prefixes:
                prefixes.append(prefix)
        return tuple(prefixes)

    def egress_dev(self, ingress: str, first_sid: str) -> str:
        ipr = self._ipr_for(ingress)
        try:
            replies = ipr.route("get", dst=first_sid)
        except (OSError, NetlinkError) as exc:
            raise SouthboundError(
                f"cannot resolve a route to {first_sid} on {ingress}: {exc}"
            ) from exc
        if not replies:
            raise SouthboundError(f"no route to {first_sid} on {ingress}")
        if isinstance(replies, list):
            reply = cast("list[Any]", replies)[0]
        else:
            reply = replies
        oif = reply.get_attr("RTA_OIF")
        if not isinstance(oif, int):
            raise SouthboundError(f"no egress device for {first_sid} on {ingress}")
        dev = _link_names(ipr).get(oif)
        if dev is None:
            raise SouthboundError(
                f"egress interface index {oif} is unknown on {ingress}"
            )
        return dev

    def list_encap_routes(self, ingress: str) -> tuple[RouteState, ...]:
        ipr = self._ipr_for(ingress)
        links = _link_names(ipr)
        routes: list[RouteState] = []
        for message in ipr.get_routes(family=socket.AF_INET6):
            state = route_state_from_message(message, links)
            if state is not None:
                routes.append(state)
        return tuple(routes)

    def replace(self, ingress: str, route: DesiredRoute) -> None:
        ipr = self._ipr_for(ingress)
        try:
            ipr.route(
                "replace",
                dst=route.prefix,
                oif=_link_index(ipr, route.dev),
                priority=route.metric,
                encap=_encap(route),
            )
        except (OSError, NetlinkError) as exc:
            raise SouthboundError(
                f"cannot install policy {route.policy_id} on {ingress}: {exc}"
            ) from exc

    def delete(self, ingress: str, route: RouteState) -> None:
        ipr = self._ipr_for(ingress)
        try:
            ipr.route(
                "del",
                dst=route.prefix,
                oif=_link_index(ipr, route.dev),
                priority=route.metric,
                encap=_encap_state(route),
            )
        except (OSError, NetlinkError) as exc:
            raise SouthboundError(
                f"cannot remove route {route.prefix} on {ingress}: {exc}"
            ) from exc


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
            f"replace {route.prefix} via {','.join(route.segments)} "
            f"metric {route.metric} dev {route.dev}"
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
            f"delete {route.prefix} via {','.join(route.segments)} "
            f"metric {route.metric} dev {route.dev}"
        )
        self.routes.pop((ingress, route.prefix), None)

    def close(self) -> None:
        pass


def _encap(route: DesiredRoute) -> dict[str, Any]:
    return {
        "type": "seg6",
        "mode": route.mode,
        "segs": list(route.segments),
    }


def _encap_state(route: RouteState) -> dict[str, Any]:
    return {
        "type": "seg6",
        "mode": route.mode,
        "segs": list(route.segments),
    }


def _link_names(ipr: Any) -> dict[int, str]:
    names: dict[int, str] = {}
    for link in ipr.get_links():
        index = link.get("index")
        name = link.get_attr("IFLA_IFNAME")
        if isinstance(index, int) and isinstance(name, str):
            names[index] = name
    return names


def _link_index(ipr: Any, dev: str) -> int:
    indices = ipr.link_lookup(ifname=dev)
    if not indices:
        raise SouthboundError(f"interface {dev!r} does not exist")
    return cast(int, indices[0])


def route_state_from_message(
    message: Any, links: Mapping[int, str]
) -> RouteState | None:
    if message.get_attr("RTA_ENCAP_TYPE") != _SEG6_ENCAP_TYPE:
        return None
    encap = message.get_attr("RTA_ENCAP")
    srh = srh_from_encap(encap)
    if srh is None:
        return None
    segments = srh.get("segs")
    if not isinstance(segments, list):
        return None
    segment_list = cast("list[Any]", segments)
    dst = message.get_attr("RTA_DST")
    prefixlen = message.get("dst_len")
    if not isinstance(dst, str) or not isinstance(prefixlen, int):
        return None
    mode = srh.get("mode")
    if not isinstance(mode, str):
        mode = "inline" if srh.get("encapmode") == 0 else "encap"
    metric = message.get_attr("RTA_PRIORITY")
    oif = message.get_attr("RTA_OIF")
    return RouteState(
        prefix=f"{dst}/{prefixlen}",
        segments=tuple(str(segment) for segment in segment_list),
        mode=mode,
        metric=metric if isinstance(metric, int) else 0,
        dev=links.get(oif, "") if isinstance(oif, int) else "",
    )


def srh_from_encap(encap: Any) -> Mapping[str, Any] | None:
    if encap is None:
        return None
    attrs = encap.get("attrs", [])
    if not isinstance(attrs, list):
        return None
    for item in cast("list[Any]", attrs):
        name: Any = None
        value: Any = None
        if hasattr(item, "name"):
            name = item.name
            value = item.value
        elif isinstance(item, tuple):
            pair = cast("tuple[Any, ...]", item)
            if len(pair) == 2:
                name, value = pair
        if name == "SEG6_IPTUNNEL_SRH" and isinstance(value, Mapping):
            return cast("Mapping[str, Any]", value)
    return None
