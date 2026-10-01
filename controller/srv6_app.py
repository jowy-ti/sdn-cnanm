"""Ryu app: SRv6 topology knowledge base for the SDN lab.

Routers started by ``sudo python3 src/net_topology.py srv6`` report their
NDP-derived neighbour tables and connected IPv4 prefixes:

    POST /v1.0/srv6/report   {"node": "n1", "intfs": {...}}

The app assembles the underlay graph from the reports, elects the edge
routers (they become ingress/egress) and answers with per-node SRv6
policies:

    GET /v1.0/srv6/policies  {"policies": {"n1": [...], ...}}

Policy types:

    encap  - ingress installs an SRv6 tunnel toward the egress SID for a
             remote host prefix.
    decap  - egress installs a seg6local End.DX4 route for its SID so the
             original IPv4 packet is delivered to the local host subnet.

Middle routers receive an empty list: they only forward with NDP.

Run with:  pixi run -e controller srv6-controller
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from ryu.app.wsgi import ControllerBase, Request, Response, WSGIApplication, route
from ryu.base import app_manager

LOG = logging.getLogger(__name__)

API = "/v1.0/srv6"


def build_adjacency(
    reports: dict[str, Any],
) -> tuple[dict[str, dict[str, dict[str, str]]], dict[str, tuple[str, str]]]:
    """Derive the underlay adjacency from the reported NDP tables.

    Returns (adjacency, addr_owner) where adjacency maps a router to its
    neighbours as {nbr: {"nexthop": <nbr's fd00 addr>, "intf": <local
    interface>}} and addr_owner maps every reported IPv6 address to the
    (router, interface) that owns it.
    """
    addr_owner: dict[str, tuple[str, str]] = {}
    for node, report in reports.items():
        for intf, info in report["intfs"].items():
            for addr in info["addrs6"]:
                addr_owner[addr] = (node, intf)

    adjacency: dict[str, dict[str, dict[str, str]]] = {}
    for node, report in reports.items():
        for intf, info in report["intfs"].items():
            for nbr in info["neighbors6"]:
                owner = addr_owner.get(nbr)
                if owner is None or owner[0] == node:
                    continue
                adjacency.setdefault(node, {})[owner[0]] = {
                    "nexthop": nbr,
                    "intf": intf,
                }
    return adjacency, addr_owner


def shortest_path(
    adjacency: dict[str, dict[str, dict[str, str]]], src: str, dst: str
) -> list[str] | None:
    """BFS shortest router path (inclusive) or None if unreachable."""
    queue: list[list[str]] = [[src]]
    seen = {src}
    while queue:
        path = queue.pop(0)
        tail = path[-1]
        if tail == dst:
            return path
        for nxt in adjacency.get(tail, {}):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(path + [nxt])
    return None


def compute_policies(reports: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Turn raw NDP reports into per-node SRv6 policies.

    A prefix reported by exactly one router marks that router as an edge
    (ingress/egress) for that host subnet.  For every ordered pair of edge
    routers the policy is:

    * encap at the ingress: underlay route to the egress SID plus an
      ``encap seg6`` route steering the remote prefix into the tunnel;
    * decap at the egress: ``seg6local End.DX4`` for the same SID.

    The SID is the egress's address on the last-hop link, so intermediate
    routers only need their connected routes and NDP to forward.
    """
    adjacency, _ = build_adjacency(reports)

    prefix_owners: dict[str, list[tuple[str, str]]] = {}
    for node, report in reports.items():
        for intf, info in report["intfs"].items():
            for prefix in info["prefixes"]:
                prefix_owners.setdefault(prefix, []).append((node, intf))
    host_prefixes: list[tuple[str, str, str]] = [
        (prefix, owners[0][0], owners[0][1])
        for prefix, owners in prefix_owners.items()
        if len(owners) == 1
    ]

    policies: dict[str, list[dict[str, str]]] = {node: [] for node in reports}
    for _src_prefix, src_node, _src_intf in host_prefixes:
        for dst_prefix, dst_node, dst_intf in host_prefixes:
            if src_node == dst_node:
                continue
            path = shortest_path(adjacency, src_node, dst_node)
            if path is None:
                LOG.warning("no underlay path from %s to %s", src_node, dst_node)
                continue

            prev = path[-2]
            last_link = adjacency.get(dst_node, {}).get(prev)
            first_link = adjacency.get(src_node, {}).get(path[1])
            if last_link is None or first_link is None:
                LOG.warning("underlay path %s is missing one-sided NDP info", path)
                continue
            sid_addrs = reports[dst_node]["intfs"][last_link["intf"]]["addrs6"]
            if not sid_addrs:
                LOG.warning(
                    "router %s has no IPv6 address on %s", dst_node, last_link["intf"]
                )
                continue
            sid = sid_addrs[0]

            policies[src_node].append(
                {
                    "type": "encap",
                    "prefix": dst_prefix,
                    "sid": sid,
                    "nexthop": first_link["nexthop"],
                    "intf": first_link["intf"],
                }
            )
            policies[dst_node].append({"type": "decap", "sid": sid, "oif": dst_intf})
    return policies


class Srv6App(app_manager.RyuApp):
    """Stores router reports and serves the SRv6 policy REST API."""

    _CONTEXTS: ClassVar[dict[str, type]] = {"wsgi": WSGIApplication}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reports: dict[str, Any] = {}
        wsgi: WSGIApplication = kwargs["wsgi"]
        wsgi.register(Srv6Controller, {"srv6_app": self})


class Srv6Controller(ControllerBase):
    def __init__(
        self, req: Request, link: Any, data: dict[str, Any], **config: Any
    ) -> None:
        super().__init__(req, link, data, **config)
        self.app: Srv6App = data["srv6_app"]

    @route("srv6", f"{API}/report", methods=["POST"])
    def post_report(self, req: Request, **kwargs: Any) -> Response:
        try:
            raw: dict[str, Any] | None = req.json if req.body else None
        except ValueError:
            return Response(status="400 Bad Request", body="invalid JSON body")
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("node"), str)
            or not isinstance(raw.get("intfs"), dict)
        ):
            return Response(
                status="400 Bad Request",
                body="expected {'node': <name>, 'intfs': {...}}",
            )
        report = raw
        node: str = report["node"]
        self.app.reports[node] = report
        self.app.logger.info("report from %s: %s", node, sorted(report["intfs"]))
        body = json.dumps({"nodes": sorted(self.app.reports)})
        return Response(
            content_type="application/json", body=body, status="201 Created"
        )

    @route("srv6", f"{API}/policies", methods=["GET"])
    def get_policies(self, req: Request, **kwargs: Any) -> Response:
        policies = compute_policies(self.app.reports)
        self.app.logger.info("policies: %s", json.dumps(policies))
        body = json.dumps({"policies": policies})
        return Response(content_type="application/json", body=body)

    @route("srv6", f"{API}/topology", methods=["GET"])
    def get_topology(self, req: Request, **kwargs: Any) -> Response:
        adjacency, _ = build_adjacency(self.app.reports)
        graph = {node: sorted(nbrs) for node, nbrs in adjacency.items()}
        body = json.dumps({"reports": self.app.reports, "adjacency": graph})
        return Response(content_type="application/json", body=body)
