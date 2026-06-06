# OKD Cluster Status (Overwatch)

## Quick Reference
- **API:** `https://api.overwatch.haist.farm:6443` ✅ HEALTHY
- **Console (External):** `https://openshift-console.tunneled.to` ✅ WORKING (Pangolin SSO + OKD auth)
- **OAuth (External):** `https://openshift-oauth.tunneled.to` ✅ WORKING (public, no Pangolin SSO)
- **Kubeadmin:** `[REDACTED - see ~/overwatch-repo/auth/kubeadmin-password on iac-control]`
- **Grafana:** `admin` / `[REDACTED - stored in Grafana values.yaml]` at `https://graf.tunneled.to`
- **Kubeconfig:** `~/overwatch-repo/auth/kubeconfig` (uses system:admin)

## Architecture
- **Network:** 10.0.0.0/24
- **Gateway/LB/DNS:** 10.0.0.1 (iac-control on 192.168.12.210)
- **Masters:** (upgraded 2026-02-05)
  - master-1: 10.0.0.221 ✅ HEALTHY (12 cores / 32GB)
  - master-2: 10.0.0.222 ✅ HEALTHY (12 cores / 32GB)
  - master-3: 10.0.0.223 ✅ HEALTHY (12 cores / 32GB)

## Pangolin Tunnel Configuration

External access goes through Pangolin tunnel at `*.tunneled.to`:

| Resource | Domain | SSO | Health Check | Notes |
|----------|--------|-----|--------------|-------|
| OKD Console | openshift-console.tunneled.to | ON | enabled | Protected by Pangolin passkeys |
| OKD OAuth | openshift-oauth.tunneled.to | OFF | disabled | Must be public for auth flow |
| Grafana | graf.tunneled.to | ON | enabled | |

### Why OAuth has SSO disabled
OAuth must be publicly accessible because:
1. OpenShift operator health checks need to reach `/healthz`
2. Console redirects users to OAuth for OKD login (after Pangolin auth)

### Auth Flow
1. User → `openshift-console.tunneled.to`
2. Pangolin SSO intercepts → user authenticates with passkey
3. After Pangolin auth → Console loads
4. Console redirects to `openshift-oauth.tunneled.to`
5. OAuth (public) → user enters kubeadmin/htpasswd credentials
6. Back to Console, logged into OKD

### Pangolin API Access
```bash
# Org ID: org_6pbq61tbb2mypkc
# API Token stored separately (ask user)

# List resources
curl -s "https://api.pangolin.net/v1/org/org_6pbq61tbb2mypkc/resources" \
  -H "Authorization: Bearer <token>"

# Update resource (e.g., disable SSO)
curl -s -X POST "https://api.pangolin.net/v1/resource/<resourceId>" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"sso": false}'

# Update target (e.g., disable health check)
curl -s -X POST "https://api.pangolin.net/v1/target/<targetId>" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"siteId": <siteId>, "ip": "<ip>", "port": <port>, "method": "https", "hcEnabled": false}'
```

## Issues Fixed (2026-02-05)

### 1. OAuth/Console Access
**Problem:** Pangolin SSO was enabled on OAuth resource, causing:
1. Health checks to be redirected to Pangolin auth (returning 302 → 503)
2. OpenShift authentication operator marked degraded
3. Console login flow broken

### Root Cause
1. OAuth resource in Pangolin had `sso: true` (requiring Pangolin auth)
2. Health check was hitting `/` which returned 403
3. Kubeconfig had stale CA certificates

### Fix Applied
1. Disabled SSO on OAuth resource via Pangolin API (`sso: false`)
2. Disabled health check on OAuth target (`hcEnabled: false`)
3. Replaced kubeconfig with fresh `lb-ext.kubeconfig` from master
4. Kept SSO enabled on Console for defense-in-depth (Pangolin passkeys)

### 2. Node Resource Upgrades
**Problem:** Nodes had insufficient resources (4 cores / 16GB) causing overcommit alerts.

**Fix Applied:**
1. User manually upgraded each node in Proxmox to 12 cores / 32GB
2. Updated `~/overwatch-repo/infrastructure/main.tf` to match
3. Terraform state now in sync (`tofu plan` shows no changes)

### 3. OVN Network Buffer Overflow
**Problem:** `OVNKubernetesNodeOVSOverflowKernel` alerts - netlink socket buffers too small (212KB default).

**Fix Applied:**
1. Created MachineConfig `99-master-ovs-sysctl` to increase buffers to 8MB
2. Applied via `oc apply` - rolling update completed on all 3 masters
3. Verified: `net.core.rmem_max = 8192000` on all nodes
4. Alert cleared - only Watchdog + informational alerts remain

## Components Status
| Component | Status | Notes |
|-----------|--------|-------|
| HAProxy (iac-control) | ✅ Running | Load balancing API + Ingress |
| Dnsmasq (iac-control) | ✅ Running | DNS for cluster |
| API Server | ✅ Healthy | All 3 masters responding |
| etcd | ✅ Running | On all masters |
| OAuth Operator | ✅ Available | Health checks working |
| Console Operator | ✅ Available | |
| Router Pods | ✅ Running | |
| CoreDNS | ✅ Running | Internal DNS working |
| Newt Tunnel | ✅ Connected | Routing OAuth + Console |

## GitOps Apps (overwatch-gitops)
- Grafana (monitoring) - `graf.tunneled.to`
- Jellyfin
- Seedbox (VPN + Arr stack)
- Pangolin/Newt tunnel
- NFS Provisioner

## SSH Access
```bash
# To masters (from iac-control)
ssh -i ~/.ssh/okd_key core@10.0.0.221
ssh -i ~/.ssh/okd_key core@10.0.0.222
ssh -i ~/.ssh/okd_key core@10.0.0.223
```

## Kubeconfig Maintenance
If you see "certificate signed by unknown authority" errors:
```bash
# Get fresh kubeconfig from master
ssh -i ~/.ssh/okd_key core@10.0.0.221 'sudo cat /etc/kubernetes/static-pod-resources/kube-apiserver-certs/secrets/node-kubeconfigs/lb-ext.kubeconfig' > ~/overwatch-repo/auth/kubeconfig
```

## Terraform State Backend (MinIO)
- **Endpoint:** `http://192.168.12.58:9000`
- **Console:** `http://192.168.12.58:9001`
- **Bucket:** `terraform-state`
- **State Keys:**
  - `overwatch/terraform.tfstate` - OKD cluster VMs
  - `sentinel-iac/terraform.tfstate` - Other infra (GitLab, n8n, etc.)
- **Credentials:** `minio-admin` / `[REDACTED - stored in Vault secret/minio]`

## Current Alerts (as of 2026-02-05)
Only informational alerts remain:
- `Watchdog` - Expected (dead-man's switch)
- `InsightsDisabled` - OKD Insights not configured
- `AlertmanagerReceiversNotConfigured` - No alert receivers set up
