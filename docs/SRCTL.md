# `srctl` — SRv6 policy controller CLI

`srctl` is the operator interface to the in-process SRv6 controller. It applies
declarative YAML/JSON policy documents to the running Mininet lab and reports
what the controller is doing.

The controller runs **inside** `src/net_topology_yaml.py` (it needs the live
`IPNet`), so `srctl` talks to it over a per-user Unix socket. There is no HTTP
server and no network port.

---

## 1. Prerequisites

The lab must be running:

```sh
make run
```

On startup it prints the control socket, for example:

```
controller: control socket /tmp/srctl-1000.sock
```

The default path is `/tmp/srctl-<uid>.sock`. Under `sudo`, the controller uses
`SUDO_UID`, so the socket belongs to the invoking user and mode `0600`.
`$SRCTL_SOCKET` overrides the path for processes that inherit it; under
`make run`, sudo may strip it, in which case use `--socket` on the client to
match a custom server path.

`./scripts/srctl` is a thin wrapper around the repo `.venv`; it needs no `sudo`
and works from any directory.

```sh
./scripts/srctl --help
```

Equivalent invocations:

```sh
./scripts/srctl status
make srctl ARGS="status"
PYTHONPATH=src .venv/bin/python -m srctl status
```

---

## 2. Concepts

### Baseline policies

The `policies` section of `topologies/srv6.yaml` is the **baseline**. The
controller loads it at startup and assigns the ids `yaml-1`, `yaml-2`, …:

```yaml
policies:
  - node: s1
    to: h2
    through: [2001:db8:4::10, 2001:db8:6::10]
```

These are installed **verbatim**: `through` is the exact segment list, never
recomputed.

### File policies (overlay)

A document applied with `srctl apply` is an **overlay**. A file policy wins over
a baseline policy when it shares:

- the same `id`, or
- the same `(ingress, destination)` pair.

A shadowed baseline policy is not deleted; it becomes active again if the file
policy is removed with `--prune`.

### Effective policies

`srctl policies` shows the **effective** set: baseline policies that are not
shadowed, plus all file policies. Each entry has an `origin` of `yaml` or
`file`.

### Diff and apply

- `diff` parses and validates the whole document, then prints the planned
  `add` / `replace` / `delete` / `noop` actions **without changing anything**.
- `apply` performs the same validation and then reconciles the data plane.

If any entry is invalid, nothing is applied.

---

## 3. Policy document format

Both YAML and JSON are accepted (JSON is valid YAML). The document is either a
mapping with a `policies` list or a bare list.

### 3.1 Friendly form: `ingress` / `destination` / `via`

```yaml
policies:
  - id: h1-to-h2-via-s4
    ingress: s1
    destination: h2
    via: [s4]
```

`via` entries are router names or IPv6 SID literals. The controller computes the
segment list as `via SIDs + the destination's terminal SID` and validates the
path through the current topology.

Fields:

| Field | Required | Default | Meaning |
|---|---|---|---|
| `id` | no | `<ingress>-<destination>` | Stable policy identifier |
| `ingress` | yes | — | Router where the encap route is installed |
| `destination` | yes | — | Node reached by the policy (host or router) |
| `via` | no | `[]` | Waypoints: router names or SID literals |
| `avoid` | no | `[]` | Nodes the computed path must not traverse |
| `mode` | no | `encap` | `encap` or `inline` |
| `metric` | no | `1` | Route metric |

### 3.2 Explicit form: `node` / `to` / `through`

```yaml
policies:
  - id: explicit-return
    node: s6
    to: h1
    through: [2001:db8:4::10, 2001:db8:1::10]
    mode: encap
    cost: 1
```

`through` is installed verbatim. It must contain IPv6 SID addresses (bare or
`/128`). `cost` is the route metric (the alias for `metric`).

The two forms are mutually exclusive in one entry. Unknown keys and mixed forms
are rejected.

### 3.3 Rules

- A `via` router must have exactly one `end` SID (a waypoint SID).
- A `via` SID literal must be defined in the topology.
- The destination must have a terminal SID: an `end.dx6` SID pointing at it, or
  a single `end` SID if the destination is a router.
- The ingress must be an IPv6-enabled router.
- Duplicate `id`s or duplicate `(ingress, destination)` pairs inside one
  document are rejected.
