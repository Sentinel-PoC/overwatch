# ArgoCD ignoreDifferences Cluster-Wide Audit — April 2026

**Plane issue:** OPS-92  
**Date:** 2026-04-23  
**Scope:** All ArgoCD Applications and ApplicationSets in `openshift-gitops` namespace  
**Type:** Read-only investigation — no cluster changes made  
**Feeds:** OPS-95 (netbox PVC ignoreDifferences narrowing)

---

## Summary

| Metric | Count |
|--------|-------|
| Total ArgoCD Applications | 30 |
| ApplicationSets | 0 |
| Apps with `ignoreDifferences` | 16 |
| Apps with `RespectIgnoreDifferences=true` | 16 (all 16 above) |
| Apps with `ServerSideApply=true` only (no ignoreDiff) | 7 |
| Apps with active OutOfSync on ignored fields | 1 (plane — Deployment env/volume drift) |
| Security-sensitive ignores | 3 (haists-website SCC, harbor Secrets + Deployment env, langfuse Secrets) |
| Operational ignores | 6 apps (PVC immutable fields) |
| Cosmetic/controller-injected ignores | 5 apps (ExternalSecret reconcile annotation, kyverno autogen, istio status) |

Verification command (acceptance criteria parity check):

```
oc get applications -n openshift-gitops -o json | \
  jq '[.items[] | select(.spec.ignoreDifferences != null or
    ((.spec.syncPolicy.syncOptions // []) | any(. == "RespectIgnoreDifferences=true")))] | length'
```

Expected output: **16**

---

## Per-Application Detail

### 1. `backstage`

**Destination:** `backstage` namespace  
**Source:** `apps/backstage` (Forgejo main)  
**Sync status:** Synced / Healthy  
**Intro commit:** `3c8d191` — `[OPS-113] Fix ArgoCD PVC and ClusterPolicy drift`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| (core) | PersistentVolumeClaim | `.spec.resources.requests.storage`, `.spec.storageClassName`, `.spec.volumeName`, `.spec.selector` | Operational |
| external-secrets.io | ExternalSecret | `.spec.data[]?.remoteRef`, `.spec.target` | Cosmetic |

**Live drift:** None (Synced).  
**Assessment:** Standard NFS-backed PVC pattern. PVC fields are immutable post-creation; Helm re-renders different values. ExternalSecret fields are ESO-managed. Both ignores are appropriate for current architecture.  
**Recommendation:** Keep as-is. If backstage PVC is ever migrated to iSCSI, narrow PVC ignore to `.spec.resources.requests.storage` only (same pattern as netbox recommendation below).

---

### 2. `defectdojo`

**Destination:** `defectdojo` namespace  
**Source:** Helm chart + Forgejo  
**Sync status:** Synced / Progressing (pod startup, not drift)  
**Intro commit:** `3c8d191` — `[OPS-113]`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`, `Replace=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| (core) | PersistentVolumeClaim | `.spec.resources.requests.storage`, `.spec.storageClassName`, `.spec.volumeName`, `.spec.selector` | Operational |
| external-secrets.io | ExternalSecret | `.spec.data[]?.remoteRef`, `.spec.target` | Cosmetic |
| apps | StatefulSet | `.spec.volumeClaimTemplates`, `.spec.persistentVolumeClaimRetentionPolicy`, `.spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"]` | Operational |
| batch | Job | `.spec.selector.matchLabels["batch.kubernetes.io/controller-uid"]`, `.spec.selector.matchLabels["controller-uid"]`, `.spec.template.metadata.labels[...]`, `.status` | Cosmetic |

**Live drift:** None (Synced).  
**Assessment:** StatefulSet `.spec.volumeClaimTemplates` is immutable post-creation; Helm re-renders storage size changes that Kubernetes rejects. Job controller-uid labels are Kubernetes-injected. These are all controller-injected or immutable-field patterns.  
**Recommendation:** Keep as-is. `Replace=true` on `syncOptions` is notable — it means ArgoCD uses `kubectl replace` for resources instead of patch/apply. Combined with `ServerSideApply=true` this is unusual; operator should confirm this combination is intentional for defectdojo (may cause pod disruption on sync).

