"""Shared data models for the SRv6 policy controller."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


class ControllerError(ValueError):
    """A controller request is invalid against the current topology."""


class NotFoundError(ControllerError):
    """A requested policy does not exist."""


class ConflictError(ControllerError):
    """A request conflicts with an existing policy."""


@dataclass(frozen=True, slots=True)
class SidEntry:
    """One local SID from the YAML catalog."""

    address: str
    """Canonical 128-bit IPv6 address (no prefix length)."""

    prefix: str
    """Canonical /128 prefix, used for validation and display."""

    behavior: str
    owner: str
    to: str | None = None
    cost: int = 1


@dataclass(frozen=True, slots=True)
class PolicyIntent:
    """Desired ingress policy, independent of the data plane."""

    id: str
    ingress: str
    destination: str
    segments: tuple[str, ...]
    mode: str = "encap"
    metric: int = 1
    origin: str = "api"


@dataclass(frozen=True, slots=True)
class DesiredRoute:
    """Fully resolved route the controller wants on an ingress router."""

    policy_id: str
    ingress: str
    prefix: str
    segments: tuple[str, ...]
    mode: str
    metric: int
    dev: str


@dataclass(frozen=True, slots=True)
class RouteState:
    """An ``encap seg6`` route read back from the data plane."""

    prefix: str
    segments: tuple[str, ...]
    mode: str
    metric: int
    dev: str


@dataclass(frozen=True, slots=True)
class ReconcileAction:
    kind: str
    ingress: str
    prefix: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    actions: tuple[ReconcileAction, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyView:
    """Public representation of a policy and its installation status."""

    id: str
    ingress: str
    destination: str
    segments: tuple[str, ...]
    mode: str
    metric: int
    origin: str
    status: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Outcome of applying one YAML/JSON policy document."""

    applied: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    actions: tuple[ReconcileAction, ...] = ()
    errors: tuple[str, ...] = ()
    policy_errors: Mapping[str, str] = field(default_factory=dict[str, str])