- A derived id is `<ingress>-<destination>` (e.g. `s1-h2`).

JSON example:

```json
{
  "policies": [
    {"id": "s6-to-h2", "ingress": "s6", "destination": "h2", "via": ["s4"]}
  ]
}
```

---

## 4. Commands

### `status`

```sh
./scripts/srctl status
```

Shows the policy count, the last reconcile actions, last reconcile errors and
per-policy errors.

### `policies`

```sh
./scripts/srctl policies
```

Lists the effective policies with `id`, `ingress`, `destination`, `segments`,
`mode`, `metric`, `origin`, `status` and `error`.

### `topology`

```sh
./scripts/srctl topology
```

Shows the controller's graph: routers, hosts, links (with weights) and the SID
catalog.

### `path`

```sh
./scripts/srctl path s1 h2
./scripts/srctl path s1 h2 --via s4
./scripts/srctl path s1 h2 --avoid r3
```

Computes a path and segment list without creating a policy. This talks to the
live controller and can use the current SID catalog.

Offline mode (no lab, no socket):

```sh
./scripts/srctl --topology topologies/srv6.yaml path s1 h2 --via s4
```

Offline mode only computes graph paths and segment lists; it cannot resolve
destination prefixes or egress devices.

### `diff`

```sh
./scripts/srctl diff -f /tmp/policies.yaml
./scripts/srctl diff -f /tmp/policies.yaml --prune
```

Validates the document and prints the planned actions. Does not modify the
data plane or the controller's desired state.

### `apply`

```sh
./scripts/srctl apply -f /tmp/policies.yaml
./scripts/srctl apply -f /tmp/policies.yaml --prune
```

Applies the document and returns:

| Field | Meaning |
|---|---|
| `applied` | Policy ids accepted from the document |
| `removed` | File-origin policy ids removed |
| `actions` | `add` / `replace` / `delete` / `noop` actions taken |
| `errors` | Data-plane or reconcile errors |
| `policy_errors` | Per-policy resolution errors, keyed by id |

### `reconcile`

```sh
./scripts/srctl reconcile
```

Forces a reconciliation immediately. The controller also reconciles every 5
seconds in the background (useful while OSPFv3 converges or after a route is
removed externally).

---

## 5. Common recipes

### Add a new policy

`/tmp/new.yaml`:

```yaml
policies:
  - id: s6-to-h2
    ingress: s6
    destination: h2
    via: [s4]
```

```sh
./scripts/srctl diff -f /tmp/new.yaml
./scripts/srctl apply -f /tmp/new.yaml
```

`diff` shows an `add` for the `h2` prefix on `s6`; `apply` installs it.

### Replace an existing path

Edit the same document and re-apply. A file policy with the same `id` or the
same `(ingress, destination)` is replaced.

```yaml
policies:
  - id: s1-to-h2-direct
    ingress: s1
    destination: h2
    via: []
```

```sh
./scripts/srctl apply -f /tmp/policies.yaml
```

This shadows the baseline `s1 → h2` policy and changes its segment list to just
the terminal SID (`2001:db8:6::10`). On the data plane it is an
`ip -6 route replace` for `fc00:0:d::/48`.

### Change one policy among several

`apply` without `--prune` only upserts the entries in the document; other file
policies are left alone:

```yaml
policies:
  - id: s1-to-h2-direct
    ingress: s1
    destination: h2
    via: [s4]
```

### Make the document authoritative

```sh
./scripts/srctl apply -f /tmp/complete-set.yaml --prune
```

`--prune` replaces the **entire file-origin set** with the document. File
policies not listed are deleted. Baseline policies are never deleted by prune;
a shadowed baseline entry becomes active again.

> `--prune` is global across files, not per file. If you previously applied
> file A and then apply file B with `--prune`, A's policies are removed unless
> B includes them.

### Return to the YAML baseline

```sh
printf 'policies: []\n' > /tmp/empty.yaml
./scripts/srctl apply -f /tmp/empty.yaml --prune
```

All file-origin policies are removed and `yaml-1`, `yaml-2`, … are active
again.

### Force a detour / avoid a node

