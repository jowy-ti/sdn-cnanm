# AGENTS.md

## Project

SDN/CNANM lab — an ipmininet (Mininet extension) SRv6 emulation, single entrypoint: `src/net_topology.py`. No tests, no CI, no build step.

The topology is the chain `h1 - r1 - r2 - r3 - h2`. OSPFv3 (FRR) is the underlay IGP — chosen over IS-IS because ipmininet has no IS-IS support — so every router keeps the ipmininet default `BasicRouterConfig` (zebra + ospfd + ospf6d) and learns the whole topology; r2 stays a pure underlay forwarder and never processes an SRH. r1 (ingress) and r3 (egress) hold all the SRv6 logic: loopback locators `2001:db8:{1,2,3}::1/64` carry the SIDs, `post_build()` installs one `SRv6Encap` route per direction (h1→h2 via `2001:db8:3::10`, h2→h1 via `2001:db8:1::10`) plus a `LocalSIDTable` + `SRv6EndDX6Function` endpoint on each router toward its local host. Installing an SRv6 route also enables SRH parsing on every node (`enable_srv6`).

## Commands

All tooling runs through pixi (there is no system `pip`, `uv`, or `npx`):

```sh
pixi run pyright          # typecheck, strict — must be 0 errors
pixi run ruff check .     # lint (always pass `.` — see ruff.toml)
pixi run ruff format .    # format
pixi install              # after editing pixi.toml
pixi run net              # run the topology (passwordless sudo; drops into the Mininet CLI)
```

Verify with `ruff check .` then `pyright`. No test suite exists.

## Dependencies (pixi)

- Default env only; there is no second environment (the old Ryu controller feature/env was removed).
- `mininet` comes from PyPI (explicit: ipmininet's setup.py does not declare it).
- `ipmininet` is **not installed from PyPI**. PyPI only has 1.0, whose sdist fails to build with modern setuptools (`setup.py` imports `pkg_resources`) and pins `mininet==2.3.0`, which does not exist on PyPI. A locally patched wheel built from [cnp3/ipmininet](https://github.com/cnp3/ipmininet) master (v1.1) lives in `vendor/` and is wired via `pypi-options.find-links` in `pixi.toml`. `vendor/` is tracked by git — do not gitignore it, and do not delete the wheel.
- To rebuild the wheel: take the upstream source, replace `setup.py` with a minimal one (drop `pkg_resources`, the mininet pin, `setup_requires`, and the `install`/`develop` cmdclass hooks that write to `/opt`; keep `install_requires = ["setuptools", "mako>=1.1,<1.2"]` and `include_package_data=True`), then `python -m build --wheel` and put the wheel in `vendor/`.

## Type checking (source of truth: `pyrightconfig.json`)

- Strict mode is enforced by both `pyrightconfig.json` and `.vscode/settings.json` (Pylance). Keep them in sync.
- `ipmininet` ships no stubs. Hand-written stubs live in `typings/` (`ipmininet/`, subpackages included, plus `mako/` — mako has no `py.typed`) and are wired in via `stubPath`. Never delete them; when you use an ipmininet API not yet covered, add it to the matching stub file instead of adding type ignores.
- `reportMissingModuleSource` is set to `none` (ipmininet/mininet source is present but untyped in places).
- `.vscode/settings.json` also sets `python.analysis.stubPath: "typings"` — required for Pylance.

## Lint

- `ruff.toml` excludes `.pixi/` and `typings/`. Do not lint `.pixi` (thousands of vendored errors) or fight stub-style rules.

## Runtime gotchas (see also `info.txt`)

- Running the topology needs root: `pixi run net` runs `sudo /home/joelgonzalez/Documents/sdn-cnanm/.pixi/envs/default/bin/python /home/joelgonzalez/Documents/sdn-cnanm/src/net_topology.py` (absolute paths, no `-E`: a matching `NOPASSWD` line in `/etc/sudoers.d/net_topology` lets it run passwordless; `-E` would require a `SETENV` sudoers tag).
- FRR must be installed system-wide (`sudo dnf install frr`). Fedora puts the daemons in `/usr/libexec/frr`, which is not on PATH; `src/net_topology.py` prepends it (plus `/usr/sbin`) at import time — ipmininet does `require_cmd("zebra"/"ospfd"/"ospf6d")` on PATH and fails at `net.start()` otherwise. Run `sudo systemctl stop frr` if the system FRR service is running (the topology starts its own instances).
- One-time host setup: `sudo usermod -aG frrvty root`. ipmininet validates daemon configs with `-Cf ... -u root` and FRR 10 aborts with `user(root) is not part of vty group specified(frrvty)` when root is not in that group (Fedora's `frrvty` group only contains `frr`), killing every router at `net.start()`.
- ipmininet's stock `templates/ospf6d.mako` assigns areas with the Quagga-era `interface X area Y` line under `router ospf6`, which FRR 10 rejects as `Unknown command`: interfaces never join an area, so OSPFv3 forms no adjacencies and SRv6 outer packets are unroutable. The repo ships a fixed copy in `src/templates/ospf6d.mako` (`ipv6 ospf6 area` inside each interface stanza), wired in `SRv6MetricTopo.build()` via `addDaemon(..., OSPF6, template_lookup=OSPF6_TEMPLATES)` (ipmininet keeps the first daemon registered per name). Rebase that file if the wheel is ever rebuilt from newer ipmininet.
- iproute2 6.17 colorizes `ip addr` when stdout is a tty, and Mininet shells are ptys, which breaks ipmininet's address parser (`AddressValueError: Only decimal digits permitted in '\x1b[1;35m127'...`). The script sets `NO_COLOR=1` at import time — do not drop that line.
- The current topology uses no OVS switches, so `openvswitch` is not required (older topologies did; `sudo systemctl start openvswitch`).
- SRv6 needs kernel `seg6` support (`net.ipv6.conf.*.seg6_enabled`, already available on this machine); ipmininet sets the sysctls when an SRv6 route is installed.
- Interactive: `__main__` drops into `IPCLI(net)` (Mininet's `Mininet` class has no `.cli()` method); exit it to stop the network.

## OpenCode

- `opencode.json` starts LSP (pyright, ruff) and the `pyright` MCP server through `pixi run` — they depend on the pixi env being installed. Restart opencode after config changes.
- The MCP server is launched via `scripts/run_jons_mcp_pyright.py`, a wrapper that injects the LSP `workspaceFolders` initialize param missing from `jons-mcp-pyright 0.0.2` (without it, pyright ignores `pyrightconfig.json` entirely and reports bogus strict errors). Delete the wrapper once upstream fixes it.
