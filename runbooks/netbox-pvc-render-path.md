# netbox PVC Render Path

**Issue:** OPS-93  
**Date:** 2026-04-24  
**Status:** Authoritative reference for OPS-94 (add PV manifest) and OPS-95 (align PVC fields)

---

## Purpose

Documents exactly how `oc -n netbox get pvc netbox-postgresql-data -o yaml` produces its
current output — which Git files, which Helm chart, and which ArgoCD configuration are
involved. This is the audit trail required before modifying the PVC manifest for the XFS
migration (OPS-94, OPS-95).

---

## ArgoCD Application Spec: `netbox`

File: `clusters/overwatch/apps/netbox-app.yaml` (in `overwatch-gitops` repo)

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: netbox
  namespace: openshift-gitops
spec:
  project: default
  sources:
    - repoURL: https://charts.netbox.oss.netboxlabs.com/
      chart: netbox
      targetRevision: 7.4.8
      helm:
        valueFiles:
          - $values/clusters/overwatch/apps/netbox/values.yaml
    - repoURL: https://forgejo.208.haist.farm/sentinel-admin/overwatch-gitops.git
      targetRevision: main
      ref: values
    - repoURL: https://forgejo.208.haist.farm/sentinel-admin/overwatch-gitops.git
      targetRevision: main
      path: apps/netbox
  destination:
    server: https://kubernetes.default.svc
    namespace: netbox
  ignoreDifferences:
    - kind: PersistentVolumeClaim
      jqPathExpressions:
        - .spec.resources.requests.storage
        - .spec.storageClassName
        - .spec.volumeName
        - .spec.selector
    - group: external-secrets.io
      kind: ExternalSecret
      jqPathExpressions:
        - .spec.data[]?.remoteRef
        - .spec.target
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
      - RespectIgnoreDifferences=true
```

This is a **multi-source Application** with three distinct sources. Each source is described below.

---

## Multi-Source Breakdown

### Source 1 — Helm Chart (renders NetBox app objects, NOT the PG PVC)

| Field | Value |
|-------|-------|
| repoURL | `https://charts.netbox.oss.netboxlabs.com/` |
| chart | `netbox` |
| targetRevision | `7.4.8` |
| valueFiles | `$values/clusters/overwatch/apps/netbox/values.yaml` |

**What this source renders:** All Helm-managed objects — the NetBox Deployment, worker
Deployment, housekeeping CronJob, Valkey StatefulSet, Services, ConfigMaps, and the
media-uploads PVC (via `persistence.enabled: true` / `storageClass: nfs-storage`).

**What this source does NOT render:** The PostgreSQL PVC (`netbox-postgresql-data`).

Evidence — `clusters/overwatch/apps/netbox/values.yaml` lines 162-164:

```yaml
# -- PostgreSQL: DISABLED bundled subchart, using raw manifests instead
postgresql:
  enabled: false
```

Because `postgresql.enabled: false`, the `netbox-community/netbox` 7.4.8 chart skips
the entire bundled PostgreSQL subchart. No `netbox-postgresql-data` PVC is templated
from the Helm chart. The chart does not render any `postgres:` prefixed objects.

### Source 2 — Values Reference (no Kubernetes objects rendered)

| Field | Value |
|-------|-------|
| repoURL | `https://forgejo.208.haist.farm/sentinel-admin/overwatch-gitops.git` |
| targetRevision | `main` |
| ref | `values` |

**Purpose:** This source only provides the `$values` alias used by Source 1's `valueFiles`
field. ArgoCD resolves `$values/clusters/overwatch/apps/netbox/values.yaml` by fetching
that path from this ref. It does NOT apply any Kubernetes manifests itself.

### Source 3 — Raw Manifests (renders PG PVC and all PostgreSQL objects)

| Field | Value |
|-------|-------|
| repoURL | `https://forgejo.208.haist.farm/sentinel-admin/overwatch-gitops.git` |
| targetRevision | `main` |
| path | `apps/netbox` |

**What this source renders:** All 9 raw YAML files in `apps/netbox/`:

| File | Object Kind | Name |
|------|-------------|------|
| `authorization-policies.yaml` | AuthorizationPolicy | netbox namespace policies |
| `external-secret.yaml` | ExternalSecret | netbox credentials from ESO |
| `network-policies.yaml` | NetworkPolicy | netbox namespace policies |
| `postgresql-deployment.yaml` | Deployment | `netbox-postgresql` (postgres:16.13-alpine3.23) |
| `postgresql-pvc.yaml` | PersistentVolumeClaim | `netbox-postgresql-data` |
| `postgresql-service.yaml` | Service | `netbox-postgresql` |
| `rbac.yaml` | ServiceAccount / RoleBinding | netbox RBAC |
| `route.yaml` | Route | `netbox.208.haist.farm` |
| `virtual-service.yaml` | VirtualService | Istio routing |

**No `kustomization.yaml` exists in `apps/netbox/`.** ArgoCD applies all files as a raw
directory sync. There is no Kustomize overlay; the path ref is not a Kustomize app.
Confirmed by directory listing — `apps/netbox/` contains only the 9 manifest files above
plus `values.yaml` (consumed by Source 1, not applied as a manifest).

---

## The PVC Specifically: `netbox-postgresql-data`

### Git source (authoritative desired state)

File: `apps/netbox/postgresql-pvc.yaml` in `overwatch-gitops` repo:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: netbox-postgresql-data
  namespace: netbox
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: nfs-storage
  resources:
    requests:
      storage: 15Gi
```

This is the ONLY Git source that declares `netbox-postgresql-data`. The Helm chart
(Source 1) renders no PVC with this name.

### Live cluster state (observed 2026-04-24)

```yaml
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 15Gi
  storageClassName: ""
  volumeMode: Filesystem
  volumeName: netbox-pg-iscsi
status:
  phase: Pending
```

The live PVC diverges from Git on three fields:
- `storageClassName`: Git has `nfs-storage`; live has `""` (empty string)
- `volumeName`: Git has none; live has `netbox-pg-iscsi`
- `selector`: Git has none; live has none (no active divergence here currently)

---

## How the Divergence Is Suppressed by ArgoCD

### `ignoreDifferences` block (lines 24-30 of `netbox-app.yaml`)

```yaml
ignoreDifferences:
  - kind: PersistentVolumeClaim
    jqPathExpressions:
      - .spec.resources.requests.storage
      - .spec.storageClassName
      - .spec.volumeName
      - .spec.selector
