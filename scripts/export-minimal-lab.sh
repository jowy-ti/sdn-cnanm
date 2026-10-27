#!/usr/bin/env bash
#
# Export a minimal SRv6 lab environment to a target directory (e.g. for copying
# to an Ubuntu VM).
#
# Copies exactly the files present in ~/Documents/srv6-lab:
#   src/net_topology_yaml.py
#   src/templates/ospf6d.mako
#   topologies/srv6.yaml
#   vendor/ipmininet-1.1-py3-none-any.whl (and any other files in vendor/ if present)
#   scripts/setup.sh, scripts/doctor.sh
#   Makefile
#   requirements.txt
#
# Usage:
#   ./scripts/export-minimal-lab.sh ~/Documents/srv6-lab-export
#
set -euo pipefail

SRC_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TARGET=${1:-}

if [[ -z "${TARGET}" ]]; then
    echo "usage: $0 <target-directory>" >&2
    exit 1
fi

# Normalize paths
TARGET=$(mkdir -p "${TARGET}" && cd "${TARGET}" && pwd)

echo "==> Exporting minimal SRv6 lab from ${SRC_ROOT}"
echo "    to ${TARGET}"

mkdir -p "${TARGET}/src/templates" "${TARGET}/topologies" "${TARGET}/vendor" "${TARGET}/scripts"

echo "    copying files..."

# Core runtime
cp "${SRC_ROOT}/src/net_topology_yaml.py" "${TARGET}/src/"
cp "${SRC_ROOT}/src/templates/ospf6d.mako" "${TARGET}/src/templates/"
cp "${SRC_ROOT}/topologies/srv6.yaml" "${TARGET}/topologies/"

# Vendor wheel
if [[ -d "${SRC_ROOT}/vendor" ]]; then
    shopt -s nullglob
    for f in "${SRC_ROOT}/vendor/"*; do
        cp "$f" "${TARGET}/vendor/"
    done
    shopt -u nullglob
fi

# Scripts (setup + doctor)
cp "${SRC_ROOT}/scripts/setup.sh" "${TARGET}/scripts/"
cp "${SRC_ROOT}/scripts/doctor.sh" "${TARGET}/scripts/"

# Makefile + requirements
cp "${SRC_ROOT}/Makefile" "${TARGET}/"
cp "${SRC_ROOT}/requirements.txt" "${TARGET}/"

# Ensure setup.sh is executable
chmod +x "${TARGET}/scripts/setup.sh" "${TARGET}/scripts/doctor.sh" 2>/dev/null || true

echo ""
echo "==> Done. Copied to ${TARGET}"
echo ""
