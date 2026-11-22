from __future__ import annotations

from typing import Any

import pytest

from srctl.models import DesiredRoute, RouteState
from srctl.southbound import (
    DryRunSouthbound,
    LiveSouthbound,
    SouthboundError,
    route_state_from_message,
    srh_from_encap,
)


class FakeEncap:
    def __init__(self, srh: dict[str, Any]) -> None:
        self._attrs = [("SEG6_IPTUNNEL_SRH", srh)]

    def get(self, key: str, default: Any = None) -> Any:
        return self._attrs if key == "attrs" else default


class FakeMessage:
    def __init__(self, attrs: dict[str, Any], **fields: Any) -> None:
        self._attrs = attrs
        self._fields = fields

    def get_attr(self, name: str) -> Any:
        return self._attrs.get(name)

    def get(self, name: str, default: Any = None) -> Any:
        return self._fields.get(name, default)


def test_parse_seg6_route_message() -> None:
    message = FakeMessage(
        {
            "RTA_ENCAP_TYPE": 5,
            "RTA_ENCAP": FakeEncap(
                {
                    "mode": "encap",
                    "segs": ["2001:db8:4::10", "2001:db8:6::10"],
                }
            ),
            "RTA_DST": "fc00:0:d::",
            "RTA_PRIORITY": 1,
            "RTA_OIF": 3,
        },
        dst_len=48,
    )
    state = route_state_from_message(message, {3: "s1-eth1"})
    assert state == RouteState(
        prefix="fc00:0:d::/48",
        segments=("2001:db8:4::10", "2001:db8:6::10"),
        mode="encap",
        metric=1,
        dev="s1-eth1",
    )


def test_non_seg6_route_is_ignored() -> None:
    message = FakeMessage({"RTA_ENCAP_TYPE": 1}, dst_len=48)
    assert route_state_from_message(message, {}) is None


def test_srh_extraction() -> None:
    assert srh_from_encap(None) is None
    assert srh_from_encap({"attrs": []}) is None
    srh = {"mode": "encap", "segs": ["2001:db8::1"]}
    assert srh_from_encap(FakeEncap(srh)) == srh


def test_dry_run_records_routes() -> None:
    southbound = DryRunSouthbound()
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
    assert southbound.list_encap_routes("s1") == (
        RouteState(
            prefix="fc00:0:d::/48",
            segments=("2001:db8:4::10", "2001:db8:6::10"),
            mode="encap",
            metric=1,
            dev="s1-eth1",
        ),
    )
    southbound.delete("s1", southbound.list_encap_routes("s1")[0])
    assert southbound.list_encap_routes("s1") == ()


def test_live_southbound_requires_a_started_node() -> None:
    with pytest.raises(SouthboundError):
        LiveSouthbound({}).egress_dev("s1", "2001:db8:4::10")
    with pytest.raises(SouthboundError):
        LiveSouthbound({"s1": object()}).egress_dev("s1", "2001:db8:4::10")
