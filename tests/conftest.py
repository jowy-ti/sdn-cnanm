from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from net_topology_yaml import Topology, load_topology  # noqa: E402
from srctl.topology import TopologyView  # noqa: E402


@pytest.fixture()
def topology() -> Topology:
    return load_topology(ROOT / "topologies" / "srv6.yaml")


@pytest.fixture()
def view(topology: Topology) -> TopologyView:
    return TopologyView.from_topology(topology)
