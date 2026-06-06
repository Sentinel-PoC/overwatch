# Workload Management

## GitOps Model

All cluster workloads are managed through ArgoCD auto-sync from the
`overwatch-gitops` GitLab repository (project 3). **Pushing to `main` is
deploying.** Never patch running deployments directly -- ArgoCD will revert
changes.

**Exception:** `pangolin-internal` Traefik is NOT managed by the app-of-apps
root application. It requires manual `oc apply`:

```bash
oc apply -f overwatch-gitops/apps/pangolin-internal/
```

### ArgoCD Configuration

| Property | Value |
|----------|-------|
| Operator | `argocd-operator.v0.17.0` (OpenShift GitOps) |
| Version | ArgoCD v3.1.11 |
| Namespace | `openshift-gitops` |
| Repository | `http://192.168.12.68/admin1/overwatch-gitops.git` |
| Target Branch | `main` |
| Sync Policy | Auto-sync enabled |

### App-of-Apps Pattern

A root application (`root-app`) recursively discovers all `*-app.yaml` files
under `clusters/overwatch/` and creates child ArgoCD Applications:

```
root-app (clusters/overwatch/*-app.yaml)
  |
  +-- clusters/overwatch/apps/
  |     backstage-app.yaml
  |     defectdojo-app.yaml
  |     falco-app.yaml
  |     grafana-app.yaml (+ grafana-dashboards-app.yaml)
  |     haists-website-app.yaml
  |     harbor-app.yaml
  |     homepage-app.yaml
  |     jellyfin-app.yaml
  |     keycloak-app.yaml
  |     kyverno-policies-app.yaml
  |     matrix-app.yaml
  |     netbox-app.yaml
  |     overwatch-console-app.yaml
  |     reloader-app.yaml
  |     seedbox-app.yaml
  |     sentinel-ops-app.yaml
  |
  +-- clusters/overwatch/service-mesh/
  |     (Istio, Jaeger, mesh-config -- managed separately)
  |
  +-- clusters/overwatch/system/
        ingress/ (pangolin-app, newt-tunnel)
        storage/ (nfs-app, static-storage)
```

## Current Applications (22 total)

Live state from ArgoCD as of 2026-03-04:

| Application | Namespace | Sync | Health | Source |
|-------------|-----------|------|--------|--------|
| backstage | backstage | Synced | Healthy | apps/backstage (multi-source) |
| defectdojo | defectdojo | OutOfSync | Healthy | DefectDojo Helm chart v1.9.12 |
| falco | falco-system | Synced | Healthy | apps/falco |
| grafana | monitoring | Synced | Healthy | Grafana Helm chart v10.5.15 |
| grafana-dashboards | monitoring | Synced | Healthy | monitoring/grafana/dashboards |
| haists-website | haists-website | Synced | Healthy | apps/haists-website |
| harbor | harbor | Synced | Healthy | Harbor Helm chart v1.18.2 |
| homepage | homepage | Synced | Healthy | apps/homepage |
| jellyfin | media | Synced | Healthy | apps/jellyfin |
| keycloak | keycloak | Synced | Healthy | apps/keycloak |
| kyverno-policies | kyverno | OutOfSync | Healthy | apps/kyverno-policies |
| matrix | matrix | Synced | Healthy | apps/matrix |
| netbox | netbox | Synced | Healthy | NetBox Helm chart v7.4.8 |
| newt-tunnel | pangolin-internal | Synced | Healthy | system/ingress/newt-resources |
| nfs-provisioner | nfs-provisioner | Synced | Healthy | NFS Helm chart v4.0.18 |
| overwatch-console | overwatch-console | Synced | Healthy | apps/overwatch-console |
| pangolin-internal | pangolin-internal | Synced | Healthy | Traefik Helm chart v26.0.0 |
| reloader | reloader | Synced | Healthy | apps/reloader |
| root-app | openshift-gitops | Synced | Healthy | clusters/overwatch (*-app.yaml) |
| seedbox | media | Synced | Healthy | apps/seedbox |
| sentinel-ops | sentinel-ops | Synced | Healthy | apps/sentinel-ops |
| static-storage | default | Synced | Healthy | system/static-storage |

