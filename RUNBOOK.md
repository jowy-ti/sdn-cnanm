# RUNBOOK — running and verifying the SRv6 lab

Operator guide for the ipmininet entrypoint in `src/`. It builds the chain
`h1 - r1 - r2 - r3 - r4 - r5 - r6 - h2` (plus an `r2 - r5` shortcut) on a
dynamic FRR underlay, with SRv6 as the data plane and a **transit** SRv6 router
(`r4`) forced into the path by the segment list.

| Lab  | Script                     | Underlay      | Data plane                                                                                                    | pixi task         |
| ---- | -------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------- | ----------------- |
| SRv6 | `src/net_topology_yaml.py` | OSPFv3 (IPv6) | r1/r6 encapsulate toward a remote SID; r4 pops its own SID and forwards (End); r1/r6 decapsulate with End.DX6. | `pixi run net-yaml` |

The lab is fully declarative: nodes, links, locator SIDs and encapsulation
policies come from `topologies/srv6.yaml` (see §4), so adding nodes/links/SIDs
does not require touching Python. The script drops into the Mininet CLI
(`mininet>`); `exit` stops and cleans up the whole network. The commands below
are grouped by "control plane", "data plane" and "on the wire", and the expected
output is what the lab actually prints today.

> There is no hard-coded twin any more: the former `src/net_topology.py` /
> `pixi run net` entrypoint has been deleted. `src/net_topology_yaml.py` is the
> single entrypoint and `topologies/srv6.yaml` the only lab definition.

---

## 0. Prerequisites (once per machine)

```sh
pixi install                 # creates .pixi/envs/default
pixi run check-yaml          # parse/validate the topology; no root, no network
sudo dnf install frr         # Fedora: daemons land in /usr/libexec/frr
sudo systemctl stop frr      # the topology starts its own FRR instances
sudo usermod -aG frrvty root # else FRR 10 aborts the config check as root
```

Passwordless sudo for the script. The rule must be a **single command line**
(the interpreter *and* the script, exact absolute argv — see §2.3):

```
# /etc/sudoers.d/net_topology
joelgonzalez ALL=(ALL) NOPASSWD: /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python /home/joelgonzalez/Documents/sdn-cnanm/src/net_topology_yaml.py
```

`pixi run clean` (§3) needs a passwordless line of its own (again a single
command line, exact argv); add it if you want cleanup without a prompt:

```
# /etc/sudoers.d/net_topology
joelgonzalez ALL=(ALL) NOPASSWD: /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python -m ipmininet.clean
```

`seg6` support is already in the kernel. See `info.txt` and `AGENTS.md` for the
rest of the machine-specific setup.

---

## 1. SRv6 lab — `src/net_topology_yaml.py`

Topology: `h1 - r1 - r2 - r3 - r4 - r5 - r6 - h2` with an `r2 - r5` shortcut.
The SRv6-aware routers are **r1** (ingress edge, `End.DX6` → h1), **r4** (middle
transit waypoint, `End` — pops its SID and keeps forwarding) and **r6** (egress
edge, `End.DX6` → h2). `r2`, `r3`, `r5` are plain OSPFv3 forwarders with no SRv6
state. The segment lists list r4's transit SID **first**, before the egress SID.

### 1.1 Start

```sh
pixi run net-yaml
# explicit equivalent (this is the argv the sudoers rule matches):
sudo -n /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python \
    /home/joelgonzalez/Documents/sdn-cnanm/src/net_topology_yaml.py
```

An explicit topology file can be passed positionally (it overrides the default
`topologies/srv6.yaml`):

```sh
sudo /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python \
    /home/joelgonzalez/Documents/sdn-cnanm/src/net_topology_yaml.py my_topo.yaml
```

Parse/validate only (no root; prints a summary of routers/hosts/links/policies
and local SIDs):

```sh
pixi run check-yaml
```

### 1.2 Wait for the underlay to converge

The SRv6 policy needs OSPFv3 to have installed the routes to the remote SID
locators, so give the IGP ~20 s. A ping issued in the first seconds often fails
for exactly this reason — it is not an SRv6 bug. Note that OSPFv3 wins the
`r2 - r5` shortcut for h1↔h2; it is the *segment list*, not the IGP, that sends
traffic through r4.

