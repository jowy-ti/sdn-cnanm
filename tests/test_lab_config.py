from __future__ import annotations

from pathlib import Path

import pytest

import net_topology_yaml as lab
from net_topology_yaml import Topology, TopologyError, load_topology


MINIMAL_TOPO = """\
hosts:
  - name: h1
routers:
  - name: r1
    loopback: 2001:db8:1::1/64
    sids:
      - address: 2001:db8:1::10
        to: h1
  - name: r2
    loopback: 2001:db8:2::1/64
    sids:
      - address: 2001:db8:2::10
        behavior: end
links:
  - [r1, r2]
  - [r1, h1]
policies:
  - node: r1
    to: h1
    via: [r2]
"""


def _write_topology(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "topo.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class FakeNode:
    name = "fake"
    cwd = "/tmp"

    def __init__(self, use_v4: bool, use_v6: bool) -> None:
        self.use_v4 = use_v4
        self.use_v6 = use_v6


def test_lab_is_ipv6_only(topology: Topology) -> None:
    assert all(not router.use_v4 for router in topology.routers)
    assert all(not host.use_v4 for host in topology.hosts)


def test_ipv6_only_router_has_no_ospfd() -> None:
    config = lab.YamlRouterConfig(FakeNode(use_v4=False, use_v6=True))
    assert [daemon.NAME for daemon in config.daemons] == ["ospf6d"]


def test_dual_stack_router_keeps_ospfd() -> None:
    config = lab.YamlRouterConfig(FakeNode(use_v4=True, use_v6=True))
    assert [daemon.NAME for daemon in config.daemons] == ["ospfd", "ospf6d"]


def test_topology_accepts_via_waypoints(tmp_path: Path) -> None:
    topology = load_topology(_write_topology(tmp_path, MINIMAL_TOPO))
    assert topology.policies[0].via == ("r2",)


@pytest.mark.parametrize(
    ("via", "message"),
    [
        ("[h1]", "hosts cannot be used"),
        ("[2001:db8:9::10]", "not defined in the topology"),
        ("[r1]", "without exactly one 'end' SID"),
    ],
)
def test_topology_rejects_invalid_via(
    tmp_path: Path, via: str, message: str
) -> None:
    text = MINIMAL_TOPO.replace("via: [r2]", f"via: {via}")
    with pytest.raises(TopologyError, match=message):
        load_topology(_write_topology(tmp_path, text))


def test_topology_rejects_through(tmp_path: Path) -> None:
    text = MINIMAL_TOPO.replace(
        "via: [r2]", "through: [2001:db8:2::10]"
    )
    with pytest.raises(
        TopologyError, match="only accepted in srctl policy documents"
    ):
        load_topology(_write_topology(tmp_path, text))
