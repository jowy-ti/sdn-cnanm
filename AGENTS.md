# AGENTS.md

## Project

SDN/CNANM lab — an ipmininet (Mininet extension) SRv6 emulation. A single entrypoint, `src/net_topology_yaml.py`, builds the lab from `topologies/srv6.yaml`; the former hard-coded `src/net_topology.py` has been removed. No tests, no CI, no build step.

The topology is the chain `h1 - r1 - r2 - r3 - r4 - r5 - r6 - h2` plus an `r2 - r5` shortcut. OSPFv3 (FRR) is the underlay IGP — chosen over IS-IS because ipmininet has no IS-IS support — so every router keeps the ipmininet default `BasicRouterConfig` (zebra + ospfd + ospf6d) and learns the whole topology; `r2`, `r3` and `r5` stay pure underlay forwarders and never process an SRH. The SRv6 logic lives on three routers: `r1` (ingress) and `r6` (egress) each carry a loopback locator (`2001:db8:1::1/64`, `2001:db8:6::1/64`) with one `SRv6EndDX6Function` endpoint toward the local host (`2001:db8:1::10` → h1, `2001:db8:6::10` → h2), while `r4` (middle) carries a transit `SRv6EndFunction` SID (`2001:db8:4::10`) that pops its own SID and forwards to the next segment. `post_build()` installs one `SRv6Encap` route per direction (h1→h2 via `[2001:db8:4::10, 2001:db8:6::10]`, h2→h1 via `[2001:db8:4::10, 2001:db8:1::10]`); because OSPFv3 takes the `r2 - r5` shortcut and would skip r4 entirely, it is the segment list that steers traffic through r4. Installing an SRv6 route also enables SRH parsing on every node (`enable_srv6`).

`src/net_topology_yaml.py` builds the lab from a declarative YAML file instead of Python literals. It parses/validates `topologies/srv6.yaml` into frozen dataclasses (routers/hosts/links/policies, per-router locator SIDs) via `yaml.safe_load`, then `YamlSRv6Topo` feeds them to ipmininet (`addRouter(lo_addresses=...)`, `addDaemon(..., OSPF6, template_lookup=...)`, `SRv6Encap`, `LocalSIDTable` plus the per-behaviour endpoint functions). Each local SID carries a `behavior` (`end.dx6` default, `end` for a transit waypoint, `end.x` for a cross-connect), so a router in the middle of the path can act as a transit SRv6 waypoint. The YAML schema is documented in the module docstring; the YAML is the source of truth, and adding nodes/links/SIDs/policies should not require touching Python. `--check` parses and validates without root.

## Commands

All tooling runs from a project-local Python venv (`.venv`, created by
`scripts/setup.sh` / `pip install -r requirements-dev.txt`):

```sh
./scripts/setup.sh        # one-time bootstrap: OS packages, .venv, sudoers rule
make typecheck            # pyright, strict — must be 0 errors
make lint                 # ruff check . (always pass `.` — see ruff.toml)
make format               # ruff format .
make check                # parse/validate topologies/srv6.yaml only (no root, no network)
make run                  # run the SRv6 topology (passwordless sudo; drops into the Mininet CLI)
make clean                # remove Mininet leftovers after a crash/Ctrl-C (sudo; mn -c + ipmininet daemon kills)
make doctor               # preflight: venv imports, mn/mnexec, FRR, seg6, frrvty
make deps                 # reinstall requirements-dev.txt into .venv
```

See `docs/RUNBOOK.md` for detailed lab instructions and `docs/VM_GUIDE.md` for VM setup. Verify with `make lint` then `make typecheck`. No test suite exists.

## Dependencies (pip + venv)

- `.venv` is the **only** Python environment. The project previously used a
  Pixi/Conda environment; `pixi.toml`/`pixi.lock` have been removed, and all
  configs, scripts, docs and Makefile targets point at `.venv`. Do not
  reintroduce a second environment.
- `requirements.txt` is the runtime set; `requirements-dev.txt` adds ruff,
  pyright and the OpenCode MCP server (`-r requirements.txt` pulls the runtime
  set in).
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
- `reportMissingModuleSource` is set to `none` (ipmininet/mininet source is present but untyped in places).
- `.vscode/settings.json` also sets `python.analysis.stubPath: "typings"` and `python.defaultInterpreterPath` — required for Pylance.

## Lint

- `ruff.toml` excludes `.venv/` and `typings/`. Do not lint `.venv` (tens of thousands of vendored errors) or fight stub-style rules. Note that setting `exclude` replaces ruff's defaults, so both entries are required.

## Runtime gotchas (see also `docs/RUNBOOK.md`)

