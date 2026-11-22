# sdn-cnanm

Lab repository for an ipmininet-based SRv6 emulation.

## Quick start

```sh
./scripts/setup.sh    # OS packages, .venv, sudoers rule
make check            # parse/validate topologies/srv6.yaml
make test             # controller unit tests (no root)
make run              # start the lab + controller, then the Mininet CLI
```

While the lab runs, drive the SRv6 controller without `make`:

```sh
./scripts/srctl.sh status
./scripts/srctl.sh apply -f policies/smoke.yaml
```

All tooling runs from the project-local `.venv` created by `scripts/setup.sh`.

- **Developer guide**: `AGENTS.md`
- **Controller CLI guide**: `docs/SRCTL.md`
- **Lab runbook**: pending (the previous version was removed)
- **VM setup**: `docs/ubuntu-22.04-vm-setup.md`, `docs/ubuntu2204-vm-operations.md`
