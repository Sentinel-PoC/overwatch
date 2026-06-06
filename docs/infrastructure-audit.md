# Infrastructure Audit Report - iac-control (192.168.12.210)
**Audit Date:** 2026-02-05

## Executive Summary

This server (`iac-control`) is the central Infrastructure-as-Code control node for a hybrid lab environment running OKD 4.19 (OpenShift) on Proxmox. It manages multiple repositories for infrastructure provisioning, GitOps configurations, and acts as the DNS/DHCP/HAProxy gateway for the Overwatch OKD cluster.

---

## 1. SENTINEL-REPO (`~/sentinel-repo/`)

**Purpose:** Main IaC repository for Proxmox VM provisioning and Ansible configuration management.

### Directory Structure
```
sentinel-repo/
├── .git/
├── .github/workflows/deploy.yml
├── .gitlab-ci.yml
├── infrastructure/
│   ├── main.tf              # Proxmox VM definitions
│   ├── provider.tf          # Proxmox + MinIO S3 backend
│   ├── variables.tf         # Environment variables
│   ├── deploy_n8n.yml       # Ansible playbook
│   ├── install_gitlab.yml   # Ansible playbook
│   ├── deploy_manageiq.yml  # Ansible playbook
│   ├── setup_config_server.yml
│   └── inventory.ini
├── packer/
│   └── fedora-coreos.pkr.hcl
├── inventory.ini
└── playbook.yml             # Pangolin/Traefik proxy setup
```

### Terraform Resources (CONFIGURED)
| Resource | VM ID | Name | Node | IP | RAM | Purpose |
|----------|-------|------|------|-----|-----|---------|
| `proxmox_virtual_environment_vm.gitlab_server` | 201 | gitlab-server | pve | DHCP | 16GB | GitLab CE |
| `proxmox_virtual_environment_vm.n8n_server` | 202 | n8n-server | pve | DHCP | 8GB | n8n Automation |
| `proxmox_virtual_environment_vm.manageiq_server` | 203 | manageiq-server | 208-pve2 | 192.168.12.203 | 8GB | ManageIQ |
| `proxmox_virtual_environment_container.sentinel_config_server` | 300 | config-server | pve | 10.0.0.2/24 | LXC | Config Server |
| `proxmox_virtual_environment_vm.vault_server` | 205+ | vault-server | 208-pve2 | 192.168.12.205+ | 4GB | HashiCorp Vault |

### Terraform Backend (VERIFIED)
- **Type:** S3 (MinIO)
- **Endpoint:** `http://192.168.12.58:9000`
- **Bucket:** `terraform-state`
- **Key:** `sentinel-iac/terraform.tfstate`

### CI/CD Configuration
**GitLab CI (`.gitlab-ci.yml`):** 3-stage pipeline
1. `build-templates` - Packer golden image build
2. `provision` - OpenTofu plan/apply
3. `configure` - Ansible playbooks

**GitHub Actions:** K3s deployment to 192.168.12.116

### Recent Git Activity
```
c2d3f02 feat: migrate to MinIO S3 backend for terraform state
678ecca Feat: Add qemu-guest-agent to GitLab install
aa3784e Cleanup: Remove unused root main.tf duplicate
bd7202e Fix: Update runner tags to match registered runner
```

---

## 2. OVERWATCH-REPO (`~/overwatch-repo/`)

**Purpose:** OKD 4.19 UPI deployment using CentOS Stream CoreOS (SCOS).

### Directory Structure
```
overwatch-repo/
├── .gitlab-ci.yml
├── infrastructure/
│   ├── main.tf              # OKD VM definitions (12 cores, 32GB per node)
│   ├── provider.tf          # Proxmox + MinIO backend
│   ├── variables.tf
│   ├── ansible/             # (empty)
│   ├── files/
│   └── templates/
├── auth/                    # OKD auth credentials
├── bin/
├── scripts/
├── scos-artifacts/
├── overwatch-gen/
├── install-config.yaml
├── bootstrap.ign
├── master.ign
├── worker.ign
└── generate_ignition.sh
```