```yaml
policies:
  - id: s1-to-h2-via-s4
    ingress: s1
    destination: h2
    via: [s4]
```

```yaml
policies:
  - id: s1-to-h2-avoid-r3
    ingress: s1
    destination: h2
    avoid: [r3]
```

### Explicit segment list

```yaml
policies:
  - id: s1-to-h2-explicit
    node: s1
    to: h2
    through: [2001:db8:4::10, 2001:db8:6::10]
```

---

## 6. What happens under the hood

1. The document is parsed and validated as a whole (unknown keys, mixed
   schemas, duplicate ids/keys, invalid modes/metrics, unknown nodes/SIDs).
2. The file-origin set is merged; file policies override baseline policies by
   `id` or `(ingress, destination)`.
3. The desired state is resolved:
   - destination prefixes from the live node interfaces,
   - egress device from `ip -6 route get <first SID>`,
   - forwarding-order segment list.
4. The reconciler diffs against the live `encap seg6` routes and applies:
   - same prefix, new segments → `ip -6 route replace ...`
   - new prefix/ingress → add the new route and delete the old controller-owned
     route,
   - unchanged → `noop`.
5. Only routes the controller installed (`owned`) are deleted. Exact prefix,
   segments, mode, metric and device are matched, so the OSPF route for the same
   prefix is preserved.
6. If OSPFv3 has not converged yet, the policy is kept with an error and the
   background loop retries every 5 seconds. `reconcile` forces it sooner.

---

## 7. Verification and troubleshooting

Check the controller's view:

```sh
./scripts/srctl status
./scripts/srctl policies
```

Check the data plane on the ingress:

```
mininet> s1 ip -6 route show | grep seg6
mininet> s1 ip -6 route get fc00:0:d::2
```

Check the SRH on the wire:

```
mininet> px s4.cmd('timeout 8 tcpdump -ni s4-eth1 -vv ip6 proto 43 > /tmp/cap.txt 2>&1 &')
mininet> h1 ping6 -c5 fc00:0:d::2
mininet> px time.sleep(4)
mininet> px print(s4.cmd('cat /tmp/cap.txt'))
```

Common failures:

| Symptom | Cause |
|---|---|
| `srctl: cannot reach the controller at ...` | Lab/controller not running, or wrong `--socket`/`SRCTL_SOCKET` |
| `policy ... has no end.dx6 ... SID` | Destination has no terminal SID |
| `router 'r2' must have exactly one 'end' SID ...` | A plain underlay router was used as a `via` waypoint |
| `SID ... is not defined in the topology` | Typo in a `via` SID literal |
| `a policy for s1 -> h2 already exists` | Duplicate key inside one document |
| `policy_errors` after apply | OSPF not converged or no route to the first SID; wait and `reconcile` |
| `action replace` never appears | The desired route already matches the data plane (`noop`) |

Exit codes: `0` on success, `1` on control errors (`invalid`, `conflict`,
`not_found`, `dataplane`, `unavailable`).

---

## 8. Scope and limitations

- File policies are **in memory**. Restarting the lab reloads the baseline from
  `topologies/srv6.yaml`; re-apply your policy file after each restart, or
  implement scope B for persistence.
- You can **replace** a baseline policy from a file, but there is no direct
  “delete baseline policy” operation: `--prune` only removes file-origin
  policies, so a shadowed baseline policy returns. A `disabled: true` field or
  file-as-source-of-truth mode would be a small follow-up.
- Segment-list minimisation is not automatic: `via` is installed as given plus
  the terminal SID. Prefer minimal waypoints in the document.
- Controller code lives in `src/srctl/`; `src/net_topology_yaml.py` only builds
  the lab and starts the controller after `net.start()`.

---

## 9. Quick reference

```sh
# inspect
./scripts/srctl status
./scripts/srctl policies
./scripts/srctl topology
./scripts/srctl path s1 h2 --via s4

# plan and apply
./scripts/srctl diff -f /tmp/policies.yaml
./scripts/srctl apply -f /tmp/policies.yaml
./scripts/srctl apply -f /tmp/policies.yaml --prune

# force a retry
./scripts/srctl reconcile

# offline path computation
./scripts/srctl --topology topologies/srv6.yaml path s1 h2 --via s4

# live end-to-end smoke test
make smoke
```
