# AGENTS.md

## Project

SDN/CNANM lab — Mininet network-emulation scripts in Python (single entrypoint: `src/net_topology.py`). No tests, no CI, no build step.

Modes passed as first argument: `openflow`, `linux` (unchanged), and `srv6` (SRv6 NDP-discovery experiment, see below).

## Commands

All tooling runs through pixi (there is no system `pip`, `uv`, or `npx`):

```sh
pixi run pyright          # typecheck, strict — must be 0 errors
pixi run ruff check .     # lint (always pass `.` — see ruff.toml)
pixi run ruff format .    # format
pixi install              # after editing pixi.toml
```

Verify with `ruff check .` then `pyright`. No test suite exists.

### srv6 mode

Two processes, controller first:

```sh
pixi run -e controller srv6-controller            # Ryu REST app on :8080 (no OFP listener)
sudo python3 src/net_topology.py srv6             # builds topology, then routing.setup()
```

- `controller/srv6_app.py` — Ryu app: routers POST NDP reports to `POST /v1.0/srv6/report`, app computes edge routers + BFS paths, serves per-node encap/decap policies at `GET /v1.0/srv6/policies`.
- `src/routing.py` — runs on the nodes after `net.start()`: assigns `fd00:0:<n>::/64` underlay (MTU 1600), triggers kernel NDP, reports to Ryu, applies the returned policies (`ip -6 route encap seg6 …` / `seg6local End.DX4`).
- Routers ping each other in srv6 mode only via hosts (`net.ping(hosts=…)`); middle routers intentionally have no IPv4 route.

## Type checking (source of truth: `pyrightconfig.json`)

- Strict mode is enforced by both `pyrightconfig.json` and `.vscode/settings.json` (Pylance). Keep them in sync.
- `mininet` is installed from PyPI but **ships no type stubs**. Hand-written stubs live in `typings/mininet/*.pyi` and are wired in via `stubPath`. Never delete them; when you use a mininet API not yet covered, add it to the matching stub file instead of adding type ignores.
- `ryu` (controller only, Python 3.9 env) also ships no stubs: minimal stubs live in `typings/ryu/*.pyi`. Same rule — extend the stubs, don't add type ignores. `reportMissingModuleSource` is set to `none` because ryu's source is intentionally absent from the default env.
- `.vscode/settings.json` also sets `python.analysis.stubPath: "typings"` — required for Pylance.

## Lint

- `ruff.toml` excludes `.pixi/` and `typings/`. Do not lint `.pixi` (thousands of vendored errors) or fight stub-style rules.

## Controller environment (pixi feature `controller`)

- Isolated from the default env (`no-default-feature`): `python = "3.9.*"` (Ryu 4.34 is broken on ≥3.10 with `eventlet 0.30.2`), `ryu == 4.34`, `eventlet == 0.30.2` (`ryu/app/wsgi.py` needs `eventlet.wsgi.ALREADY_HANDLED`, removed in 0.31).
- Ryu's sdist cannot build with setuptools ≥68 → the wheel is vendored at `vendor/ryu-4.34-py3-none-any.whl` and wired via feature-level `pypi-options.find-links`. Feature pypi-options replace the defaults, so `index-url = "https://pypi.org/simple"` must stay restated next to `find-links`.
- pyright does not exclude `controller/`: `controller/srv6_app.py` must stay strict-clean like the rest of the repo.

## Runtime gotchas

- Running `net_topology.py` requires root plus Open vSwitch: `sudo systemctl start openvswitch` (see `info.txt`).
- The code expects a remote SDN controller (e.g. os-ken/Ryu) at `127.0.0.1:6653`; without one, `Mininet(... controller=RemoteController)` fails at `net.start()`.
- srv6 mode additionally requires the Ryu app (`pixi run -e controller srv6-controller`) to be up before launching the topology, and IPv6 forwarding + `seg6` lwtunnel support (already verified on this machine).

## OpenCode

- `opencode.json` starts LSP (pyright, ruff) and the `pyright` MCP server through `pixi run` — they depend on the pixi env being installed. Restart opencode after config changes.
- The MCP server is launched via `scripts/run_jons_mcp_pyright.py`, a wrapper that injects the LSP `workspaceFolders` initialize param missing from `jons-mcp-pyright 0.0.2` (without it, pyright ignores `pyrightconfig.json` entirely and reports bogus strict errors). Delete the wrapper once upstream fixes it.
