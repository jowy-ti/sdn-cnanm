"""Topology graph and SID catalog built from the YAML topology objects."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from ipaddress import IPv6Address, IPv6Network
from typing import Any, cast

import networkx as nx

from .models import ControllerError, SidEntry

# networkx types the optional ``**backend_kwargs`` as Unknown, which trips
# pyright strict at every call site.  Pin the overload we use once.
_shortest_path = cast(
    "Callable[[nx.Graph[str], str, str, str], list[str]]",
    getattr(nx, "shortest_path"),
)


def canonical_sid(value: str) -> tuple[str, str]:
    """Return ``(address, prefix)`` for an IPv6 SID given as ``addr`` or ``addr/128``."""
    try:
        prefix = IPv6Network(value)
    except ValueError as exc:
        raise ControllerError(f"{value!r} is not a valid IPv6 SID") from exc
    if prefix.prefixlen != 128:
        raise ControllerError(f"SID {value!r} must be a /128 prefix")
    return str(prefix.network_address), prefix.with_prefixlen


def _link_weight(options: Mapping[str, Any], where: str) -> int:
    value = options.get("igp_metric", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ControllerError(f"{where}: 'igp_metric' must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class TopologyView:
    """A controller-friendly view of the YAML topology."""

    graph: nx.Graph[str]
    routers: tuple[str, ...]
    hosts: tuple[str, ...]
    links: tuple[tuple[str, str, int], ...]
    sids_by_router: Mapping[str, tuple[SidEntry, ...]]
    sids_by_address: Mapping[str, SidEntry]
    use_v6: Mapping[str, bool]

    @classmethod
    def from_topology(cls, topology: Any) -> TopologyView:
        routers = tuple(str(router.name) for router in topology.routers)
        hosts = tuple(str(host.name) for host in topology.hosts)
        nodes = (*routers, *hosts)
        if len(set(nodes)) != len(nodes):
            raise ControllerError("duplicate node names in the topology")

        use_v6: dict[str, bool] = {}
        for router in topology.routers:
            use_v6[str(router.name)] = bool(router.use_v6)
        for host in topology.hosts:
            use_v6[str(host.name)] = bool(host.use_v6)

        sids_by_router: dict[str, tuple[SidEntry, ...]] = {}
        sids_by_address: dict[str, SidEntry] = {}
        for router in topology.routers:
            entries: list[SidEntry] = []
            for raw in router.sids:
                address, prefix = canonical_sid(str(raw.address))
                entry = SidEntry(
                    address=address,
                    prefix=prefix,
                    behavior=str(raw.behavior),
                    owner=str(router.name),
                    to=str(raw.to) if raw.to is not None else None,
                    cost=int(raw.cost),
                )
                if address in sids_by_address:
                    raise ControllerError(
                        f"SID {address} is defined by more than one router"
                    )
                sids_by_address[address] = entry
                entries.append(entry)
            sids_by_router[str(router.name)] = tuple(entries)

        graph = cast("nx.Graph[str]", nx.Graph())
        for name in nodes:
            graph.add_node(name, kind="router" if name in routers else "host")
        links: list[tuple[str, str, int]] = []
        for index, link in enumerate(topology.links):
            first, second = (str(node) for node in link.nodes)
            if first not in use_v6 or second not in use_v6:
                raise ControllerError(
                    f"link {index} references an unknown node: {first!r}, {second!r}"
                )
            weight = _link_weight(link.options, f"link {index}")
            graph.add_edge(first, second, weight=weight)
            links.append((first, second, weight))

        return cls(
            graph=graph,
            routers=routers,
            hosts=hosts,
            links=tuple(links),
            sids_by_router=sids_by_router,
            sids_by_address=sids_by_address,
            use_v6=use_v6,
        )

    # -- lookups ----------------------------------------------------------- #

    @property
    def nodes(self) -> tuple[str, ...]:
        return (*self.routers, *self.hosts)

    def is_router(self, node: str) -> bool:
        return node in self.routers

    def is_host(self, node: str) -> bool:
        return node in self.hosts

    def require_node(self, node: str) -> None:
        if node not in self.use_v6:
            raise ControllerError(f"unknown node {node!r}")
        if not self.use_v6[node]:
            raise ControllerError(f"node {node!r} has IPv6 disabled")

    def require_router(self, node: str) -> None:
        self.require_node(node)
        if not self.is_router(node):
            raise ControllerError(f"node {node!r} is not a router")

    def node_sid(self, router: str) -> SidEntry:
        """Return the transit (``end``) SID that identifies a router."""
        self.require_router(router)
        candidates = [
            sid
            for sid in self.sids_by_router.get(router, ())
            if sid.behavior == "end"
        ]
        if len(candidates) != 1:
            raise ControllerError(
                f"router {router!r} must have exactly one 'end' SID to be used "
                f"as a waypoint (found {len(candidates)})"
            )
        return candidates[0]

    def terminal_sid(self, destination: str) -> SidEntry:
        """Return the SID that delivers traffic to ``destination``."""
        self.require_node(destination)
        decap = [
            sid
            for entries in self.sids_by_router.values()
            for sid in entries
            if sid.behavior == "end.dx6" and sid.to == destination
        ]
        if len(decap) == 1:
            return decap[0]
        if len(decap) > 1:
            raise ControllerError(
                f"destination {destination!r} has more than one end.dx6 SID"
            )
        if self.is_router(destination):
            candidates = [
                sid
                for sid in self.sids_by_router.get(destination, ())
                if sid.behavior == "end"
            ]
            if len(candidates) == 1:
                return candidates[0]
        raise ControllerError(
            f"destination {destination!r} has no end.dx6 (or router 'end') SID"
        )

    def resolve_via(self, item: str) -> SidEntry:
        """Resolve a waypoint given as a router name or a SID literal."""
        if item in self.routers:
            return self.node_sid(item)
        if item in self.hosts:
            raise ControllerError(
                f"host {item!r} cannot be used as an SRv6 waypoint"
            )
        address, _prefix = canonical_sid(item)
        sid = self.sids_by_address.get(address)
        if sid is None:
            raise ControllerError(f"SID {item!r} is not defined in the topology")
        return sid

    # -- computation ------------------------------------------------------- #

    def segments_for(self, destination: str, via: Sequence[str]) -> tuple[str, ...]:
        """Build a forwarding-order segment list for an API-created intent."""
        terminal = self.terminal_sid(destination)
        segments: list[str] = [self.resolve_via(item).address for item in via]
        if not segments or segments[-1] != terminal.address:
            segments.append(terminal.address)
        return tuple(segments)

    def path(
        self,
        ingress: str,
        destination: str,
        via: Sequence[str] = (),
        avoid: Sequence[str] = (),
    ) -> tuple[str, ...]:
        """Compute the node path through the waypoints, honouring ``avoid``."""
        self.require_router(ingress)
        self.require_node(destination)
        waypoint_owners = [self.resolve_via(item).owner for item in via]
        avoided = set(avoid)
        for node in avoided:
            self.require_node(node)
        if ingress in avoided or destination in avoided:
            raise ControllerError("the ingress and destination cannot be avoided")

        waypoints = [ingress, *waypoint_owners, destination]
        waypoints = [
            node
            for index, node in enumerate(waypoints)
            if index == 0 or node != waypoints[index - 1]
        ]
        if waypoints.count(ingress) > 1:
            raise ControllerError("the computed path loops back to the ingress")

        graph = self.graph.copy()
        graph.remove_nodes_from(avoided)
        graph.remove_nodes_from(
            node for node, enabled in self.use_v6.items() if not enabled
        )
        if ingress not in graph or destination not in graph:
            raise ControllerError("the ingress or destination was removed by 'avoid'")

        result: list[str] = []
        try:
            for source, target in zip(waypoints, waypoints[1:]):
                if source not in graph or target not in graph:
                    raise ControllerError(
                        f"waypoint {source!r} or {target!r} is not reachable"
                    )
                leg = _shortest_path(graph, source, target, "weight")
                result.extend(leg if not result else leg[1:])
        except (nx.NetworkXNoPath, nx.NodeNotFound) as exc:
            raise ControllerError("no path satisfies the requested constraints") from exc
        return tuple(result)

    # -- serialization ----------------------------------------------------- #

    def as_dict(self) -> dict[str, Any]:
        return {
            "routers": list(self.routers),
            "hosts": list(self.hosts),
            "links": [
                {"a": first, "b": second, "weight": weight}
                for first, second, weight in self.links
            ],
            "sids": [
                {
                    "address": sid.prefix,
                    "behavior": sid.behavior,
                    "owner": sid.owner,
                    "to": sid.to,
                    "cost": sid.cost,
                }
                for sid in self.sids_by_address.values()
            ],
        }


def is_ipv6_address(value: str) -> bool:
    try:
        IPv6Address(value)
    except ValueError:
        return False
    return True
