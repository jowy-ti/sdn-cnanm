# AGENTS.md

## Project

SDN/CNANM lab — an ipmininet (Mininet extension) SRv6 emulation. A single entrypoint, `src/net_topology_yaml.py`, builds the lab from `topologies/srv6.yaml`; the former hard-coded `src/net_topology.py` has been removed. The controller has unit tests (`make test`); no CI, no build step.

The topology is the chain `h1 - s1 - r2 - r3 - s4 - r5 - s6 - h2` plus an `r2 - r5` shortcut. OSPFv3 (FRR) is the underlay IGP — chosen over IS-IS because ipmininet has no IS-IS support — so every router runs zebra + ospf6d through `YamlRouterConfig` (our `RouterConfig` subclass, because `BasicRouterConfig` cannot switch OSPFv3 off) and learns the whole topology. The lab is IPv6-only: `use_v4` defaults to false, so there is no `ospfd`/OSPFv2 and no IPv4 addressing. `r2`, `r3` and `r5` stay pure underlay forwarders and never process an SRH. The SRv6 logic lives on three routers: `s1` (ingress) and `s6` (egress) each carry a loopback locator (`2001:db8:1::1/64`, `2001:db8:6::1/64`) with one `SRv6EndDX6Function` endpoint toward the local host (`2001:db8:1::10` → h1, `2001:db8:6::10` → h2), while `s4` (middle) carries a transit `SRv6EndFunction` SID (`2001:db8:4::10`) that pops its own SID and forwards to the next segment. The in-process controller (`src/srctl`) installs one `encap seg6` policy per direction (h1→h2 via `[2001:db8:4::10, 2001:db8:6::10]`, h2→h1 via `[2001:db8:4::10, 2001:db8:1::10]`); because OSPFv3 takes the `r2 - r5` shortcut and would skip s4 entirely, it is the segment list that steers traffic through s4. SRH parsing is enabled on every node by `enable_srv6()` before `net.start()`.

`src/net_topology_yaml.py` builds the lab from a declarative YAML file instead of Python literals. It parses/validates `topologies/srv6.yaml` into frozen dataclasses (routers/hosts/links/policies, per-router locator SIDs) via `yaml.safe_load`, then `YamlSRv6Topo` feeds them to ipmininet (`addRouter(lo_addresses=..., config=YamlRouterConfig)`, `LocalSIDTable` plus the per-behaviour endpoint functions). Each local SID carries a `behavior` (`end.dx6` default, `end` for a transit waypoint, `end.x` for a cross-connect), so a router in the middle of the path can act as a transit SRv6 waypoint. The YAML schema is documented in the module docstring and the header comments of the topology files; the YAML is the source of truth, and adding nodes/links/SIDs/policies should not require touching Python. `--check` parses and validates without root.

## Commands

All tooling runs from a project-local Python venv (`.venv`, created by
`scripts/setup.sh` / `pip install -r requirements-dev.txt`):

```sh
./scripts/setup.sh        # one-time bootstrap: OS packages, .venv, sudoers rule
make check                # parse/validate topologies/srv6.yaml only (no root, no network)
make test                 # controller unit tests (no root, no network)
./scripts/srctl.sh status    # controller CLI without make (lab must be running)
make srctl ARGS="status"  # same CLI through make
make smoke                # live controller smoke driver (root; ~1 minute)
make run                  # start the lab + controller (passwordless sudo; Mininet CLI)
make clean                # remove Mininet leftovers after a crash/Ctrl-C (sudo; mn -c + ipmininet daemon kills)
make doctor               # preflight: venv imports, mn/mnexec, FRR, seg6, frrvty
make deps                 # reinstall requirements-dev.txt into .venv
```

See `docs/ubuntu-22.04-vm-setup.md` (recipe), `docs/ubuntu-22.04-vm-setup-notes.md` (deviations observed on this host) and `docs/ubuntu2204-vm-operations.md` (day-to-day VM operations) for VM setup. `scripts/testbed-creation.sh` stages the minimal runtime set on the host, copies it into the `ubuntu2204` VM and runs `make setup` / `make doctor` inside the guest. Verify locally with `make check` and `make test` (and `make doctor` for the machine).

