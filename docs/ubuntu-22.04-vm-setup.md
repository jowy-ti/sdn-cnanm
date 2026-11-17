# Ubuntu Server 22.04 LTS VM on QEMU/KVM (virt-install)

Host: Fedora 44, x86_64, 16 cores, 13 GiB RAM, ~458 GiB free.
Fully unattended: no installer wizard, cloud-init provisions everything on first boot.

## 1. Software to install (host)

```bash
sudo dnf install virt-install            # REQUIRED - currently installed
```

Already present (no action): `qemu-kvm`/`qemu-system-x86`/`qemu-img` (10.2.2),
`libvirt-daemon`+`libvirt-client` (12.0.0), `libvirt-daemon-driver-qemu`,
`libvirt-daemon-config-network` (NAT `default` on `virbr0` 192.168.122.0/24),
`edk2-ovmf`, `genisoimage`, `dnsmasq`, `osinfo-db`.

## 2. One-time host prep

```bash
sudo usermod -aG libvirt $USER   # then log out / reboot
sudo virsh pool-define-as default dir --target /var/lib/libvirt/images
sudo virsh pool-start default
sudo virsh pool-autostart default
```

(`/dev/kvm` is already mode 666; `virtqemud.socket` is enabled.)

## 3. Fetch the cloud image

```bash
cd /var/lib/libvirt/images
sudo curl -O https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img
curl -s https://cloud-images.ubuntu.com/jammy/current/SHA256SUMS | grep amd64.img | sudo sha256sum -c -
```

## 4. Create overlay disk + cloud-init seed

```bash
sudo qemu-img create -f qcow2 -F qcow2 -b jammy-server-cloudimg-amd64.img ubuntu2204.qcow2 40G
```

`user-data` (change `instance-id` for every new VM):

```yaml
#cloud-config
users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
    plain_text_passwd: ubuntu
    ssh_authorized_keys:
      - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH8ghZ1An7fvZrgIKu/cUHygieZS9L5QTb45167Xnmxv joel.gonzalez.jimenez
ssh_pwauth: true
package_update: true
package_upgrade: false
packages: [qemu-guest-agent, nginx]
growpart:
  mode: auto
  devices: ['/']
resize_rootfs: true
runcmd:
  - [ sh, -c, "echo 'VM ready' > /etc/motd" ]
```

`meta-data`:

```yaml
instance-id: ubuntu2204-001
local-hostname: ubuntu2204
```

Build the seed ISO (label MUST be `cidata`):

```bash
genisoimage -output seed.iso -volid cidata -joliet -rock user-data meta-data
sudo mv seed.iso /var/lib/libvirt/images/
```

## 5. Create the VM

```bash
sudo virt-install \
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

## 6. Verify

```bash
virsh list --all
virsh domifaddr ubuntu2204            # get IP
ssh ubuntu@<ip>                        # key auth via ~/.ssh/github
```

Disk should auto-grow from 2.2G to 40G; cloud-init log: `/var/log/cloud-init-output.log`.

## Troubleshooting

- **No login** → `sudo virsh console ubuntu2204`; cloud-init logs in the guest.
- **cloud-init re-runs / skips** → change `instance-id` in `meta-data`.
- **Seed not found** → verify label: `isoinfo -d -i seed.iso | grep cidata`.
- **Bridge/NAT issues** → use `qemu:///system`, not `qemu:///session`.
- **virt-install deleted its generated seed ISO** → that's why we attach a persistent `seed.iso`.
- **UEFI** → `--boot uefi` maps to OVMF in `/usr/share/qemu/firmware/`.
