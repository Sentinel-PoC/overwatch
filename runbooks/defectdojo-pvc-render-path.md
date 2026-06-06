# DefectDojo PVC Render Path

**Issue:** OPS-102
**Date:** 2026-04-24
**Status:** Authoritative reference for OPS-103 (codify PV in Git), OPS-104 (audit ignoreDifferences), OPS-105 (pg_dump + restore)

---

## Purpose

Documents exactly how the `defectdojo-postgresql-data` PVC and its backing storage reach
the cluster — which Git files, which Helm chart, and which ArgoCD configuration are
involved. This is the audit trail required before the XFS migration for defectdojo
(OPS-82 parent chain).

The pattern is identical to the netbox render-path (OPS-93). Per-app deviations are
called out explicitly in each section.

---

## ArgoCD Application Spec: `defectdojo`

File: `clusters/overwatch/apps/defectdojo-app.yaml` (in `overwatch-gitops` repo)

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: defectdojo
  namespace: openshift-gitops
spec:
  project: default
  sources:
    - repoURL: https://raw.githubusercontent.com/DefectDojo/django-DefectDojo/helm-charts
      chart: defectdojo
      targetRevision: 1.9.12
      helm:
        valueFiles:
          - $values/clusters/overwatch/apps/defectdojo/values.yaml
        parameters:
          - name: "django.image.registry"
            value: "harbor.208.haist.farm"
          - name: "django.image.repository"
            value: "sentinel/defectdojo-django"
          - name: "nginx.image.registry"
            value: "harbor.208.haist.farm"
          - name: "nginx.image.repository"
            value: "sentinel/defectdojo-nginx"
          - name: "celery.image.registry"
            value: "harbor.208.haist.farm"
          - name: "celery.image.repository"
            value: "sentinel/defectdojo-django"
          - name: "initializer.image.registry"
            value: "harbor.208.haist.farm"
          - name: "initializer.image.repository"
            value: "sentinel/defectdojo-django"
    - repoURL: https://forgejo.208.haist.farm/sentinel-admin/overwatch-gitops.git
      targetRevision: main
      ref: values
    - repoURL: https://forgejo.208.haist.farm/sentinel-admin/overwatch-gitops.git
      targetRevision: main
      path: apps/defectdojo
  destination:
    server: https://kubernetes.default.svc
    namespace: defectdojo
```

This is a **three-source Application**. Each source is described below.

---

## Multi-Source Breakdown

### Source 1 — Helm Chart (renders DefectDojo app objects, NOT the PG PVC)

| Field | Value |
|-------|-------|
| repoURL | `https://raw.githubusercontent.com/DefectDojo/django-DefectDojo/helm-charts` |
| chart | `defectdojo` |
| targetRevision | `1.9.12` |
| valueFiles | `$values/clusters/overwatch/apps/defectdojo/values.yaml` |

**What this source renders:** All Helm-managed objects — the DefectDojo Django Deployment,
Celery beat and worker Deployments, Nginx Deployment, Valkey StatefulSet (cloudpirates
subchart), initializer Job, Services, ConfigMaps, and Istio resources.

**What this source does NOT render:** The PostgreSQL PVC (`defectdojo-postgresql-data`).

Evidence — `clusters/overwatch/apps/defectdojo/values.yaml` lines 134-136:

```yaml
# -- PostgreSQL: DISABLED bundled subchart, using raw manifests instead
postgresql:
  enabled: false
```

Because `postgresql.enabled: false`, the defectdojo 1.9.12 chart skips the entire
bundled PostgreSQL subchart. No `defectdojo-postgresql-data` PVC is templated from
the Helm chart.

**Per-app quirk (vs. netbox):** The defectdojo chart is sourced from GitHub raw
(`DefectDojo/django-DefectDojo/helm-charts`), not a Helm repository endpoint. This
does not affect the render path for the PVC — the postgresql subchart disable is
identical in effect.

**Additional chart parameters override:** Several `parameters:` blocks in the Application
spec redirect image pulls to `harbor.208.haist.farm/sentinel/`. These are chart-level
overrides applied at render time by ArgoCD; they do not affect the PVC.

