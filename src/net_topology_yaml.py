"""SRv6 lab whose topology is read from a YAML file.

Builds the SRv6 lab described by ``topologies/srv6.yaml`` (or a file passed on
the command line): an OSPFv3/FRR underlay with SRv6 as the data plane.  Nodes,
links, locator SIDs and encapsulation policies are declarative; ``YamlSRv6Topo``
only translates them into ipmininet objects.

Schema
------
``hosts``
    ``{name, use_v6?}``
``routers``
    ``{name, use_v6?, loopback?, ospf6?, sids?}``.  ``loopback`` is the CIDR
    locator that carries the router's SIDs; it is required when ``sids`` is
    non-empty and every SID must fall inside it.  ``ospf6`` (default ``true``)
    controls whether the OSPFv3 daemon runs.  ``sids`` entries are
    ``{address, behavior?, to?, cost?}`` where ``behavior`` is one of
    ``end.dx6`` (default; needs ``to``), ``end`` (transit waypoint) or
    ``end.x`` (cross-connect; needs ``to``).
``links``
    ``[a, b]`` or ``{nodes: [a, b], options: {...}}``; ``options`` is passed
    verbatim to ``IPTopo.addLink``.
``policies``
    ``{node, to, through, mode?, cost?}``.  ``node`` is the ingress router,
    ``to`` a destination node and ``through`` a non-empty list of IPv6 SIDs
    (list transit SIDs before the egress SID); ``mode`` is ``encap`` (default)
    or ``inline``.

Run as root (``make run``) to start the lab, or pass ``--check`` to parse and
validate a file without root.
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
from ipmininet.router.config import OSPF, OSPF6, RouterConfig
from ipmininet.srv6 import (
    LocalSIDTable,
    SRv6Encap,
    SRv6EndDX6Function,
    SRv6EndFunction,
    SRv6EndXFunction,
)
from mako.lookup import TemplateLookup

# The FRR/RADVVD/SSHD daemons ipmininet starts live outside the default PATH,
# in a directory that depends on the distribution: /usr/lib/frr on
# Debian/Ubuntu, /usr/libexec/frr on Fedora/RHEL.  ipmininet looks the daemons
# up with require_cmd("zebra"/"ospfd"/"ospf6d") at net.start(), so the right
# directory has to be on PATH or every router dies before it starts.
_FRR_DAEMON_DIRS = ("/usr/lib/frr", "/usr/libexec/frr", "/usr/local/lib/frr")
_FRR_DAEMONS = ("zebra", "ospfd", "ospf6d")


def _prepend_to_path(*directories: str) -> None:
    """Move each existing directory to the front of PATH, once."""
    entries = os.environ.get("PATH", "").split(os.pathsep)
    for directory in directories:
        if os.path.isdir(directory) and directory not in entries:
            entries.insert(0, directory)
    os.environ["PATH"] = os.pathsep.join(entries)


for _frr_dir in _FRR_DAEMON_DIRS:
    if any(
        os.path.isfile(os.path.join(_frr_dir, daemon)) for daemon in _FRR_DAEMONS
    ):
        _prepend_to_path(_frr_dir)
        break
_prepend_to_path("/usr/sbin", "/usr/local/sbin")

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
    use_v6 = _bool_field(mapping, "use_v6", where, True)
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
    if not use_v6 and (sids or loopback is not None):
        raise TopologyError(
            f"{where}: router {name!r} has 'use_v6: false' but defines IPv6 "
            "locators or SIDs"
        )
    return Router(
        name=name,
        use_v6=use_v6,
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
    if "nodes" not in mapping:
        raise TopologyError(f"{where}: missing required key 'nodes'")
    nodes = _as_sequence(mapping["nodes"], f"{where}.nodes")
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

    nodes: dict[str, Node] = {
        node.name: node for node in (*topology.routers, *topology.hosts)
    }
    routers = {router.name: router for router in topology.routers}

    for link in topology.links:
        first, second = link.nodes
        if first == second:
            raise TopologyError(f"link: node {first!r} cannot be linked to itself")
        for node in link.nodes:
            if node not in nodes:
                raise TopologyError(f"link references unknown node {node!r}")

    for router in topology.routers:
        locator = (
            IPv6Network(router.loopback, strict=False)
            if router.loopback is not None
            else None
        )
        for sid in router.sids:
            if sid.to is not None:
                if sid.to not in nodes:
                    raise TopologyError(
                        f"router {router.name!r}: SID {sid.address} targets "
                        f"unknown node {sid.to!r}"
                    )
                if not nodes[sid.to].use_v6:
                    raise TopologyError(
                        f"router {router.name!r}: SID {sid.address} targets "
                        f"{sid.to!r}, which has 'use_v6: false'"
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
        source = routers.get(policy.node)
        if source is None:
            raise TopologyError(f"policy: {policy.node!r} is not a router")
        if not source.use_v6:
            raise TopologyError(
                f"policy: ingress router {policy.node!r} has 'use_v6: false'"
            )
        target = nodes.get(policy.to)
        if target is None:
            raise TopologyError(
                f"policy on {policy.node!r}: unknown destination {policy.to!r}"
            )
        if not target.use_v6:
            raise TopologyError(
                f"policy on {policy.node!r}: destination {policy.to!r} has "
                "'use_v6: false'"
            )
        for segment in policy.through:
            if segment in nodes:
                raise TopologyError(
                    f"policy on {policy.node!r}: segment {segment!r} is a node "
                    "name; use the IPv6 address of a local SID"
                )
            try:
                IPv6Address(segment)
            except ValueError as exc:
                raise TopologyError(
                    f"policy on {policy.node!r}: segment {segment!r} is not a "
                    "valid IPv6 address"
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
class YamlRouterConfig(RouterConfig):
    """Router config with a real OSPFv3 on/off switch.

    ``BasicRouterConfig`` appends OSPFv3 automatically whenever ``use_v6`` is
    set, so ``ospf6: false`` cannot be expressed with it; simply skipping the
    registration would leave the stock ipmininet OSPFv3 template in place,
    which FRR 10 rejects.  This config builds the daemon list itself and only
    registers OSPFv3 -- always with the patched template -- when asked.
    """

    def __init__(
        self,
        node: Any,
        daemons: Sequence[Any] = (),
        additional_daemons: Sequence[Any] = (),
        ospf6: bool = True,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        selected = list(daemons)
        if node.use_v4:
            selected.append(OSPF)
        if node.use_v6 and ospf6:
            selected.append((OSPF6, {"template_lookup": OSPF6_TEMPLATES}))
        selected.extend(additional_daemons)
        super().__init__(node, daemons=selected, *args, **kwargs)


class YamlSRv6Topo(IPTopo):
    def __init__(self, topology: Topology, *args: object, **kwargs: object) -> None:
        self.topology = topology
        super().__init__(*args, **kwargs)

    def build(self, *args: object, **kwargs: object) -> None:
        for host in self.topology.hosts:
            self.addHost(host.name, use_v6=host.use_v6)
        for router in self.topology.routers:
            config = (YamlRouterConfig, {"ospf6": router.ospf6})
            if router.loopback is not None:
                self.addRouter(
                    router.name,
                    use_v6=router.use_v6,
                    lo_addresses=[router.loopback],
                    config=config,
                )
            else:
                self.addRouter(router.name, use_v6=router.use_v6, config=config)
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
