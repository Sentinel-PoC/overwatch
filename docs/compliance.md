# Compliance

This page documents how the Overwatch OKD cluster addresses NIST 800-53
controls relevant to container platform security.

## Applicable Control Families

The cluster directly implements or supports evidence for the following
control families:

| Family | Controls | Focus Area |
|--------|----------|------------|
| CM (Configuration Management) | CM-2, CM-3, CM-6, CM-7, CM-8 | Baseline config, change control, least functionality |
| SC (System & Communications) | SC-7, SC-8, SC-12, SC-13 | Boundary protection, network segmentation, cryptography |
| AC (Access Control) | AC-3, AC-4, AC-6 | Least privilege, information flow, RBAC |
| SI (System & Info Integrity) | SI-3, SI-4, SI-6 | Malicious code protection, monitoring, image verification |
| AU (Audit & Accountability) | AU-2, AU-3, AU-6 | Audit events, content, review |

## CM-2: Baseline Configuration

**Requirement:** Maintain a current baseline configuration of the system.

### Evidence

The cluster baseline is fully defined as code across two repositories:

| Component | Source | Format |
|-----------|--------|--------|
| VM infrastructure | `overwatch/infrastructure/main.tf` | Terraform (OpenTofu) |
| Cluster config | `overwatch/install-config.yaml` | OKD install config |
| MachineConfigs | `overwatch/manifests/machineconfigs/` | Kubernetes YAML |
| Workload manifests | `overwatch-gitops/apps/` | Kubernetes YAML + Helm |
| ArgoCD app definitions | `overwatch-gitops/clusters/overwatch/` | ArgoCD Application YAML |
| Network services | `sentinel-iac/ansible/roles/iac-control/` | Ansible templates |

### Drift Detection

- **Terraform:** `tofu plan` detects VM-level drift (CPU, memory, disk, network)
- **ArgoCD:** Continuous sync monitoring; OutOfSync status triggers alerts
- **Ansible:** `--check --diff` mode run daily at 08:00 UTC on iac-control
  detects drift in HAProxy, dnsmasq, keepalived, iptables, Squid
- **Ansible auto-remediation:** Runs daily at 08:30 UTC if drift detected

### Baseline Artifacts

| Artifact | Location |
|----------|----------|
| Node specs (12 cores / 32GB) | `infrastructure/main.tf` lines 60-65, 107-112, 154-159 |
| OVN-Kubernetes CNI | `install-config.yaml` line 16 |
| Network CIDRs | `install-config.yaml` lines 13-18 |
| SSH authorized keys | `manifests/machineconfigs/99-master-ssh.yaml` |
| OVS buffer tuning | `manifests/machineconfigs/99-master-sysctl-ovs-buffer.yaml` |

## CM-3: Configuration Change Control

**Requirement:** Track, review, approve, and audit changes to the system.

### Evidence

All configuration changes flow through version-controlled repositories:

```
Developer -> Git commit -> GitLab push -> CI pipeline (lint, security scan)
  -> ArgoCD auto-sync (for overwatch-gitops)
  -> Manual apply (for Terraform/Ansible in sentinel-iac)
```

| Change Type | Workflow | Approval |
|-------------|----------|----------|
| Workload deployment | Push to overwatch-gitops `main` | ArgoCD auto-sync |
| VM infrastructure | Push to overwatch `main` -> `tofu apply` | Manual trigger in CI |
| Node configuration | `oc apply -f` MachineConfig | Rolling restart (automatic) |
| Network services | Push to sentinel-iac `main` -> Ansible | Manual trigger in CI |

### ArgoCD Sync Monitoring

ArgoCD tracks sync state for all 22 applications. Current known OutOfSync
items:

- `defectdojo` -- StatefulSet volume claim template drift (accepted)
- `kyverno-policies` -- Webhook-injected fields (accepted)

## CM-6: Configuration Settings

**Requirement:** Establish and enforce security configuration settings.

### Evidence

| Setting | Enforced By | Value |
|---------|------------|-------|
| TLS 1.2 minimum | HAProxy config | `ssl-min-ver TLSv1.2` |
| No TLS session tickets | HAProxy config | `no-tls-tickets` |
| Non-root containers | Kyverno policy | `require-run-as-nonroot` (Enforce) |
| Resource limits | Kyverno policy | `require-resource-limits` (Enforce) |
| Image signature verification | Kyverno policy | `verify-image-signatures` (Enforce) |
| Restricted image registries | Kyverno policy | `restrict-image-registries` (Enforce) |
| No privileged containers | Kyverno policy | `disallow-privileged-containers` (Enforce) |
| mTLS between services | Istio PeerAuthentication | STRICT mode (mesh-wide) |
| OVS buffer sizing | MachineConfig | `net.core.rmem_max=16777216` |

### Pod Security Standards

Namespaces enforce Pod Security Standards via labels:

| Level | Namespaces |
|-------|-----------|
| `privileged` | nfs-provisioner, media, falco-system, kube-system, openshift-* |
| `baseline` | backstage, defectdojo, harbor, homepage, keycloak, matrix, monitoring, netbox, overwatch-console, reloader, haists-website |
| `restricted` | demo, external-secrets, sentinel-ops, istio-system |

## CM-7: Least Functionality

**Requirement:** Configure the system to provide only essential capabilities.

### Evidence

| Control | Implementation |
|---------|---------------|
| Egress filtering | Squid domain allowlist limits outbound to ~24 allowed domain patterns |
| Port restrictions | iptables FORWARD chain allows only specific ports (53, 80, 123, 443, 587, 2049, 9000) |
| Image registry restriction | Kyverno restricts pulls to `harbor.208.haist.farm` only |
| No worker nodes | 0 workers -- all pods run on control-plane nodes (reduced attack surface) |
| No Internet from pods | Pod network (10.128.0.0/14) cannot reach mgmt VLAN (192.168.12.0/24) |

