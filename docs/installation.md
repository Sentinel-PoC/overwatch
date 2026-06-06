# Installation

This document covers the OKD 4.19 UPI (User Provisioned Infrastructure)
installation process. The cluster is rarely modified post-install -- this
page exists primarily for disaster recovery and full cluster rebuild scenarios.

## Prerequisites

The following must be in place before installation or rebuild:

- **iac-control** (192.168.12.210) running HAProxy, dnsmasq, keepalived, nginx, Squid
- **Proxmox API** accessible from iac-control (pve at 192.168.12.6, 208-pve2 at 192.168.12.56)
- **SCOS artifacts** in `~/overwatch-repo/scos-artifacts/` (Fedora CoreOS / CentOS Stream CoreOS ISO + kernel + initramfs)
- **openshift-install** binary at `/usr/local/bin/openshift-install`
- **Vault** operational with root token available (for Proxmox API credentials)
- **MinIO** at 192.168.12.58 accessible (Terraform state backend)

## install-config.yaml

The cluster configuration is defined in `install-config.yaml` at the repo root:

```yaml
apiVersion: v1
baseDomain: haist.farm
metadata:
  name: overwatch
compute:
- name: worker
  replicas: 0
controlPlane:
  name: master
  replicas: 3
networking:
  clusterNetwork:
  - cidr: 10.128.0.0/14
    hostPrefix: 23
  networkType: OVNKubernetes
  serviceNetwork:
  - 172.30.0.0/16
platform:
  none: {}
```

Key decisions:

- **`platform: none`** -- UPI deployment, no cloud provider integration
- **`worker replicas: 0`** -- compact cluster, masters schedule all workloads
- **OVN-Kubernetes CNI** -- default for OKD 4.19, provides network policy and multicast support
- **Cluster network 10.128.0.0/14** -- pod CIDR (up to 4 /23 subnets per node = 510 pods each)
- **Service network 172.30.0.0/16** -- ClusterIP service range

## Ignition Generation

The `generate_ignition.sh` script creates manifests and ignition configs:

```bash
#!/bin/bash
set -e
rm -rf overwatch-gen
mkdir overwatch-gen
cp install-config.yaml overwatch-gen/

# Create Manifests (consumes install-config.yaml)
/usr/local/bin/openshift-install create manifests --dir=overwatch-gen

# Inject MachineConfig for Masters (QEMU guest agent)
cat <<YAML > overwatch-gen/openshift/99-master-qemu-guest-agent.yaml
apiVersion: machineconfiguration.openshift.io/v1
kind: MachineConfig
metadata:
  labels:
    machineconfiguration.openshift.io/role: master
  name: 99-master-qemu-guest-agent
spec:
  config:
    ignition:
      version: 3.2.0
      systemd:
        units:
          - name: install-qemu-guest-agent.service
            enabled: true
            contents: |
              [Unit]
              Description=Install QEMU Guest Agent
              ...
YAML

# Create Ignition Configs (consumes manifests)
/usr/local/bin/openshift-install create ignition-configs --dir=overwatch-gen
```

This produces three ignition files in `overwatch-gen/`:

| File | Size | Purpose |
|------|------|---------|
| `bootstrap.ign` | ~271 KB | Bootstrap node (temporary control plane) |
| `master.ign` | ~1.7 KB | Master nodes (pointer to MCS at api-int:22623) |
| `worker.ign` | ~1.7 KB | Worker nodes (not used -- 0 replicas) |

The ignition files also produce `auth/kubeconfig` and `auth/kubeadmin-password`
in the output directory.

**Important:** `openshift-install create manifests` consumes and deletes
`install-config.yaml`. Always copy it into a working directory first (as the
script does).

## MachineConfigs

Additional MachineConfigs are applied post-install via `oc apply`:

### 99-master-ssh

Injects the SSH authorized key for the `core` user on all masters:

```yaml
apiVersion: machineconfiguration.openshift.io/v1
kind: MachineConfig
metadata:
  labels:
    machineconfiguration.openshift.io/role: master
  name: 99-master-ssh
spec:
  config:
    ignition:
      version: 3.2.0
    passwd:
      users:
        - name: core
          sshAuthorizedKeys:
            - ssh-ed25519 AAAAC3NzaC1...Sfw haist@208PC001
```

### 99-master-sysctl-ovs-buffer

Increases OVN network socket buffer sizes to resolve
`OVNKubernetesNodeOVSOverflowKernel` alerts:

```
net.core.rmem_max=16777216
net.core.rmem_default=16777216
net.core.wmem_max=16777216
net.core.wmem_default=16777216
net.core.netdev_budget=600
net.core.netdev_budget_usecs=4000
net.core.netdev_max_backlog=10000
net.core.somaxconn=4096
```

## Terraform Provisioning

VMs are provisioned via OpenTofu using the `bpg/proxmox` provider v0.70.0.
The Terraform configuration is in `infrastructure/`:

```hcl
# Bootstrap node (pve)
resource "proxmox_virtual_environment_vm" "overwatch_bootstrap" {
  node_name = "pve"
  vm_id     = 210
  cpu { cores = 4;  type = "host" }
  memory { dedicated = 16384 }
  network_device { bridge = "vmbr1"; mac_address = "BC:24:11:D8:CF:14" }
  disk { datastore_id = "local-lvm"; size = 120 }
  boot_order = ["scsi0", "net0"]
}

# Master 1 (pve), Master 2 (208-pve2), Master 3 (208-pve2)
# Each: 12 cores, 32GB RAM, 120GB disk, vmbr1 bridge
# Static MAC addresses mapped to IPs via dnsmasq DHCP
```

The Terraform state is stored in MinIO S3 at `terraform-state/overwatch/terraform.tfstate`.

### CI/CD Pipeline

The `.gitlab-ci.yml` defines a 4-stage pipeline:

1. **lint** -- YAML validation
2. **security-scan** -- Trivy + Gitleaks
3. **generate** -- Run `generate_ignition.sh`, copy ignition files to nginx web root
4. **provision** -- `tofu plan` (auto), `tofu apply` (manual trigger)

## PXE Boot Process

Nodes boot via PXE using dnsmasq and nginx on iac-control:

1. Node powers on, requests DHCP from dnsmasq on 10.0.0.0/24
2. dnsmasq assigns IP based on static MAC-to-IP mapping
3. dnsmasq sends iPXE boot script URL (served by nginx on port 8080)
4. Node downloads SCOS kernel + initramfs + ignition from nginx
5. Node installs CoreOS to disk with embedded ignition config
6. On reboot, node contacts Machine Config Server at `api-int.overwatch.haist.farm:22623`

### DHCP-to-IP Mapping

| MAC Address | Hostname | IP | Role |
|-------------|----------|----|------|
| BC:24:11:D8:CF:14 | bootstrap | 10.0.0.220 | Bootstrap (temporary) |
| BC:24:11:F1:90:7A | master-1 | 10.0.0.221 | Control plane |
| BC:24:11:54:EF:CF | master-2 | 10.0.0.222 | Control plane |
| BC:24:11:EC:8C:91 | master-3 | 10.0.0.223 | Control plane |

## Full Cluster Rebuild

The `scripts/rebuild-cluster.sh` script orchestrates a complete cluster rebuild.
It handles the full lifecycle from ignition generation through GitOps bootstrap.

```bash
# Full rebuild (interactive, prompts for confirmation)
./scripts/rebuild-cluster.sh

# Dry-run mode (validate pre-flight checks only)
./scripts/rebuild-cluster.sh --dry-run
```

**Prerequisites:**

- Run from iac-control (192.168.12.210)
- Vault root token (prompted at start)
- SCOS artifacts in `~/overwatch-repo/scos-artifacts/`
- openshift-install binary at `/usr/local/bin/openshift-install`

The script:

1. Validates pre-flight checks (tools, network, Vault, Proxmox)
2. Generates new ignition configs
3. Enables bootstrap in HAProxy
4. Destroys and recreates VMs via Terraform
5. Copies ignition files to nginx web root
6. Monitors bootstrap progress
7. Approves CSRs
8. Disables bootstrap in HAProxy
9. Waits for cluster operators to stabilize
10. Bootstraps ArgoCD and GitOps applications

## Single Node Replacement

The `scripts/replace-node.sh` replaces a single master node without full rebuild:

```bash
# Replace master-1 (interactive)
./scripts/replace-node.sh --node master-1

# Dry-run
./scripts/replace-node.sh --node master-2 --dry-run

# Show node map
./scripts/replace-node.sh --list
```

**Critical:** This script extracts the Machine Config Server CA from the
live cluster -- it does NOT regenerate ignition. Regenerating ignition creates
a new PKI that will not match the running cluster.

### Node Map

| Node | VM ID | Proxmox Host | Terraform Resource |
|------|-------|--------------|--------------------|
| master-1 | 211 | pve | `proxmox_virtual_environment_vm.overwatch_node_1` |
| master-2 | 212 | 208-pve2 | `proxmox_virtual_environment_vm.overwatch_node_2` |
| master-3 | 213 | 208-pve2 | `proxmox_virtual_environment_vm.overwatch_node_3` |

## Helper Scripts

### toggle-haproxy-bootstrap.sh

Enables or disables the bootstrap node in HAProxy backends during cluster
rebuild:

```bash
./scripts/toggle-haproxy-bootstrap.sh enable   # Add bootstrap to LB
./scripts/toggle-haproxy-bootstrap.sh disable  # Remove bootstrap from LB
./scripts/toggle-haproxy-bootstrap.sh status   # Show current state
```

### approve-csrs.sh

Automatically approves pending Certificate Signing Requests during bootstrap:

```bash
./scripts/approve-csrs.sh              # Loop until quiet for 5 minutes
./scripts/approve-csrs.sh --once       # Approve current pending CSRs and exit
./scripts/approve-csrs.sh --timeout 10 # Custom timeout (minutes)
```
