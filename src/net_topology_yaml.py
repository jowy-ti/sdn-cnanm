"""SRv6 lab whose topology is read from a YAML file.

An SRv6 lab (dynamic OSPFv3/FRR underlay, SRv6 data plane) whose nodes, links,
locator SIDs and encapsulation policies are not hard-coded: they are described
by a YAML file, by default ``topologies/srv6.yaml``.  The bundled lab chains
``h1 - r1 - r2 - r3 - r4 - r5 - r6 - h2`` with an ``r2 - r5`` shortcut and uses
a segment list to force traffic through the transit router ``r4``.

Everything ipmininet needs is derived from that file:

* every router gets the default ``BasicRouterConfig`` (zebra + ospfd + ospf6d)
  and therefore learns the whole topology; a router without any local SID stays
  a pure underlay forwarder and never processes an SRH;
* each router ``loopback`` is the prefix that carries its locator SIDs;
* every entry in a router's ``sids`` list installs one local SID endpoint into
  a per-router ``LocalSIDTable`` matching the loopback prefix.  The endpoint
  ``behavior`` selects what happens once a packet reaches the SID:

  - ``end.dx6`` (default): decapsulate and deliver to the ``to`` node's local
    IPv6 address;
  - ``end``: a pure transit waypoint -- the router pops its own SID and
    forwards toward the next segment.  This is what lets a segment list force
    a path through an SRv6 router in the middle of the network;
  - ``end.x``: like ``end`` but with an explicit L3 cross-connect, sending the
    packet to the ``to`` node's IPv6 address.

  A transit router therefore carries one or more ``behavior: end`` SIDs inside
  its locator and is listed as an intermediate hop (its SID first) in a
  ``policies`` segment list.  It keeps running the plain OSPFv3 underlay: the
  IGP routes the outer packet to the locator and the SID advances the SRH;
* every entry in ``policies`` installs one ``SRv6Encap`` route on the named
  ingress router, steering the destination toward the given segment list.  The
  segment list is what enforces the path: listing a transit router's SID before
  the egress SID makes the packet traverse that router even when it is not on
  the IGP shortest path.

Schema
------
``hosts``
    List of ``{name, use_v6?}`` mappings.
``routers``
    List of ``{name, use_v6?, loopback?, ospf6?, sids?}`` mappings.
    ``loopback`` is a CIDR (e.g. ``2001:db8:1::1/64``); it is mandatory when
    ``sids`` is non-empty and every SID must fall inside it.  ``ospf6``
    (default ``true``) toggles the OSPFv3 daemon.  ``sids`` is a list of
    ``{address, behavior?, to?, cost?}`` where ``address`` is the locator SID
    (``/128`` assumed) and ``behavior`` is one of:

    * ``end.dx6`` (default) -- deliver the decapsulated packet to ``to``;
    * ``end`` -- transit waypoint, no ``to`` (pops the SID, keeps forwarding);
    * ``end.x`` -- transit waypoint cross-connected to ``to``.
``links``
    List of ``[node_a, node_b]`` pairs, or ``{nodes: [a, b], options: {...}}``
    where ``options`` is forwarded verbatim to ``IPTopo.addLink``.
``policies``
    List of ``{node, to, through, mode?, cost?}``.  ``node`` is the ingress
    router, ``to`` a destination node, ``through`` the segment list (list any
    transit SRv6 router SIDs before the egress SID), ``mode`` either ``encap``
    (default) or ``inline``.

Run as root:  pixi run net-yaml
or with an explicit file:
    sudo .pixi/envs/default/bin/python src/net_topology_yaml.py my_topo.yaml
Use ``--check`` to only parse and validate the file (no root required).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from ipaddress import IPv6Address, IPv6Network
from pathlib import Path
from typing import Any, cast

import yaml
from ipmininet.cli import IPCLI
from ipmininet.ipnet import IPNet
from ipmininet.iptopo import IPTopo
from ipmininet.router.config import OSPF6
from ipmininet.srv6 import (
    LocalSIDTable,
    SRv6Encap,
    SRv6EndDX6Function,
    SRv6EndFunction,
    SRv6EndXFunction,
)
from mako.lookup import TemplateLookup

# On Fedora the FRR/RADVVD/SSHD daemons ipmininet starts live outside the
# default PATH.
for _dir in ("/usr/libexec/frr", "/usr/sbin", "/usr/local/sbin"):
    if os.path.isdir(_dir) and _dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = _dir + os.pathsep + os.environ["PATH"]

os.environ.setdefault("NO_COLOR", "1")

DEFAULT_TOPOLOGY = Path(__file__).resolve().parent.parent / "topologies" / "srv6.yaml"

OSPF6_TEMPLATES = TemplateLookup(
    directories=[os.path.join(os.path.dirname(__file__), "templates")]
)

# iproute2 mode names; SRv6Encap.ENCAP/INLINE happen to use these exact strings.
_SRV6_MODES = ("encap", "inline")

# Local SID endpoint behaviours ipmininet can install, keyed by the iproute2
# action name.  A behaviour that ends in a nexthop ("end.dx6", "end.x") needs a
# "to" node, while the plain "end" is a self-contained transit waypoint.
_SRV6_SID_BEHAVIORS = ("end.dx6", "end", "end.x")
_DEFAULT_SID_BEHAVIOR = "end.dx6"
_NEXTHOP_SID_BEHAVIORS = frozenset({"end.dx6", "end.x"})


class TopologyError(ValueError):
    """A YAML topology file is missing or malformed."""


# --------------------------------------------------------------------------- #
# Parsed representation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Sid:
    address: str
    behavior: str = _DEFAULT_SID_BEHAVIOR
    to: str | None = None
    cost: int = 1


@dataclass(frozen=True, slots=True)
class Node:
    name: str
    use_v6: bool = True


@dataclass(frozen=True, slots=True)
class Router(Node):
    loopback: str | None = None
    ospf6: bool = True
    sids: tuple[Sid, ...] = ()


@dataclass(frozen=True, slots=True)
class Host(Node):
    pass


@dataclass(frozen=True, slots=True)
class Link:
    nodes: tuple[str, str]
    options: Mapping[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True, slots=True)
class Policy:
    node: str
    to: str
    through: tuple[str, ...]
    mode: str = "encap"
    cost: int = 1


@dataclass(frozen=True, slots=True)
class Topology:
    routers: tuple[Router, ...]
    hosts: tuple[Host, ...]
    links: tuple[Link, ...]
    policies: tuple[Policy, ...]


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _as_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TopologyError(f"{where}: expected a mapping, got {type(value).__name__}")
    return cast("Mapping[str, Any]", value)


def _as_sequence(value: Any, where: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TopologyError(f"{where}: expected a list, got {type(value).__name__}")
    return cast("list[Any]", value)


def _as_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise TopologyError(f"{where}: expected a non-empty string")
    return value


def _str_field(mapping: Mapping[str, Any], key: str, where: str) -> str:
    if key not in mapping:
        raise TopologyError(f"{where}: missing required key {key!r}")
    return _as_str(mapping[key], f"{where}.{key}")


def _opt_str_field(mapping: Mapping[str, Any], key: str, where: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    return _as_str(value, f"{where}.{key}")


def _bool_field(
    mapping: Mapping[str, Any], key: str, where: str, default: bool
) -> bool:
    value = mapping.get(key, default)
    if not isinstance(value, bool):
        raise TopologyError(f"{where}.{key}: expected a boolean")
    return value


def _int_field(mapping: Mapping[str, Any], key: str, where: str, default: int) -> int:
    value = mapping.get(key, default)
    # bool is an int subclass; reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TopologyError(f"{where}.{key}: expected an integer")
    return value


def _parse_sid(raw: Any, where: str) -> Sid:
    mapping = _as_mapping(raw, where)
    address = _str_field(mapping, "address", where)
    try:
        prefix = IPv6Network(address)
    except ValueError as exc:
        raise TopologyError(
            f"{where}.address: {address!r} is not a valid IPv6 SID"
        ) from exc
    if prefix.prefixlen != 128:
        raise TopologyError(f"{where}.address: SID {address!r} must be a /128 prefix")
    behavior = _opt_str_field(mapping, "behavior", where) or _DEFAULT_SID_BEHAVIOR
    if behavior not in _SRV6_SID_BEHAVIORS:
        raise TopologyError(
            f"{where}.behavior: expected one of {', '.join(_SRV6_SID_BEHAVIORS)}, "
            f"got {behavior!r}"
        )
    to = _opt_str_field(mapping, "to", where)
    if behavior in _NEXTHOP_SID_BEHAVIORS and to is None:
        raise TopologyError(f"{where}.to: required for SID behavior {behavior!r}")
    if behavior not in _NEXTHOP_SID_BEHAVIORS and to is not None:
        raise TopologyError(f"{where}.to: not allowed for SID behavior {behavior!r}")
    return Sid(
        address=prefix.with_prefixlen,
        behavior=behavior,
        to=to,
        cost=_int_field(mapping, "cost", where, 1),
    )


def _parse_router(raw: Any, where: str) -> Router:
    mapping = _as_mapping(raw, where)
    name = _str_field(mapping, "name", where)
    loopback = _opt_str_field(mapping, "loopback", where)
    if loopback is not None:
        try:
            IPv6Network(loopback, strict=False)
        except ValueError as exc:
            raise TopologyError(
                f"{where}.loopback: {loopback!r} is not a valid IPv6 prefix"
            ) from exc
    sids = tuple(
        _parse_sid(sid, f"{where}.sids[{index}]")
        for index, sid in enumerate(_as_sequence(mapping.get("sids"), f"{where}.sids"))
    )
    if sids and loopback is None:
        raise TopologyError(
            f"{where}: router {name!r} defines 'sids' but has no 'loopback' locator"
        )
    return Router(
        name=name,
        use_v6=_bool_field(mapping, "use_v6", where, True),
        loopback=loopback,
        ospf6=_bool_field(mapping, "ospf6", where, True),
        sids=sids,
    )


def _parse_host(raw: Any, where: str) -> Host:
    mapping = _as_mapping(raw, where)
    return Host(
        name=_str_field(mapping, "name", where),
        use_v6=_bool_field(mapping, "use_v6", where, True),
    )


def _parse_link(raw: Any, where: str) -> Link:
    if isinstance(raw, list):
        mapping: Mapping[str, Any] = {"nodes": cast("list[Any]", raw)}
    else:
        mapping = _as_mapping(raw, where)
    nodes = _as_sequence(mapping.get("nodes"), f"{where}.nodes")
    if len(nodes) != 2:
        raise TopologyError(f"{where}.nodes: expected exactly two node names")
    first = _as_str(nodes[0], f"{where}.nodes[0]")
    second = _as_str(nodes[1], f"{where}.nodes[1]")
    options = _as_mapping(mapping.get("options", dict[str, Any]()), f"{where}.options")
    return Link(nodes=(first, second), options=dict(options))


def _parse_policy(raw: Any, where: str) -> Policy:
    mapping = _as_mapping(raw, where)
    through = _as_sequence(mapping.get("through"), f"{where}.through")
    if not through:
        raise TopologyError(f"{where}.through: at least one segment is required")
    segments = tuple(
        _as_str(segment, f"{where}.through[{index}]")
        for index, segment in enumerate(through)
    )
    mode = _opt_str_field(mapping, "mode", where) or "encap"
    if mode not in _SRV6_MODES:
        raise TopologyError(
            f"{where}.mode: expected one of {', '.join(_SRV6_MODES)}, got {mode!r}"
        )
    return Policy(
        node=_str_field(mapping, "node", where),
        to=_str_field(mapping, "to", where),
        through=segments,
        mode=mode,
        cost=_int_field(mapping, "cost", where, 1),
    )


def _parse_list(raw: Any, key: str, where: str) -> list[Any]:
    return _as_sequence(raw, f"{where}.{key}")


def _validate(topology: Topology) -> None:
    names = [node.name for node in (*topology.routers, *topology.hosts)]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise TopologyError(f"duplicate node names: {', '.join(duplicates)}")

    if not topology.routers:
        raise TopologyError("the topology must define at least one router")

    known = set(names)
    routers = {router.name for router in topology.routers}

    for link in topology.links:
        for node in link.nodes:
            if node not in known:
                raise TopologyError(f"link references unknown node {node!r}")

    for router in topology.routers:
        locator = (
            IPv6Network(router.loopback, strict=False)
            if router.loopback is not None
            else None
        )
        for sid in router.sids:
            if sid.to is not None and sid.to not in known:
                raise TopologyError(
                    f"router {router.name!r}: SID {sid.address} targets unknown node {sid.to!r}"
                )
            # A SID is only reachable through its router's LocalSIDTable, which
            # matches the loopback locator: outside of it the endpoint is never
            # hit and the SRv6 route silently does nothing.
            if locator is not None and not IPv6Network(sid.address).subnet_of(locator):
                raise TopologyError(
                    f"router {router.name!r}: SID {sid.address} is outside its "
                    f"loopback locator {router.loopback}"
                )

    for policy in topology.policies:
        if policy.node not in routers:
            raise TopologyError(f"policy: {policy.node!r} is not a router")
        if policy.to not in known:
            raise TopologyError(
                f"policy on {policy.node!r}: unknown destination {policy.to!r}"
            )
        for segment in policy.through:
            if segment in known:
                continue
            try:
                IPv6Address(segment)
            except ValueError as exc:
                raise TopologyError(
                    f"policy on {policy.node!r}: segment {segment!r} is neither a node name nor an IPv6 address"
                ) from exc


def load_topology(path: Path) -> Topology:
    """Read, parse and validate a YAML topology file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TopologyError(f"cannot read topology file {path}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TopologyError(f"cannot parse {path}: {exc}") from exc

    where = str(path)
    root = _as_mapping(raw, where)
    topology = Topology(
        routers=tuple(
            _parse_router(router, f"{where}:routers[{index}]")
            for index, router in enumerate(
                _parse_list(root.get("routers"), "routers", where)
            )
        ),
        hosts=tuple(
            _parse_host(host, f"{where}:hosts[{index}]")
            for index, host in enumerate(_parse_list(root.get("hosts"), "hosts", where))
        ),
        links=tuple(
            _parse_link(link, f"{where}:links[{index}]")
            for index, link in enumerate(_parse_list(root.get("links"), "links", where))
        ),
        policies=tuple(
            _parse_policy(policy, f"{where}:policies[{index}]")
            for index, policy in enumerate(
                _parse_list(root.get("policies"), "policies", where)
            )
        ),
    )
    _validate(topology)
    return topology


# --------------------------------------------------------------------------- #
# ipmininet topology
# --------------------------------------------------------------------------- #
class YamlSRv6Topo(IPTopo):
    def __init__(self, topology: Topology, *args: object, **kwargs: object) -> None:
        self.topology = topology
        self.sid_tables: dict[str, LocalSIDTable] = {}
        super().__init__(*args, **kwargs)

    def build(self, *args: object, **kwargs: object) -> None:
        for host in self.topology.hosts:
            self.addHost(host.name, use_v6=host.use_v6)
        for router in self.topology.routers:
            if router.loopback is not None:
                self.addRouter(
                    router.name, use_v6=router.use_v6, lo_addresses=[router.loopback]
                )
            else:
                self.addRouter(router.name, use_v6=router.use_v6)
            if router.ospf6:
                self.addDaemon(router.name, OSPF6, template_lookup=OSPF6_TEMPLATES)
        for link in self.topology.links:
            self.addLink(link.nodes[0], link.nodes[1], **link.options)
        super().build(*args, **kwargs)

    def post_build(self, net: IPNet) -> None:
        for policy in self.topology.policies:
            SRv6Encap(
                net=net,
                node=policy.node,
                to=policy.to,
                through=list(policy.through),
                mode=policy.mode,
                cost=policy.cost,
            )

        for router in self.topology.routers:
            if not router.sids:
                continue
            assert router.loopback is not None  # guaranteed by load_topology()
            table = LocalSIDTable(
                net[router.name],
                matching=[IPv6Network(router.loopback, strict=False)],
            )
            self.sid_tables[router.name] = table
            for sid in router.sids:
                self._install_sid(net, router.name, table, sid)
        super().post_build(net)

    @staticmethod
    def _install_sid(net: IPNet, node: str, table: LocalSIDTable, sid: Sid) -> None:
        """Install one local SID endpoint on ``node``.

        ``end`` is a self-contained transit waypoint: the node pops its own SID
        and forwards toward the next segment, which is what lets a segment list
        steer traffic through a router in the middle of the path.  ``end.x``
        and ``end.dx6`` instead hand the packet to the ``to`` node's IPv6
        address (L3 cross-connect / decapsulation toward a local host).
        """
        if sid.behavior == "end":
            SRv6EndFunction(
                net=net, node=node, to=sid.address, table=table, cost=sid.cost
            )
            return
        assert sid.to is not None  # guaranteed by load_topology()
        if sid.behavior == "end.x":
            SRv6EndXFunction(
                net=net,
                node=node,
                to=sid.address,
                nexthop=net[sid.to],
                table=table,
                cost=sid.cost,
            )
        elif sid.behavior == "end.dx6":
            SRv6EndDX6Function(
                net=net,
                node=node,
                to=sid.address,
                nexthop=net[sid.to],
                table=table,
                cost=sid.cost,
            )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def _summary(topology: Topology) -> str:
    lines = [
        f"  routers:  {', '.join(r.name for r in topology.routers)}",
        f"  hosts:    {', '.join(h.name for h in topology.hosts)}",
        f"  links:    {len(topology.links)}",
        f"  policies: {len(topology.policies)}",
    ]
    sids = [
        f"{r.name}:{sid.address}({sid.behavior})"
        + (f"->{sid.to}" if sid.to is not None else "")
        for r in topology.routers
        for sid in r.sids
    ]
    if sids:
        lines.append(f"  local SIDs: {', '.join(sids)}")
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "topology",
        nargs="?",
        type=Path,
        default=DEFAULT_TOPOLOGY,
        help=f"YAML topology file (default: {DEFAULT_TOPOLOGY})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="parse and validate the topology file, then exit (no root needed)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    path: Path = args.topology
    try:
        topology = load_topology(path)
    except TopologyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        print(f"{path}: ok")
        print(_summary(topology))
        return 0

    net = IPNet(topo=YamlSRv6Topo(topology))
    net.start()
    IPCLI(net)
    net.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
