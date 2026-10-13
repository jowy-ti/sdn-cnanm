"""MPLS lab: h1 - r1 - r2 - r3 - h2, with a dynamic OSPFv2 (FRR) underlay.

MPLS counterpart of ``src/net_topology.py`` (the SRv6 lab): same chain, same
idea -- the ingress router steers traffic for the far host into a transport
header that the core forwards -- but the data plane is a static MPLS label
switched path (LSP) instead of an SRv6 segment list.

* r1 is the ingress LER for h1 -> h2: it pushes label 100 on every packet
  whose destination is h2.
* r2 is a transit LSR: it swaps 100 -> 200 (and 300 -> 400 for the reverse
  direction).  This is the key architectural difference with the SRv6 lab:
  every hop of an LSP holds per-FEC forwarding state, whereas SRv6's r2 is
  label- and SID-agnostic and only forwards the outer IPv6 packet.
* r3 is the egress LER: it pops 200 and delivers the plain IPv4 packet to h2.

The reverse LSP (h2 -> h1) is provisioned the same way on r3/r2/r1.

The kernel's ``mpls_iptunnel`` pushes labels onto IPv4 packets only, so this
lab is IPv4-only: every node is created with ``use_v6=False``, which makes the
default ipmininet router config run zebra + ospfd (OSPFv2) and never spawn an
OSPFv3 daemon (the daemon that needs the template fix in the SRv6 lab).

MPLS is provisioned straight through the kernel data plane, mirroring how
ipmininet's ``srv6`` helpers drive ``ip route ... encap seg6``:
``mpls_router`` for label switching, ``mpls_iptunnel`` for the push, and the
per-interface ``net.mpls.conf.<itf>.input`` sysctl to enable label reception.

Run as root, e.g.::

    sudo .pixi/envs/default/bin/python src/net_topology_mpls.py

or ``pixi run net-mpls`` once the matching sudoers entry exists (Mirror the
``net_topology`` line in /etc/sudoers.d/ with ``net_topology_mpls.py``).

Useful checks inside the CLI::

    mininet> h1 ping -c3 h2
    mininet> r2 ip -f mpls route show        # 100 as to 200, 300 as to 400
    mininet> r2 timeout 3 tcpdump -ni r2-eth0 mpls   # labeled packets
"""

import logging
import os
import shlex
import subprocess
from collections.abc import Sequence
from typing import Any

from ipmininet.cli import IPCLI
from ipmininet.ipnet import IPNet
from ipmininet.iptopo import IPTopo

# On Fedora the FRR/RADVD/SSHD daemons ipmininet starts live outside the
# default PATH.
for _dir in ("/usr/libexec/frr", "/usr/sbin", "/usr/local/sbin"):
    if os.path.isdir(_dir) and _dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = _dir + os.pathsep + os.environ["PATH"]

os.environ.setdefault("NO_COLOR", "1")

log = logging.getLogger(__name__)

# One label per hop of each LSP: the label in use changes at every router,
# unlike an SRv6 SID, which names an endpoint and travels unchanged end to end.
LABEL_R1_TO_R2 = 100  # h1 -> h2, pushed by r1
LABEL_R2_TO_R3 = 200  # h1 -> h2, swapped in by r2
LABEL_R3_TO_R2 = 300  # h2 -> h1, pushed by r3
LABEL_R2_TO_R1 = 400  # h2 -> h1, swapped in by r2

# Highest label the platform may switch (net.mpls.platform_labels).
MAX_LABELS = 1048575

# IPv4 loopback addresses, mirroring the locator loopbacks of the SRv6 lab:
# OSPFv2 advertises them as /32s, giving each router a stable node address in
# the IGP. (They are not used for the router-id: ipmininet derives that from
# the most visible non-loopback address.)
LO_R1 = "10.0.0.1/32"
LO_R2 = "10.0.0.2/32"
LO_R3 = "10.0.0.3/32"


def load_kernel_mpls() -> None:
    """Make the kernel MPLS modules available.

    ``mpls_router`` implements the label forwarding plane,
    ``mpls_iptunnel`` the ``encap mpls`` push done by the ingress routers and
    ``mpls_gso`` keeps the MPLS GSO path working for large segments.
    Loading needs root and is idempotent.
    """
    subprocess.run(
        ["modprobe", "mpls_router", "mpls_iptunnel", "mpls_gso"], check=False
    )


def _run(node: Any, cmd: str) -> None:
    """Run one command inside a node namespace, logging failures.

    Unlike a bare ``node.cmd()``, this keeps the exit code so a misconfigured
    label (e.g. reception not enabled on the input interface) is reported
    instead of silently producing a black hole.
    """
    out, err, code = node.pexec(shlex.split(cmd))
    if code:
        log.error(
            "MPLS: command failed on %s (exit %d): %s\n%s%s",
            getattr(node, "name", node),
            code,
            cmd,
            out,
            err,
        )


