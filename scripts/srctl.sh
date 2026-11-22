#!/usr/bin/env bash
#
# Run the SRv6 controller CLI without make.
#
#   ./scripts/srctl.sh status
#   ./scripts/srctl.sh apply -f policies/smoke.yaml
#   ./scripts/srctl.sh diff -f policies/smoke.yaml
#   ./scripts/srctl.sh path s1 h2 --via s4
#
# The controller itself must be running (make run).  Relative file arguments
# are resolved against the caller's current directory.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if [[ ! -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    echo "srctl: missing ${REPO_ROOT}/.venv (run scripts/setup.sh)" >&2
    exit 1
fi

PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}" \
    exec "${REPO_ROOT}/.venv/bin/python" -m srctl.cli "$@"
