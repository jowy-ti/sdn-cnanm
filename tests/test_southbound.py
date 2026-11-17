from __future__ import annotations

import pytest

from srctl.models import DesiredRoute, RouteState
from srctl.southbound import LiveSouthbound, SouthboundError

ROUTE_OUTPUT = """\
fc00:0:d::/48  encap seg6 mode encap segs 2 [ 2001:db8:4::10 2001:db8:6::10 ] dev s1-eth1 metric 1 pref medium
2001:db8:6::/64 dev s6-eth1 proto kernel metric 256 pref medium
"""


class FakeNode:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def pexec(self, argv: list[str]) -> tuple[str, str, int]:
        self.calls.append(argv)
        if argv[:3] == ["ip", "-6", "route"] and argv[3] == "show":
            return ROUTE_OUTPUT, "", 0
        if argv[:4] == ["ip", "-6", "route", "get"]:
            return (
                "2001:db8:4::10 from :: via fe80::1 dev s1-eth1 src fc00:0::1 "
                "metric 20 pref medium\n",
                "",
                0,
            )
        return "", "", 0


class FakeNet(dict[str, FakeNode]):
    pass


def test_parse_encap_routes_and_egress_dev() -> None:
    node = FakeNode()
    southbound = LiveSouthbound(FakeNet(s1=node))

    routes = southbound.list_encap_routes("s1")
    assert routes == (
        RouteState(
            prefix="fc00:0:d::/48",
            segments=("2001:db8:4::10", "2001:db8:6::10"),
            mode="encap",
            metric=1,
            dev="s1-eth1",
        ),
    )
    assert southbound.egress_dev("s1", "2001:db8:4::10") == "s1-eth1"


def test_replace_and_delete_commands() -> None:
    node = FakeNode()
    southbound = LiveSouthbound(FakeNet(s1=node))
    route = DesiredRoute(
        policy_id="p1",
        ingress="s1",
        prefix="fc00:0:d::/48",
        segments=("2001:db8:4::10", "2001:db8:6::10"),
        mode="encap",
        metric=1,
        dev="s1-eth1",
    )
    southbound.replace("s1", route)
    assert node.calls[-1] == [
        "ip",
        "-6",
        "route",
        "replace",
        "fc00:0:d::/48",
        "encap",
        "seg6",
        "mode",
        "encap",
        "segs",
        "2001:db8:4::10,2001:db8:6::10",
        "metric",
        "1",
        "dev",
        "s1-eth1",
    ]

    southbound.delete(
        "s1",
        RouteState(
            prefix=route.prefix,
            segments=route.segments,
            mode=route.mode,
            metric=route.metric,
            dev=route.dev,
        ),
    )
    assert node.calls[-1][3] == "del"


def test_unknown_node_is_an_error() -> None:
    southbound = LiveSouthbound(FakeNet())
    with pytest.raises(SouthboundError):
        southbound.egress_dev("s1", "2001:db8:4::10")
