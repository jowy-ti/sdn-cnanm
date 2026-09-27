from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
# from mininet.node import CPULimitedHost, RemoteController
from mininet.topo import Topo
from mininet.util import dumpNodeConnections


class Topology(Topo):
    "Single switch connected to n hosts."

    def build(self, n_switch: int = 1, n_host: int = 1):

        for s in range(n_switch):
            switch = self.addSwitch(f"s{s + 1}")

            for h in range(n_host):
                host = self.addHost(f"h{h + 1}")
                self.addLink(
                    host,
                    switch,
                    bw=10,
                    delay="5ms",
                    loss=1,
                    max_queue_size=1000,
                    use_htb=True,
                )


def perfTest():
    "Create network and run automated performance test."
    topo = Topology(sw=2, hosts=2)

    # Example using RemoteController (e.g., OS-Ken listening on 6653)
    net = Mininet(
        topo=topo,
        link=TCLink,
        # host=CPULimitedHost,
        # controller=lambda name: RemoteController(name, ip="127.0.0.1", port=6653),
    )

    net.start()

    print("*** Dumping host connections")
    dumpNodeConnections(net.hosts)

    print("*** Testing network connectivity")
    net.pingAll()

    print("*** Testing bandwidth between h1 and h4")
    h1, h4 = net.get("h1", "h4")
    net.iperf((h1, h4))

    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    perfTest()