**Note:** `defectdojo` and `kyverno-policies` show OutOfSync -- these are
cosmetic. DefectDojo has StatefulSet volume claim drift that is expected.
Kyverno webhook injects `skipBackgroundRequests` and `allowExistingViolations`
into ClusterPolicy specs; this is an accepted risk.

## Namespace Inventory

Active namespaces with workloads (excludes openshift-* system namespaces):

| Namespace | Istio Injection | ArgoCD Managed | Workloads |
|-----------|----------------|----------------|-----------|
| backstage | enabled | yes | Backstage service catalog |
| defectdojo | enabled | yes | DefectDojo vulnerability management |
| demo | -- | yes | Demo/hello-world apps |
| external-secrets | -- | -- | External Secrets Operator |
| falco-system | -- | yes | Falco runtime security |
| haists-website | enabled | yes | Public website |
| harbor | enabled | yes | Container registry |
| homepage | enabled | yes | Homepage dashboard |
| istio-cni | -- | -- | Istio CNI plugin |
| istio-ingress | enabled | -- | Istio IngressGateway |
| istio-system | -- | -- | Istio control plane |
| keycloak | disabled | yes | Identity provider |
| kyverno | -- | -- | Policy engine |
| matrix | disabled | yes | Synapse + Element + MAS |
| media | enabled | yes | Jellyfin, seedbox (qBittorrent, Sonarr, Radarr, Prowlarr) |
| monitoring | enabled | yes | Grafana + dashboards |
| netbox | enabled | yes | NetBox DCIM/IPAM |
| nfs-provisioner | -- | yes | NFS dynamic provisioner |
| observability | -- | -- | Kiali, Jaeger |
| overwatch-console | enabled | yes | Security operations dashboard |
| pangolin-internal | -- | yes | Traefik + Newt tunnel |
| reloader | -- | yes | Stakater Reloader |
| sentinel-ops | disabled | yes | Platform automation CronJobs |

## Kyverno Policy Enforcement

Kyverno enforces security policies across all namespaces:

### Enforce Mode (5 policies)

| Policy | Description |
|--------|-------------|
| `disallow-privileged-containers` | Blocks privileged containers |
| `require-resource-limits` | Requires CPU/memory limits on all containers |
| `require-run-as-nonroot` | Enforces `runAsNonRoot: true` |
| `restrict-image-registries` | Only allows images from `harbor.208.haist.farm` |
| `verify-image-signatures` | Requires cosign signature on all images |

### Audit Mode (1 policy)

| Policy | Description |
|--------|-------------|
| `require-labels` | Warns on pods missing standard labels |

### Image Signing Requirements

All container images deployed to the cluster must be cosign-signed:

```bash
# Sign an image (from iac-control)
cosign sign --key /etc/cosign/cosign.key --tlog-upload=false --yes \
  harbor.208.haist.farm/sentinel/<image>:<tag>
```

Air-gapped configuration in Kyverno:

- `verifyDigest: false` -- pods cannot reach Harbor on mgmt VLAN to resolve digests
- `rekor.ignoreTlog: true` -- no Rekor transparency log access
- `ctlog.ignoreSCT: true` -- no CT log verification

### Istio Exclusions

Kyverno preconditions exclude Istio-injected sidecar containers from policy
checks: `istio-init`, `istio-proxy`, `istio-validation`. The
`require-resource-limits` policy uses `foreach` (not `pattern`) to exclude
`istio-proxy` from memory limit checks.

## Istio Service Mesh

Istio provides STRICT mTLS across 10 meshed services.

### Meshed Services (istio-injection: enabled)

