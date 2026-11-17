# `ubuntu2204` — Day-to-Day Operations Guide

Companion to `ubuntu-22.04-vm-setup.md` (the recipe) and
`ubuntu-22.04-vm-setup-notes.md` (what actually happened on this host).

This file is the *operating manual*: how to get into the VM, how to power it
on and off, how to check on it, and what to do when something breaks.

**VM identity**

| | |
|---|---|
| Name | `ubuntu2204` |
| ID / UUID | `2` / `631448a7-ef3d-4e8a-bacc-d6902a8f3b63` |
| OS | Ubuntu Server 22.04 LTS (cloud image, UEFI + Secure Boot) |
| Resources | 4 vCPU, 4096 MiB RAM, 40 GB disk |
| NIC | virtio on libvirt NAT network `default` → `192.168.122.0/24` |
| Guest login | `ubuntu` / `ubuntu` (password auth enabled, NOPASSWD sudo) |
| SSH key | `~/.ssh/github` (already installed in the guest) |
| Host files | `/var/lib/libvirt/images/ubuntu2204.qcow2` (disk), `seed.iso` (cloud-init) |

---

## 0. The one thing to get right first

On this host the **default libvirt URI is `qemu:///session`** (unprivileged,
per-user). The VM lives in the **system** daemon (`qemu:///system`). If you
forget the URI you get confusing errors like
`error creating bridge interface virbr0: Operation not permitted`, or
`domain not found`.

Two ways to handle it:

**Option A — set it once per shell (recommended):**

```bash
export LIBVIRT_DEFAULT_URI=qemu:///system
```

Add that line to `~/.bashrc` to make it permanent.

**Option B — pin it on every command:**

```bash
virsh -c qemu:///system list --all
```

Every example below uses Option B's explicit `-c qemu:///system` so it works
even in a fresh shell. Swap in Option A and you can drop the flag.

Check what you are pointed at:

```bash
virsh uri
# qemu:///system   <- good
# qemu:///session  <- wrong daemon for this VM
```

---

## 1. Connecting

### 1.1 SSH (the normal way)

```bash
ssh -i ~/.ssh/github ubuntu@192.168.122.149
```

Key auth is already installed in the guest — no prompt, no password.

### 1.2 SSH alias — `ssh ubuntu2204` (already configured)

The IP is DHCP-assigned and *can* change after a reboot, so an alias is
convenient. `~/.ssh/config` has been set up with:

```
Host ubuntu2204
    HostName 192.168.122.149
    User ubuntu
    IdentityFile ~/.ssh/github
    ServerAliveInterval 60
```

So the short form works out of the box:

```bash
ssh ubuntu2204
```

If the IP changes, edit the single `HostName` line — nothing else breaks:

```bash
vim ~/.ssh/config
```

To reproduce the config on a fresh host:

```bash
cat >> ~/.ssh/config <<'EOF'

Host ubuntu2204
    HostName 192.168.122.149
    User ubuntu
    IdentityFile ~/.ssh/github
    ServerAliveInterval 60
EOF

chmod 600 ~/.ssh/config
```

### 1.3 Password login (fallback)