def enable_mpls(node: Any, interfaces: Sequence[str]) -> None:
    """Turn on label processing inside a router namespace.

    :param interfaces: the interfaces that must accept incoming MPLS packets
    """
    _run(node, f"sysctl -qw net.mpls.platform_labels={MAX_LABELS}")
    for itf in interfaces:
        _run(node, f"sysctl -qw net.mpls.conf.{itf}.input=1")


def push_label(node: Any, fec: str, label: int, nexthop: str, dev: str) -> None:
    """Ingress LER: encapsulate traffic to ``fec`` in a single-label header.

    Implemented as an IPv4 route with an ``encap mpls`` attribute, i.e. the
    exact MPLS analogue of ``ip route ... encap seg6 mode encap segs <SID>``.
    """
    _run(node, f"ip route replace {fec} encap mpls {label} via {nexthop} dev {dev}")


def swap_label(
    node: Any, in_label: int, out_label: int, nexthop: str, dev: str
) -> None:
    """Transit LSR: swap the incoming label and forward to the next hop."""
    _run(
        node,
        f"ip -f mpls route replace {in_label} as {out_label}"
        f" via inet {nexthop} dev {dev}",
    )


def pop_label(node: Any, in_label: int, nexthop: str, dev: str) -> None:
    """Egress LER: pop the label and deliver the IPv4 packet to ``nexthop``."""
    _run(node, f"ip -f mpls route replace {in_label} via inet {nexthop} dev {dev}")


class MPLSTopo(IPTopo):
    """Static MPLS LSP over a dynamic OSPFv2 underlay (SRv6 counterpart)."""

    def build(self, *args: object, **kwargs: object) -> None:
        r1 = self.addRouter("r1", use_v6=False, lo_addresses=[LO_R1])
        r2 = self.addRouter("r2", use_v6=False, lo_addresses=[LO_R2])
        r3 = self.addRouter("r3", use_v6=False, lo_addresses=[LO_R3])
        h1 = self.addHost("h1", use_v6=False)
        h2 = self.addHost("h2", use_v6=False)

        self.addLink(h1, r1)
        self.addLink(r1, r2)
        self.addLink(r2, r3)
        self.addLink(r3, h2)

        super().build(*args, **kwargs)

    def post_build(self, net: IPNet) -> None:
        # Label switching needs the modules on the host and, per router
        # namespace, platform_labels > 0 plus input=1 on every interface that
        # receives an MPLS packet.
        load_kernel_mpls()

        def endpoint(a: str, b: str) -> tuple[str, str]:
            """Return (a's interface towards b, b's address on that link)."""
            intf, peer = net[a].connectionsTo(net[b])[0]
            return intf.name, peer.ip

        r1_r2_dev, r2_fwd = endpoint("r1", "r2")
        r2_r1_dev, r1_rev = endpoint("r2", "r1")
        r2_r3_dev, r3_fwd = endpoint("r2", "r3")
        r3_r2_dev, r2_rev = endpoint("r3", "r2")
        r3_h2_dev, h2_addr = endpoint("r3", "h2")
        r1_h1_dev, h1_addr = endpoint("r1", "h1")

        enable_mpls(net["r1"], [r1_r2_dev])
        enable_mpls(net["r2"], [r2_r1_dev, r2_r3_dev])
        enable_mpls(net["r3"], [r3_r2_dev])

        # h1 -> h2
        log.info(
            "MPLS: h1->h2 LSP r1(%d) -> r2(%d) -> r3(pop)",
            LABEL_R1_TO_R2,
            LABEL_R2_TO_R3,
        )
        push_label(net["r1"], f"{h2_addr}/32", LABEL_R1_TO_R2, r2_fwd, r1_r2_dev)
        swap_label(net["r2"], LABEL_R1_TO_R2, LABEL_R2_TO_R3, r3_fwd, r2_r3_dev)
        pop_label(net["r3"], LABEL_R2_TO_R3, h2_addr, r3_h2_dev)

        # h2 -> h1
        log.info(
            "MPLS: h2->h1 LSP r3(%d) -> r2(%d) -> r1(pop)",
            LABEL_R3_TO_R2,
            LABEL_R2_TO_R1,
        )
        push_label(net["r3"], f"{h1_addr}/32", LABEL_R3_TO_R2, r2_rev, r3_r2_dev)
        swap_label(net["r2"], LABEL_R3_TO_R2, LABEL_R2_TO_R1, r1_rev, r2_r1_dev)
        pop_label(net["r1"], LABEL_R2_TO_R1, h1_addr, r1_h1_dev)

        super().post_build(net)


if __name__ == "__main__":
    net = IPNet(topo=MPLSTopo())
    net.start()
    IPCLI(net)
    net.stop()