### Source 2 — Values Reference (no Kubernetes objects rendered)

| Field | Value |
|-------|-------|
| repoURL | `https://forgejo.208.haist.farm/sentinel-admin/overwatch-gitops.git` |
| targetRevision | `main` |
| ref | `values` |

**Purpose:** Provides the `$values` alias used by Source 1's `valueFiles` field. ArgoCD
resolves `$values/clusters/overwatch/apps/defectdojo/values.yaml` by fetching from this
ref. It does NOT apply any Kubernetes manifests itself.

### Source 3 — Raw Manifests (renders PG PVC and all PostgreSQL objects)

| Field | Value |
|-------|-------|
| repoURL | `https://forgejo.208.haist.farm/sentinel-admin/overwatch-gitops.git` |
| targetRevision | `main` |
| path | `apps/defectdojo` |

**What this source renders:** All 9 raw YAML files in `apps/defectdojo/`:

| File | Object Kind(s) | Name(s) |
|------|----------------|---------|
| `authorization-policies.yaml` | AuthorizationPolicy | defectdojo namespace policies |
| `external-secrets.yaml` | ExternalSecret (x5) | defectdojo-credentials, defectdojo-postgresql-specific, defectdojo-postgresql, defectdojo-valkey-specific, defectdojo-extrasecrets, harbor-pull-secret |
| `network-policies.yaml` | NetworkPolicy | defectdojo namespace policies |
| `postgresql-deployment.yaml` | Deployment | `defectdojo-postgresql` (postgres:16.13-alpine3.23) |
| `postgresql-pvc.yaml` | PersistentVolumeClaim | `defectdojo-postgresql-data` |
| `postgresql-service.yaml` | Service | `defectdojo-postgresql` |
| `rbac.yaml` | ServiceAccount (x2) + RoleBinding (x2) | defectdojo, defectdojo-postgresql (anyuid SCC) |
| `route.yaml` | Route | `defectdojo` (host: defectdojo.208.haist.farm) |
| `virtual-service.yaml` | VirtualService | Istio routing |

**No `kustomization.yaml` exists in `apps/defectdojo/`.** ArgoCD applies all files as a
raw directory sync. There is no Kustomize overlay.

---

## The PVC: `defectdojo-postgresql-data`

### Git source (authoritative desired state)

File: `apps/defectdojo/postgresql-pvc.yaml` in `overwatch-gitops` repo:

```yaml
---
# DefectDojo PostgreSQL data PVC — binds statically to the retained iSCSI PV
# `defectdojo-pg-iscsi` (15Gi, iqn.2026-03.farm.haist:okd-defectdojo-pg).
#
# Production data was provisioned on a manually-attached iSCSI LUN for
# performance. Drift was in git (manifest said nfs-storage) not cluster.
# Corrected during the OPS-206 recovery incident (2026-04-17) so future
# syncs don't try to dynamically provision a new empty NFS volume and
# disconnect the real data. The iSCSI PV is reclaimPolicy: Retain, so the
# data survived the accidental PVC deletion that triggered this fix.
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: defectdojo-postgresql-data
  namespace: defectdojo
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ""
  volumeName: defectdojo-pg-iscsi
  resources:
    requests:
      storage: 15Gi
```

This is the ONLY Git source that declares `defectdojo-postgresql-data`. The Helm chart
(Source 1) renders no PVC with this name.

**Note:** Unlike the netbox PVC (which still had `storageClassName: nfs-storage` in Git at
the time of OPS-93), the defectdojo PVC was already corrected in Git during the OPS-206
recovery on 2026-04-17. It explicitly sets `storageClassName: ""` and `volumeName:
defectdojo-pg-iscsi`. There is no Git/live divergence on these fields.

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
  volumeName: defectdojo-pg-iscsi
status:
  accessModes:
  - ReadWriteOnce
  capacity:
    storage: 15Gi
  phase: Bound
