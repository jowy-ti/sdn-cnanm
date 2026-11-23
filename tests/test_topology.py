from __future__ import annotations

from pathlib import Path

import pytest

from net_topology_yaml import load_topology
from srctl.models import ControllerError
from srctl.topology import TopologyView


def test_graph_matches_topology(view: TopologyView) -> None:
    assert set(view.routers) == {"s1", "r2", "r3", "s4", "r5", "s6"}
    assert set(view.hosts) == {"h1", "h2"}
    assert view.graph.number_of_nodes() == 8
    assert view.graph.number_of_edges() == 8
    assert view.graph["r2"]["r5"]["weight"] == 1


def test_sid_catalog(view: TopologyView) -> None:
    assert view.node_sid("s4").address == "2001:db8:4::10"
    assert view.terminal_sid("h2").address == "2001:db8:6::10"
    assert view.terminal_sid("h1").address == "2001:db8:1::10"
    with pytest.raises(ControllerError):
        view.node_sid("r2")  # pure underlay forwarder, no end SID


def test_segments_keep_yaml_order(view: TopologyView) -> None:
    assert view.segments_for("h2", ["s4"]) == (
        "2001:db8:4::10",
        "2001:db8:6::10",
    )
    assert view.segments_for("h2", []) == ("2001:db8:6::10",)


def test_path_uses_shortcut_without_waypoints(view: TopologyView) -> None:
    path = view.path("s1", "h2")
    assert "s4" not in path
    assert path[0] == "s1" and path[-1] == "h2"


def test_path_honours_waypoints_and_avoid(view: TopologyView) -> None:
    path = view.path("s1", "h2", via=["s4"])
    assert path == ("s1", "r2", "r3", "s4", "r5", "s6", "h2")
    shortcut = view.path("s1", "h2", avoid=["r3"])
    assert "r3" not in shortcut
    assert shortcut == ("s1", "r2", "r5", "s6", "h2")
    with pytest.raises(ControllerError):
        view.path("s1", "h2", avoid=["r2"])  # s1's only link is to r2


def test_unknown_waypoint_fails(view: TopologyView) -> None:
    with pytest.raises(ControllerError):
        view.segments_for("h2", ["nope"])
    with pytest.raises(ControllerError):
        view.path("s1", "h2", via=["r2"])  # no end SID


def test_explicit_terminal_sid_disambiguates() -> None:
    root = Path(__file__).resolve().parents[1]
    big = TopologyView.from_topology(
        load_topology(root / "topologies" / "srv6_big.yaml")
    )
    # h1 has two end.dx6 SIDs (ing1 and ing2), so terminal_sid is ambiguous.
    with pytest.raises(ControllerError):
        big.terminal_sid("h1")
    assert big.segments_for("h1", ["msrv", "2001:db8:1::10"]) == (
        "2001:db8:7::10",
        "2001:db8:1::10",
    )
    assert big.segments_for("h2", ["msrv"]) == (
        "2001:db8:7::10",
        "2001:db8:18::10",
    )
