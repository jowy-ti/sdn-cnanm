"""Controller service: policy state, path computation and reconciliation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from threading import RLock
from typing import Any

from .models import (
    ApplyResult,
    ConflictError,
    ControllerError,
    DesiredRoute,
    PolicyIntent,
    PolicyView,
    ReconcileResult,
)
from .policy_doc import PolicySpec, parse_policy_document
from .reconciler import Reconciler
from .southbound import Southbound, SouthboundError
from .topology import TopologyView, canonical_sid

_MODES = ("encap", "inline")


class SaltController:
    """Owns the desired SRv6 policies and keeps the lab in sync with them.

    The YAML topology ``policies`` are the baseline.  Policies applied from a
    YAML/JSON document are an overlay: they win when they share an id or an
    ``(ingress, destination)`` pair with a baseline policy, and ``--prune``
    removes only overlay policies, so the baseline becomes active again.
    """

    def __init__(self, topology: Any, southbound: Southbound) -> None:
        self._topology = topology
        self.view = TopologyView.from_topology(topology)
        self._southbound = southbound
        self._reconciler = Reconciler(southbound)
        self._lock = RLock()
        self._baseline: dict[str, PolicyIntent] = {}
        self._file: dict[str, PolicyIntent] = {}
        self._errors: dict[str, str] = {}
        self._last = ReconcileResult()

    # -- lifecycle --------------------------------------------------------- #

    def start(self) -> ReconcileResult:
        """Load the baseline YAML policies and install them."""
        with self._lock:
            baseline = self._load_baseline()
            self._baseline = baseline
            self._file = {}
            return self.reconcile_locked()

    def _load_baseline(self) -> dict[str, PolicyIntent]:
        loaded: dict[str, PolicyIntent] = {}
        seen: set[tuple[str, str]] = set()
        for index, raw in enumerate(getattr(self._topology, "policies", ())):
            policy = self._from_yaml(f"yaml-{index + 1}", raw)
            key = (policy.ingress, policy.destination)
            if key in seen:
                raise ConflictError(
                    f"a policy for {policy.ingress} -> {policy.destination} "
                    "already exists in the YAML file"
                )
            seen.add(key)
            loaded[policy.id] = policy
        return loaded

    def _from_yaml(self, policy_id: str, raw: Any) -> PolicyIntent:
        # The topology baseline uses the same friendly form as a policy
        # document: router names or SID literals in ``via``, with the
        # destination's terminal SID appended by the controller.
        via = tuple(str(item) for item in raw.via)
        segments = self.view.segments_for(str(raw.to), via)
        policy = PolicyIntent(
            id=policy_id,
            ingress=str(raw.node),
            destination=str(raw.to),
            segments=segments,
            mode=str(raw.mode),
            metric=int(raw.cost),
            origin="yaml",
        )
        self._validate_policy(policy)
        return policy

    # -- declarative apply ------------------------------------------------- #

    def apply_document(
        self, text: str, prune: bool = False, dry_run: bool = False
    ) -> ApplyResult:
        """Apply a YAML/JSON policy document (or plan it when ``dry_run``)."""
        with self._lock:
            specs = parse_policy_document(text)
            intents = tuple(self._intent_from_spec(spec) for spec in specs)
            self._check_document_ids(intents)

            old_file = dict(self._file)
            new_file = self._merge_file(old_file, intents, prune)
            removed = tuple(sorted(set(old_file) - set(new_file)))

            if dry_run:
                desired, errors = self._desired_routes(
                    self._effective_for(new_file)
                )
                result = self._reconciler.plan(desired)
                return ApplyResult(
                    applied=tuple(policy.id for policy in intents),
                    removed=removed,
                    actions=result.actions,
                    errors=result.errors,
                    policy_errors=errors,
                )

            self._file = new_file
            result = self.reconcile_locked()
            return ApplyResult(
                applied=tuple(policy.id for policy in intents),
                removed=removed,
                actions=result.actions,
                errors=result.errors,
                policy_errors=dict(self._errors),
            )

    def _intent_from_spec(self, spec: PolicySpec) -> PolicyIntent:
        policy_id = spec.id or f"{spec.ingress}-{spec.destination}"
        if spec.through is not None:
            segments = tuple(canonical_sid(segment)[0] for segment in spec.through)
        else:
            self.view.path(spec.ingress, spec.destination, spec.via, spec.avoid)
            segments = self.view.segments_for(spec.destination, spec.via)
        policy = PolicyIntent(
            id=policy_id,
            ingress=spec.ingress,
            destination=spec.destination,
            segments=segments,
            mode=spec.mode,
            metric=spec.metric,
            origin="file",
        )
        self._validate_policy(policy)
        return policy

    @staticmethod
    def _check_document_ids(intents: Sequence[PolicyIntent]) -> None:
        ids: set[str] = set()
        for policy in intents:
            if policy.id in ids:
                raise ConflictError(
                    f"policy id {policy.id!r} appears more than once in the document"
                )
            ids.add(policy.id)

    @staticmethod
    def _merge_file(
        old: Mapping[str, PolicyIntent],
        intents: Sequence[PolicyIntent],
        prune: bool,
    ) -> dict[str, PolicyIntent]:
        merged: dict[str, PolicyIntent] = {} if prune else dict(old)
        for policy in intents:
            collisions = [
                policy_id
                for policy_id, existing in merged.items()
                if policy_id == policy.id
                or (existing.ingress, existing.destination)
                == (policy.ingress, policy.destination)
            ]
            for policy_id in collisions:
                del merged[policy_id]
            merged[policy.id] = policy
        return merged

    # -- queries ----------------------------------------------------------- #

    def list_policies(self) -> tuple[PolicyView, ...]:
        with self._lock:
            return tuple(
                self._view_for(policy)
                for policy in self._effective().values()
            )

    def path(
        self,
        source: str,
        destination: str,
        via: Sequence[str] = (),
        avoid: Sequence[str] = (),
    ) -> dict[str, Any]:
        with self._lock:
            nodes = self.view.path(source, destination, via, avoid)
            segments = self.view.segments_for(destination, via)
            return {"path": list(nodes), "segments": list(segments)}

    def reconcile(self) -> ReconcileResult:
        with self._lock:
            return self.reconcile_locked()

    def reconcile_locked(self) -> ReconcileResult:
        desired, errors = self._desired_routes(self._effective())
        result = self._reconciler.apply(desired)
        self._errors = errors
        self._last = result
        return result

    def close(self) -> None:
        with self._lock:
            self._southbound.close()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "policies": len(self._effective()),
                "last_actions": [
                    {
                        "kind": action.kind,
                        "ingress": action.ingress,
                        "prefix": action.prefix,
                        "detail": action.detail,
                    }
                    for action in self._last.actions
                ],
                "last_errors": list(self._last.errors),
                "policy_errors": dict(self._errors),
            }

    def topology_dict(self) -> dict[str, Any]:
        return self.view.as_dict()

    # -- internals --------------------------------------------------------- #

    def _effective(self) -> dict[str, PolicyIntent]:
        return self._effective_for(self._file)

    def _effective_for(
        self, file_policies: Mapping[str, PolicyIntent]
    ) -> dict[str, PolicyIntent]:
        file_ids = set(file_policies)
        file_keys = {
            (policy.ingress, policy.destination)
            for policy in file_policies.values()
        }
        effective = {
            policy_id: policy
            for policy_id, policy in self._baseline.items()
            if policy_id not in file_ids
            and (policy.ingress, policy.destination) not in file_keys
        }
        effective.update(file_policies)
        return effective

    def _validate_policy(self, policy: PolicyIntent) -> None:
        self.view.require_router(policy.ingress)
        self.view.require_node(policy.destination)
        if policy.mode not in _MODES:
            raise ControllerError(
                f"policy {policy.id}: mode must be one of {', '.join(_MODES)}"
            )
        if isinstance(policy.metric, bool) or policy.metric < 0:
            raise ControllerError(f"policy {policy.id}: metric must be >= 0")
        if not policy.segments:
            raise ControllerError(f"policy {policy.id}: empty segment list")
        for segment in policy.segments:
            canonical_sid(segment)

    def _desired_routes(
        self, policies: Mapping[str, PolicyIntent]
    ) -> tuple[dict[tuple[str, str], DesiredRoute], dict[str, str]]:
        desired: dict[tuple[str, str], DesiredRoute] = {}
        errors: dict[str, str] = {}
        for policy in policies.values():
            try:
                prefixes = self._southbound.dest_prefixes(policy.destination)
                if not prefixes:
                    raise ControllerError(
                        f"no IPv6 prefix found for {policy.destination!r}"
                    )
                dev = self._southbound.egress_dev(
                    policy.ingress, policy.segments[0]
                )
            except (ControllerError, SouthboundError) as exc:
                errors[policy.id] = str(exc)
                continue
            for prefix in prefixes:
                key = (policy.ingress, prefix)
                if key in desired:
                    errors[policy.id] = (
                        f"prefix {prefix} is already covered by "
                        f"{desired[key].policy_id}"
                    )
                    continue
                desired[key] = DesiredRoute(
                    policy_id=policy.id,
                    ingress=policy.ingress,
                    prefix=prefix,
                    segments=policy.segments,
                    mode=policy.mode,
                    metric=policy.metric,
                    dev=dev,
                )
        return desired, errors

    def _view_for(self, policy: PolicyIntent) -> PolicyView:
        error = self._errors.get(policy.id)
        return PolicyView(
            id=policy.id,
            ingress=policy.ingress,
            destination=policy.destination,
            segments=policy.segments,
            mode=policy.mode,
            metric=policy.metric,
            origin=policy.origin,
            status="error" if error else "ok",
            error=error,
        )
