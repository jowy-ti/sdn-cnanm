#!/usr/bin/env bash
#
# Create/recreate the ubuntu2204 VM from scratch using the existing cloud image and seed.
#
# Usage:
#   ./scripts/create-ubuntu2204-vm.sh           # create/recreate the VM
#   ./scripts/create-ubuntu2204-vm.sh --help    # show help
#
# This script will:
#   1. Stop and undefine the VM if it exists
#   2. Remove old disk and seed files
#   3. Create a new overlay disk from the base image
#   4. Regenerate the cloud-init seed ISO
#   5. Create and start the VM with virt-install
#   6. Wait for it to boot and show its IP address
#
# Prerequisites (already configured on this host):
#   - /var/lib/libvirt/images/jammy-server-cloudimg-amd64.img (base image)
#   - /var/lib/libvirt/cloud-init/ubuntu2204/user-data and meta-data
#   - libvirt default network configured
#   - SSH key and ~/.ssh/config for ubuntu2204
#
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export LIBVIRT_DEFAULT_URI=${LIBVIRT_DEFAULT_URI:-qemu:///system}

# Configuration
VM_NAME="ubuntu2204"
VM_MEMORY="4096"
VM_VCPUS="4"
VM_DISK_SIZE="40G"
BASE_IMAGE="/var/lib/libvirt/images/jammy-server-cloudimg-amd64.img"
VM_DISK="/var/lib/libvirt/images/${VM_NAME}.qcow2"
SEED_ISO="/var/lib/libvirt/images/seed.iso"
CLOUD_INIT_DIR="/var/lib/libvirt/cloud-init/${VM_NAME}"
OS_VARIANT="ubuntu22.04"
NETWORK="default"

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Create or recreate the ${VM_NAME} VM from scratch.

Options:
  -h, --help     Show this help message
  --force        Skip confirmation prompt

Configuration (can be overridden via environment variables):
  LIBVIRT_DEFAULT_URI   Default: qemu:///system
  VM_MEMORY             Default: 4096
  VM_VCPUS              Default: 4
  VM_DISK_SIZE          Default: 40G
EOF
}

FORCE=false
while (($# > 0)); do
    case $1 in
        --force) FORCE=true ;;
        -h | --help) usage; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
    shift
done

step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    warning: %s\n' "$*"; }
die() {
    printf '\nERROR: %s\n' "$*" >&2
    exit 1
}

# Check prerequisites
if [[ ! -f ${BASE_IMAGE} ]]; then
    die "Base image not found: ${BASE_IMAGE}"
fi

if [[ ! -f ${CLOUD_INIT_DIR}/user-data ]] || [[ ! -f ${CLOUD_INIT_DIR}/meta-data ]]; then
    die "Cloud-init files not found in ${CLOUD_INIT_DIR}"
fi

command -v virsh >/dev/null 2>&1 || die "virsh not found"
command -v virt-install >/dev/null 2>&1 || die "virt-install not found"
command -v qemu-img >/dev/null 2>&1 || die "qemu-img not found"
command -v genisoimage >/dev/null 2>&1 || die "genisoimage not found"

step "Checking for existing VM"
if virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
    info "VM ${VM_NAME} exists"
    if ! $FORCE; then
        echo
        read -r -p "This will destroy and recreate ${VM_NAME}. Continue? [y/N] " response
        case "$response" in
            [yY][eE][sS]|[yY]) ;;
            *) echo "Aborted."; exit 0 ;;
        esac
    fi
    
    step "Stopping and removing existing VM"
    virsh shutdown "${VM_NAME}" 2>/dev/null || true
    sleep 10
    if virsh domstate "${VM_NAME}" 2>/dev/null | grep -q "running"; then
        virsh destroy "${VM_NAME}" 2>/dev/null || true
        sleep 5
    fi
    virsh undefine "${VM_NAME}" --nvram --tpm 2>/dev/null || true
    info "VM undefined"
else
    info "No existing VM found"
fi

step "Removing old disk and seed files"
if [[ -f ${VM_DISK} ]]; then
    sudo rm -f "${VM_DISK}"
    info "Removed ${VM_DISK}"
fi
if [[ -f ${SEED_ISO} ]]; then
    sudo rm -f "${SEED_ISO}"
    info "Removed ${SEED_ISO}"
fi

step "Creating overlay disk from base image"
sudo qemu-img create -f qcow2 -F qcow2 -b "${BASE_IMAGE}" "${VM_DISK}" "${VM_DISK_SIZE}"
sudo chown qemu:qemu "${VM_DISK}"
sudo chmod 0644 "${VM_DISK}"
info "Created ${VM_DISK} (${VM_DISK_SIZE})"

step "Generating cloud-init seed ISO"
cd "${CLOUD_INIT_DIR}"
sudo genisoimage -output "${SEED_ISO}" -volid cidata -joliet -rock user-data meta-data >/dev/null 2>&1
sudo chown qemu:qemu "${SEED_ISO}"
sudo chmod 0644 "${SEED_ISO}"
info "Created ${SEED_ISO}"

step "Creating and starting VM"
virt-install \
    --connect "${LIBVIRT_DEFAULT_URI}" \
    --name "${VM_NAME}" \
    --memory "${VM_MEMORY}" \
    --vcpus "${VM_VCPUS}" \
    --disk "path=${VM_DISK},format=qcow2,bus=virtio" \
    --disk "path=${SEED_ISO},device=disk,readonly=on" \
    --import \
    --osinfo "${OS_VARIANT}" \
    --network "network=${NETWORK},model=virtio" \
    --boot uefi \
    --graphics vnc,listen=127.0.0.1 \
    --console pty,target_type=virtio \
    --noautoconsole

info "VM created and started"

step "Waiting for VM to boot"
for i in {1..60}; do
    if virsh domifaddr "${VM_NAME}" --source agent >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

step "VM Information"
virsh dominfo "${VM_NAME}" | grep -E "Name|State|CPU|Memory|UUID"
echo
virsh domifaddr "${VM_NAME}" --source agent 2>&1 | tail -10
echo
info "SSH: ssh ${VM_NAME} or ssh ubuntu@<ip>"
info "Console: virsh console ${VM_NAME} (Ctrl+] to exit)"
info "User: ubuntu (check ~/.ssh/config for key)"
