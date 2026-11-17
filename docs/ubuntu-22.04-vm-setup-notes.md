# Ubuntu 22.04 VM — Actual Run Notes & Deviations from `ubuntu-22.04-vm-setup.md`

Date: 2026-10-21
Host: Fedora 44 (workstation), x86_64, 16 cores, 13 GiB RAM, ~458 GiB free
Result: **VM `ubuntu2204` created, running, and verified.**

This file records what was *actually* done, and every point where reality differed
from `ubuntu-22.04-vm-setup.md`. The original document is still valid as the
"intended" recipe; read this alongside it.

---

## 1. TL;DR — what changed vs. the original doc

| # | Original doc said | Reality on this host | Action taken |
|---|---|---|---|
| 1 | `sudo` works for all host steps | `sudo` requires a password; user was **not** in `libvirt` group | Used `pkexec` (authorized by the running polkit agent); added user to `libvirt` group |
| 2 | NAT `default` network already exists on `virbr0` 192.168.122.0/24 | **Did not exist** — `virsh net-list --all` was empty, no `virbr0` | Defined from `/usr/share/libvirt/networks/default.xml`, started, autostart enabled |
| 3 | Pool named `default` → `/var/lib/libvirt/images` | System pool is named **`images`** and pointed at `~/.local/share/libvirt/images` | Redefined pool `images` → `/var/lib/libvirt/images` |
| 4 | Bare `virsh` / `virt-install` | Default URI on this host is **`qemu:///session`**, not `qemu:///system` | Always used explicit `-c qemu:///system` / `--connect qemu:///system` |
| 5 | Image paths under `/var/lib/libvirt/images` | Not writable without root; and `qemu` (uid 107) couldn't traverse `$HOME` (mode 0700) | Moved images to `/var/lib/libvirt/images`, `chown qemu:qemu`, `chmod 0644`, `restorecon` |
| 6 | OVMF lives in `/usr/share/qemu/firmware/` | That dir has **only JSON descriptors**; real firmware is in `/usr/share/edk2/ovmf/` | No action — libvirt auto-selected the right firmware from the descriptors |
| 7 | (not mentioned) | Domain autostart was `disable` | Enabled (`virsh autostart ubuntu2204`) |
| 8 | (not mentioned) | cloud-init source files would live in an ephemeral temp dir | Preserved at `/var/lib/libvirt/cloud-init/ubuntu2204/` |
| 9 | (not mentioned) | SSH needed the full IP every time (DHCP-assigned) | Created `~/.ssh/config` with a `Host ubuntu2204` alias → `ssh ubuntu2204` |
| 10 | (not mentioned) | No GUI console tooling | `virt-manager` / `virt-viewer` deliberately **not** installed — `virsh` + `ssh` is sufficient |

---

## 2. Two traps that will bite again

### 2.1 `qemu:///session` is the default URI here

```
$ virsh uri
qemu:///session
```

The session daemon runs **unprivileged** as `joelgonzalez`. Anything that needs
`CAP_NET_ADMIN` or `CAP_SYS_ADMIN` fails there with confusing errors:

```
error creating bridge interface virbr0: Operation not permitted
```

The original doc's troubleshooting section already warns
*"Bridge/NAT issues → use `qemu:///system`, not `qemu:///session`"* — that warning
is real and load-bearing. **Every** command below pins the connection explicitly.

### 2.2 The `default` network did not exist

The doc states the NAT network is already provided by
`libvirt-daemon-config-network`. On this host it was **absent** (no `virbr0`,
empty network list), despite the package being present. It had to be created.

### 2.3 The storage pool pointed into `$HOME`

The system pool (`images`) targeted `/home/joelgonzalez/.local/share/libvirt/images`
with mode `0711`. Even after `virt-install` created the overlay there, the
hypervisor could not open it:

```
ERROR  Cannot access storage file '.../ubuntu2204.qcow2' (as uid:107, gid:107): Permission denied
```

`/home/joelgonzalez` is mode `0700`, so the `qemu` user cannot traverse it.
Fixing this by loosening home-dir permissions (`chmod o+x ~`) would work but
weakens the host's home directory. Instead the pool was moved to the canonical
`/var/lib/libvirt/images` (SELinux `virt_image_t`, `0711 root:root`), which is
what the original doc intended anyway.

---

## 3. Actual command sequence (as executed)

### 3.1 Privileged host prep (via `pkexec`)

`sudo` needs a password on this host and the session has a working polkit agent,
so `pkexec` was used instead. It was verified up-front with `pkexec /usr/bin/id`
→ `uid=0(root)`.

```bash
# group membership (takes effect at next login)
usermod -aG libvirt joelgonzalez

# relocate the storage pool to the canonical libvirt path
virsh -c qemu:///system pool-destroy images
virsh -c qemu:///system pool-undefine images
virsh -c qemu:///system pool-define-as images dir --target /var/lib/libvirt/images
virsh -c qemu:///system pool-start images
virsh -c qemu:///system pool-autostart images
```