- Running a topology needs root: `make run` runs `sudo <repo>/.venv/bin/python <repo>/src/net_topology_yaml.py` (absolute paths, no `-E`: a matching `NOPASSWD` line in `/etc/sudoers.d/net_topology` lets it run passwordless; `-E` would require a `SETENV` sudoers tag). The rule must be a single command line (`python` *and* the script) and it matches the exact argv, so any extra argument (e.g. `--check`) needs a password — run `--check` without sudo via `make check`. `scripts/setup.sh` generates the rule for the current checkout.
- FRR must be installed system-wide (`apt-get install frr` / `dnf install frr`). The daemons live in `/usr/lib/frr` on Debian/Ubuntu and `/usr/libexec/frr` on Fedora/RHEL, neither of which is on PATH; the entrypoint probes both (plus `/usr/local/lib/frr`) and prepends the one that contains `zebra`/`ospfd`/`ospf6d` — ipmininet does `require_cmd("zebra"/"ospfd"/"ospf6d")` on PATH and fails at `net.start()` otherwise. Run `sudo systemctl disable --now frr` if the system FRR service is running (the topology starts its own instances).
- One-time host setup: `sudo usermod -aG frrvty root`. ipmininet validates daemon configs with `-Cf ... -u root` and FRR aborts with `user(root) is not part of vty group specified(frrvty)` when root is not in that group (Debian/Ubuntu's `frr.postinst` only adds the `frr` user; Fedora's `frrvty` group only contains `frr`), killing every router at `net.start()`.
- The FRR vty socket directory is `/run/frr` (`/var/run/frr`) on both Debian/Ubuntu and Fedora, so ipmininet's private tmpfs mount and the RUNBOOK's `r4 vtysh -c ...` commands work unchanged.
- ipmininet's stock `templates/ospf6d.mako` assigns areas with the Quagga-era `interface X area Y` line under `router ospf6`, which FRR 10 rejects as `Unknown command`: interfaces never join an area, so OSPFv3 forms no adjacencies and SRv6 outer packets are unroutable. The repo ships a fixed copy in `src/templates/ospf6d.mako` (`ipv6 ospf6 area` inside each interface stanza), wired in `YamlSRv6Topo.build()` via `addDaemon(..., OSPF6, template_lookup=OSPF6_TEMPLATES)` (ipmininet keeps the first daemon registered per name). The patched template is valid on FRR 8.1 (Ubuntu 22.04), 8.4 (24.04) and 10.x, so no FRR pin is needed. Rebase that file if the wheel is ever rebuilt from newer ipmininet.
- iproute2 6.17 colorizes `ip addr` when stdout is a tty, and Mininet shells are ptys, which breaks ipmininet's address parser (`AddressValueError: Only decimal digits permitted in '\x1b[1;35m127'...`). The entrypoint sets `NO_COLOR=1` at import time — do not drop that line (harmless on older iproute2).
- The current topology uses no OVS switches, so `openvswitch` is not required (older topologies did; `sudo systemctl start openvswitch`).
- SRv6 needs kernel `seg6` support (`net.ipv6.conf.*.seg6_enabled`); Ubuntu has `CONFIG_IPV6_SEG6_LWTUNNEL=y` since 18.04. ipmininet sets the sysctls when an SRv6 route is installed.
- Interactive: `__main__` drops into `IPCLI(net)` (Mininet's `Mininet` class has no `.cli()` method); exit it to stop the network.

## OpenCode

- `opencode.json` starts LSP (pyright, ruff) and the `pyright` MCP server from `.venv/bin/` — all three need the venv to exist (`make setup`). Use relative paths so the config survives moving the checkout; OpenCode resolves them from the workspace root, so a missing `.venv` fails the spawn with `NotFound: ChildProcess.spawn (.venv/bin/python …)`.
- A failed local MCP server is **not** retried: the status stays `failed` after `.venv` appears. Recover with `/mcps` → reconnect, a real config save, or `opencode service restart`. V2 needs no restart for config edits, but reloads fire on content changes — editing only the mtime does not trigger one.
- V2 shape: servers live under `mcp.servers.<name>`; `enabled` is not a V2 field (use `disabled` to keep one configured but disconnected). Do not reintroduce `mcp.pyright`.
- The MCP server is launched via `scripts/run_jons_mcp_pyright.py`, a wrapper that injects the LSP `workspaceFolders` initialize param missing from `jons-mcp-pyright 0.0.2`. Without it pyright registers no workspace (it falls back to `rootPath`, never `rootUri`) and ignores `pyrightconfig.json` — including `stubPath` — so `src/net_topology_yaml.py` goes from 0 to 16 bogus `Stub file not found for "ipmininet.*"` diagnostics while the server still shows `connected`. Upstream `master` and the latest PyPI release (0.0.2) both still omit the param; the patch monkeypatches `PyrightClient.request`, so re-validate it on any version bump and drop it once upstream sends `workspaceFolders`.