If key auth stops working (e.g. you regenerated the guest's `authorized_keys`):

```bash
ssh ubuntu@192.168.122.149      # password: ubuntu
```

`PasswordAuthentication` is enabled in the guest via the cloud-init
`ssh_pwauth: true` setting.

### 1.4 Serial console (no network involved)

The guest has a virtio serial console. This is the escape hatch when SSH is
not up yet — e.g. cloud-init is still running on first boot, or the network
config is broken:

```bash
virsh -c qemu:///system console ubuntu2204
```

`Ctrl-]` exits back to your shell. Note: cloud-init writes its log to the
console during provisioning, so you can watch it run.

### 1.5 Graphical console (VNC)

VNC is listening on `127.0.0.1:5900` (host-local only). `virt-viewer` and
`virt-manager` are **not installed** on this host. Options:

```bash
sudo dnf install virt-viewer    # then:
virt-viewer ubuntu2204
```

Or point any VNC client at `127.0.0.1:5900`. Or install the full GUI manager:

```bash
sudo dnf install virt-manager
virt-manager --connect qemu:///system
```

> `virt-manager` / `virt-viewer` are **not** installed on this host — that is
> a deliberate deviation from the original recipe, which needs neither. They
> are purely optional conveniences; everything in this guide works with
> `virsh` alone. Installing them is also an addition to the host's package
> set, so it is called out here and in the notes file.

### 1.6 What is running inside the guest

```bash
ssh ubuntu2204 'systemctl is-active ssh nginx qemu-guest-agent; cloud-init status'
```

Expected:

```
active
active
active
status: done
```

---

## 2. Turning it on and off

All commands assume `LIBVIRT_DEFAULT_URI=qemu:///system` or the explicit
`-c qemu:///system` flag.

### 2.1 Power commands

| Action | Command | Notes |
|---|---|---|
| **Start (boot)** | `virsh -c qemu:///system start ubuntu2204` | ~15 s to SSH-ready |
| **Graceful shutdown** | `virsh -c qemu:///system shutdown ubuntu2204` | ACPI power button; waits for clean FS unmount |
| **Reboot** | `virsh -c qemu:///system reboot ubuntu2204` | guest OS restarts |
| **Hard power cut** | `virsh -c qemu:///system destroy ubuntu2204` | ⚠️ equivalent to yanking the plug |
| **Suspend (freeze RAM)** | `virsh -c qemu:///system suspend ubuntu2204` | VM state kept in host RAM |
| **Resume from suspend** | `virsh -c qemu:///system resume ubuntu2204` | |
| **Save to disk** | `virsh -c qemu:///system managedsave ubuntu2204` | stops the VM, state goes to disk and survives host reboot |
| **Restore saved state** | `virsh -c qemu:///system start ubuntu2204` | `start` auto-restores a managed save (there is no separate `managedsave-restore` command) |

**Prefer `shutdown` over `destroy`.** `destroy` kills the guest mid-write and
can corrupt the qcow2 filesystem. If `shutdown` hangs (guest already wedged),
`destroy` is the escape hatch — then repair on next boot with
`fsck` inside the guest.

### 2.2 Shutdown from inside the guest

Usually nicer, because it runs the normal systemd shutdown path:

```bash
ssh ubuntu2204 'sudo shutdown -h now'
```

The guest agent (`qemu-guest-agent`) also lets libvirt power off cleanly:

```bash
virsh -c qemu:///system shutdown ubuntu2204 --mode agent
```

### 2.3 Auto-start on host boot

Autostart is **currently enabled** for the domain, the `default` network, and
the `images` pool — so the VM comes up automatically when the host boots.

```bash
virsh -c qemu:///system autostart ubuntu2204              # enable
virsh -c qemu:///system autostart --disable ubuntu2204     # disable
virsh -c qemu:///system dominfo ubuntu2204 | grep -i autostart
```

> The **host** `virbr0` bridge and its NAT/DHCP come from the system daemon's
> `default` network, which is separate from the VM's autostart flag. If the VM
> starts but has no IP, check `virsh -c qemu:///system net-list --all` shows
> `default` **active**.

### 2.4 Typical daily loop

```bash
export LIBVIRT_DEFAULT_URI=qemu:///system

virsh list --all                                  # is it running?
virsh start ubuntu2204                            # boot it
sleep 20
virsh domifaddr ubuntu2204 --source agent         # get the IP
ssh ubuntu2204                                    # log in
# ... work ...
virsh shutdown ubuntu2204                          # power off
```

---

## 3. Finding the IP address

DHCP means the address is not fixed. Three ways to get it, best first:

```bash
# 1. From the guest agent — always accurate
virsh -c qemu:///system domifaddr ubuntu2204 --source agent

# 2. From the DHCP lease table (works even if the agent is down)
virsh -c qemu:///system domifaddr ubuntu2204 --source lease

# 3. From inside the guest
ssh ubuntu2204 'ip -4 addr show enp1s0'
```

Current address: **`192.168.122.149`** (interface `enp1s0` — Ubuntu 22.04 uses
predictable interface names, so it is *not* `eth0`).

To pin a fixed IP instead of using DHCP, add to the network's DHCP range a
static lease, or switch the interface to a static address inside the guest
via `/etc/netplan/`.

---

## 4. Status and diagnostics

```bash
# one-line state
virsh -c qemu:///system domstate ubuntu2204
# -> running | shut off | paused | in shutdown

# full domain info (state, autostart, memory, uuid, security label)
virsh -c qemu:///system dominfo ubuntu2204

# live resource usage: CPU time, memory balloon, disk cache stats
virsh -c qemu:///system domstats ubuntu2204

# the XML definition (edit this to change the hardware)
virsh -c qemu:///system dumpxml ubuntu2204

# attached disks
virsh -c qemu:///system domblklist ubuntu2204

# attached NICs
virsh -c qemu:///system domiflist ubuntu2204

# network and pool health
virsh -c qemu:///system net-list --all
virsh -c qemu:///system pool-list --all
```

On the host, check the libvirt daemons are healthy:

```bash
systemctl status virtqemud.socket virtnetworkd.service
```

> `libvirtd.service` being inactive is **normal** on Fedora's modular-daemon
> layout — `virtqemud.socket` replaces it.

From inside the guest:

```bash
ssh ubuntu2204 'uptime; free -h; df -h /; systemctl --failed'
```

---

## 5. Snapshots and the disk

The VM's disk is a **40 GB qcow2 overlay** (`ubuntu2204.qcow2`) on top of the
read-only base image `jammy-server-cloudimg-amd64.img`. Snapshots are
instantaneous and cheap.

```bash
# list snapshots
virsh -c qemu:///system snapshot-list ubuntu2204

# take one — on a RUNNING domain the default captures disk + RAM
# (verified: <memory snapshot='internal'/> and <disk name='vda' snapshot='internal'/>)
virsh -c qemu:///system snapshot-create-as ubuntu2204 before-upgrade --description "before upgrade"

# disk only, no RAM captured (smaller, faster)
virsh -c qemu:///system snapshot-create-as ubuntu2204 before-upgrade --disk-only

# revert to one (discards everything after it)
virsh -c qemu:///system snapshot-revert ubuntu2204 before-upgrade

# delete a snapshot
virsh -c qemu:///system snapshot-delete ubuntu2204 before-upgrade
```

> There is **no `--memory` flag** on this libvirt version (12.0.0). Memory is
> included by default for a running domain; use `--disk-only` to exclude it.
> `--memspec file=/path.raw` is available if you want an external RAM file.
>
> Snapshots live *inside* the qcow2 file and grow it. Prune old ones, and
> check free space with `df -h /var/lib/libvirt/images`.

To grow the disk beyond 40 GB:

```bash
# 1. grow the qcow2 (VM must be OFF; needs write access to the file -> root)
pkexec qemu-img resize /var/lib/libvirt/images/ubuntu2204.qcow2 +20G

# 2. boot the VM — cloud-init's growpart/resize_rootfs handles the partition
virsh -c qemu:///system start ubuntu2204

# 3. verify
ssh ubuntu2204 'df -h /'
```

> `qemu-img resize` needs write access to the qcow2, which is owned by
> `qemu:qemu` — so it needs `pkexec`/`sudo`. The disk is already 40 GB with
> 37 GB free inside the guest, so this is only needed if you outgrow it.

(`/var/lib/libvirt/images` is mode `0711 root:root`, so plain `ls` returns
*Permission denied* — that is expected. Use `pkexec ls -la` or
`virsh -c qemu:///system vol-list images`.)

---

## 6. Changing the hardware

RAM and vCPU can be changed live (both commands verified present on this
libvirt 12.0.0 build):

```bash
virsh -c qemu:///system setvcpu ubuntu2204 8 --config --live
virsh -c qemu:///system setmaxmem ubuntu2204 8192 --config --live
```

`--config` makes it permanent, `--live` applies it to the running VM.
`setmaxmem` on a running guest only resizes the *ceiling*; to actually give
the guest more RAM, shut it down, change the value with `--config` only, and
start it again.

To edit anything else, edit the XML:

```bash
virsh -c qemu:///system edit ubuntu2204    # opens $EDITOR
```

Then shut the VM down and start it again for the change to take effect.

---

## 7. Cloning this VM to make a second one

Two independent things must be unique: the **disk** and the **cloud-init
`instance-id`**. If `instance-id` repeats, cloud-init treats the clone as the
same machine and skips provisioning.

Steps that write into `/var/lib/libvirt/images` need root (`pkexec` /
`sudo`) — that directory is `0711 root:root`.

```bash
# 1. new overlay off the same base image (backing-file path is relative,
#    so it resolves next to the overlay itself)
cd /var/lib/libvirt/images
pkexec qemu-img create -f qcow2 -F qcow2 \
  -b jammy-server-cloudimg-amd64.img ubuntu2204-b.qcow2 40G
pkexec chown qemu:qemu ubuntu2204-b.qcow2
pkexec chmod 0644 ubuntu2204-b.qcow2
pkexec restorecon -v /var/lib/libvirt/images/

# 2. new seed with a NEW instance-id
mkdir -p ~/seed-b && cd ~/seed-b
cp /var/lib/libvirt/cloud-init/ubuntu2204/user-data .
sed 's/^instance-id: .*/instance-id: ubuntu2204-b-001/' \
    /var/lib/libvirt/cloud-init/ubuntu2204/meta-data > meta-data
sed -i 's/^local-hostname: .*/local-hostname: ubuntu2204-b/' meta-data
genisoimage -output seed-b.iso -volid cidata -joliet -rock user-data meta-data
pkexec cp seed-b.iso /var/lib/libvirt/images/seed-b.iso
pkexec chown qemu:qemu /var/lib/libvirt/images/seed-b.iso
pkexec chmod 0644 /var/lib/libvirt/images/seed-b.iso

# 3. create the VM
virt-install --connect qemu:///system \
  --name ubuntu2204-b \
  --memory 4096 --vcpus 4 \
  --disk path=/var/lib/libvirt/images/ubuntu2204-b.qcow2,format=qcow2,bus=virtio \
  --disk path=/var/lib/libvirt/images/seed-b.iso,device=disk,readonly=on \
  --import --osinfo ubuntu22.04 \
  --network network=default,model=virtio \
  --boot uefi \
  --graphics vnc,listen=127.0.0.1 \
  --console pty,target_type=virtio \
  --noautoconsole

# 4. confirm the seed label
isoinfo -d -i /var/lib/libvirt/images/seed-b.iso | grep -i "volume id"   # -> cidata
```

Also give the clone a **different MAC** if you ever want both on the same
network without confusion — libvirt normally picks a fresh one automatically.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `error: failed to connect to the hypervisor` | Wrong URI. Use `-c qemu:///system` or export `LIBVIRT_DEFAULT_URI=qemu:///system` |
| `error creating bridge interface virbr0: Operation not permitted` | You are on `qemu:///session`. Pin the system URI |
| `domain not found: ubuntu2204` | Wrong daemon again — confirm with `virsh uri` |
| `Cannot access storage file ... (as uid:107, gid:107): Permission denied` | Image ownership/label wrong. `chown qemu:qemu`, `chmod 0644`, `restorecon` the file |
| VM starts but has **no IP** | NAT network down: `systemctl start virtnetworkd.service`, then `virsh -c qemu:///system net-start default` |
| `network is already active` | It is already up — harmless, ignore |
| SSH refuses the key | Key auth broken; fall back to password `ubuntu`, then re-add the pubkey to `~/.ssh/authorized_keys` in the guest |
| SSH times out | VM still booting (wait ~15 s), or the IP changed — re-check with `domifaddr --source agent` |
| Need to see the boot | `virsh -c qemu:///system console ubuntu2204` (`Ctrl-]` to exit) |
| cloud-init did not run | Wrong `instance-id` in `meta-data`, or the seed ISO is not labelled `cidata`. Check `isoinfo -d -i seed.iso \| grep -i "volume id"` |
| cloud-init re-runs on every boot | `instance-id` did not change between seeds |
| Guest filesystem errors after `destroy` | Expected. Boot and run `sudo fsck /dev/vda1` |
| `ls /var/lib/libvirt/images` → Permission denied | Expected (0711 root:root). Use `pkexec ls` or `virsh -c qemu:///system vol-list images` |

**Reset the whole VM** (destroy and rebuild from the pristine base image —
loses all data):

```bash
virsh -c qemu:///system destroy ubuntu2204
virsh -c qemu:///system undefine ubuntu2204 --nvram --tpm
pkexec rm -f /var/lib/libvirt/images/ubuntu2204.qcow2
pkexec rm -f /var/lib/libvirt/images/seed.iso
# then re-run sections 4-5 of ubuntu-22.04-vm-setup.md
```

> `undefine` needs `--nvram` (and `--tpm`) here — this domain has a Secure Boot
> NVRAM file and a TPM state, and libvirt refuses to undefine without being
> told what to do with them. Omitting the flags gives
> `Cannot delete domain with nvram file`.

---

## 9. File reference

```
# disks and seed (host)
/var/lib/libvirt/images/jammy-server-cloudimg-amd64.img   # base cloud image (read-only)
/var/lib/libvirt/images/ubuntu2204.qcow2                  # 40G overlay — the VM's disk
/var/lib/libvirt/images/seed.iso                          # cloud-init seed (label: cidata)

# cloud-init source copies
/var/lib/libvirt/cloud-init/ubuntu2204/user-data
/var/lib/libvirt/cloud-init/ubuntu2204/meta-data

# UEFI NVRAM (Secure Boot variables)
/var/lib/libvirt/qemu/nvram/ubuntu2204_VARS.qcow2

# stock NAT network definition used for `default`
/usr/share/libvirt/networks/default.xml
```

---

## 10. Copy-paste cheat sheet

```bash
export LIBVIRT_DEFAULT_URI=qemu:///system

# --- connect ---
ssh -i ~/.ssh/github ubuntu@192.168.122.149     # or: ssh ubuntu2204
virsh console ubuntu2204                        # serial console

# --- power ---
virsh start ubuntu2204                           # boot
virsh shutdown ubuntu2204                        # graceful off
virsh reboot ubuntu2204                          # restart
virsh destroy ubuntu2204                         # hard cut (last resort)
virsh suspend ubuntu2204                         # freeze
virsh resume ubuntu2204                          # thaw
virsh autostart --disable ubuntu2204             # don't auto-boot on host start
virsh autostart ubuntu2204                       # auto-boot on host start

# --- inspect ---
virsh list --all
virsh domstate ubuntu2204
virsh dominfo ubuntu2204
virsh domifaddr ubuntu2204 --source agent
virsh domblklist ubuntu2204
virsh domiflist ubuntu2204
virsh domstats ubuntu2204
virsh net-list --all
virsh pool-list --all

# --- snapshots ---
virsh snapshot-list ubuntu2204
virsh snapshot-create-as ubuntu2204 start
virsh snapshot-revert ubuntu2204 start
virsh snapshot-delete ubuntu2204 start

# --- guest ---
ssh ubuntu2204 'cloud-init status; systemctl is-active ssh nginx qemu-guest-agent'
ssh ubuntu2204 'df -h /; free -h; uptime'
```