### 3.2 NAT network

```bash
# the stock libvirt NAT definition: virbr0, 192.168.122.1/24, DHCP .2-.254
virsh -c qemu:///system net-define /usr/share/libvirt/networks/default.xml
virsh -c qemu:///system net-start default
virsh -c qemu:///system net-autostart default
```

Note: the first `net-start` attempt timed out because the **system**
`virtnetworkd.service` was `inactive (dead)` and had to be socket-activated:

```bash
systemctl start virtnetworkd.service
```

After that the network came up cleanly (`virbr0` = 192.168.122.1/24,
`net.ipv4.ip_forward` auto-flipped `0 → 1`, dnsmasq serving DHCP
192.168.122.2–254).

### 3.3 Cloud image

```bash
cd /var/lib/libvirt/images
curl -O https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img
curl -s https://cloud-images.ubuntu.com/jammy/current/SHA256SUMS \
  | grep "jammy-server-cloudimg-amd64.img$" | sha256sum -c -
# -> jammy-server-cloudimg-amd64.img: OK
```

(Images were first downloaded into `~/.local/share/libvirt/images` while testing
writability, then moved into `/var/lib/libvirt/images` together, so the overlay's
relative backing-file reference stayed valid.)

### 3.4 Overlay disk + seed ISO

```bash
qemu-img create -f qcow2 -F qcow2 \
  -b jammy-server-cloudimg-amd64.img ubuntu2204.qcow2 40G

chown qemu:qemu *.img *.qcow2 seed.iso
chmod 0644 *.img *.qcow2 seed.iso
restorecon -v /var/lib/libvirt/images/

genisoimage -output seed.iso -volid cidata -joliet -rock user-data meta-data
mv seed.iso /var/lib/libvirt/images/

# verify the label really is "cidata"
isoinfo -d -i /var/lib/libvirt/images/seed.iso | grep -i "volume id"
# -> Volume id: cidata
```

`user-data` / `meta-data` were used **verbatim** from the original doc
(`instance-id: ubuntu2204-001`). Copies kept at
`/var/lib/libvirt/cloud-init/ubuntu2204/`.

### 3.5 VM creation

```bash
virt-install \
  --connect qemu:///system \
  --name ubuntu2204 \
  --memory 4096 --vcpus 4 \
  --disk path=/var/lib/libvirt/images/ubuntu2204.qcow2,format=qcow2,bus=virtio \
  --disk path=/var/lib/libvirt/images/seed.iso,device=disk,readonly=on \
  --import --osinfo ubuntu22.04 \
  --network network=default,model=virtio \
  --boot uefi \
  --graphics vnc,listen=127.0.0.1 \
  --console pty,target_type=virtio \
  --noautoconsole
```

Only the path prefixes changed vs. the doc; every flag is as originally written.

### 3.6 Post-creation

```bash
virsh -c qemu:///system autostart ubuntu2204
```

---

## 4. Verification results — all passing

| Check | Result |
|---|---|
| `virsh -c qemu:///system list --all` | `ubuntu2204` — **running** |
| `virsh -c qemu:///system domifaddr ubuntu2204` | `192.168.122.149` (DHCP from `virbr0`) |
| `domifaddr --source agent` | returns guest-reported addresses → `qemu-guest-agent` working |
| SSH key auth | `ssh -i ~/.ssh/github ubuntu@192.168.122.149` → OK (user `ubuntu`) |
| `/etc/motd` | `VM ready` → cloud-init `runcmd` executed |
| `cloud-init status --long` | `status: done`, `detail: DataSourceNoCloud [seed=/dev/vdb]`, `errors: []` |
| Disk grow | `vda` 40G → `vda1` 39.9G mounted on `/` (was 2.2G) |
| Packages | `nginx` 1.18.0 + `qemu-guest-agent` 6.2 installed, both active |
| nginx | `systemctl is-active nginx` → active; `curl localhost` → **HTTP 200** |
| Host key | seeded `~/.ssh/github.pub` → `joel.gonzalez.jimenez` |
| Reboot test | `sudo shutdown -r now` → VM came back up, SSH reachable, `cloud-init status: done` (**did not** re-run) |
| UEFI | `<loader>/usr/share/edk2/ovmf/OVMF_CODE_4M.secboot.qcow2`, NVRAM `/var/lib/libvirt/qemu/nvram/ubuntu2204_VARS.qcow2` |
| VNC | `127.0.0.1:0` (port 5900) |

### Guest layout

```
NAME    MAJ:MIN RM  SIZE RO TYPE MOUNTPOINTS
vda     252:0    0   40G  0 disk
├─vda1  252:1    0 39.9G  0 part /
├─vda14 252:14   0    4M  0 part
└─vda15 252:15   0  106M  0 part /boot/efi
vdb     252:16   0  366K  1 disk      <- seed.iso (cidata)
```