### Terraform Resources (VERIFIED - Running)
| Resource | VM ID | Name | Node | MAC Address | IP | Cores | RAM |
|----------|-------|------|------|-------------|-----|-------|-----|
| `overwatch_bootstrap` | 210 | overwatch-bootstrap | pve | BC:24:11:D8:CF:14 | 10.0.0.220 | 4 | 16GB |
| `overwatch_node_1` | 211 | overwatch-node-1 | pve | BC:24:11:F1:90:7A | 10.0.0.221 | 12 | 32GB |
| `overwatch_node_2` | 212 | overwatch-node-2 | 208-pve2 | BC:24:11:54:EF:CF | 10.0.0.222 | 12 | 32GB |
| `overwatch_node_3` | 213 | overwatch-node-3 | 208-pve2 | BC:24:11:EC:8C:91 | 10.0.0.223 | 12 | 32GB |

### OKD Cluster Configuration (`install-config.yaml`)
- **Base Domain:** `haist.farm`
- **Cluster Name:** `overwatch`
- **Control Plane Replicas:** 3
- **Worker Replicas:** 0
- **Network Type:** OVNKubernetes
- **Cluster Network:** 10.128.0.0/14
- **Service Network:** 172.30.0.0/16

### Terraform Backend (VERIFIED)
- **Key:** `overwatch/terraform.tfstate`
- Same MinIO backend as sentinel-repo

---

## 3. OVERWATCH-GITOPS (`~/overwatch-gitops/`)

**Purpose:** GitOps repository for OKD cluster applications managed by OpenShift GitOps (ArgoCD).

### Directory Structure
```
overwatch-gitops/
├── argocd/
│   └── install.yaml         # OpenShift GitOps Operator subscription
├── apps/
│   ├── jellyfin/
│   ├── seedbox/             # qBittorrent, Sonarr, Radarr, Prowlarr, Gluetun VPN
│   └── hello-world/
└── clusters/overwatch/
    ├── apps/
    │   ├── monitoring/grafana/values.yaml
    │   ├── seedbox-app.yaml
    │   └── jellyfin-app.yaml
    └── system/
        ├── ingress/
        │   ├── pangolin-app.yaml
        │   ├── newt-app.yaml
        │   └── newt-resources/newt.yaml
        └── storage/
            └── nfs-app.yaml
```

### Grafana Configuration
- **Admin User:** `admin`
- **Admin Password:** `[REDACTED - stored in overwatch-gitops values.yaml]`
- **URL:** `https://graf.tunneled.to`
- **Datasource:** Prometheus (Thanos Querier)

### Newt Tunnel (CONFIGURED)
- **Pangolin Endpoint:** `https://app.pangolin.net`
- **Newt ID:** `ba4xgc9d0nf3lir`
- **NodePort:** 31820/UDP (WireGuard)

---

## 4. RUNNING SERVICES ON IAC-CONTROL (VERIFIED)

| Service | Status | Purpose |
|---------|--------|---------|
| **dnsmasq** | RUNNING | DNS/DHCP for OKD cluster (10.0.0.0/24) |
| **haproxy** | RUNNING | Load balancer for OKD API/Ingress |
| **gitlab-runner** | RUNNING | CI/CD executor for GitLab |
| **docker** | RUNNING | Container runtime |
| **nginx** | RUNNING | Serves ignition/SCOS artifacts |
| **ssh** | RUNNING | Remote access |

### DNSmasq Configuration
**File:** `/etc/dnsmasq.d/overwatch.conf`

**DNS Records:**
- `api.overwatch.haist.farm` -> 10.0.0.1
- `api-int.overwatch.haist.farm` -> 10.0.0.1
- `*.apps.overwatch.haist.farm` -> 10.0.0.1

**DHCP Static Leases:**
| MAC | Hostname | IP |
|-----|----------|-----|
| BC:24:11:D8:CF:14 | bootstrap | 10.0.0.220 |
| BC:24:11:F1:90:7A | master-1 | 10.0.0.221 |
| BC:24:11:54:EF:CF | master-2 | 10.0.0.222 |
| BC:24:11:EC:8C:91 | master-3 | 10.0.0.223 |

### HAProxy Configuration
**File:** `/etc/haproxy/haproxy.cfg`

| Frontend | Port | Backend Servers |
|----------|------|-----------------|
| okd4_api_frontend | 6443 | bootstrap, master1-3 |
| okd4_machine_config_frontend | 22623 | bootstrap, master1-3 |
| okd4_http_ingress_frontend | 80 | master1-3 |
| okd4_https_ingress_frontend | 443 | master1-3 |
| stats | 9000 | HAProxy stats page |

---

## 5. NETWORK CONFIGURATION (VERIFIED)

