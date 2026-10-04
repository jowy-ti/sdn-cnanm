"""SRv6 lab: h1 - r1 - r2 - r3 - h2, with a dynamic OSPFv3 (FRR) underlay.

Every router runs the ipmininet default BasicRouterConfig (zebra + OSPF +
OSPFv3). r1 (ingress) and r3 (egress) hold all the SRv6 logic: each one
encapsulates traffic toward the peer's loopback SID and terminates the
peer's policy with an End.DX6 endpoint toward its local host. The segment
list carries a single segment, so r2 never processes SRH and only forwards
the outer packet along the path computed dynamically by the IGP.

Run as root:  pixi run net
"""

import os

from ipmininet.cli import IPCLI
from ipmininet.ipnet import IPNet
from ipmininet.iptopo import IPTopo
from ipmininet.router.config import OSPF6
from ipmininet.srv6 import LocalSIDTable, SRv6Encap, SRv6EndDX6Function
from mako.lookup import TemplateLookup

# On Fedora the FRR/RADVVD/SSHD daemons ipmininet starts live outside the
# default PATH.
for _dir in ("/usr/libexec/frr", "/usr/sbin", "/usr/local/sbin"):
    if os.path.isdir(_dir) and _dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = _dir + os.pathsep + os.environ["PATH"]

os.environ.setdefault("NO_COLOR", "1")

SID_R1 = "2001:db8:1::10"
SID_R3 = "2001:db8:3::10"

OSPF6_TEMPLATES = TemplateLookup(
    directories=[os.path.join(os.path.dirname(__file__), "templates")]
)


class SRv6MetricTopo(IPTopo):
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.sid_tables: dict[str, LocalSIDTable] = {}
        super().__init__(*args, **kwargs)

    def build(self, *args: object, **kwargs: object) -> None:
        r1 = self.addRouter("r1", lo_addresses=["2001:db8:1::1/64"])
        r2 = self.addRouter("r2", lo_addresses=["2001:db8:2::1/64"])
        r3 = self.addRouter("r3", lo_addresses=["2001:db8:3::1/64"])
        for r in (r1, r2, r3):
            self.addDaemon(r, OSPF6, template_lookup=OSPF6_TEMPLATES)
        h1 = self.addHost("h1")
        h2 = self.addHost("h2")

        self.addLink(h1, r1)
        self.addLink(r1, r2)
        self.addLink(r2, r3)
        self.addLink(r3, h2)

        super().build(*args, **kwargs)

    def post_build(self, net: IPNet) -> None:
        SRv6Encap(
            net=net,
            node="r1",
            to="h2",
            through=[SID_R3],
            mode=SRv6Encap.ENCAP,
        )
        SRv6Encap(
            net=net,
            node="r3",
            to="h1",
            through=[SID_R1],
            mode=SRv6Encap.ENCAP,
        )

        for node, sid, dest in (("r1", SID_R1, "h1"), ("r3", SID_R3, "h2")):
            self.sid_tables[node] = LocalSIDTable(
                net[node],
                matching=[next(net[node].intf("lo").ip6s()).network],
            )
            SRv6EndDX6Function(
                net=net,
                node=node,
                to=sid + "/128",
                nexthop=net[dest],
                table=self.sid_tables[node],
            )
        super().post_build(net)

    def clean(self) -> None:
        for table in self.sid_tables.values():
            table.clean()


if __name__ == "__main__":
    net = IPNet(topo=SRv6MetricTopo())
    net.start()
    IPCLI(net)
    net.stop()
