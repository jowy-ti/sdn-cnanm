# sdn-cnanm

Lab repository for an ipmininet-based SRv6 emulation.

## Quick start

```sh
./scripts/setup.sh    # OS packages, .venv, sudoers rule
make check            # parse/validate topologies/srv6.yaml
make run              # start the lab and drop into the Mininet CLI
```

All tooling runs from the project-local `.venv` created by `scripts/setup.sh`.

- **Developer guide**: `AGENTS.md`
- **Lab runbook**: `docs/RUNBOOK.md`
- **VM setup**: `docs/VM_GUIDE.md`