```

Live PVC is **Bound** — unlike netbox which was Pending at OPS-93 time. The PVC is
actively bound to the iSCSI PV (`defectdojo-pg-iscsi`) with live data present.

---

## Live PV State: `defectdojo-pg-iscsi` (NOT in Git)

**This PV was applied live during the OPS-206 recovery incident (2026-04-17) and is NOT
currently represented in the Git repository.** OPS-103 is scoped to codify it in Git.

### Full PV spec (fetched 2026-04-24 via `kubectl get pv defectdojo-pg-iscsi -o yaml`)

```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  labels:
    app: defectdojo-postgresql
  name: defectdojo-pg-iscsi
  uid: ed5b76f3-495e-402e-8b4a-97d4087608ca
spec:
  accessModes:
  - ReadWriteOnce
  capacity:
    storage: 15Gi
  claimRef:
    apiVersion: v1
    kind: PersistentVolumeClaim
    name: defectdojo-postgresql-data
    namespace: defectdojo
    uid: 13e10c74-1577-4e24-8e2e-1c2a7233475e
  iscsi:
    fsType: ext4
    iqn: iqn.2026-03.farm.haist:okd-defectdojo-pg
    iscsiInterface: default
    lun: 0
    targetPortal: 192.168.12.205:3260
  persistentVolumeReclaimPolicy: Retain
  volumeMode: Filesystem
status:
  lastPhaseTransitionTime: "2026-04-17T20:49:07Z"
  phase: Bound
```

### iSCSI Connection Details

| Field | Value |
|-------|-------|
| IQN | `iqn.2026-03.farm.haist:okd-defectdojo-pg` |
| Target portal | `192.168.12.205:3260` |
| LUN | `0` |
| iSCSI interface | `default` |
| fsType | `ext4` (will change to `xfs` post-migration) |
| Reclaim policy | `Retain` |
| Capacity | `15Gi` |
| Bound to | `defectdojo/defectdojo-postgresql-data` |
| Phase | `Bound` (live data present) |

**TrueNAS zvol path:** `SSD/iscsi-okd/defectdojo-pg` (inferred from platform naming
pattern; target portal 192.168.12.205 is the TrueNAS SCALE host).

**OPS-103 baseline:** The Git manifest that OPS-103 creates should match this spec exactly
for the pre-migration state (fsType: ext4), then the XFS migration chain (OPS-104/105)
updates fsType to xfs after wipefs + mkfs.xfs completes.

---

## `ignoreDifferences` Block

File: `clusters/overwatch/apps/defectdojo-app.yaml`

The full `ignoreDifferences` block (lines 42-85):

```yaml
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
  - group: apps
    kind: StatefulSet
    jqPathExpressions:
      - .spec.volumeClaimTemplates
      - .spec.persistentVolumeClaimRetentionPolicy
      - .spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"]
  - group: batch
    kind: Job
    jqPathExpressions:
      - .spec.selector.matchLabels["batch.kubernetes.io/controller-uid"]
      - .spec.selector.matchLabels["controller-uid"]
      - .spec.template.metadata.labels["batch.kubernetes.io/controller-uid"]
      - .spec.template.metadata.labels["batch.kubernetes.io/job-name"]
      - .spec.template.metadata.labels["controller-uid"]
      - .spec.template.metadata.labels["job-name"]
      - .status
