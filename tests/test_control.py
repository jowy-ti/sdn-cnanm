from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from net_topology_yaml import Topology
from srctl.control import ControlClient, ControlError, ControlServer
from srctl.service import SaltController
from srctl.southbound import DryRunSouthbound

from .test_service import DEVS, PREFIXES

YAML_DOC = """\
policies:
  - id: applied
    ingress: s6
    destination: h2
    via: [s4]
"""


@pytest.fixture()
def client(topology: Topology, tmp_path: Path) -> Iterator[ControlClient]:
    controller = SaltController(
        topology, DryRunSouthbound(prefixes=PREFIXES, devs=DEVS)
    )
    controller.start()
    server = ControlServer(controller, str(tmp_path / "srctl.sock"))
    server.start()
    try:
        yield ControlClient(server.path)
    finally:
        server.stop()


def test_query_methods(client: ControlClient) -> None:
    assert client.request("health") == {"status": "ok"}
    status = client.request("status")
    assert status["policies"] == 2
    policies = client.request("policies.list")
    assert {policy["id"] for policy in policies} == {"yaml-1", "yaml-2"}
    assert "s4" in client.request("topology")["routers"]


def test_apply_and_prune(client: ControlClient) -> None:
    result = client.request("policies.apply", {"content": YAML_DOC})
    assert result["applied"] == ["applied"]
    assert result["errors"] == []
    ids = {policy["id"] for policy in client.request("policies.list")}
    assert "applied" in ids

    result = client.request(
        "policies.apply", {"content": "policies: []\n", "prune": True}
    )
    assert result["removed"] == ["applied"]
    ids = {policy["id"] for policy in client.request("policies.list")}
    assert ids == {"yaml-1", "yaml-2"}


def test_diff_does_not_apply(client: ControlClient) -> None:
    result = client.request("policies.diff", {"content": YAML_DOC})
    assert any(action["kind"] == "add" for action in result["actions"])
    ids = {policy["id"] for policy in client.request("policies.list")}
    assert "applied" not in ids


def test_paths_compute(client: ControlClient) -> None:
    result = client.request(
        "paths.compute",
        {"source": "s1", "destination": "h2", "via": ["s4"]},
    )
    assert result["segments"] == ["2001:db8:4::10", "2001:db8:6::10"]


def test_invalid_document_is_reported(client: ControlClient) -> None:
    with pytest.raises(ControlError) as excinfo:
        client.request("policies.apply", {"content": "policies: [{ingress: s1}]\n"})
    assert excinfo.value.kind == "invalid"


def test_unavailable_socket(tmp_path: Path) -> None:
    with pytest.raises(ControlError) as excinfo:
        ControlClient(str(tmp_path / "missing.sock")).request("status")
    assert excinfo.value.kind == "unavailable"
