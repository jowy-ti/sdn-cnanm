# RUNBOOK — running and verifying the SRv6 lab

Operator guide for the ipmininet entrypoint in `src/`. It builds the chain
`h1 - r1 - r2 - r3 - h2` on a dynamic FRR underlay, with SRv6 as the data plane.

| Lab  | Script                | Underlay      | Data plane                                                                  | pixi task       |
| ---- | --------------------- | ------------- | --------------------------------------------------------------------------- | --------------- |
| SRv6 | `src/net_topology.py` | OSPFv3 (IPv6) | r1/r3 encapsulate toward a remote SID; the peer decapsulates with End.DX6. r2 is SID-agnostic. | `pixi run net`      |

The script drops into the Mininet CLI (`mininet>`); `exit` stops and cleans up
the whole network. The commands below are grouped by "control plane", "data
plane" and "on the wire", and the expected output is what the lab actually
prints today.

---

## 0. Prerequisites (once per machine)

```sh
pixi install                 # creates .pixi/envs/default
sudo dnf install frr         # Fedora: daemons land in /usr/libexec/frr
sudo systemctl stop frr      # the topology starts its own FRR instances
sudo usermod -aG frrvty root # else FRR 10 aborts the config check as root
```

Passwordless sudo for the script (exact absolute path, see §2.3):

```
# /etc/sudoers.d/net_topology
joelgonzalez ALL=(ALL) NOPASSWD: /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python /home/joelgonzalez/Documents/sdn-cnanm/src/net_topology.py
joelgonzalez ALL=(ALL) NOPASSWD: /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python -m ipmininet.clean
```

`seg6` support is already in the kernel. See `info.txt` and `AGENTS.md` for the
rest of the machine-specific setup.

---

## 1. SRv6 lab — `src/net_topology.py`

### 1.1 Start

```sh
pixi run net
# explicit equivalent (this is the argv the sudoers rule matches):
sudo -n /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python \
    /home/joelgonzalez/Documents/sdn-cnanm/src/net_topology.py
```

### 1.2 Wait for the underlay to converge

The SRv6 policy needs OSPFv3 to have installed the route to the remote SID's
locator, so give the IGP ~20 s. A ping issued in the first seconds often fails
for exactly this reason — it is not an SRv6 bug.

```
mininet> r1 vtysh -c "show ipv6 ospf6 neighbor"
```

Expected (wait until `State/IfState` is `Full/DR`):

```
Neighbor ID     Pri    DeadTime    State/IfState         Duration I/F[State]
192.168.5.1      10    00:00:02     Full/DR              00:00:17 r1-eth1[BDR]
```

### 1.3 Prove the data plane

Addresses are assigned deterministically, so use h2's IPv6 directly. **Do not
use `h1 ping h2`** — `IPCLI` substitutes a bare node name with its *IPv4*
address, which takes the native OSPFv2 path and never enters SRv6 (see §1.6).

```
mininet> ips h1 h2
mininet> h1 ping6 -c3 fc00:0:6::1
mininet> h2 ping6 -c3 fc00:0:3::1
```

Expected: `3 packets transmitted, 3 received, 0% packet loss`, `ttl=63`.

### 1.4 Prove the control plane and the SRv6 state

```
mininet> r1 ip -6 route show | grep seg6
mininet> r3 ip -6 route show | grep seg6
mininet> r1 ip -6 route show table 1
mininet> r3 ip -6 route show table 1
mininet> r1 ip -6 rule show
mininet> r1 sysctl net.ipv6.conf.all.seg6_enabled net.ipv6.conf.r1-eth1.seg6_enabled
mininet> r2 ip -6 route show
```

Expected:

```
# r1: ingress policy for h2, via r3's SID
fc00:0:6::/48  encap seg6 mode encap segs 1 [ 2001:db8:3::10 ] dev r1-eth0 metric 1 pref medium
# r3: ingress policy for h1, via r1's SID
fc00:0:3::/48  encap seg6 mode encap segs 1 [ 2001:db8:1::10 ] dev r3-eth0 metric 1 pref medium

# local SID table (End.DX6 = decapsulate and deliver to the local host)
2001:db8:1::10  encap seg6local action End.DX6 nh6 fc00:0:3::1 dev r1-eth0 metric 1 pref medium
2001:db8:3::10  encap seg6local action End.DX6 nh6 fc00:0:6::1 dev r3-eth0 metric 1 pref medium

# the LocalSIDTable is a policy rule steering the locator into table 1
32765:  from all to 2001:db8:1::/64 lookup 1
net.ipv6.conf.all.seg6_enabled = 1
net.ipv6.conf.r1-eth1.seg6_enabled = 1

# r2 is a pure underlay forwarder: OSPFv3/kernel routes only, no `encap seg6`
2001:db8:3::/64 nhid 20 via fe80::1838:7ff:fee9:bd52 dev r2-eth1 proto ospf metric 20 pref medium
```

### 1.5 Prove it on the wire (the SRH)

`r2` forwards the *outer* IPv6 packet; capture protocol 43 (Routing header) on
its link towards r1 while a ping runs. The `px`/`py` form works both
interactively and from a pipe (see §3.4).

```
mininet> px h1.cmd('ping6 -c10 -i0.3 -W2 fc00:0:6::1 > /dev/null 2>&1 &')
mininet> py r2.cmd('timeout 8 tcpdump -ni r2-eth0 -vv -c 2 ip6 proto 43 2>&1 | head -40')
```

Expected — an SRH (`RT6 ... type=4`) whose outer destination is the SID, with
the original ICMPv6 echo inside:

```
IP6 (flowlabel 0x47a66, hlim 63, next-header Routing (43), payload length 128)
    fc00:0:3::2 > 2001:db8:3::10: RT6 (len=2, type=4, segleft=0, last-entry=0, flags=0x0, tag=0, [0]2001:db8:3::10)
    IP6 (flowlabel 0x47a66, hlim 64, next-header ICMPv6 (58), payload length 64)
    fc00:0:3::1 > fc00:0:6::1: ICMP6, echo request, id 12456, seq 2
IP6 (flowlabel 0xa441d, hlim 62, next-header Routing (43), payload length 128)
    fc00:0:5::1 > 2001:db8:1::10: RT6 (len=2, type=4, segleft=0, last-entry=0, flags=0x0, tag=0, [0]2001:db8:1::10)
    IP6 (flowlabel 0xa441d, hlim 64, next-header ICMPv6 (58), payload length 64)
    fc00:0:6::1 > fc00:0:3::1: ICMP6, echo reply, id 12456, seq 2
```

### 1.6 Control experiment: IPv4 does *not* use SRv6

Useful to show the policy is IPv6-only:

```
mininet> r1 ip -4 route get 192.168.3.1     # plain OSPFv2 route, no encap
mininet> r1 ip -6 route get fc00:0:6::1     # encap seg6 ... segs 1 [ 2001:db8:3::10 ]
mininet> r1 ip -4 route show | grep -c encap
mininet> h1 ping -c3 192.168.3.1
```

Expected: the IPv4 lookup returns `via 192.168.1.2 dev r1-eth1` (native), the
IPv6 lookup returns the `encap seg6` route, `grep -c` prints `0`, and the IPv4
ping succeeds with `ttl=61`.

---

## 2. Non-interactive verification

The same checks can be driven from a file, which is handy for a quick
regression run. Two Mininet CLI behaviours have to be worked around, and both
bite silently, so read §2.3 before editing a driver.

### 2.1 SRv6 driver

Save as `/tmp/srv6_check.txt`:

```
px print("")
px print("========== SRv6 PROBE ==========")
px print("== addresses ==")
ips h1 h2 r1 r2 r3
px import time
px print("== waiting 20s for OSPFv3 to converge ==")
px time.sleep(20)
px print("== OSPFv3 adjacency on r1 ==")
py r1.cmd('vtysh -c "show ipv6 ospf6 neighbor"')
px print("== ping h1 -> h2 (IPv6, through the SRv6 policy) ==")
py h1.cmd('ping -6 -c3 -W2 ' + h2.defaultIntf().ip6)
px print("== ping h2 -> h1 (IPv6) ==")
py h2.cmd('ping -6 -c3 -W2 ' + h1.defaultIntf().ip6)
px print("== r1: SRv6 ingress encap route (to h2 via SID 2001:db8:3::10) ==")
py r1.cmd('ip -6 route show | grep seg6')
px print("== r3: SRv6 ingress encap route (to h1 via SID 2001:db8:1::10) ==")
py r3.cmd('ip -6 route show | grep seg6')
px print("== r1/r3: local SID table 1 (End.DX6 toward local host) ==")
py r1.cmd('ip -6 route show table 1')
py r3.cmd('ip -6 route show table 1')
px print("== r1: policy rules + seg6 sysctl ==")
py r1.cmd('ip -6 rule show')
py r1.cmd('sysctl net.ipv6.conf.all.seg6_enabled net.ipv6.conf.r1-eth1.seg6_enabled')
px print("== r2: underlay only (OSPFv3 routes, no SRv6 state) ==")
py r2.cmd('ip -6 route show')
px print("== r2: SRH on the wire (tcpdump r2-eth0, proto 43) ==")
px h1.cmd('ping -6 -c10 -i0.3 -W2 ' + h2.defaultIntf().ip6 + ' > /dev/null 2>&1 &')
py r2.cmd('timeout 8 tcpdump -ni r2-eth0 -vv -c 2 ip6 proto 43 2>&1 | head -40')
exit
```

### 2.2 Running a driver

```sh
sudo -n /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python \
    /home/joelgonzalez/Documents/sdn-cnanm/src/net_topology.py < /tmp/srv6_check.txt
```

Each run takes roughly a minute (about 20 s of it being the convergence wait)
and ends by itself: the `exit` line stops the network.

### 2.3 The two CLI gotchas that make or break a driver

1. **Mininet strips everything after `#`.** `mininet/cli.py` treats `#` as a
   comment start, so a line like `px print("#### header ####")` is truncated to
   `print("` and dies with `unterminated string literal`. Never put `#` in a
   driver line.
2. **Piping plain node commands does not work.** `IPCLI`'s node-command path
   (`default()` → `waitForNode`) reads one byte from *its own stdin* and forwards
   it to the node. With a regular file as stdin, every byte of the rest of the
   script is drained into the first node's shell and the remaining lines never
   run. `py` (eval) and `px` (exec) bypass that path entirely and are the reason
   these drivers use them; `getLocals()` injects the node names, so
   `h2.defaultIntf().ip6` works directly.

Also note the sudoers rule matches the *exact* absolute argv, so a relative
`.pixi/envs/default/bin/python src/net_topology.py` (or any extra argv such as
`-u`) fails with `sudo: a password is required`.

---

## 3. Teardown and troubleshooting

- Normal stop: `exit` at the `mininet>` prompt (runs `net.stop()`).
- After a crash or `Ctrl-C`, clear leftovers with
  `sudo -n /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python -m ipmininet.clean`
  (leaves no namespaces or stray FRR daemons behind).
- `sudo: a password is required` → relative path or extra argv, see §2.3.
- The very first ping fails → the IGP has not converged yet; wait for the
  `Full/DR` adjacency (§1.2).
- `net.start()` aborts with `configuration check failed` on a router → FRR
  problem: system `frr` still running (`sudo systemctl stop frr`) or root not in
  `frrvty`. For OSPFv3 specifically, `src/templates/ospf6d.mako` must stay wired
  in (see `AGENTS.md`).
- `ip addr` parses fail with `\x1b[1;35m` garbage → the `NO_COLOR=1` line was
  dropped from a script.
