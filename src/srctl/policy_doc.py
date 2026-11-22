"""Parse declarative YAML/JSON policy documents.

Both formats go through ``yaml.safe_load`` (JSON is a YAML subset).  Two policy
forms are accepted, but not mixed in the same entry:

``ingress`` / ``destination`` / ``via`` / ``avoid``
    The friendly form.  ``via`` entries are router names or SID literals and
    the controller computes the segment list.

``node`` / ``to`` / ``through``
    The topology-style form.  ``through`` is installed verbatim as the explicit
    segment list.

The per-entry schema and validation are Pydantic models; this module converts
them to the transport-free ``PolicySpec`` dataclass.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

from .models import ControllerError

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]

_API_KEYS = frozenset({"ingress", "destination", "via", "avoid"})
_TOPOLOGY_KEYS = frozenset({"node", "to", "through"})


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


class _PolicyBase(BaseModel):
    """Fields shared by both document forms."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: NonEmptyStr | None = None
    mode: Literal["encap", "inline"] = "encap"
    metric: int = Field(default=1, ge=0)
    cost: int | None = Field(default=None, ge=0)

    @property
    def selected_metric(self) -> int:
        return self.cost if self.cost is not None else self.metric


class ApiPolicy(_PolicyBase):
    """``ingress`` / ``destination`` / ``via`` form."""

    ingress: NonEmptyStr
    destination: NonEmptyStr
    via: list[NonEmptyStr] | None = None
    avoid: list[NonEmptyStr] | None = None


class TopologyPolicy(_PolicyBase):
    """``node`` / ``to`` / ``through`` form."""

    node: NonEmptyStr
    to: NonEmptyStr
    through: list[NonEmptyStr] = Field(min_length=1)


class _PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    policies: list[Any]


def parse_policy_document(text: str) -> tuple[PolicySpec, ...]:
    """Parse and validate a YAML/JSON policy document."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ControllerError(f"cannot parse policy document: {exc}") from exc

    if raw is None:
        raise ControllerError("the policy document is empty")
    if isinstance(raw, Mapping):
        root = cast("Mapping[str, Any]", raw)
        try:
            document = _PolicyDocument.model_validate(dict(root))
        except ValidationError as exc:
            raise ControllerError(
                f"invalid policy document: {_format_errors(exc)}"
            ) from exc
        raw_policies: list[Any] = document.policies
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
    api_style = bool(keys & _API_KEYS)
    topology_style = bool(keys & _TOPOLOGY_KEYS)
    if api_style and topology_style:
        raise ControllerError(
            f"{where}: cannot mix ingress/destination/via with node/to/through"
        )
    if not api_style and not topology_style:
        raise ControllerError(
            f"{where}: expected ingress/destination or node/to/through"
        )
    if "metric" in mapping and "cost" in mapping:
        raise ControllerError(f"{where}: use either 'metric' or 'cost', not both")

    try:
        if api_style:
            api = ApiPolicy.model_validate(dict(mapping))
            return PolicySpec(
                ingress=api.ingress,
                destination=api.destination,
                via=tuple(api.via or ()),
                avoid=tuple(api.avoid or ()),
                mode=api.mode,
                metric=api.selected_metric,
                id=api.id,
            )
        topology = TopologyPolicy.model_validate(dict(mapping))
        return PolicySpec(
            ingress=topology.node,
            destination=topology.to,
            through=tuple(topology.through),
            mode=topology.mode,
            metric=topology.selected_metric,
            id=topology.id,
        )
    except ValidationError as exc:
        raise ControllerError(f"{where}: {_format_errors(exc)}") from exc


def _format_errors(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)
