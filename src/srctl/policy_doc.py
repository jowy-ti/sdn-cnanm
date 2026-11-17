"""Parse declarative YAML/JSON policy documents.

Both formats go through ``yaml.safe_load`` (JSON is a YAML subset).  Two policy
forms are accepted, but not mixed in the same entry:

``ingress`` / ``destination`` / ``via`` / ``avoid``
    The friendly form.  ``via`` entries are router names or SID literals and
    the controller computes the segment list.

``node`` / ``to`` / ``through``
    The topology-style form.  ``through`` is installed verbatim as the explicit
    segment list.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import yaml

from .models import ControllerError

_MODES = ("encap", "inline")
_API_KEYS = frozenset({"ingress", "destination", "via", "avoid"})
_TOPOLOGY_KEYS = frozenset({"node", "to", "through"})
_COMMON_KEYS = frozenset({"id", "mode", "metric", "cost"})


@dataclass(frozen=True, slots=True)
class PolicySpec:
    """One parsed policy entry, before topology resolution."""

    ingress: str
    destination: str
    via: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    through: tuple[str, ...] | None = None
    mode: str = "encap"
    metric: int = 1
    id: str | None = None


def parse_policy_document(text: str) -> tuple[PolicySpec, ...]:
    """Parse and validate a YAML/JSON policy document."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ControllerError(f"cannot parse policy document: {exc}") from exc

    if raw is None:
        raise ControllerError("the policy document is empty")
    if isinstance(raw, Mapping):
        mapping = cast("Mapping[str, Any]", raw)
        unknown = set(mapping) - {"policies"}
        if unknown:
            raise ControllerError(
                "unknown top-level keys: " + ", ".join(sorted(unknown))
            )
        if "policies" not in mapping:
            raise ControllerError("the policy document needs a 'policies' list")
        value = mapping["policies"]
        if not isinstance(value, list):
            raise ControllerError("'policies' must be a list")
        raw_policies = cast("list[Any]", value)
    elif isinstance(raw, list):
        raw_policies = cast("list[Any]", raw)
    else:
        raise ControllerError("the policy document must be a mapping or a list")

    specs: list[PolicySpec] = []
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    for index, entry in enumerate(raw_policies):
        spec = _parse_spec(entry, f"policies[{index}]")
        key = (spec.ingress, spec.destination)
        if key in seen_keys:
            raise ControllerError(
                f"policies[{index}]: duplicate policy for "
                f"{spec.ingress} -> {spec.destination}"
            )
        seen_keys.add(key)
        if spec.id is not None:
            if spec.id in seen_ids:
                raise ControllerError(
                    f"policies[{index}]: duplicate policy id {spec.id!r}"
                )
            seen_ids.add(spec.id)
        specs.append(spec)
    return tuple(specs)


def _parse_spec(raw: Any, where: str) -> PolicySpec:
    if not isinstance(raw, Mapping):
        raise ControllerError(f"{where}: expected a mapping")
    mapping = cast("Mapping[str, Any]", raw)
    keys = set(mapping)
    unknown = keys - (_API_KEYS | _TOPOLOGY_KEYS | _COMMON_KEYS)
    if unknown:
        raise ControllerError(
            f"{where}: unknown keys: " + ", ".join(sorted(unknown))
        )

    api_style = bool(keys & _API_KEYS)
    topology_style = bool(keys & _TOPOLOGY_KEYS)
    if api_style and topology_style:
        raise ControllerError(
            f"{where}: cannot mix ingress/destination/via with node/to/through"
        )

    policy_id = _optional_str(mapping, "id", where)
    mode = _optional_str(mapping, "mode", where) or "encap"
    if mode not in _MODES:
        raise ControllerError(f"{where}.mode: expected one of {', '.join(_MODES)}")
    if "metric" in mapping and "cost" in mapping:
        raise ControllerError(f"{where}: use either 'metric' or 'cost', not both")
    metric = _int_field(mapping, "cost" if "cost" in mapping else "metric", where, 1)

    if api_style:
        ingress = _required_str(mapping, "ingress", where)
        destination = _required_str(mapping, "destination", where)
        via = _str_list(mapping.get("via"), f"{where}.via")
        avoid = _str_list(mapping.get("avoid"), f"{where}.avoid")
        return PolicySpec(
            ingress=ingress,
            destination=destination,
            via=via,
            avoid=avoid,
            mode=mode,
            metric=metric,
            id=policy_id,
        )

    if topology_style:
        ingress = _required_str(mapping, "node", where)
        destination = _required_str(mapping, "to", where)
        through = _str_list(mapping.get("through"), f"{where}.through")
        if not through:
            raise ControllerError(f"{where}.through: at least one SID is required")
        return PolicySpec(
            ingress=ingress,
            destination=destination,
            through=through,
            mode=mode,
            metric=metric,
            id=policy_id,
        )

    raise ControllerError(
        f"{where}: expected ingress/destination or node/to/through"
    )


def _required_str(mapping: Mapping[str, Any], key: str, where: str) -> str:
    if key not in mapping:
        raise ControllerError(f"{where}: missing required key {key!r}")
    value = mapping[key]
    if not isinstance(value, str) or not value:
        raise ControllerError(f"{where}.{key}: expected a non-empty string")
    return value


def _optional_str(mapping: Mapping[str, Any], key: str, where: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ControllerError(f"{where}.{key}: expected a non-empty string")
    return value


def _str_list(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ControllerError(f"{where}: expected a list")
    items: list[str] = []
    for index, item in enumerate(cast("list[Any]", value)):
        if not isinstance(item, str) or not item:
            raise ControllerError(f"{where}[{index}]: expected a non-empty string")
        items.append(item)
    return tuple(items)


def _int_field(mapping: Mapping[str, Any], key: str, where: str, default: int) -> int:
    value = mapping.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ControllerError(f"{where}.{key}: expected an integer >= 0")
    return value