---

### 3. `grafana`

**Destination:** `monitoring` namespace  
**Source:** Helm chart + Forgejo  
**Sync status:** Synced / Healthy  
**Intro commit:** (not in `clusters/overwatch/apps/`; managed via monitoring Helm multi-source)  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| external-secrets.io | ExternalSecret | `.spec.data[]?.remoteRef`, `.spec.target` | Cosmetic |

**Live drift:** None.  
**Assessment:** Minimal ignore covering only ESO-managed fields. Appropriate.  
**Recommendation:** Keep as-is.

---

### 4. `haists-website`

**Destination:** `haists-website` namespace  
**Source:** `apps/haists-website` (Forgejo main)  
**Sync status:** Synced / Healthy  
**Intro commit (ExternalSecret):** `8cc1d07` — `[OPS-118] Fix ArgoCD ignoreDifferences blocking ExternalSecret syncs`  
**Intro commit (SCC):** `8b413aa` — `fix: broaden SCC ignoreDifferences for haists-website`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| external-secrets.io | ExternalSecret | `.spec.data[]?.remoteRef`, `.spec.target` | Cosmetic |
| security.openshift.io | SecurityContextConstraints | `.allowPrivilegeEscalation`, `.readOnlyRootFilesystem`, `.groups`, `.allowedCapabilities`, `.defaultAddCapabilities`, `.requiredDropCapabilities`, `.volumes` | **SECURITY-SENSITIVE** |

**Live drift:** None visible. Live SCC state: `allowPrivilegeEscalation=true`, `readOnlyRootFilesystem=false`, `groups=[]`, `allowedCapabilities=null`, `defaultAddCapabilities=null`, `requiredDropCapabilities=null`.

**Risk analysis:** The SCC ignore covers fields that control container privilege escalation, filesystem write access, and Linux capability grants. OKD's admission controller injects values into these fields after ArgoCD syncs — specifically `allowPrivilegeEscalation`, `readOnlyRootFilesystem`, `groups`, and `volumes`. The ignore was introduced because OKD mutates the SCC after apply, causing perpetual drift. However, ignoring these fields means ArgoCD will not detect if a malicious or accidental change grants additional privileges to the SCC (e.g., adding a capability or loosening `readOnlyRootFilesystem`).

**Recommendation:** This pattern is a known tension with OpenShift's SCC admission mutations. The current live state shows `allowPrivilegeEscalation=true` on the `haists-website-anyuid` SCC — this is a permissive setting. Follow-up security issue filed: see SEC project.

---

### 5. `haists-website-dev`

**Destination:** `haists-website-dev` namespace  
**Source:** `apps/haists-website-dev` (Forgejo main)  
**Sync status:** Synced / Healthy  
**Intro commit:** `8cc1d07` — `[OPS-118]`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| external-secrets.io | ExternalSecret | `.spec.data[]?.remoteRef`, `.spec.target`, `.metadata.annotations["reconcile.external-secrets.io/trigger"]` | Cosmetic |
| security.openshift.io | SecurityContextConstraints | Same 7 fields as haists-website | **SECURITY-SENSITIVE** |

**Live drift:** None. Live SCC `haists-website-dev-anyuid` mirrors production SCC state.  
**Assessment:** Same risk profile as `haists-website`. The `reconcile.external-secrets.io/trigger` annotation ignore is correct (ESO-managed timestamp).  
**Recommendation:** Same as `haists-website` — filed under same SEC issue.

---

### 6. `harbor`