### Interfaces
| Interface | IP | Purpose |
|-----------|-----|---------|
| eth0 | 192.168.12.210/24 | Management network |
| ens19 | 10.0.0.10/24, 10.0.0.1/24 | OKD cluster network (gateway) |
| docker0 | 172.17.0.1/16 | Docker bridge |

---

## 6. OKD CLUSTER STATUS (VERIFIED)

### Cluster Info
- **API Server:** `https://api.overwatch.haist.farm:6443`
- **Console:** `https://openshift-console.tunneled.to`
- **OAuth:** `https://openshift-oauth.tunneled.to`
- **Kubeadmin:** `[REDACTED - see ~/overwatch-repo/auth/kubeadmin-password on iac-control]`

### Nodes (VERIFIED - All Running)
| Name | Status | Roles | Version | Resources |
|------|--------|-------|---------|-----------|
| master-1.overwatch.haist.farm | Ready | control-plane,master,worker | v1.32.7 | 12c/32GB |
| master-2.overwatch.haist.farm | Ready | control-plane,master,worker | v1.32.7 | 12c/32GB |
| master-3.overwatch.haist.farm | Ready | control-plane,master,worker | v1.32.7 | 12c/32GB |

### Key Namespaces
- `monitoring` - Grafana (1 pod running)
- `media` - Jellyfin, Seedbox (6 pods running)
- `pangolin-internal` - Newt tunnel, Traefik (3 pods running)
- `nfs-provisioner` - NFS dynamic provisioner
- `openshift-gitops` - ArgoCD components

### Active Routes (VERIFIED)
| Namespace | Route | Host |
|-----------|-------|------|
| media | jellyfin | jellyfin-media.apps.overwatch.haist.farm |
| media | qbittorrent | qbittorrent-media.apps.overwatch.haist.farm |
| media | sonarr | sonarr-media.apps.overwatch.haist.farm |
| media | radarr | radarr-media.apps.overwatch.haist.farm |
| media | prowlarr | prowlarr-media.apps.overwatch.haist.farm |
| monitoring | grafana | grafana-monitoring.apps.overwatch.haist.farm |
| openshift-console | console-custom | openshift-console.tunneled.to |

### Storage Classes (VERIFIED)
- **nfs-storage (default)** - NFS dynamic provisioner

---

## 7. SUMMARY TABLE

| Component | Status | Notes |
|-----------|--------|-------|
| **OKD Cluster** | ✅ VERIFIED RUNNING | 3 master nodes, v1.32.7 |
| **HAProxy LB** | ✅ VERIFIED RUNNING | API/Ingress load balancing |
| **DNSmasq** | ✅ VERIFIED RUNNING | Cluster DNS/DHCP |
| **GitLab Runner** | ✅ VERIFIED RUNNING | Connected to 192.168.12.68 |
| **Grafana** | ✅ VERIFIED RUNNING | graf.tunneled.to |
| **Jellyfin** | ✅ VERIFIED RUNNING | jellyfin-media.apps.overwatch.haist.farm |
| **Seedbox Stack** | ✅ VERIFIED RUNNING | qBittorrent/Sonarr/Radarr/Prowlarr |
| **Newt Tunnel** | ✅ VERIFIED RUNNING | Pangolin.net integration |
| **ArgoCD** | ✅ VERIFIED RUNNING | OpenShift GitOps operator |
| **NFS Provisioner** | ✅ VERIFIED RUNNING | Default storage class |
| **GitLab Server** | CONFIGURED | 192.168.12.68 (not verified) |
| **n8n Server** | CONFIGURED | 192.168.12.202 (not verified) |
| **ManageIQ** | CONFIGURED | 192.168.12.203 (not verified) |
| **Vault Server** | ✅ VERIFIED | 192.168.12.206 |
| **MinIO** | CONFIGURED | 192.168.12.58:9000 (used for state) |

---

## 8. RECOMMENDATIONS

1. **Security:** Move credentials from provider.tf/playbook.yml to environment variables or Vault
2. **DNS:** Consider adding reverse DNS entries for cluster nodes
3. **Backup:** Document terraform state backup procedures for MinIO
4. **Monitoring:** Verify Grafana dashboards are receiving Prometheus metrics
5. **GitOps:** Ensure ArgoCD is syncing with the overwatch-gitops repository
6. **Documentation:** The archive_cleanup_20260125 directory contains legacy scripts that could be documented or removed
