from __future__ import annotations

import pytest

from srctl.models import ControllerError
from srctl.policy_doc import parse_policy_document


def test_yaml_api_form() -> None:
    specs = parse_policy_document(
        """
policies:
  - id: p1
    ingress: s1
    destination: h2
    via: [s4]
    avoid: [r3]
    mode: inline
    metric: 2
"""
    )
    assert len(specs) == 1
    spec = specs[0]
    assert spec.id == "p1"
    assert (spec.ingress, spec.destination) == ("s1", "h2")
    assert spec.via == ("s4",)
    assert spec.avoid == ("r3",)
    assert spec.mode == "inline"
    assert spec.metric == 2


def test_json_topology_form() -> None:
    specs = parse_policy_document(
        '{"policies": [{"node": "s6", "to": "h1", '
        '"through": ["2001:db8:4::10"], "cost": 3}]}'
    )
    assert specs[0].ingress == "s6"
    assert specs[0].destination == "h1"
    assert specs[0].through == ("2001:db8:4::10",)
    assert specs[0].metric == 3


def test_bare_list_is_accepted() -> None:
    specs = parse_policy_document(
        "- {ingress: s1, destination: h2}\n"
    )
    assert specs[0].via == ()


def test_empty_document_is_valid_for_prune() -> None:
    assert parse_policy_document("policies: []\n") == ()


def test_mixing_forms_is_rejected() -> None:
    with pytest.raises(ControllerError):
        parse_policy_document(
            "policies: [{node: s1, destination: h2}]\n"
        )


def test_duplicate_keys_and_ids_are_rejected() -> None:
    with pytest.raises(ControllerError):
        parse_policy_document(
            "policies:\n"
            "  - {ingress: s1, destination: h2}\n"
            "  - {ingress: s1, destination: h2}\n"
        )
    with pytest.raises(ControllerError):
        parse_policy_document(
            "policies:\n"
            "  - {id: x, ingress: s1, destination: h2}\n"
            "  - {id: x, ingress: s6, destination: h1}\n"
        )


def test_unknown_keys_and_bad_modes_are_rejected() -> None:
    with pytest.raises(ControllerError):
        parse_policy_document("policies: [{ingress: s1, destination: h2, x: 1}]\n")
    with pytest.raises(ControllerError):
        parse_policy_document(
            "policies: [{ingress: s1, destination: h2, mode: tunnel}]\n"
        )
    with pytest.raises(ControllerError):
        parse_policy_document(
            "policies: [{ingress: s1, destination: h2, metric: -1}]\n"
        )


def test_missing_policies_key_is_rejected() -> None:
    with pytest.raises(ControllerError):
        parse_policy_document("other: []\n")