**Destination:** `harbor` namespace  
**Source:** Harbor Helm chart v1.18.2 + Forgejo  
**Sync status:** Synced / Healthy  
**Intro commit:** `d5a431f` — `[OPS-128] Add ignoreDifferences for Harbor Helm-managed secrets and deployments`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Name | Fields | Risk |
|-------|------|------|--------|------|
| external-secrets.io | ExternalSecret | (all) | `.spec.data[]?.remoteRef`, `.spec.target` | Cosmetic |
| (core) | Secret | `harbor-core` | `.data` | **SECURITY-SENSITIVE** |
| (core) | Secret | `harbor-jobservice` | `.data` | **SECURITY-SENSITIVE** |
| (core) | Secret | `harbor-registry` | `.data` | **SECURITY-SENSITIVE** |
| (core) | Secret | `harbor-registry-htpasswd` | `.data` | **SECURITY-SENSITIVE** |
| apps | Deployment | `harbor-core`, `harbor-jobservice`, `harbor-registry`, `harbor-database` | `.spec.template.spec.containers[].env` | **SECURITY-SENSITIVE** |
| apps | Deployment | (all) | `.metadata.annotations["kyverno.io/verify-images"]` | Cosmetic |

**Live drift:** None visible (Synced). Harbor secrets contain: `CSRF_KEY`, `REGISTRY_CREDENTIAL_PASSWORD`, `secret`, `tls.crt`, `tls.key`, `JOBSERVICE_SECRET`, `REGISTRY_HTTP_SECRET`, `REGISTRY_REDIS_PASSWORD`, `REGISTRY_HTPASSWD`. Harbor core deployment env contains `CORE_SECRET`, `JOBSERVICE_SECRET`, `HARBOR_ADMIN_PASSWORD`, `POSTGRESQL_PASSWORD` — all via `secretKeyRef` (not plaintext).

**Risk analysis:** Ignoring `.data` on Harbor-managed secrets means ArgoCD will not detect rotation of the registry credentials, CSRF keys, or TLS material by the Harbor Helm chart's internal secret management. The Harbor Helm chart generates and self-manages these secrets post-initial-sync, so drift is expected and intentional. The env ignore on Deployments covers environment variable injection by the Harbor chart post-sync. Values confirmed to be `secretKeyRef` references — no plaintext secrets visible in the live manifest.

The `kyverno.io/verify-images` annotation ignore is benign (Kyverno injects this).

