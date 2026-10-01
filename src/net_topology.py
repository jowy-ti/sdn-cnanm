"""Mininet test topology: h1 - n1 - n2 - n3 - h2.

Usage: sudo python3 net_topology.py [openflow|linux]

- openflow: three chained OVS switches, hosts on one subnet.
- linux:    three chained routers, one /24 subnet per host plus
            static routes so the two hosts can reach each other.
"""

import sys
from typing import Any

from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import DefaultController, Node, OVSSwitch, Switch
from mininet.topo import Topo
from mininet.util import dumpNodeConnections

HOST_LINK: dict[str, Any] = {
    "bw": 10,
    "delay": "5ms",
    "loss": 0,
    "max_queue_size": 1000,
    "use_htb": True,
}

# Subnets: hosts live in 10.0.1.0/24 (h1) and 10.0.2.0/24 (h2); the
# router links are 192.168.1.0/24 (n1-n2) and 192.168.2.0/24 (n2-n3).
# ROUTES = {
#     "n1": ["10.0.2.0/24 via 192.168.1.2"],
#     "n2": ["10.0.1.0/24 via 192.168.1.1", "10.0.2.0/24 via 192.168.2.2"],
#     "n3": ["10.0.1.0/24 via 192.168.2.1"],
# }


class LinuxRouter(Node):
    """A Node with IP forwarding enabled."""

    def config(
        self,
        mac: str | None = None,
        ip: str | None = None,
        defaultRoute: str | None = None,
        lo: str = "up",
        **params: Any,
    ) -> dict[str, Any]:
        result = super().config(
            mac=mac, ip=ip, defaultRoute=defaultRoute, lo=lo, **params
        )
        self.cmd("sysctl net.ipv4.ip_forward=1")
        return result


class Topology(Topo):
    def build(self, node_cls: type[Node] = OVSSwitch) -> None:
        is_router = not issubclass(node_cls, Switch)

        for n in range(1, 4):
            if is_router:
                # ip=None keeps mininet from overwriting the link address.
                self.addNode(f"n{n}", cls=node_cls, ip=None)
            else:
                self.addSwitch(f"n{n}", cls=node_cls)

        # One host on each edge node (n1 and n3).
        for k, node in enumerate(("n1", "n3"), start=1):
            host = f"h{k}"
            if is_router:
                gateway = f"10.0.{k}.1"
                self.addHost(
                    host,
                    ip=f"10.0.{k}.11/24",
                    defaultRoute=f"via {gateway}",
                )
                self.addLink(host, node, **HOST_LINK, params2={"ip": f"{gateway}/24"})
            else:
                self.addHost(host)
                self.addLink(host, node, **HOST_LINK)

        # Chain the three nodes.
        for n in (1, 2):
            left, right = f"n{n}", f"n{n + 1}"
            if is_router:
                self.addLink(
                    left,
                    right,
                    params1={"ip": f"192.168.{n}.1/24"},
                    params2={"ip": f"192.168.{n}.2/24"},
                )
            else:
                self.addLink(left, right)


def build(mode: str) -> None:
    """Create the network and run connectivity/bandwidth tests."""
    is_router = mode != "openflow"
    node_cls: type[Node] = LinuxRouter if is_router else OVSSwitch

    net = Mininet(
        topo=Topology(node_cls),
        link=TCLink,
        waitConnected=True,
        controller=None if is_router else DefaultController,
    )
    net.start()

    # if is_router:
    #     for name, routes in ROUTES.items():
    #         router = net.get(name)
    #         for route in routes:
    #             router.cmd(f"ip route add {route}")

    print("*** Dumping host connections")
    dumpNodeConnections(net.hosts)

    print("*** Testing network connectivity")
    net.pingAll()

    print("*** Testing bandwidth between h1 and h2")
    h1, h2 = net.get("h1", "h2")
    net.iperf((h1, h2))

    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    build(sys.argv[1] if len(sys.argv) > 1 else "openflow")