```

### PVC `ignoreDifferences` (lines 43-48)

The four jqPathExpressions for `kind: PersistentVolumeClaim`:

| jqPathExpression | Reason |
|-----------------|--------|
| `.spec.resources.requests.storage` | Kubernetes may expand this field at bind time |
| `.spec.storageClassName` | Immutable after binding; set to `""` live but chart value could drift |
| `.spec.volumeName` | Immutable after binding; prevents ArgoCD from clearing the static binding |
| `.spec.selector` | Immutable; prevents ArgoCD from patching selector back to nil |

Combined with `syncOptions: - RespectIgnoreDifferences=true`, ArgoCD omits these fields
from server-side apply patches and does not revert the static PV binding on sync.

**Assessment for OPS-104 (audit/narrow):** Since Git already has `storageClassName: ""`
and `volumeName: defectdojo-pg-iscsi` matching live state, narrowing to
`.spec.resources.requests.storage` only (netbox OPS-95 pattern) may be warranted. However,
the current four-field block causes no harm and the Planner noted "narrowing likely not
needed." OPS-104 scope is to confirm and decide.

### Additional `ignoreDifferences` entries (defectdojo-specific vs. netbox)

The defectdojo Application has **two extra `ignoreDifferences` groups** not present in the
netbox Application:

1. **StatefulSet** (lines 54-58): Covers Valkey StatefulSet volumeClaimTemplates (immutable
   at runtime), PVC retention policy, and restartedAt annotation. Required because defectdojo
   uses a Valkey subchart that renders a StatefulSet.

2. **Job** (lines 60-76): Covers the defectdojo-initializer Job. The `batch.kubernetes.io/
   controller-uid` and `controller-uid` labels are assigned dynamically by the Job controller
   and cannot be represented in Git. The Job is tracked as a normal ArgoCD resource (not a
   Helm hook) because `disableHooks: true` is set in values.yaml. The `.status` ignore
   prevents ArgoCD from diffing Job completion status.

---

## Admin Role and Database Configuration

Source: `apps/defectdojo/postgresql-deployment.yaml`, env block of the `postgresql` container.

```yaml
env:
  - name: POSTGRES_DB
    value: defectdojo
  - name: POSTGRES_USER
    value: defectdojo
  - name: POSTGRES_PASSWORD
    valueFrom:
      secretKeyRef:
        name: defectdojo-postgresql
        key: password
  - name: PGDATA
    value: /var/lib/postgresql/data/pgdata
```

| Setting | Value |
|---------|-------|
| `POSTGRES_USER` | `defectdojo` |
| `POSTGRES_DB` | `defectdojo` |
| `PGDATA` | `/var/lib/postgresql/data/pgdata` |

**The admin role is `defectdojo` — NOT `postgres`.** This differs from default PostgreSQL
images where the superuser is `postgres`. The defectdojo deployment uses `POSTGRES_USER=
defectdojo` which is granted superuser privileges at container init time.

The readiness and liveness probes confirm the role and database:
```yaml
command:
  - /bin/sh
  - -c
  - pg_isready -U defectdojo -d defectdojo
```

### `pg_dump` invocation for OPS-105

```bash
kubectl exec -n defectdojo deploy/defectdojo-postgresql -- \
  pg_dump -U defectdojo -d defectdojo --no-password
```

Piped to file via `kubectl exec` output redirect, or via `kubectl cp` after writing inside
pod. The pattern validated by OPS-81 (netbox) is:

```bash
# Step 1: dump inside pod to /tmp
kubectl exec -n defectdojo deploy/defectdojo-postgresql -- \
  pg_dump -U defectdojo -d defectdojo -f /tmp/defectdojo-pg-dump.sql

# Step 2: copy out to iac-control
kubectl cp defectdojo/defectdojo-postgresql-7944687bd4-94c8l:/tmp/defectdojo-pg-dump.sql \
  /home/ubuntu/defectdojo-pg-dumpall.sql

# Step 3: scp to workstation
scp ubuntu@192.168.12.210:/home/ubuntu/defectdojo-pg-dumpall.sql \
  /home/koiakoia/plane-recovery-backup/defectdojo-pg-dumpall.sql

# Step 4: SHA256 verify both copies before any wipefs
sha256sum /home/koiakoia/plane-recovery-backup/defectdojo-pg-dumpall.sql
# Post both hashes in Plane CHANGE note before proceeding
```

Restore command (after mkfs.xfs, after scaling back to 1):
```bash
kubectl cp /home/ubuntu/defectdojo-pg-dumpall.sql \
  defectdojo/defectdojo-postgresql-7944687bd4-94c8l:/tmp/defectdojo-pg-dump.sql

kubectl exec -n defectdojo deploy/defectdojo-postgresql -- \
  psql -U defectdojo -d defectdojo -f /tmp/defectdojo-pg-dump.sql
