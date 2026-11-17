from __future__ import annotations

import net_topology_yaml as lab
from net_topology_yaml import Topology


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
