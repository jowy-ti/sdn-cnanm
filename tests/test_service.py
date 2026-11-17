from __future__ import annotations

import textwrap

import pytest

from net_topology_yaml import Topology
from srctl.models import ControllerError
from srctl.service import SaltController
from srctl.southbound import DryRunSouthbound

PREFIXES = {
    "h1": ("fc00:0:6::/48",),
    "h2": ("fc00:0:d::/48",),
}
DEVS = {
    ("s1", "2001:db8:4::10"): "s1-eth1",
    ("s6", "2001:db8:4::10"): "s6-eth0",
    ("s1", "2001:db8:6::10"): "s1-eth1",
    ("s6", "2001:db8:6::10"): "s6-eth0",
}


@pytest.fixture()
def controller(topology: Topology) -> SaltController:
    controller = SaltController(
        topology, DryRunSouthbound(prefixes=PREFIXES, devs=DEVS)
    )
    controller.start()
    return controller


def _apply(controller: SaltController, text: str, prune: bool = False) -> None:
    controller.apply_document(textwrap.dedent(text), prune=prune)


def test_baseline_policies(controller: SaltController) -> None:
    policies = {policy.id: policy for policy in controller.list_policies()}
    assert policies["yaml-1"].segments == (
        "2001:db8:4::10",
        "2001:db8:6::10",
    )
    assert policies["yaml-2"].segments == (
        "2001:db8:4::10",
        "2001:db8:1::10",
    )
    assert all(policy.origin == "yaml" for policy in policies.values())


def test_file_policy_overrides_baseline(controller: SaltController) -> None:
    _apply(
        controller,
        """
        policies:
          - id: h1-h2-direct
            ingress: s1
            destination: h2
            via: []
        """,
    )
    policies = {policy.id: policy for policy in controller.list_policies()}
    assert "yaml-1" not in policies
    assert policies["h1-h2-direct"].origin == "file"
    assert policies["h1-h2-direct"].segments == ("2001:db8:6::10",)


def test_explicit_through_is_verbatim(controller: SaltController) -> None:
    _apply(
        controller,
        """
        policies:
          - id: explicit
            node: s6
            to: h1
            through: [2001:db8:4::10, 2001:db8:1::10]
        """,
    )
    policies = {policy.id: policy for policy in controller.list_policies()}
    assert policies["explicit"].segments == (
        "2001:db8:4::10",
        "2001:db8:1::10",
    )


def test_prune_removes_only_file_policies(controller: SaltController) -> None:
    _apply(
        controller,
        """
        policies:
          - id: extra
            ingress: s6
            destination: h2
            via: [s4]
        """,
    )
    assert "extra" in {p.id for p in controller.list_policies()}

    _apply(controller, "policies: []", prune=True)
    ids = {p.id for p in controller.list_policies()}
    assert ids == {"yaml-1", "yaml-2"}


def test_prune_restores_shadowed_baseline(controller: SaltController) -> None:
    _apply(
        controller,
        """
        policies:
          - id: override
            ingress: s1
            destination: h2
            via: []
        """,
    )
    assert "yaml-1" not in {p.id for p in controller.list_policies()}

    _apply(controller, "policies: []", prune=True)
    assert "yaml-1" in {p.id for p in controller.list_policies()}


def test_diff_does_not_change_state(controller: SaltController) -> None:
    before = controller.list_policies()
    result = controller.apply_document(
        """
policies:
  - id: planned
    ingress: s6
    destination: h2
    via: [s4]
""",
        dry_run=True,
    )
    assert result.actions
    assert controller.list_policies() == before


def test_invalid_document_changes_nothing(controller: SaltController) -> None:
    before = controller.list_policies()
    with pytest.raises(ControllerError):
        _apply(controller, "policies: [{ingress: s1}]")
    assert controller.list_policies() == before


def test_invalid_waypoint_is_rejected(controller: SaltController) -> None:
    with pytest.raises(ControllerError):
        _apply(
            controller,
            """
            policies:
              - id: bad
                ingress: s1
                destination: h2
                via: [r2]
            """,
        )
