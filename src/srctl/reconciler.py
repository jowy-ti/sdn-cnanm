"""Diff desired policies against the data plane and apply the changes."""

from __future__ import annotations

from collections.abc import Mapping

from .models import DesiredRoute, ReconcileAction, ReconcileResult, RouteState
from .southbound import Southbound, SouthboundError


class Reconciler:
    """Owns the set of routes the controller has installed.

    Only routes recorded in ``owned`` are ever deleted.  This keeps the
    controller from removing an OSPF route or a route installed by someone
    else when a policy disappears.
    """

    def __init__(self, southbound: Southbound) -> None:
        self._southbound = southbound
        self._owned: dict[tuple[str, str], RouteState] = {}

    @property
    def owned(self) -> Mapping[tuple[str, str], RouteState]:
        return dict(self._owned)

    def apply(
        self, desired: Mapping[tuple[str, str], DesiredRoute], dry_run: bool = False
    ) -> ReconcileResult:
        actions: list[ReconcileAction] = []
        errors: list[str] = []

        by_ingress: dict[str, dict[str, DesiredRoute]] = {}
        for (ingress, prefix), route in desired.items():
            by_ingress.setdefault(ingress, {})[prefix] = route
        ingress_names = set(by_ingress) | {ingress for ingress, _ in self._owned}

        for ingress in sorted(ingress_names):
            try:
                actual = {
                    route.prefix: route
                    for route in self._southbound.list_encap_routes(ingress)
                }
            except SouthboundError as exc:
                errors.append(str(exc))
                continue

            wanted = by_ingress.get(ingress, {})
            for prefix, route in sorted(wanted.items()):
                current = actual.get(prefix)
                if current is not None and _matches(current, route):
                    if not dry_run:
                        self._owned[(ingress, prefix)] = current
                    actions.append(
                        ReconcileAction("noop", ingress, prefix, route.policy_id)
                    )
                    continue
                if not dry_run:
                    try:
                        self._southbound.replace(ingress, route)
                    except SouthboundError as exc:
                        errors.append(str(exc))
                        continue
                    installed = RouteState(
                        prefix=route.prefix,
                        segments=route.segments,
                        mode=route.mode,
                        metric=route.metric,
                        dev=route.dev,
                    )
                    self._owned[(ingress, prefix)] = installed
                kind = "replace" if current is not None else "add"
                actions.append(ReconcileAction(kind, ingress, prefix, route.policy_id))

            for prefix, current in sorted(actual.items()):
                if prefix in wanted:
                    continue
                if (ingress, prefix) not in self._owned:
                    continue
                if not dry_run:
                    try:
                        self._southbound.delete(ingress, current)
                    except SouthboundError as exc:
                        errors.append(str(exc))
                        continue
                    del self._owned[(ingress, prefix)]
                actions.append(ReconcileAction("delete", ingress, prefix))

            # Drop bookkeeping for routes that vanished outside our control and
            # are no longer desired.
            if not dry_run:
                for key in list(self._owned):
                    owned_ingress, owned_prefix = key
                    if owned_ingress != ingress:
                        continue
                    if owned_prefix in wanted or owned_prefix in actual:
                        continue
                    del self._owned[key]

        return ReconcileResult(actions=tuple(actions), errors=tuple(errors))

    def plan(self, desired: Mapping[tuple[str, str], DesiredRoute]) -> ReconcileResult:
        """Compute the actions without touching the data plane or owned state."""
        return self.apply(desired, dry_run=True)


def _matches(current: RouteState, desired: DesiredRoute) -> bool:
    return (
        current.prefix == desired.prefix
        and current.segments == desired.segments
        and current.mode == desired.mode
        and current.metric == desired.metric
        and current.dev == desired.dev
    )
