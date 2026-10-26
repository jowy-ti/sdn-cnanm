# VM Guide (ubuntu2204)

This repo can create and manage the Ubuntu 22.04 VM used for running the lab in a clean environment.

## Prerequisites (host)
- libvirt/qemu installed and configured (system URI `qemu:///system`)
- Base cloud image: `/var/lib/libvirt/images/jammy-server-cloudimg-amd64.img`
- Cloud-init templates in `vm/cloud-init/` (or in `/var/lib/libvirt/cloud-init/ubuntu2204/`)
- SSH key at `~/.ssh/github` and SSH config entry for `ubuntu2204`

## Create/Recreate the VM
Create a clean VM with no repo installed:

```bash
./scripts/create-ubuntu2204-vm.sh          # interactive (prompts before destroying)
./scripts/create-ubuntu2204-vm.sh --force  # non-interactive
```

The script will:
1. Stop/undefine the VM if it exists
2. Remove old disk (`ubuntu2204.qcow2`) and seed ISO
3. Create a new overlay disk from the base image (40GB)
4. Regenerate the cloud-init seed ISO from `vm/cloud-init/` (or host paths)
5. Create and boot the VM with `virt-install` (UEFI, virtio)
6. Wait for the guest agent and show the IP

## SSH Access
After boot, connect with:

```bash
ssh ubuntu2204
# or
ssh -i ~/.ssh/github ubuntu@<ip>
```

The SSH config is already set up on the host. If the IP changes after recreation, update `~/.ssh/config` or use the IP from `domifaddr`.

## Virsh Quick Reference
All commands use the system URI:

```bash
export LIBVIRT_DEFAULT_URI=qemu:///system  # optional
```

### Power / state
```bash
virsh list --all          # list VMs
virsh start ubuntu2204    # boot
virsh shutdown ubuntu2204 # graceful shutdown
virsh reboot ubuntu2204   # reboot
virsh destroy ubuntu2204  # hard power off (last resort)
virsh domstate ubuntu2204 # state
```

### Connect / inspect
```bash
ssh ubuntu2204
virsh console ubuntu2204                # serial console (Ctrl+] to exit)
virsh domifaddr ubuntu2204 --source agent  # get guest IP
virsh dominfo ubuntu2204
virsh domblklist ubuntu2204
```

### Snapshots
```bash
virsh snapshot-list ubuntu2204
virsh snapshot-create-as ubuntu2204 <name>
virsh snapshot-revert ubuntu2204 <name>
virsh snapshot-delete ubuntu2204 <name>
```

### Autostart / network
```bash
virsh autostart ubuntu2204
virsh autostart --disable ubuntu2204
virsh net-list --all
```

## Tips
- Default URI on this host is `qemu:///session` - always use `qemu:///system` for `ubuntu2204`.
- If SSH times out, wait a bit for cloud-init to finish provisioning.
- Prefer `shutdown` over `destroy`. Use `destroy` only if unresponsive.
- Current known IP: `192.168.122.220` (user: `ubuntu`, key: `~/.ssh/github`)
- Cloud-init templates are stored in `vm/cloud-init/` in this repo for reproducibility.