backstage, defectdojo, haists-website, harbor, homepage, jellyfin, media
(seedbox), monitoring (grafana), netbox, overwatch-console

### Unmeshed Services (via OKD Router)

keycloak, matrix, argocd, console, kiali, jaeger -- these route through
OKD Router port 443 instead of the Istio IngressGateway.

### Traffic Flow

```
Traefik (pangolin-proxy)
  -> HAProxy :8081 (iac-control)
    -> Istio IngressGateway NodePort :31080 (istio-ingress namespace)
      -> mTLS -> Target pod sidecar -> Application container
```

### Key Configuration

- IngressGateway: 2 replicas in `istio-ingress` namespace, NodePort 31080
- Default deny-all AuthorizationPolicies per namespace (21+ active)
- 5 port-level PERMISSIVE PeerAuthentication exceptions for unmeshed-to-meshed access

## Deploying New Workloads

### Step 1: Create Application Manifests

Create a directory under `overwatch-gitops/apps/<app-name>/` with Kubernetes
manifests (Deployment, Service, Route/VirtualService, PVC, etc.).

### Step 2: Create ArgoCD Application

Create `clusters/overwatch/apps/<app-name>-app.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: <app-name>
  namespace: openshift-gitops
spec:
  project: default
  source:
    repoURL: http://192.168.12.68/admin1/overwatch-gitops.git
    path: apps/<app-name>
    targetRevision: main
  destination:
    server: https://kubernetes.default.svc
    namespace: <target-namespace>
  syncPolicy:
    automated:
      selfHeal: true
      prune: true
```

### Step 3: Prepare Namespace

Ensure the target namespace has the required labels:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: <target-namespace>
  labels:
    argocd.argoproj.io/managed-by: openshift-gitops
    # Add for Istio mesh enrollment:
    istio-injection: enabled
```

The `argocd.argoproj.io/managed-by=openshift-gitops` label is required for
ArgoCD to manage resources in the namespace. It must also be listed in the
ArgoCD cluster secret `namespaces` field.

### Step 4: Sign Container Images

Before deploying, sign all container images with cosign:

```bash
cosign sign --key /etc/cosign/cosign.key --tlog-upload=false --yes \
  harbor.208.haist.farm/sentinel/<image>:<tag>
```

Unsigned images will be blocked by Kyverno's `verify-image-signatures` policy.

### Step 5: Push to Main

```bash
cd ~/overwatch-gitops
git add apps/<app-name>/ clusters/overwatch/apps/<app-name>-app.yaml
git commit -m "feat: deploy <app-name>"
git push origin main   # ArgoCD auto-syncs within minutes
```

### Step 6: Verify

```bash
# Check ArgoCD sync status
oc get application <app-name> -n openshift-gitops

# Check pods
oc get pods -n <target-namespace>
```

## External Secrets Operator

ESO v0.11.0 syncs secrets from HashiCorp Vault into Kubernetes Secrets:

| Property | Value |
|----------|-------|
| ClusterSecretStore | `vault-backend` |
| Auth Method | Kubernetes auth |
| Vault Roles | `external-secrets`, `sentinel-ops` |
| ExternalSecrets | 23 across 8 namespaces |

### Reloader Integration

Stakater Reloader v1.3.0 watches for Secret changes and triggers pod restarts
via annotations:

```yaml
metadata:
  annotations:
    reloader.stakater.com/auto: "true"
```

When ESO rotates a secret, Reloader detects the change and restarts affected
pods automatically.

## Node Resource Utilization

Current resource usage (as of 2026-03-04):

| Node | CPU Usage | CPU % | Memory Usage | Memory % |
|------|-----------|-------|-------------|----------|
| master-1 | 2974m | 25% | 15857 Mi | 52% |
| master-2 | 1021m | 8% | 16866 Mi | 55% |
| master-3 | 1856m | 16% | 18178 Mi | 60% |
