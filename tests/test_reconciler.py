from __future__ import annotations

from srctl.models import DesiredRoute, RouteState
from srctl.reconciler import Reconciler
from srctl.southbound import DryRunSouthbound


def _desired(
    segments: tuple[str, ...] = ("2001:db8:4::10", "2001:db8:6::10"),
) -> DesiredRoute:
    return DesiredRoute(
        policy_id="p1",
        ingress="s1",
        prefix="fc00:0:d::/48",
        segments=segments,
        mode="encap",
        metric=1,
        dev="s1-eth1",
    )


def test_add_noop_replace_delete() -> None:
    sb = DryRunSouthbound()
    reconciler = Reconciler(sb)

    result = reconciler.apply({("s1", "fc00:0:d::/48"): _desired()})
    assert [action.kind for action in result.actions] == ["add"]
    assert sb.routes[("s1", "fc00:0:d::/48")].segments == _desired().segments

    result = reconciler.apply({("s1", "fc00:0:d::/48"): _desired()})
    assert [action.kind for action in result.actions] == ["noop"]

    changed = _desired(("2001:db8:6::10",))
    result = reconciler.apply({("s1", "fc00:0:d::/48"): changed})
    assert [action.kind for action in result.actions] == ["replace"]
    assert sb.routes[("s1", "fc00:0:d::/48")].segments == ("2001:db8:6::10",)

    result = reconciler.apply({})
    assert [action.kind for action in result.actions] == ["delete"]
    assert not sb.routes


def test_foreign_route_is_not_deleted() -> None:
    sb = DryRunSouthbound()
    foreign = RouteState(
        prefix="fc00:99::/48",
        segments=("2001:db8:99::1",),
        mode="encap",
        metric=1,
        dev="s1-eth1",
    )
    sb.routes[("s1", foreign.prefix)] = foreign

    reconciler = Reconciler(sb)
    result = reconciler.apply({})
    assert result.actions == ()
    assert sb.routes[("s1", foreign.prefix)] == foreign