```
mininet> r4 vtysh -c "show ipv6 ospf6 neighbor"
```

Expected (wait until `State/IfState` is `Full/...`):

```
Neighbor ID     Pri    DeadTime    State/IfState         Duration I/F[State]
192.168.10.1     10    00:00:02     Full/BDR             00:00:22 r4-eth0[DR]
192.168.12.1     10    00:00:02     Full/DR              00:00:22 r4-eth1[BDR]
```

### 1.3 Prove the data plane

Addresses are assigned deterministically, so use h2's IPv6 directly. **Do not
use `h1 ping h2`** — `IPCLI` substitutes a bare node name with its *IPv4*
address, which takes the native OSPFv2 path and never enters SRv6 (see §1.6).

```
mininet> ips h1 h2
mininet> h1 ping6 -c3 fc00:0:d::2
mininet> h2 ping6 -c3 fc00:0:6::1
```

Expected: `3 packets transmitted, 3 received, 0% packet loss`, `ttl=63` (the
inner packet crosses one SRv6 hop; the transit routers only touch the outer
header).

### 1.4 Prove the control plane and the SRv6 state

```
mininet> r1 ip -6 route show | grep seg6
mininet> r6 ip -6 route show | grep seg6
mininet> r1 ip -6 route show table 1
mininet> r4 ip -6 route show table 1
mininet> r6 ip -6 route show table 1
mininet> r1 ip -6 rule show
mininet> r4 ip -6 rule show
mininet> r1 sysctl net.ipv6.conf.all.seg6_enabled net.ipv6.conf.r1-eth1.seg6_enabled
mininet> r2 ip -6 route show | grep -c seg6
mininet> r5 ip -6 route show | grep -c seg6
```

Expected:

```
# r1: ingress policy for h2 -- r4's transit SID first, then r6's decapsulating SID
fc00:0:d::/48  encap seg6 mode encap segs 2 [ 2001:db8:4::10 2001:db8:6::10 ] dev r1-eth0 metric 1 pref medium
# r6: ingress policy for h1 -- same idea in reverse
fc00:0:6::/48  encap seg6 mode encap segs 2 [ 2001:db8:4::10 2001:db8:1::10 ] dev r6-eth0 metric 1 pref medium

# local SID tables (each ends with a blackhole default in table 1)
2001:db8:1::10  encap seg6local action End.DX6 nh6 fc00:0:6::1 dev r1-eth0 metric 1 pref medium
2001:db8:4::10  encap seg6local action End dev r4-eth0 metric 1 pref medium
2001:db8:6::10  encap seg6local action End.DX6 nh6 fc00:0:d::2 dev r6-eth0 metric 1 pref medium

# the LocalSIDTable is a policy rule steering the locator into table 1
32765:  from all to 2001:db8:1::/64 lookup 1     # r1
32765:  from all to 2001:db8:4::/64 lookup 1     # r4
net.ipv6.conf.all.seg6_enabled = 1
net.ipv6.conf.r1-eth1.seg6_enabled = 1

# r2 / r5 are pure underlay forwarders: 0 `encap seg6` routes each
0
0
```

### 1.5 Prove it on the wire (the SRH)

The outer packet is routed by OSPFv3 to r4's locator. Capture protocol 43
(Routing header) on r4's r5-facing link (`r4-eth1`) while a ping runs. The
segment list is stored *reversed* on the wire: `[0]` is the last hop, `[1]` the
first. The `px`/`py` form works both interactively and from a pipe (see §2.3).

```
mininet> px r4.cmd('timeout 8 tcpdump -ni r4-eth1 -vv ip6 proto 43 > /tmp/cap.txt 2>&1 &')
mininet> px import time
mininet> h1 ping6 -c5 fc00:0:d::2
mininet> px time.sleep(4)
mininet> px print(r4.cmd('cat /tmp/cap.txt'))
```

Expected (trimmed to one request/reply pair). Watch the outer destination move
from r4's SID to the next segment and `segleft` drop from 1 to 0 as r4 processes
the packet:

```
IP6 (flowlabel 0xee057, hlim 61, next-header Routing (43), payload length 144)
    fc00:0:6::2 > 2001:db8:4::10: RT6 (len=4, type=4, segleft=1, last-entry=1, flags=0x0, tag=0, [0]2001:db8:6::10, [1]2001:db8:4::10)
    IP6 (flowlabel 0xee057, hlim 64, next-header ICMPv6 (58), payload length 64)
    fc00:0:6::1 > fc00:0:d::2: ICMP6, echo request, id 28490, seq 1
IP6 (flowlabel 0xee057, hlim 60, next-header Routing (43), payload length 144)
    fc00:0:6::2 > 2001:db8:6::10: RT6 (len=4, type=4, segleft=0, last-entry=1, flags=0x0, tag=0, [0]2001:db8:6::10, [1]2001:db8:4::10)
    IP6 (flowlabel 0xee057, hlim 64, next-header ICMPv6 (58), payload length 64)
    fc00:0:6::1 > fc00:0:d::2: ICMP6, echo request, id 28490, seq 1
```

The reply direction shows the mirror image
(`2001:db8:4::10` → `2001:db8:1::10`, source `fc00:0:c::2`).

### 1.6 Control experiments

**The SRH is what forces r4, not the IGP.** OSPFv3 routes h1→h2 straight through
`r2, r5, r6` (the shortcut) and never touches r4; if the packet still crosses r4
it is because of the segment list. r1's only OSPFv3 neighbour is r2 on
`r1-eth1`:

```
mininet> r1 ip -6 route get 2001:db8:6::10     # plain IGP: via r1-eth1 (r2), no r4
mininet> r1 ip -6 route get fc00:0:d::2        # encap seg6 ... segs 2 [ 2001:db8:4::10 2001:db8:6::10 ]
```

Expected:

```
2001:db8:6::10 from :: via fe80::d4b3:d5ff:fe12:311f dev r1-eth1 proto ospf src fc00:0:7::2 metric 20 pref medium
fc00:0:d::2 from ::  encap seg6 mode encap segs 2 [ 2001:db8:4::10 2001:db8:6::10 ] dev r1-eth0 src fc00:0:6::2 metric 1 pref medium
```

**The policy is IPv6-only.** IPv4 takes the native OSPFv2 path with no
encapsulation:

```
mininet> r1 ip -4 route get 192.168.7.2       # plain OSPFv2 route, no encap
mininet> r1 ip -4 route show | grep -c encap
mininet> h1 ping -c3 192.168.7.2
```

Expected: `via 192.168.1.1 dev r1-eth1` (native), `grep -c` prints `0`, and the
IPv4 ping succeeds with `ttl=60`.

---

## 2. Non-interactive verification

The same checks can be driven from a file, which is handy for a quick regression
run. Two Mininet CLI behaviours have to be worked around, and both bite
silently, so read §2.3 before editing a driver.

### 2.1 SRv6 transit driver

Save as `/tmp/srv6_check.txt`:

```
px print("")
px print("========== SRv6 TRANSIT PROBE ==========")
px print("== addresses ==")
ips h1 h2 r1 r2 r3 r4 r5 r6
px import time
px print("== waiting 25s for OSPFv3 to converge ==")
px time.sleep(25)
px print("== OSPFv3 adjacency on r4 ==")
py r4.cmd('vtysh -c "show ipv6 ospf6 neighbor"')
px print("== ping h1 -> h2 (IPv6, through the SRv6 transit) ==")
py h1.cmd('ping -6 -c3 -W2 ' + h2.defaultIntf().ip6)
px print("== ping h2 -> h1 (IPv6) ==")
py h2.cmd('ping -6 -c3 -W2 ' + h1.defaultIntf().ip6)
px print("== r1 / r6: ingress encap routes (2 segments, r4 first) ==")
py r1.cmd('ip -6 route show | grep seg6')
py r6.cmd('ip -6 route show | grep seg6')
px print("== local SID tables (r1/r6 End.DX6, r4 End transit) ==")
py r1.cmd('ip -6 route show table 1')
py r4.cmd('ip -6 route show table 1')
py r6.cmd('ip -6 route show table 1')
px print("== r2 / r5: underlay only (expect 0) ==")
py r2.cmd('ip -6 route show | grep -c seg6')
py r5.cmd('ip -6 route show | grep -c seg6')
px print("== r1: IGP path to r6 locator (shortcut via r2, not r4) ==")
py r1.cmd('ip -6 route get 2001:db8:6::10')
px print("== SRH on the wire at r4-eth1 ==")
px r4.cmd('timeout 8 tcpdump -ni r4-eth1 -vv ip6 proto 43 > /tmp/cap.txt 2>&1 &')
px time.sleep(1)
py h1.cmd('ping -6 -c5 -i0.3 -W2 ' + h2.defaultIntf().ip6)
px time.sleep(8)
py r4.cmd('cat /tmp/cap.txt')
exit
```

