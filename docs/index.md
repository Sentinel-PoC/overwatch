# OKD Cluster Operations - Overwatch

## Cluster Identity

| Property | Value |
|----------|-------|
| **Cluster Name** | overwatch |
| **Base Domain** | haist.farm |
| **API Endpoint** | `https://api.overwatch.haist.farm:6443` |
| **Console** | `https://console-openshift-console.apps.overwatch.haist.farm` |
| **Platform** | OKD 4.19 (Kubernetes v1.32.7) |
| **Distribution** | UPI on bare-metal Proxmox VMs |
| **CNI** | OVN-Kubernetes |
| **Workers** | 0 (control-plane nodes schedule all workloads) |

## Architecture Overview

The Overwatch cluster is a 3-node compact OKD deployment running on Proxmox
virtual machines across two physical hosts. All nodes carry both
`control-plane` and `worker` roles -- there are no dedicated worker nodes.

```
                         Internet
                            |
                    [pangolin-proxy]
                    192.168.12.168
                            |
               +--- Management VLAN ---+
               |    192.168.12.0/24    |
               |                       |
         [iac-control]          [config-server]
         192.168.12.210          VM 300 (HA backup)
         10.0.0.1 (VIP)         10.0.0.2
         10.0.0.10 (DHCP)
               |
               | vmbr1 (internal bridge)
               | 10.0.0.0/24
               |
     +---------+---------+
     |         |         |
 [master-1] [master-2] [master-3]
 10.0.0.221 10.0.0.222 10.0.0.223
 pve        208-pve2    208-pve2
```

### Node Inventory

| Node | VM ID | IP Address | Proxmox Host | CPU | Memory | Disk |
|------|-------|-----------|--------------|-----|--------|------|
| master-1 | 211 | 10.0.0.221 | pve | 12 cores | 32 GB | 120 GB |
| master-2 | 212 | 10.0.0.222 | 208-pve2 | 12 cores | 32 GB | 120 GB |
| master-3 | 213 | 10.0.0.223 | 208-pve2 | 12 cores | 32 GB | 120 GB |
| bootstrap | 210 | 10.0.0.220 | pve | 4 cores | 16 GB | 120 GB |

The bootstrap node (VM 210) is powered off post-install. It is only needed for
full cluster rebuilds.

### Supporting Infrastructure

| Component | Host | Role |
|-----------|------|------|
| **iac-control** (192.168.12.210 / 10.0.0.1) | pve | HAProxy LB, dnsmasq DNS/DHCP/PXE, keepalived VIP, Squid egress proxy, nginx ignition server |
| **config-server** (10.0.0.2 / 192.168.12.132) | pve (VM 300) | HA failover: keepalived BACKUP, dnsmasq, HAProxy mirror |
| **pangolin-proxy** (192.168.12.168) | pve | Traefik reverse proxy, Cloudflare tunnel, CrowdSec |
| **vault-server** (192.168.12.206) | 208-pve2 | HashiCorp Vault (secrets, SSH CA, ESO backend), NFS storage |

### Network Topology

The cluster lives on an isolated internal network (`vmbr1`, 10.0.0.0/24)
with no direct internet access. iac-control bridges the management VLAN
(192.168.12.0/24) to the cluster network and provides:

- **NAT** for outbound traffic (with Squid domain-based allowlisting)
- **HAProxy** load balancing for API (6443), Machine Config (22623), and Ingress (80/443)
- **dnsmasq** DNS resolution (cluster + wildcard `*.apps.overwatch.haist.farm`) and DHCP
- **keepalived** VIP (10.0.0.1) for HA failover to config-server

**Critical constraint:** OKD pods CANNOT reach 192.168.12.0/24 (management
VLAN). Only the 10.0.0.0/24 internal network and NAT-routed internet
destinations are accessible.

### Air-Gapped Characteristics

While not fully air-gapped (Squid allows allowlisted domains), the cluster
has significant restrictions:

- No direct internet egress from pod network
- Grafana `gnetId` dashboard references silently fail -- always use inline JSON
- All container images are pulled from Harbor at `harbor.208.haist.farm` (on mgmt VLAN, accessible via iptables FORWARD rules to 192.168.12.68 and image registries)
- Squid allowlist controls which external registries are reachable

### Access Methods

```bash
# From WSL workstation to iac-control
ssh -i ~/.ssh/id_sentinel ubuntu@192.168.12.210

# From iac-control: oc login
export KUBECONFIG=~/overwatch-repo/auth/kubeconfig
oc login https://api.overwatch.haist.farm:6443 \
  -u kubeadmin -p $(cat ~/overwatch-repo/auth/kubeadmin-password) \
  --insecure-skip-tls-verify

# From iac-control: SSH to master nodes
ssh -i ~/.ssh/okd_key core@10.0.0.221   # master-1
ssh -i ~/.ssh/okd_key core@10.0.0.222   # master-2
ssh -i ~/.ssh/okd_key core@10.0.0.223   # master-3
```

### Kubeconfig Maintenance

If `oc` returns "certificate signed by unknown authority" errors, refresh the
kubeconfig from a running master:

```bash
ssh -i ~/.ssh/okd_key core@10.0.0.221 \
  'sudo cat /etc/kubernetes/static-pod-resources/kube-apiserver-certs/secrets/node-kubeconfigs/lb-ext.kubeconfig' \
  > ~/overwatch-repo/auth/kubeconfig
```

### GitOps Repository

The cluster state is managed by ArgoCD from the `overwatch-gitops` repository
(GitLab project 3) at `http://192.168.12.68/admin1/overwatch-gitops.git`.
Pushing to `main` triggers auto-sync. See [Workload Management](workload-management.md)
for the full app-of-apps structure.

### Terraform State

VM infrastructure state is stored in MinIO S3-compatible storage:

| Property | Value |
|----------|-------|
| Endpoint | `http://192.168.12.58:9000` |
| Bucket | `terraform-state` |
| State Key | `overwatch/terraform.tfstate` |
| Provider | `bpg/proxmox` v0.70.0 |
| Terraform | OpenTofu >= 1.6.0 |
