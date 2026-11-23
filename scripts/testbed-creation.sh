#!/usr/bin/bash
#
# Runs ON THE HOST (outside the VM). A minimal runtime export of this checkout
# is staged in SRC_DIR first, copied into the guest over ssh, then
# `make setup` / `make doctor` run inside the guest.
#
# The `ubuntu2204` alias comes from ~/.ssh/config (ubuntu@192.168.122.149,
# key ~/.ssh/github).

set -euo pipefail

VM=${VM:-ubuntu2204}
DOMAIN=${DOMAIN:-ubuntu2204}
SRC_DIR=${SRC_DIR:-$HOME/Documents/srv6-lab}
REMOTE_DIR=${REMOTE_DIR:-srv6-lab}
VM_PATH="~/${REMOTE_DIR}"

# The domain lives in the system daemon on this host (see
# ubuntu2204-vm-operations.md); the default URI is qemu:///session.
export LIBVIRT_DEFAULT_URI=${LIBVIRT_DEFAULT_URI:-qemu:///system}

# Stage the minimal runtime set (all topologies and policy documents, no dev
# tooling) in a destination directory.  Preserves whatever the destination
# already holds.
export_minimal_lab() {
    local dest=$1
    local src_root
    local f
    src_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    dest=$(mkdir -p "$dest" && cd "$dest" && pwd)

    echo "==> Exporting minimal SRv6 lab from ${src_root}"
    echo "    to ${dest}"

    mkdir -p "${dest}/src/templates" "${dest}/topologies" "${dest}/policies" "${dest}/vendor" "${dest}/scripts"

    echo "    copying files..."

    # Core runtime
    cp "${src_root}/src/net_topology_yaml.py" "${dest}/src/"
    mkdir -p "${dest}/src/srctl"
    cp "${src_root}"/src/srctl/*.py "${dest}/src/srctl/"
    cp "${src_root}/src/templates/ospf6d.mako" "${dest}/src/templates/"
    cp "${src_root}"/topologies/*.yaml "${dest}/topologies/"
    cp "${src_root}"/policies/*.yaml "${dest}/policies/"

    # Vendor wheel
    if [[ -d "${src_root}/vendor" ]]; then
        shopt -s nullglob
        for f in "${src_root}/vendor/"*; do
            cp "$f" "${dest}/vendor/"
        done
        shopt -u nullglob
    fi

    # Scripts (setup + doctor + controller CLI + controller smoke driver)
    cp "${src_root}/scripts/setup.sh" "${dest}/scripts/"
    cp "${src_root}/scripts/doctor.sh" "${dest}/scripts/"
    cp "${src_root}/scripts/srctl.sh" "${dest}/scripts/"
    cp "${src_root}/scripts/controller-smoke.txt" "${dest}/scripts/"

    # Makefile + requirements
    cp "${src_root}/Makefile" "${dest}/"
    cp "${src_root}/requirements.txt" "${dest}/"

    # Ensure the lab scripts are executable
    chmod +x "${dest}/scripts/setup.sh" "${dest}/scripts/doctor.sh" \
        "${dest}/scripts/srctl.sh" 2>/dev/null || true

    echo ""
    echo "==> Done. Copied to ${dest}"
    echo ""
}

wait_for_ssh() {
    local tries=${1:-60} i
    for ((i = 1; i <= tries; i++)); do
        if ssh -o BatchMode=yes -o ConnectTimeout=3 \
            -o StrictHostKeyChecking=accept-new "$VM" true 2>/dev/null; then
            return 0
        fi
        sleep 2
    done
    printf 'ERROR: SSH to %s did not come up after %d attempts\n' "$VM" "$tries" >&2
    return 1
}

export_minimal_lab "$SRC_DIR"

virsh snapshot-revert "$DOMAIN" start
# `start` fails if the reverted snapshot was already running.
if ! virsh domstate "$DOMAIN" | grep -q running; then
    virsh start "$DOMAIN"
fi

wait_for_ssh

# Copy the folder into the VM's home directory, skipping the host-only venv
# and Python caches. scp -r has no exclude support, so stream a tar instead.
# The venv is rebuilt by `make setup` inside the guest (and it is for a
# different Python version anyway).
ssh "$VM" "rm -rf ${VM_PATH} && mkdir -p ${VM_PATH}"
tar -C "$SRC_DIR" \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.py[cod]' \
    --exclude='.ruff_cache' \
    -czf - . | ssh "$VM" "tar -xzf - -C ${VM_PATH}"

# Everything below this line is executed INSIDE the virtual machine.
ssh "$VM" bash -s -- "$REMOTE_DIR" <<'EOF'
set -euo pipefail

cd ~/"$1"

# `make` is only needed to bootstrap; `make setup` installs the rest.
sudo env DEBIAN_FRONTEND=noninteractive apt-get update -q
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -q make

make setup
make doctor
EOF