---

## 5. Notes, caveats and leftovers

1. **`seed.iso` was NOT deleted by virt-install.** It was attached explicitly as a
   persistent disk (`vdb`), and cloud-init picked it up as
   `DataSourceNoCloud [seed=/dev/vdb]`. The doc's "virt-install deleted its
   generated seed ISO" workaround was unnecessary here because we never asked
   virt-install to generate one.

2. **Secure Boot is ON.** libvirt auto-selected the `secboot` OVMF variant
   (`OVMF_CODE_4M.secboot.qcow2`) plus the `OVMF_VARS_4M.secboot.qcow2`
   NVRAM template. Ubuntu's cloud image boots fine under it. If a custom kernel
   or unsigned EFI binary is ever needed, switch the loader to the `nosb`
   variant.

3. **Machine type** was auto-selected as `pc-q35-10.2` (libvirt 12 / QEMU 10.2.2).

4. **The `libvirt` group membership is in effect but only for new logins.**
   The current session still relies on polkit for `qemu:///system` operations.
   Log out and back in to get direct group access. The `pkexec`/`polkit` path
   will keep working regardless.

5. **Session-mode pools were left untouched.** The *session* daemon still has
   `boot`, `default`, `opencode`, `vmt` pools. The session `default` pool still
   points at `~/.local/share/libvirt/images`, which is now **empty** (the
   images moved to `/var/lib/libvirt/images`). Harmless, but do not confuse the
   two pools — the one the VM uses is the **system** pool `images`.

6. **`libvirtd.service` is inactive; `virtqemud.socket` is active.** This is
   Fedora's modular-daemon layout, not a fault. Use `qemu:///system`.

7. **No `sudo` password was used or requested.** All privilege escalation went
   through `pkexec` → polkit → the GNOME polkit agent already running in the
   desktop session.

8. **`/var/lib/libvirt/images` is not listable by your user** (`0711 root:root`,
   the standard libvirt layout). `ls` there returns `Permission denied` — use
   `pkexec ls -la /var/lib/libvirt/images/` or `virsh -c qemu:///system
   vol-list images`. This is expected, not a fault.

9. **The guest's primary NIC appears as `enp1s0`**, not `eth0` — Ubuntu 22.04
   cloud images use predictable interface naming derived from the PCI slot.

10. **To clone this VM later**, change `instance-id` in `meta-data` (see the
   original doc's troubleshooting note), then rebuild the seed ISO with a
   **different filename** and a **new** `instance-id**.

11. **`~/.ssh/config` was created** with a `Host ubuntu2204` alias pointing at
    `192.168.122.149` (user `ubuntu`, identity `~/.ssh/github`,
    `ServerAliveInterval 60`). This is an addition not present in the original
    recipe — it exists so `ssh ubuntu2204` works without re-typing the
    DHCP-assigned address. Recreate it with:

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

12. **`virt-manager` and `virt-viewer` were deliberately NOT installed.**
    The original recipe does not need them and the VM is fully operable with
    `virsh` + `ssh`. They are optional and can be added later with
    `sudo dnf install virt-manager virt-viewer` if a GUI console is wanted.

13. **A new operations guide was written**: `ubuntu2204-vm-operations.md` —
    the day-to-day manual for connecting, powering on/off, inspecting,
    snapshotting and cloning this VM, plus a troubleshooting table and a
    copy-paste cheat sheet.

---

## 6. Quick reference

```bash
# always pin the system connection
export LIBVIRT_DEFAULT_URI=qemu:///system   # optional, saves typing

virsh list --all
virsh domifaddr ubuntu2204
virsh domifaddr ubuntu2204 --source agent
virsh console ubuntu2204                    # if SSH is not up yet

ssh -i ~/.ssh/github ubuntu@192.168.122.149
ssh ubuntu2204                                   # alias from ~/.ssh/config

# host files
/var/lib/libvirt/images/jammy-server-cloudimg-amd64.img   # base image (read-only backing)
/var/lib/libvirt/images/ubuntu2204.qcow2                  # 40G overlay (the VM's disk)
/var/lib/libvirt/images/seed.iso                          # cloud-init seed (cidata)
/var/lib/libvirt/cloud-init/ubuntu2204/user-data          # cloud-init source (copy)
/var/lib/libvirt/cloud-init/ubuntu2204/meta-data          # cloud-init source (copy)
/var/lib/libvirt/qemu/nvram/ubuntu2204_VARS.qcow2         # UEFI NVRAM
```

---

## 7. Related documents

| File | Purpose |
|---|---|
| `ubuntu-22.04-vm-setup.md` | The original recipe (unmodified) |
| `ubuntu-22.04-vm-setup-notes.md` | This file — what was actually done and where it deviated |
| `ubuntu2204-vm-operations.md` | Day-to-day operations: connect, power on/off, inspect, snapshot, clone, troubleshoot |