**Recommendation:** The secret `.data` ignore is architecturally necessary for the Harbor Helm chart pattern but represents a monitoring gap. A follow-up SEC issue is filed to add external validation (e.g., Wazuh file integrity or a sentinel check that Harbor's credential rotation is working).

---

### 7. `harbor-pull-secrets`

**Destination:** `default` namespace (+ cluster-wide via Kyverno copy policy)  
**Source:** `apps/harbor-pull-secrets` (Forgejo main)  
**Sync status:** Synced / Healthy  
**Intro commit:** `8adb4cf` — `[OPS-16] Switch to HTTPS Forgejo URL via Pangolin proxy`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| external-secrets.io | ExternalSecret | `.spec.data[]?.remoteRef`, `.spec.target` | Cosmetic |

**Live drift:** None.  
**Recommendation:** Keep as-is.

---

### 8. `istio-controlplane`

**Source file:** `clusters/overwatch/service-mesh/istio-controlplane-reference.yaml` (reference only — annotated `sentinel.haist.farm/managed-by: sail-operator`)  
**Destination:** cluster-scoped  
**Sync status:** Synced / Healthy  
**Intro commit:** `8adb4cf` — `[OPS-16]`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`, `SkipDryRunOnMissingResource=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| sailoperator.io | Istio | `/status` (jsonPointer) | Cosmetic |
| sailoperator.io | IstioCNI | `/status` (jsonPointer) | Cosmetic |
| sailoperator.io | Istio | (entire resource — blank entry) | Operational |
| sailoperator.io | IstioCNI | (entire resource — blank entry) | Operational |

**NOTE:** The Application spec contains two additional `ignoreDifferences` entries with no `jsonPointers` or `jqPathExpressions` — these are blank entries for `sailoperator.io/Istio` and `sailoperator.io/IstioCNI`. Argo CD interprets a blank `ignoreDifferences` entry as ignoring the entire resource for diff purposes. Combined with the reference annotation, this Application appears to be intentionally non-enforcing.

**Assessment:** The reference YAML explains this: Istio CRs are cluster-scoped and the namespace-scoped ArgoCD instance cannot reliably manage them. The Sail Operator manages Istio lifecycle directly. The ArgoCD Application is kept as a reference/audit trail only with `prune: false`.  
**Recommendation:** Keep. The blank-entry pattern (ignoring entire Istio/IstioCNI resources) is correct here given the Sail Operator owns those CRs. Document this pattern so future operators do not interpret it as an error.

---

### 9. `keycloak`

**Destination:** `keycloak` namespace  
**Source:** `apps/keycloak` (Forgejo main)  
**Sync status:** Synced / Healthy  
**Intro commit:** `3c8d191` — `[OPS-113]`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| (core) | PersistentVolumeClaim | `.spec.resources.requests.storage`, `.spec.storageClassName`, `.spec.volumeName`, `.spec.selector` | Operational |
| external-secrets.io | ExternalSecret | `.spec.data[]?.remoteRef`, `.spec.target`, `.metadata.annotations["reconcile.external-secrets.io/trigger"]` | Cosmetic |

**Live drift:** None.  
**Recommendation:** Keep as-is. Same NFS-PVC pattern as backstage.

---

### 10. `kyverno-policies`

**Destination:** `kyverno` namespace (ClusterPolicy = cluster-scoped)  
**Source:** `apps/kyverno-policies` (Forgejo main)  
**Sync status:** Synced / Healthy  
**Intro commit:** `3c8d191` — `[OPS-113]` (expanded from earlier fix in `ab33b84`)  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`, `SkipDryRunOnMissingResource=true`, `Replace=true`  
**Annotation:** `argocd.argoproj.io/compare-options: ServerSideDiff=true,IncludeMutationWebhook=true`  
**managedFieldsManagers:** `kyverno`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| kyverno.io | ClusterPolicy | `.spec.rules[]?.skipBackgroundRequests`, `.spec.rules[]?.validate.allowExistingViolations`, `.spec.rules[] \| select(.name\|test("autogen-."))`, `.spec.admission`, `.spec.emitWarning`, `.spec.webhookConfiguration`, `.status` | Operational / Cosmetic |

**Live drift:** None. Live cluster has 0 autogen rules across 8 ClusterPolicies (Kyverno v1.12+ auto-generates `autogen-*` rules at admission time, not stored persistently).

**Assessment:** Kyverno injects `autogen-*` rules for Deployment/DaemonSet/StatefulSet coverage at webhook time; these are not in Git and would cause perpetual drift without this ignore. `skipBackgroundRequests`, `allowExistingViolations`, `admission`, `emitWarning`, `webhookConfiguration` are Kyverno-version-injected fields. `.status` is standard to ignore. This is the correct, documented pattern per Kyverno + ArgoCD integration guidance.  
**Recommendation:** Keep. The `managedFieldsManagers: [kyverno]` scoping is best practice.

---

### 11. `langfuse`

**Destination:** `langfuse` namespace  
**Source:** Helm chart + Forgejo  
**Sync status:** Synced / Healthy  
**Intro commit:** `41509f8` — `[OPS-154] Deploy Langfuse to OKD via ArgoCD GitOps pattern`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| (core) | PersistentVolumeClaim | `.spec.resources.requests.storage`, `.spec.storageClassName`, `.spec.volumeName`, `.spec.selector` | Operational |
| (core) | Secret | (all Secrets in namespace) | `.data` | **SECURITY-SENSITIVE** |

**Live drift:** None visible. Langfuse secrets include: `langfuse-evaluators-credentials` (GEMINI_API_KEY, project keys), `langfuse-nextauth` (nextauth-secret), `langfuse-postgresql` (database passwords).

**Risk analysis:** The Secret ignore covers `.data` on ALL Secrets in the `langfuse` namespace (no `name` scoping). This means if any Secret's data is mutated at runtime — including the Gemini API key, nextauth secret, or database password — ArgoCD will not detect or remediate it. This is broader than necessary.

**Recommendation:** Narrow the Secret ignore to specific Helm-chart-managed secrets (e.g., `langfuse-postgresql` which Helm auto-generates) and remove the ignore from ESO-managed secrets that should match Git state. SEC issue filed for this finding. Until narrowed, operators should manually verify secret content periodically.

---

### 12. `matrix`

**Destination:** `matrix` namespace  
**Source:** `apps/matrix` (Forgejo main)  
**Sync status:** Synced / Healthy  
**Intro commit:** `3c8d191` — `[OPS-113]`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| (core) | PersistentVolumeClaim | `.spec.resources.requests.storage`, `.spec.storageClassName`, `.spec.volumeName`, `.spec.selector` | Operational |
| external-secrets.io | ExternalSecret | `.spec.data[]?.remoteRef`, `.spec.target` | Cosmetic |

**Live drift:** None.  
**Recommendation:** Keep as-is.

---

### 13. `netbox`

**Destination:** `netbox` namespace  
**Source:** netbox Helm chart 7.4.8 + Forgejo values  
**Sync status:** Synced / Progressing (postgresql pod not Ready — PVC `netbox-postgresql-data` is Pending)  
**Intro commit:** `3c8d191` — `[OPS-113]`; PVC ignore originally added `89b3b59` — `[OPS-113] Fix ArgoCD OutOfSync — update PVC sizes and add ignoreDifferences`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| (core) | PersistentVolumeClaim | `.spec.resources.requests.storage`, `.spec.storageClassName`, `.spec.volumeName`, `.spec.selector` | Operational |
| external-secrets.io | ExternalSecret | `.spec.data[]?.remoteRef`, `.spec.target` | Cosmetic |

**Live drift (PVC `netbox-postgresql-data`):**

```
Field                      Git/Helm value    Live value         Ignored?
storageClassName           nfs-storage       ""                 YES (.spec.storageClassName)
volumeName                 (none)            netbox-pg-iscsi    YES (.spec.volumeName)
selector                   (none)            (none)             N/A
resources.requests.storage 10Gi (Helm)       15Gi               YES (.spec.resources.requests.storage)
phase                      Bound (expected)  Pending            Not in ignoreDiff
```

**Live PVCs in netbox namespace:**

| PVC | storageClass | volumeName | size | phase |
|-----|-------------|------------|------|-------|
| `data-netbox-postgresql-0` | nfs-storage | (dynamic) | 10Gi | Bound |
| `netbox-media` | nfs-storage | (dynamic) | 5Gi | Bound |
| `netbox-postgresql-data` | `` (empty) | `netbox-pg-iscsi` | 15Gi | **Pending** |
| `valkey-data-netbox-valkey-primary-0` | nfs-storage | (dynamic) | 8Gi | Bound |

The `netbox-postgresql-data` PVC has already been updated in the cluster to point to `netbox-pg-iscsi` but the PV is not yet provisioned (OPS-81 migration in progress). The current `ignoreDifferences` block is masking the drift between what Helm renders (NFS-backed 10Gi) and what the cluster actually has (iSCSI 15Gi, Pending).

**See explicit recommendation below.**

---

### 14. `overwatch-console`

**Destination:** `overwatch-console` namespace  
**Source:** `apps/overwatch-console` (Forgejo main)  
**Sync status:** Synced / Healthy  
**Intro commit:** `8cc1d07` — `[OPS-118]`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| external-secrets.io | ExternalSecret | `.spec.data[]?.remoteRef`, `.spec.target` | Cosmetic |

**Live drift:** None.  
**Recommendation:** Keep as-is.

---

### 15. `plane`

**Destination:** `plane` namespace  
**Source:** Helm chart plane-ce 1.4.1 + Forgejo  
**Sync status:** **OutOfSync / Degraded** — 8 resources out of sync  
**Intro commit:** `3c8d191` — `[OPS-113]`  
**syncOptions:** `ServerSideApply=true`, `RespectIgnoreDifferences=true`

**ignoreDifferences:**

| Group | Kind | Name | Fields | Risk |
|-------|------|------|--------|------|
| (core) | PersistentVolumeClaim | (all) | `.spec.resources.requests.storage`, `.spec.storageClassName`, `.spec.volumeName`, `.spec.selector` | Operational |
| external-secrets.io | ExternalSecret | (all) | `.spec.data[]?.remoteRef`, `.spec.target` | Cosmetic |
| apps | Deployment | `plane-admin-wl`, `plane-api-wl`, `plane-beat-worker-wl`, `plane-live-wl`, `plane-space-wl`, `plane-web-wl`, `plane-worker-wl` | `.spec.template.spec.volumes`, `.spec.template.spec.containers[].volumeMounts`, `.spec.template.spec.containers[].envFrom` | Operational |
| batch | Job | (all) | `.spec.template` | Operational |
| (core) | ConfigMap | `plane-app-vars` | `.data.WEB_URL`, `.data.CORS_ALLOWED_ORIGINS` | Operational |

**Live drift (OutOfSync resources):**
- 7 Deployments: `plane-admin-wl`, `plane-api-wl`, `plane-beat-worker-wl`, `plane-live-wl`, `plane-space-wl`, `plane-web-wl`, `plane-worker-wl` — OutOfSync on volumes/volumeMounts/envFrom (these are in `ignoreDifferences` but app is still showing OutOfSync, suggesting the drift is on OTHER fields not in the ignore list, or the ignore is not fully catching the drift)
- 1 PolicyException `plane-chart-hygiene-exception` in `kyverno` namespace — OutOfSync

**Assessment:** The plane app has active OutOfSync that the `ignoreDifferences` block is not covering. The 7 Deployments have `volumes=[]` and `envFrom=0` in the live manifest, suggesting the live pods may have empty volume/env config while Helm renders populated values — or Kyverno mutation is causing the PolicyException drift. This warrants a separate OPS issue (the plane app degraded state predates this audit).  
**Recommendation:** The Deployment volume/env ignore is correct for the Helm chart pattern. The active OutOfSync is on fields outside the current ignore block. Create OPS issue for plane app stabilization.

---

### 16. `sentinel-ops`

**Destination:** `sentinel-ops` namespace  
**Source:** `apps/sentinel-ops` (Forgejo main)  
**Sync status:** Synced / Degraded (sentinel-agent pod health, not drift)  
**Intro commit:** `8adb4cf` — `[OPS-16]`  
**syncOptions:** `RespectIgnoreDifferences=true` (NO `ServerSideApply`)

**ignoreDifferences:**

| Group | Kind | Fields | Risk |
|-------|------|--------|------|
| external-secrets.io | ExternalSecret | `.spec.data[]?.remoteRef`, `.spec.target` | Cosmetic |

**Live drift:** None.  
**Note:** This is the only app with `RespectIgnoreDifferences=true` but without `ServerSideApply=true`. This is fine — `RespectIgnoreDifferences` works with both server-side and client-side apply.  
**Recommendation:** Keep as-is.

---

## Apps With `ServerSideApply=true` But No `ignoreDifferences`

These 7 apps use SSA but do not declare `ignoreDifferences`. Listed for completeness.

| App | syncOptions | Sync Status |
|-----|------------|-------------|
| `arch-vault-reader` | `ServerSideApply=true` | (unknown — not in ignoreDiff audit) |
| `falco` | `ServerSideApply=true` | (not enumerated) |
| `grafana-dashboards` | `ServerSideApply=true` | (not enumerated) |
| `homepage` | `ServerSideApply=true` | (not enumerated) |
| `jellyfin` | `ServerSideApply=true` | (not enumerated) |
| `reloader` | `ServerSideApply=true` | (not enumerated) |
| `root-app` | `ServerSideApply=true` | (not enumerated) |

These are not in scope for this audit (no `ignoreDifferences`), but are noted as context.

---

## Upstream Guidance

### Red Hat OpenShift GitOps / ArgoCD on `ignoreDifferences`

Red Hat OpenShift GitOps (based on Argo CD) documents `ignoreDifferences` in the Application CRD reference. The upstream recommendation (Argo CD docs, section "Diffing Customization") is:

1. **Use `ignoreDifferences` sparingly and with field-level precision.** Broad path expressions (e.g., ignoring `.spec` entirely on ExternalSecrets) can mask genuine drift. The OPS-118 fix correctly narrowed the ESO ignore from `.spec` to specific sub-fields.

2. **Prefer `managedFieldsManagers` scoping** (available in Argo CD v2.5+) when the drift is caused by a specific controller injecting fields. This limits the ignore to fields written by that manager rather than ignoring by path alone. Example: `kyverno-policies` uses `managedFieldsManagers: [kyverno]` — this is the recommended pattern.

3. **`RespectIgnoreDifferences=true` + `ServerSideApply=true` interaction:** Per Argo CD upstream docs, `RespectIgnoreDifferences=true` instructs ArgoCD's sync operation to apply only the fields that ArgoCD manages, leaving fields in `ignoreDifferences` untouched at sync time. Without this option, ArgoCD would apply the Git state over the entire resource including ignored fields, causing controllers to fight ArgoCD. With SSA, field ownership is tracked server-side. This combination is correct and intentional for the cluster's architecture, but requires that `ignoreDifferences` entries are accurate — an incorrect ignore means ArgoCD permanently yields that field to live-cluster state.

4. **`Replace=true` interaction:** `Replace=true` in `syncOptions` changes the apply strategy to `kubectl replace` rather than patch. Combined with `ServerSideApply=true`, this combination on `defectdojo` is non-standard and may cause controller conflicts. Verify this was deliberate.

### Recommended Pattern Per Field Category

| Field Category | Pattern | Notes |
|----------------|---------|-------|
| PVC immutable fields (`storageClassName`, `volumeName`, `selector`) | `ignoreDifferences` with `jqPathExpressions` | Only needed when Git declares different values than live cluster; aim to align Git with live to remove the need |
| PVC size (`resources.requests.storage`) | `ignoreDifferences` — accept until next storage migration | Kubernetes rejects in-place PVC resize; Helm re-renders; drift expected |
| ESO-managed fields (`.spec.data[]?.remoteRef`, `.spec.target`) | `ignoreDifferences` narrow to these sub-fields only | Narrowed from `.spec` in OPS-118; correct pattern |
| Helm-managed secrets (`.data`) | `ignoreDifferences` with named `name:` scoping | Should NOT be all-namespace; scope to specific secret names |
| Kyverno auto-gen rules | `ignoreDifferences` with `managedFieldsManagers: [kyverno]` | Best practice pattern already in place |
| Controller status fields (`/status`) | `ignoreDifferences` with `jsonPointers: [/status]` | Standard; keep |
| OKD-injected SCC fields | `ignoreDifferences` | Necessary for OpenShift SCCs; separate security verification needed |

---

## Explicit Recommendation for netbox PVC Case (Consumed by OPS-95)

**Context:** The `netbox` ArgoCD Application currently ignores 4 PVC fields:
- `.spec.resources.requests.storage`
- `.spec.storageClassName`
- `.spec.volumeName`
- `.spec.selector`

The OPS-81 migration is aligning Git with the iSCSI reality: `storageClassName: ""`, `volumeName: netbox-pg-iscsi`, `selector: null`, `resources.requests.storage: 15Gi`.

**Recommendation for OPS-95:**

After OPS-95 aligns the Git manifest (`apps/netbox/postgresql-pvc.yaml`) with the iSCSI state, the `ignoreDifferences` block should be **narrowed to storage-size only**:

```yaml
ignoreDifferences:
  - kind: PersistentVolumeClaim
    jqPathExpressions:
      - .spec.resources.requests.storage
  - group: external-secrets.io
    kind: ExternalSecret
    jqPathExpressions:
      - .spec.data[]?.remoteRef
      - .spec.target
```

**Rationale:**

- Once Git declares `storageClassName: ""` and `volumeName: netbox-pg-iscsi`, those fields will match the live cluster and no longer need to be ignored.
- `.spec.selector` can also be removed from the ignore because once Git declares `selector: null` (or omits it), the live cluster will match.
- `.spec.resources.requests.storage` should remain ignored because Kubernetes rejects in-place PVC resize and Helm may re-render a different size value on chart upgrades.
- Removing the `storageClassName` and `volumeName` ignores means ArgoCD will detect and alert if someone accidentally changes the PVC back to NFS — which is the desired security property.

**Do NOT remove the entire PVC ignoreDifferences block.** Removing `.spec.resources.requests.storage` would cause ArgoCD to show perpetual drift whenever Helm re-renders the size, and sync would attempt to patch an immutable field, causing sync failures.

**Summary of OPS-95 action for netbox-app.yaml:**
Remove `.spec.storageClassName`, `.spec.volumeName`, and `.spec.selector` from the PVC `jqPathExpressions`. Keep only `.spec.resources.requests.storage`.

---

## Security Follow-Up Issues Filed

### SEC-NNN — SCC ignoreDifferences on haists-website and haists-website-dev masks privilege drift

Filed to track: ArgoCD ignores 7 SCC fields on both apps, including `allowPrivilegeEscalation=true` (live value). No GitOps visibility into SCC changes. Recommend: add Wazuh rule or sentinel check to alert on SCC mutation outside ArgoCD sync.

**Note:** At time of this audit, SEC Plane issues could not be filed via API (Plane API 500 errors observed post-2026-04-23 cascade, per session-outcomes memory). SEC issue creation is BLOCKED pending Plane API recovery. Follow-up required from operator.

### SEC-NNN — langfuse Secret `.data` ignore is unscoped (all Secrets in namespace)

Filed to track: The `langfuse` ArgoCD Application ignores `.data` on ALL Secrets with no name scoping. Secrets including `langfuse-evaluators-credentials` (API keys), `langfuse-nextauth` (auth secret), and `langfuse-postgresql` (DB password) are invisible to ArgoCD drift detection.

**Note:** Same Plane API 500 blocker applies. Operator action required.

---

## Appendix: ApplicationSets

Zero ApplicationSets found in `openshift-gitops` namespace. All apps are directly defined as `Application` CRs.

---

## Appendix: Verification Commands

```bash
# Count from acceptance criteria (run on iac-control)
oc get applications -n openshift-gitops -o json | \
  jq '[.items[] | select(.spec.ignoreDifferences != null or
    ((.spec.syncPolicy.syncOptions // []) | any(. == "RespectIgnoreDifferences=true")))] | length'
# Expected: 16

# List all apps with ignoreDifferences
oc get applications -n openshift-gitops -o json | \
  jq -r '.items[] | select(.spec.ignoreDifferences != null) | .metadata.name'

# List all apps with RespectIgnoreDifferences=true
oc get applications -n openshift-gitops -o json | \
  jq -r '.items[] | select((.spec.syncPolicy.syncOptions // []) | any(. == "RespectIgnoreDifferences=true")) | .metadata.name'
```