`make check` validates exactly one file — the default `topologies/srv6.yaml`. It does not discover or check the other YAML files: `topologies/srv6_big.yaml` is an experimental topology that no make target covers, so validate it explicitly with `.venv/bin/python src/net_topology_yaml.py --check topologies/srv6_big.yaml` (no root, no network). The same applies to `topologies/srv6_lab.yaml`, the practice topology (its corrected baseline is active and the faulty pair is commented at the bottom).

`docs/RUNBOOK.md` does not exist right now: the previous version was removed and a new one will be written later. Do not recreate it, and do not treat the removed content as current, unless explicitly asked; until the new file exists, this file (`AGENTS.md`) is the source of developer guidance.

## SDN controller roadmap

The next phase of the project is a small SRv6 controller that computes and
updates SRv6 policies on the lab's routers. Do **not** reach for an OpenFlow
controller (Ryu, Faucet) for this: SRv6 is programmed with
`ip -6 route ... encap seg6` / `seg6local` on the FRR/kernel data plane, not
with OpenFlow flow mods. The work is split into four scopes; implementation
starts with A and only moves up once the current scope is usable. **Scope A is
the priority until the project deadline.**

| Scope | Controller capability | Status |
|---|---|---|
| **A. Policy-only** | Fixed topology (from `topologies/srv6.yaml` + the SID catalog); compute paths with networkx and install/update/remove `encap seg6` policies on existing SRv6 routers. No topology changes. | Implemented (MVP) |
| **B. YAML reload + lab rebuild** | Topology changes by editing the YAML; the lab is restarted, then the controller reloads the graph and reapplies policies. | Planned |
| **C. Pre-staged nodes, runtime attach** | All routers (including spares) are created and started at boot; the controller creates/removes links at runtime, configures OSPFv3 and the SRv6 sysctls, then recomputes policies. | Optional — only if time remains |
| **D. True hot-add** | The controller creates namespaces, veths, addresses, FRR daemons and locator/SIDs on a live lab, with rollback and reconciliation. | Not planned |

Design notes for whichever scope is being implemented:

- Reuse `load_topology()` and the frozen dataclasses in `src/net_topology_yaml.py` as the topology model instead of duplicating the YAML schema.
- Keep controller code out of the lab entrypoint (e.g. a separate `src/srctl/` package); `src/net_topology_yaml.py` stays responsible only for building the Mininet lab.
- Only SRv6-capable routers may be segment endpoints. Plain OSPFv3 routers are graph nodes/weights only: they cannot be an SRv6 waypoint, and adding one changes the IGP path between SIDs without appearing in a segment list.
- Make policy updates idempotent: compute the desired state, diff it against `ip -6 route show`, then `replace`/`delete` only the routes that changed.
- Prefer minimal segment lists: a node SID is a waypoint, not one hop. Include a router's SID only when the path must deviate from what OSPFv3 would choose. (Automatic compression is still a follow-up; the MVP uses an explicit `via` plus the terminal SID.)

## SRv6 controller (scope A)

`src/srctl/` is a small in-process controller. `main()` starts it **after** `net.start()` (local SIDs and `seg6_enabled` are already in place) and stops it before `net.stop()`:

- `models.py` — SID/policy/route dataclasses and controller exceptions.
- `topology.py` — YAML → networkx graph, SID catalog, waypoint/path resolution.
- `southbound.py` — `LiveSouthbound` (pyroute2 netlink on the Mininet nodes) and `DryRunSouthbound` (tests).
- `reconciler.py` — desired-vs-actual diff; only routes the controller installed are ever deleted.
- `policy_doc.py` — Pydantic models and parser for YAML/JSON policy documents.
- `service.py` — baseline/overlay policy state, document apply, path computation.
- `control.py` — Unix-domain-socket server and client (the only client surface is `srctl`).
- `runtime.py` — starts the control socket and the periodic reconcile loop.
- `cli.py` — `make srctl ARGS="..."` client; no HTTP/REST layer.

Operating model:

- The YAML topology `policies[].via` list is the **baseline** (`yaml-1`, `yaml-2`, ...). It is resolved once at load into `via SIDs + the destination's terminal SID` and installed; it is not recomputed at runtime. Topology policies only accept `via`; an explicit `through` list is only available in `srctl` policy documents.
- A YAML/JSON policy document applied with `srctl apply` is an **overlay**. A file policy wins when it shares an `id` or an `(ingress, destination)` pair with a baseline policy; `apply --prune` removes only file-origin policies, so a shadowed baseline policy becomes active again.
- In a document, `via` (router names or SID literals) is computed into `via SIDs + terminal SID`; `through` is installed verbatim. Both forms are accepted, but not mixed in one policy.
- The whole document is parsed and validated before anything changes. `srctl diff` runs the same validation and prints the planned `add`/`replace`/`delete`/`noop` actions without touching the data plane.
- `post_build()` no longer installs encapsulation routes; it only installs local SIDs and calls `enable_srv6()`. The controller is the sole owner of `encap seg6` policies.
- Routes are installed with `ip -6 route replace <prefix> encap seg6 mode <mode> segs <list> metric <cost> dev <dev>`, where `<dev>` comes from `ip -6 route get <first SID>` (not ipmininet's first-interface heuristic). Deletion matches the exact prefix/segments/mode/metric/dev, so the OSPF route for the same prefix is preserved.
- The controller listens on a per-user Unix socket (`/tmp/srctl-<uid>.sock`, `$SRCTL_SOCKET` overrides). There is no network port; `make run` needs no extra argv by default (use `make run ARGS=<topology>` to pick another YAML file), so the existing sudoers rule keeps matching.

Policy document example (`policies/*.yaml`, JSON also accepted):

```yaml
policies:
  - id: h1-to-h2-via-s4
    ingress: s1
    destination: h2
    via: [s4]

  - id: explicit-return
    node: s6
    to: h1
    through: [2001:db8:4::10, 2001:db8:1::10]
```

Common commands:

```sh
make run                                   # lab + controller
./scripts/srctl.sh status                     # no make needed; works from any cwd
./scripts/srctl.sh policies
./scripts/srctl.sh diff -f policies/smoke.yaml
./scripts/srctl.sh apply -f policies/smoke.yaml
./scripts/srctl.sh apply -f policies/smoke.yaml --prune
./scripts/srctl.sh path s1 h2 --via s4        # against the live controller
./scripts/srctl.sh --topology topologies/srv6.yaml path s1 h2 --via s4  # offline
make smoke                                 # live end-to-end driver (root)
```

`scripts/srctl.sh` is a thin wrapper that runs `python -m srctl.cli` with `PYTHONPATH=src` and the repo `.venv`; `make srctl ARGS="..."` is the equivalent make target. Neither needs `sudo`; only `make run` / `make smoke` do.

Run `./scripts/srctl.sh --help` for the CLI surface. `scripts/controller-smoke.txt` is the Mininet driver behind `make smoke`: it waits for OSPFv3, lists/validates/applies/prunes `policies/smoke.yaml`, checks the ingress routes and pings h1→h2.

IPv4 is intentionally absent: `use_v4` defaults to false in the YAML schema and is passed to both routers and hosts, so `YamlRouterConfig` registers only OSPFv6 (zebra comes in as its dependency). Setting `use_v4: true` on a node restores OSPFv2/IPv4 for that node.

`scripts/controller-smoke.txt` replaces the removed `srv6_check.txt` probe; the old driver used the pre-rename `r1`/`r4`/`r6` names and did not exercise the controller.

## Dependencies (pip + venv)

- `.venv` is the **only** Python environment. The project previously used a
  Pixi/Conda environment; `pixi.toml`/`pixi.lock` have been removed, and all
  configs, scripts, docs and Makefile targets point at `.venv`. Do not
  reintroduce a second environment.
- `requirements.txt` is the runtime set; `requirements-dev.txt` adds pyright, the
  OpenCode MCP server and the controller test tooling (`pytest`;
  `-r requirements.txt` pulls the runtime set in).
- The controller adds `networkx`, `pyroute2` and `pydantic` to the runtime set
  (pinned to versions that still install on the Ubuntu 22.04 VM's Python 3.10).
  Controller unit tests live in `tests/` and run with `make test`; they do not
  need root, Mininet or FRR.
- Use the venv, not system `pip3`: Ubuntu 23.04+ enforces PEP 668 (externally
  managed environment) and a root-owned system install of `ipmininet` would
  shadow the distro `mininet`. Create `.venv` as your user, never root.
- `mininet` comes from PyPI (explicit: ipmininet's setup.py does not declare it).
- `pyyaml` is declared explicitly for `src/net_topology_yaml.py` (a copy was already present transitively; the entrypoint should not depend on that). It has no `py.typed`, so a minimal stub lives in `typings/yaml/__init__.pyi`.
- `pyright[nodejs]` bundles Node (`nodejs-wheel-binaries`) so no system nodejs is needed; Ubuntu 22.04 ships nodejs 12, which is too old.
- `ipmininet` is **not installed from PyPI**. PyPI only has 1.0, whose sdist fails to build with modern setuptools (`setup.py` imports `pkg_resources`) and pins `mininet==2.3.0`, which does not exist on PyPI. A locally patched wheel built from [cnp3/ipmininet](https://github.com/cnp3/ipmininet) master (v1.1) lives in `vendor/` and is referenced by path from `requirements.txt` (`ipmininet @ file:./vendor/...`; pip resolves it relative to the requirements file). `vendor/` is tracked by git — do not gitignore it, and do not delete the wheel.
- To rebuild the wheel: take the upstream source, replace `setup.py` with a minimal one (drop `pkg_resources`, the mininet pin, `setup_requires`, and the `install`/`develop` cmdclass hooks that write to `/opt`; keep `install_requires = ["setuptools", "mako>=1.1,<1.2"]` and `include_package_data=True`), then `python -m build --wheel` and put the wheel in `vendor/`.

## Type checking (source of truth: `pyrightconfig.json`)

- Strict mode is enforced by both `pyrightconfig.json` and `.vscode/settings.json` (Pylance). Keep them in sync.
- pyright does **not** auto-detect `.venv`: `pyrightconfig.json` sets `venvPath`/`venv` explicitly. Without that, pyright analyses against the system interpreter and reports bogus `reportMissingImports` for `jons_mcp_pyright`.
- `ipmininet` ships no stubs. Hand-written stubs live in `typings/` (`ipmininet/`, subpackages included, plus `mako/` and `yaml/` — neither ships `py.typed`) and are wired in via `stubPath`. Never delete them; when you use an ipmininet API not yet covered, add it to the matching stub file instead of adding type ignores.
- The controller uses `networkx`, `pyroute2` and `pydantic`; pyroute2 ships no stubs, so a minimal stub lives in `typings/pyroute2/`. The ipmininet stubs needed extending (`enable_srv6`, `IPNet.routers`/`hosts`, `RouterConfig.daemons`). `tests/` is covered by strict mode too.
- `reportMissingModuleSource` is set to `none` (ipmininet/mininet source is present but untyped in places).
- `.vscode/settings.json` also sets `python.analysis.stubPath: "typings"` and `python.defaultInterpreterPath` — required for Pylance.

## Runtime gotchas

- Running a topology needs root: `make run` runs `sudo <repo>/.venv/bin/python <repo>/src/net_topology_yaml.py` (absolute paths, no `-E`: a matching `NOPASSWD` line in `/etc/sudoers.d/net_topology` lets it run passwordless; `-E` would require a `SETENV` sudoers tag). The rule must be a single command line (`python` *and* the script) and it matches the exact argv, so any extra argument needs a password: `make run ARGS="topologies/srv6_lab.yaml"` selects another topology but prompts on a host whose rule covers only the argument-free command. `make check` never needs root (`make check ARGS=<topology>` validates another file). `scripts/setup.sh` generates the rule for the current checkout.
- FRR must be installed system-wide (`apt-get install frr` / `dnf install frr`). The daemons live in `/usr/lib/frr` on Debian/Ubuntu and `/usr/libexec/frr` on Fedora/RHEL, neither of which is on PATH; the entrypoint probes both (plus `/usr/local/lib/frr`) and prepends the one that contains `zebra`/`ospfd`/`ospf6d` — ipmininet does `require_cmd("zebra"/"ospfd"/"ospf6d")` on PATH and fails at `net.start()` otherwise. Run `sudo systemctl disable --now frr` if the system FRR service is running (the topology starts its own instances).
- One-time host setup: `sudo usermod -aG frrvty root`. ipmininet validates daemon configs with `-Cf ... -u root` and FRR aborts with `user(root) is not part of vty group specified(frrvty)` when root is not in that group (Debian/Ubuntu's `frr.postinst` only adds the `frr` user; Fedora's `frrvty` group only contains `frr`), killing every router at `net.start()`.
- The FRR vty socket directory is `/run/frr` (`/var/run/frr`) on both Debian/Ubuntu and Fedora, so ipmininet's private tmpfs mount and its `vtysh` commands work unchanged.
- ipmininet's stock `templates/ospf6d.mako` assigns areas with the Quagga-era `interface X area Y` line under `router ospf6`, which FRR 10 rejects as `Unknown command`: interfaces never join an area, so OSPFv3 forms no adjacencies and SRv6 outer packets are unroutable. The repo ships a fixed copy in `src/templates/ospf6d.mako` (`ipv6 ospf6 area` inside each interface stanza), wired in via `YamlRouterConfig`, which registers OSPFv3 with `template_lookup=OSPF6_TEMPLATES` only when the router has `ospf6: true`. A router with `ospf6: false` therefore really loses the OSPFv3 daemon instead of silently falling back to the stock template. The patched template is valid on FRR 8.1 (Ubuntu 22.04), 8.4 (24.04) and 10.x, so no FRR pin is needed. Rebase that file if the wheel is ever rebuilt from newer ipmininet.
- iproute2 6.17 colorizes `ip addr` when stdout is a tty, and Mininet shells are ptys, which breaks ipmininet's address parser (`AddressValueError: Only decimal digits permitted in '\x1b[1;35m127'...`). The entrypoint sets `NO_COLOR=1` at import time — do not drop that line (harmless on older iproute2).
- The current topology uses no OVS switches, so `openvswitch` is not required (older topologies did; `sudo systemctl start openvswitch`).
- SRv6 needs kernel `seg6` support (`net.ipv6.conf.*.seg6_enabled`); Ubuntu has `CONFIG_IPV6_SEG6_LWTUNNEL=y` since 18.04. ipmininet sets the sysctls when an SRv6 route is installed.
- Interactive: `__main__` drops into `IPCLI(net)` (Mininet's `Mininet` class has no `.cli()` method); exit it to stop the network.

## OpenCode

- `opencode.json` starts the pyright LSP and the `pyright` MCP server from `.venv/bin/` — both need the venv to exist (`make setup`). Use relative paths so the config survives moving the checkout; OpenCode resolves them from the workspace root, so a missing `.venv` fails the spawn with `NotFound: ChildProcess.spawn (.venv/bin/python …)`.
- A failed local MCP server is **not** retried: the status stays `failed` after `.venv` appears. Recover with `/mcps` → reconnect, a real config save, or `opencode service restart`. V2 needs no restart for config edits, but reloads fire on content changes — editing only the mtime does not trigger one.
- V2 shape: servers live under `mcp.servers.<name>`; `enabled` is not a V2 field (use `disabled` to keep one configured but disconnected). Do not reintroduce `mcp.pyright`.
- The MCP server is launched via `scripts/run_jons_mcp_pyright.py`, a wrapper that injects the LSP `workspaceFolders` initialize param missing from `jons-mcp-pyright 0.0.2`. Without it pyright registers no workspace (it falls back to `rootPath`, never `rootUri`) and ignores `pyrightconfig.json` — including `stubPath` — so `src/net_topology_yaml.py` goes from 0 to 16 bogus `Stub file not found for "ipmininet.*"` diagnostics while the server still shows `connected`. Upstream `master` and the latest PyPI release (0.0.2) both still omit the param; the patch monkeypatches `PyrightClient.request`, so re-validate it on any version bump and drop it once upstream sends `workspaceFolders`.
