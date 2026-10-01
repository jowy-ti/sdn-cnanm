"""NDP-based IP discovery and SRv6 policy application (srv6 mode).

Called by ``net_topology.py`` right after ``net.start()``:

    routing.setup(net, routers=("n1", "n2", "n3"))

Steps:

1. enable IPv6 forwarding on every router;
2. assign an underlay prefix fd00:0:<n>::/64 to each router-router link
   (and raise its MTU so the SRv6 encapsulation still fits);
3. trigger kernel NDP (multicast ff02::1 + a directed probe carrying the
   peer's underlay address) and read ``ip -6 neigh`` on every interface;
4. report the neighbour tables to the Ryu app, which elects the edge
   routers and computes the SRv6 policies;
5. apply the returned policies: encap on the ingress, decap (End.DX4) on
   the egress, nothing on the middle routers.
"""

import ipaddress
import json
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

from mininet.log import info
from mininet.net import Mininet
from mininet.node import Node

CONTROLLER = "http://127.0.0.1:8080"
API = "/v1.0/srv6"
UNDERLAY_MTU = 1600
REQUEST_TIMEOUT = 5
REPORT_ATTEMPTS = 5


def _request(
    method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        CONTROLLER + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        result: dict[str, Any] = json.loads(resp.read().decode())
    return result


def _post_report(report: dict[str, Any]) -> None:
    last_error: OSError | None = None
    for attempt in range(REPORT_ATTEMPTS):
        try:
            _request("POST", f"{API}/report", report)
            return
        except urllib.error.HTTPError:
            raise
        except OSError as exc:
            last_error = exc
            if attempt < REPORT_ATTEMPTS - 1:
                time.sleep(1)
    raise RuntimeError(
        f"cannot reach the Ryu app at {CONTROLLER} - start it with "
        "'pixi run -e controller srv6-controller'"
    ) from last_error


def _router_intfs(net: Mininet, routers: set[str]) -> dict[str, list[str]]:
    """Map each router to the names of all its link interfaces."""
    intfs: dict[str, list[str]] = {name: [] for name in routers}
    for link in net.links:
        for intf in (link.intf1, link.intf2):
            if intf.node.name in routers:
                intfs[intf.node.name].append(intf.name)
    return intfs


def assign_underlay(
    net: Mininet, routers: set[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Give every router-router link an fd00:0:<n>::/64 prefix.

    Returns (own, probes) keyed by interface name: the address assigned
    to the local interface and the peer address to probe for NDP.
    """
    own: dict[str, str] = {}
    probes: dict[str, str] = {}
    index = 0
    for link in net.links:
        left, right = link.intf1, link.intf2
        if left.node.name not in routers or right.node.name not in routers:
            continue
        index += 1
        subnet = ipaddress.ip_network(f"fd00:0:{index}::/64")
        addr_left, addr_right = str(subnet[1]), str(subnet[2])
        for intf, addr, peer in (
            (left, addr_left, addr_right),
            (right, addr_right, addr_left),
        ):
            intf.node.cmd(f"ip -6 addr add {addr}/64 dev {intf.name}")
            intf.node.cmd(f"ip link set dev {intf.name} mtu {UNDERLAY_MTU}")
            own[intf.name] = addr
            probes[intf.name] = peer
    if index == 0:
        raise RuntimeError("no router-router links found for the SRv6 underlay")
    return own, probes


def _parse_neighbors(raw: str) -> list[str]:
    """Keep valid neighbour entries carrying a global underlay address."""
    addrs: list[str] = []
    for line in raw.splitlines():
        fields = line.split()
        if not fields or not fields[0].startswith("fd00:"):
            continue
        if "lladdr" in fields:
            addrs.append(fields[0])
    return addrs


def _parse_ipv4_prefixes(raw: str) -> list[str]:
    prefixes: list[str] = []
    for line in raw.splitlines():
        fields = line.split()
        if "inet" in fields:
            addr = fields[fields.index("inet") + 1]
            prefixes.append(str(ipaddress.ip_network(addr, strict=False)))
    return prefixes


def _parse_ipv6_addrs(raw: str) -> list[str]:
    addrs: list[str] = []
    for line in raw.splitlines():
        fields = line.split()
        if "inet6" in fields:
            addr = fields[fields.index("inet6") + 1].split("/")[0]
            if addr.startswith("fd00:"):
                addrs.append(addr)
    return addrs


def discover_neighbors(
    node: Node, intfs: list[str], probes: dict[str, str]
) -> dict[str, list[str]]:
    """Nudge kernel NDP on every interface and read the neighbour table.

    The multicast ping (all-nodes ff02::1) makes every peer answer; the
    directed probe to the peer's underlay address guarantees a neighbour
    entry with an lladdr for the fd00:: address we route toward.
    """
    result: dict[str, list[str]] = {}
    for intf in intfs:
        node.cmd(f"ping -6 -c 1 -W 1 -I {intf} ff02::1")
        target = probes.get(intf)
        if target is not None:
            node.cmd(f"ping -6 -c 1 -W 1 -I {intf} {target}")
        result[intf] = _parse_neighbors(node.cmd(f"ip -6 neigh show dev {intf}"))
    return result


def build_report(
    node: Node, intfs: list[str], neighbors: dict[str, list[str]]
) -> dict[str, Any]:
    """Read the live interface state of one router into a report."""
    interfaces: dict[str, dict[str, list[str]]] = {}
    for intf in intfs:
        interfaces[intf] = {
            "prefixes": _parse_ipv4_prefixes(
                node.cmd(f"ip -o -4 addr show dev {intf}")
            ),
            "addrs6": _parse_ipv6_addrs(
                node.cmd(f"ip -6 -o addr show dev {intf} scope global")
            ),
            "neighbors6": neighbors.get(intf, []),
        }
    return {"node": node.name, "intfs": interfaces}


def _policy_commands(policy: dict[str, Any]) -> list[str]:
    if policy["type"] == "encap":
        return [
            (
                f"ip -6 route replace {policy['sid']}/128 "
                f"via {policy['nexthop']} dev {policy['intf']}"
            ),
            (
                f"ip route replace {policy['prefix']} encap seg6 mode encap "
                f"segs {policy['sid']} dev {policy['intf']}"
            ),
        ]
    if policy["type"] == "decap":
        return [
            (
                f"ip -6 route replace {policy['sid']}/128 encap seg6local "
                f"action End.DX4 oif {policy['oif']}"
            )
        ]
    raise ValueError(f"unknown SRv6 policy type: {policy['type']!r}")


def _apply_policy(name: str, node: Node, policy: dict[str, Any]) -> None:
    for command in _policy_commands(policy):
        info(f"    {name}: {command}\n")
        output = node.cmd(command).strip()
        if output:
            info(f"    {name}: {output}\n")


def setup(net: Mininet, routers: Sequence[str] = ("n1", "n2", "n3")) -> None:
    """Discover the underlay with NDP and install Ryu's SRv6 policies."""
    router_set = set(routers)
    intfs = _router_intfs(net, router_set)
    nodes = {name: net.get(name) for name in routers}

    info("*** Enabling IPv6 forwarding\n")
    for node in nodes.values():
        node.cmd("sysctl -w net.ipv6.conf.all.forwarding=1")

    info("*** Assigning the SRv6 underlay\n")
    own, probes = assign_underlay(net, router_set)
    info(f"    {own}\n")

    reports: list[dict[str, Any]] = []
    for name in routers:
        node = nodes[name]
        neighbors = discover_neighbors(node, intfs[name], probes)
        info(f"*** {name} NDP neighbours: {neighbors}\n")
        reports.append(build_report(node, intfs[name], neighbors))

    info(f"*** Reporting topology to {CONTROLLER}\n")
    for report in reports:
        _post_report(report)

    policies: dict[str, Any] = _request("GET", f"{API}/policies")["policies"]

    info("*** Applying SRv6 policies\n")
    for name in routers:
        for policy in policies.get(name, []):
            _apply_policy(name, nodes[name], policy)