```

**Note per OPS-97 lesson:** If the container ran initdb against the fresh XFS volume before
the restore, `psql -f` may encounter `DROP DATABASE` / `CREATE DATABASE` conflicts. Handle
by connecting to `postgres` database and dropping/recreating `defectdojo` first, or by
using `--clean --if-exists` flags with `pg_dump` at dump time.

---

## Current Pod Location and Health

### Pod location (SCSI PR holder)

Observed 2026-04-24:

```
NAME                                        READY   STATUS    RESTARTS   AGE   NODE
defectdojo-postgresql-7944687bd4-94c8l      1/1     Running   0          17h   master-2.overwatch.haist.farm
defectdojo-django-6496cf9474-s9fd4          3/3     Running   3          34h   master-2.overwatch.haist.farm
defectdojo-celery-beat-5c45c78898-rltxt     2/2     Running   2          34h   master-2.overwatch.haist.farm
defectdojo-celery-worker-5648c87744-2dzdc   2/2     Running   2          34h   master-2.overwatch.haist.farm
defectdojo-valkey-0                         2/2     Running   2          37h   master-2.overwatch.haist.farm
```

**All defectdojo pods are on master-2.** The iSCSI PV uses a ReadWriteOnce access mode with
a direct iSCSI initiator connection (no CSI driver); the SCSI Persistent Reservation is held
by the node that first attached the LUN, which is master-2. Any wipefs/mkfs operation for
OPS-105 must run from master-2.

### Table count

```sql
SELECT count(*) FROM information_schema.tables WHERE table_schema='public';
-- Result: 204
```

Observed 2026-04-24. Matches Planner baseline. This is the dump baseline count — OPS-105
restore verification requires count = 204 after restore.

### HTTP health

```bash
# From within defectdojo-django pod (nginx container), with Host header:
curl -sk -o /dev/null -w '%{http_code}' \
  -H 'Host: defectdojo.208.haist.farm' http://localhost:8080/
# Result: 302
```

HTTP 302 on `/` observed 2026-04-24. This matches the Planner's pre-migration baseline.
The 302 redirects unauthenticated requests to `/login` — expected Django behavior.

The Route at `clusters/overwatch/apps/defectdojo/` (via Source 3: `apps/defectdojo/route.yaml`)
exposes `defectdojo.208.haist.farm` with TLS edge termination and `insecureEdgeTerminationPolicy:
Redirect`. Direct HTTP to the ClusterIP returns the same 302.

---

## Summary Table

| Question | Answer |
|----------|--------|
| Which source renders `defectdojo-postgresql-data` PVC? | Source 3 only: `path: apps/defectdojo` in overwatch-gitops |
| Does the Helm chart render this PVC? | No. `postgresql.enabled: false` in values.yaml disables the subchart entirely. |
| Is there a kustomization.yaml in apps/defectdojo? | No. Raw directory sync. |
| Is `defectdojo-pg-iscsi` PV in Git? | No. Applied live during OPS-206 on 2026-04-17. OPS-103 creates it in Git. |
| iSCSI IQN | `iqn.2026-03.farm.haist:okd-defectdojo-pg` |
| iSCSI target portal | `192.168.12.205:3260` |
| iSCSI LUN | `0` |
| Current fsType | `ext4` (target: `xfs` post-migration) |
| Reclaim policy | `Retain` |
| PVC phase | `Bound` (live data, 204 public tables) |
| Admin role | `defectdojo` (NOT `postgres`) |
| Database name | `defectdojo` |
| pg_dump command | `pg_dump -U defectdojo -d defectdojo` |
| Pod location | master-2.overwatch.haist.farm (SCSI PR holder) |
| Table count | 204 (observed 2026-04-24) |
| HTTP 302 on `/` | Confirmed (nginx container, Host: defectdojo.208.haist.farm) |
| ignoreDifferences PVC fields | 4: storage, storageClassName, volumeName, selector |

---

## NIST Controls Supported

| Control | How |
|---------|-----|
| CM-3 | All PVC/PV changes tracked via Plane issues and GitLab MRs |
| CM-4 | This runbook documents impact analysis before OPS-103/104/105 changes |
| AU-6 | Git blame + Plane issue trail provides full audit trail |
| CM-5 | Token-gated session; read-only investigation; no cluster writes |