```

Combined with `syncOptions: - RespectIgnoreDifferences=true`, this configuration instructs
ArgoCD to:

1. **Exclude** the four listed jqPathExpressions when computing the diff between desired
   (Git) and live (cluster) state for any PVC.
2. **Not overwrite** those fields when performing a server-side apply sync.

With `RespectIgnoreDifferences=true`, the ignored fields are also omitted from the
server-side apply patch, meaning ArgoCD will NOT reset `storageClassName` back to
`nfs-storage` or clear `volumeName: netbox-pg-iscsi` on sync.

### Git blame for `ignoreDifferences` additions

The PVC block was introduced and extended across two commits:

| Commit | SHA | Author | Date | What was added |
|--------|-----|--------|------|----------------|
| First PVC ignore | `89b3b59` | Jim Haist | 2026-03-28 | `.spec.resources.requests.storage` + ExternalSecret ignores + `RespectIgnoreDifferences=true` |
| Extended PVC ignore | `3c8d191` | Jim Haist | 2026-03-31 | `.spec.storageClassName`, `.spec.volumeName`, `.spec.selector` |

Commit `3c8d191` message: `[OPS-113] Fix ArgoCD PVC and ClusterPolicy drift — add
ignoreDifferences for immutable PVC fields`. This was a fleet-wide fix applied to
backstage, defectdojo, keycloak, matrix, netbox, and plane simultaneously, driven by
ArgoCD OutOfSync errors on immutable PVC fields after the iSCSI migration began.

---

## How `kubectl get pvc netbox-postgresql-data` Gets Its Values

Sequence of events that produced the current live state:

1. ArgoCD applied `apps/netbox/postgresql-pvc.yaml` from overwatch-gitops Source 3 during
   initial netbox deployment (commit `c7cbdfc`, 2026-02-12 — original netbox deploy).

2. The PVC was initially bound to an NFS-backed PV (matching `storageClassName: nfs-storage`).

3. During the iSCSI migration work (OPS-81 scope), an operator manually patched the PVC
   or deleted/re-created it with `storageClassName: ""` and `volumeName: netbox-pg-iscsi`
   to pre-bind it to the iSCSI PV.

4. ArgoCD self-heal would normally revert these fields on the next sync. Commits `89b3b59`
   and `3c8d191` (OPS-113, 2026-03-28 and 2026-03-31) added the `ignoreDifferences` block
   to prevent this revert. After those commits, ArgoCD computes sync diff excluding the
   four PVC fields and does not patch them back.

5. The live PVC therefore retains `storageClassName: ""` and `volumeName: netbox-pg-iscsi`
   regardless of what `apps/netbox/postgresql-pvc.yaml` says on those fields.

6. The PVC is currently `Pending` because the referenced PV (`netbox-pg-iscsi`) does not
   yet exist in the cluster (it will be created by OPS-94).

---

## Authoritative Source for OPS-94 and OPS-95

### OPS-94 (add PV manifest with fsType: xfs)

The new PV manifest should be added as a new file in:

```
apps/netbox/postgresql-pv.yaml   (in overwatch-gitops repo)
```

This places it in Source 3 (path: `apps/netbox`), which is the raw-manifest source
already managing all netbox PostgreSQL objects. ArgoCD will apply it on next sync.

The PV manifest must declare:
- `metadata.name: netbox-pg-iscsi` (to match `spec.volumeName` in the live PVC)
- `spec.csi.fsType: xfs` (or `spec.iscsi.fsType: xfs` depending on CSI driver used)
- `spec.claimRef` pointing to `netbox/netbox-postgresql-data` to prevent accidental binding

### OPS-95 (align PVC fields in Git)

The file to modify is:

```
apps/netbox/postgresql-pvc.yaml   (in overwatch-gitops repo)
```

Changes needed per OPS-95 scope:
- `storageClassName: nfs-storage` -> `storageClassName: ""`
- Add `volumeName: netbox-pg-iscsi`
- Keep `storage: 15Gi` and `accessModes: ReadWriteOnce`

After OPS-95 merges, Git will agree with live state on `storageClassName` and
`volumeName`. OPS-95 may also narrow the `ignoreDifferences` block (removing
`.spec.storageClassName` and `.spec.volumeName` entries that will no longer be needed).

---

## Summary Table

| Question | Answer |
|----------|--------|
| Which source renders `netbox-postgresql-data` PVC? | Source 3 only: `path: apps/netbox` in overwatch-gitops |
| Does the Helm chart render this PVC? | No. `postgresql.enabled: false` in values.yaml disables the subchart entirely. |
| Is there a kustomization.yaml in apps/netbox? | No. Raw directory sync. |
| Why does live have `storageClassName: ""`? | Operator-patched during iSCSI migration; suppressed from revert by `ignoreDifferences` |
| Why does live have `volumeName: netbox-pg-iscsi`? | Same: operator-patched; suppressed by `ignoreDifferences` + `RespectIgnoreDifferences=true` |
| When was `ignoreDifferences` added? | Initial: 2026-03-28 (commit `89b3b59`, OPS-113); extended: 2026-03-31 (commit `3c8d191`, OPS-113) |
| File OPS-95 modifies | `apps/netbox/postgresql-pvc.yaml` in overwatch-gitops |
| File OPS-94 creates | `apps/netbox/postgresql-pv.yaml` in overwatch-gitops (new file) |

---

## NIST Controls Supported

| Control | How |
|---------|-----|
| CM-3 | All PVC/PV changes tracked via Plane issues and GitLab MRs |
| CM-4 | This runbook documents the impact analysis before OPS-94/95 changes |
| AU-6 | Git blame provides full audit trail of ignoreDifferences additions |
