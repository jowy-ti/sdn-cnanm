# AGENTS.md

## Project

SDN/CNANM lab — Mininet network-emulation scripts in Python (single entrypoint: `src/net_topology.py`). No tests, no CI, no build step.

## Commands

All tooling runs through pixi (there is no system `pip`, `uv`, or `npx`):

```sh
pixi run pyright          # typecheck, strict — must be 0 errors
pixi run ruff check .     # lint (always pass `.` — see ruff.toml)
pixi run ruff format .    # format
pixi install              # after editing pixi.toml
```

Verify with `ruff check .` then `pyright`. No test suite exists.

## Type checking (source of truth: `pyrightconfig.json`)

- Strict mode is enforced by both `pyrightconfig.json` and `.vscode/settings.json` (Pylance). Keep them in sync.
- `mininet` is installed from PyPI but **ships no type stubs**. Hand-written stubs live in `typings/mininet/*.pyi` and are wired in via `stubPath`. Never delete them; when you use a mininet API not yet covered, add it to the matching stub file instead of adding type ignores.
- `.vscode/settings.json` also sets `python.analysis.stubPath: "typings"` — required for Pylance.

## Lint

- `ruff.toml` excludes `.pixi/` and `typings/`. Do not lint `.pixi` (thousands of vendored errors) or fight stub-style rules.

## Runtime gotchas

- Running `net_topology.py` requires root plus Open vSwitch: `sudo systemctl start openvswitch` (see `info.txt`).
- The code expects a remote SDN controller (e.g. os-ken/Ryu) at `127.0.0.1:6653`; without one, `Mininet(... controller=RemoteController)` fails at `net.start()`.

## OpenCode

- `opencode.json` starts LSP (pyright, ruff) and the `jons-mcp-pyright` MCP server through `pixi run` — they depend on the pixi env being installed. Restart opencode after config changes.