### Squid Allowlist Audit

The egress allowlist (`/etc/squid/okd-egress-allowlist.txt`) contains 24
domain patterns. Each entry serves a documented purpose (container registries,
certificate validation, NTP, internal services). See
[Networking](networking.md#allowed-domains) for the complete list.

## SC-7: Boundary Protection

**Requirement:** Monitor and control communications at external boundaries
and key internal boundaries.

### Network Boundaries

```
+=====================================================+
| Management VLAN (192.168.12.0/24)                   |
|                                                     |
|  [pangolin]  [gitlab]  [vault]  [minio]  [wazuh]   |
|  .168        .68       .206     .58      .100       |
+=====================================================+
         |                    |
         | (Traefik)          | (iptables NAT + Squid)
         |                    |
+=====================================================+
| OKD Cluster Network (10.0.0.0/24)                   |
|                                                     |
|  [iac-control gateway]  VIP: 10.0.0.1              |
|  [master-1] [master-2] [master-3]                   |
|  .221       .222       .223                         |
+=====================================================+
         |
         | (OVN-Kubernetes)
         |
+=====================================================+
| Pod Network (10.128.0.0/14)                         |
| Service Network (172.30.0.0/16)                     |
|                                                     |
| NetworkPolicies + Istio AuthorizationPolicies       |
+=====================================================+
```

### Boundary Controls

| Boundary | Control | Implementation |
|----------|---------|---------------|
| Internet -> Cluster | Reverse proxy | Traefik on pangolin-proxy, Cloudflare Access |
| Mgmt VLAN <-> Cluster | Stateful firewall | iptables on iac-control (FORWARD chain) |
| Cluster -> Internet | Egress proxy | Squid domain-based allowlist |
| Pod -> Pod | Network policy | Istio AuthorizationPolicies (default deny-all) |
| Pod -> External | mTLS termination | Istio IngressGateway + sidecar mTLS |
| HAProxy access | Source filtering | iptables INPUT rules restrict to pangolin + vault IPs |

### Monitoring

- iptables logging: `HAPROXY-BLOCK:` and `OKD-EGRESS-DENY:` prefixes
- Squid access logs: sent to syslog (picked up by Wazuh)
- Falco: Runtime security monitoring in `falco-system` namespace
- Wazuh: SIEM agents on iac-control and cluster nodes [VERIFY]

## SC-8: Transmission Confidentiality and Integrity

**Requirement:** Protect the confidentiality and integrity of transmitted
information.

### Evidence

| Path | Protection |
|------|-----------|
| Client -> Traefik | TLS 1.2+ (Let's Encrypt wildcard, Cloudflare DNS-01) |
| Traefik -> HAProxy | TLS passthrough (TCP mode) |
| HAProxy -> OKD Router | TLS passthrough (TCP mode) |
| Pod -> Pod (meshed) | Istio mTLS (STRICT mode) |
| iac-control -> Vault | TLS (Vault TLS cert, chown uid 100) |
| iac-control -> GitLab | HTTP (internal network only) |

HAProxy operates in TCP mode for all frontends -- it does not terminate TLS.
This preserves end-to-end encryption from client to OKD Router or Istio
IngressGateway.

## AC-6: Least Privilege

**Requirement:** Employ the principle of least privilege.

### Evidence

| Resource | Least Privilege Implementation |
|----------|-------------------------------|
| Kyverno policies | Enforce non-root, no privileged containers |
| Istio AuthorizationPolicies | Default deny-all per namespace, explicit allow rules |
| RBAC | ArgoCD service accounts scoped per namespace |
| ESO | Vault K8s auth roles with path-limited policies |
| NFS provisioner | RBAC created separately, minimal permissions |
| Sentinel-ops | Vault role `sentinel-ops` with read-only secret access |
| HAProxy admin | Stats socket restricted to haproxy group, mode 660 |

## SI-6: Software, Firmware, and Information Integrity

**Requirement:** Verify the integrity of software and information.

### Evidence

| Component | Integrity Check |
|-----------|----------------|
| Container images | Kyverno `verify-image-signatures` (cosign, Enforce mode) |
| GitOps manifests | ArgoCD compares git state to live state continuously |
| Terraform state | S3 backend in MinIO with state locking |
| Node configuration | MachineConfig operator ensures desired state on all nodes |
| Ansible baseline | Daily `--check --diff` drift detection with Wazuh alerting |

## Compliance Automation

### Automated Checks

The `nist-compliance-check.sh` script on iac-control runs 115 checks daily
at 06:00 UTC covering 111 unique controls. Cluster-relevant checks include:

- HAProxy configuration integrity
- dnsmasq DNS/DHCP configuration
- keepalived VIP status
- iptables rule validation
- Squid allowlist verification
- ArgoCD sync status (CM-3(2) check)

### Evidence Pipeline

Daily at 07:00 UTC, the evidence pipeline:

1. Collects compliance check JSON results
2. Converts to markdown reports
3. Auto-commits to `compliance-vault` GitLab repo
4. Generates trend tracking data

### sentinel-ops CronJobs

The `sentinel-ops` namespace runs platform automation:

| CronJob | Schedule | Purpose |
|---------|----------|---------|
| grafana-health | Every 5 min | Grafana dashboard health check |
| nist-compliance-check | Daily 06:00 UTC | Automated compliance validation |
| minio-replicate | Every 6 hours | MinIO bucket replication |
| evidence-pipeline | Daily 07:00 UTC | Compliance evidence collection |