### 2.2 Running a driver

```sh
sudo -n /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python \
    /home/joelgonzalez/Documents/sdn-cnanm/src/net_topology_yaml.py < /tmp/srv6_check.txt
```

Each run takes roughly a minute (about 25 s of it being the convergence wait)
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
`.pixi/envs/default/bin/python src/net_topology_yaml.py`, or any extra argv such
as `--check` or `-u`, fails with `sudo: a password is required`. Run `--check`
without sudo (`pixi run check-yaml`).

---

## 3. Teardown and troubleshooting

- Normal stop: `exit` at the `mininet>` prompt (runs `net.stop()`).
- After a crash or `Ctrl-C`, clear leftovers with `pixi run clean` (i.e.
  `sudo /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python -m ipmininet.clean`).
  This runs Mininet's `mn -c` plus ipmininet's daemon kill patterns, so it
  removes leftover namespaces, links, FRR daemons and `/tmp/*.log`. It needs
  root; add the passwordless line below to avoid a prompt.
- On shutdown Mininet may print a few
  `(rX exited - ignoring cmd('ip link del ...'))` lines. This is benign teardown
  noise: the namespaces/daemons have already exited and the deletion is skipped.
- `sudo: a password is required` → relative path or extra argv, see §2.3.
- The very first ping fails → the IGP has not converged yet; wait for the
  `Full/...` adjacency (§1.2).
- `net.start()` aborts with `configuration check failed` on a router → FRR
  problem: system `frr` still running (`sudo systemctl stop frr`) or root not in
  `frrvty`. For OSPFv3 specifically, `src/templates/ospf6d.mako` must stay wired
  in (see `AGENTS.md`).
- `ip addr` parses fail with `\x1b[1;35m` garbage → the `NO_COLOR=1` line was
  dropped from the script.

---

## 4. The topology file (`topologies/srv6.yaml`)

The lab is defined entirely in YAML and validated before anything is built;
`pixi run check-yaml` parses it without root and prints a summary. Adding
hosts, routers, links, locator SIDs or policies is a data edit, not a code
change.

```
hosts:    [ {name, use_v6?} ]
routers:  [ {name, use_v6?, loopback?, ospf6?, sids?: [{address, behavior?, to?, cost?}]} ]
links:    [ [a, b] | {nodes: [a, b], options?: {...}} ]
policies: [ {node, to, through: [...], mode?, cost?} ]
```

Key points:

- `loopback` is the locator prefix (a CIDR, e.g. `2001:db8:1::1/64`) that
  carries a router's SIDs; it is mandatory when `sids` is non-empty, and every
  SID must fall inside it (otherwise the `LocalSIDTable` never matches).
- `sids[].behavior` is one of `end.dx6` (default; needs `to`, decapsulate to that
  node's host), `end` (transit waypoint; pops the SID and forwards, no `to`) or
  `end.x` (transit with an explicit cross-connect; needs `to`).
- `policies[].through` is the segment list. Put a transit router's SID *before*
  the egress SID to force the packet through it; `mode` is `encap` (default) or
  `inline`.

The full schema and semantics are documented in the module docstring of
`src/net_topology_yaml.py`; the comments at the top of `topologies/srv6.yaml`
explain how this particular lab steers traffic through r4.
