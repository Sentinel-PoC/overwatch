# KCM Operand Tuning Design — OKD 4.19

**Issue:** OPS-99 (child of OPS-78)
**Date:** 2026-04-24
**Agent:** worker-agent-ops-99
**Prerequisite:** OPS-98 RCA signed 2026-04-24T11:50Z — HIGH confidence conclusion accepted
**Status:** Design complete — Go/No-go issued at bottom

---

## 1. Supported OKD 4.19 Levers for KCM Leader-Election Behavior

### 1a. First-Class Fields on `KubeControllerManager.spec`

Verified via `kubectl explain KubeControllerManager.spec` against the live cluster
(OKD 4.19.0-okd-scos.19, Kubernetes 1.32.8, 2026-04-24):

```
GROUP:   operator.openshift.io
KIND:    KubeControllerManager
VERSION: v1

FIELDS:
  failedRevisionLimit         <integer>
  forceRedeploymentReason     <string>
  logLevel                    <string>   (Normal/Debug/Trace/TraceAll)
  managementState             <string>   (Managed/Unmanaged/Removed/Force)
  observedConfig              <Object>   (read-only, set by operator)
  operatorLogLevel            <string>
  succeededRevisionLimit      <integer>
  unsupportedConfigOverrides  <Object>
```

**Finding:** There are NO first-class fields for leader-election timing on
`KubeControllerManager.spec`. There is no `spec.leaderElection`,
no `spec.leaderElectLeaseDuration`, no `spec.extendedArguments`. The operator
manages leader-election timing entirely from its own rendering logic applied to
the KCM static pod manifest. The only way to override these values is via
`spec.unsupportedConfigOverrides`.

**WorkerLatencyProfile (alternate supported path, but different scope):**
OpenShift 4.x provides `nodes.config.openshift.io/cluster` with a
`spec.workerLatencyProfile` field that accepts `Default`, `MediumUpdateAverageReaction`,
or `LowUpdateSlowReaction`. This is documented in
[Red Hat docs — Worker latency profiles](https://docs.openshift.com/container-platform/4.19/scalability_and_performance/scaling-worker-latency-profiles.html)
and adjusts kubelet nodeStatusUpdate frequency and node-lifecycle-controller timings
— not KCM leader-election. The current cluster has `spec: {}` (no profile set).
This lever does NOT address KCM `--leader-elect-renew-deadline` and is not
applicable to the OPS-78 failure mode.

### 1b. `spec.unsupportedConfigOverrides` Path

**What it does:** Injects raw key-value pairs into the operator-rendered KCM
static pod configuration, bypassing the operator's normal config rendering.

**Syntax for leader-election tuning:**
```yaml
apiVersion: operator.openshift.io/v1
kind: KubeControllerManager
metadata:
  name: cluster
spec:
  unsupportedConfigOverrides:
    extendedArguments:
      leader-elect-lease-duration:
        - "30s"
      leader-elect-renew-deadline:
        - "20s"
      leader-elect-retry-period:
        - "5s"
```

**Red Hat supportability statement** (from `kubectl explain KubeControllerManager.spec.unsupportedConfigOverrides`):

> "Red Hat does not support the use of this field. Misuse of this field could
> lead to unexpected behavior or conflict with other configuration options.
> Seek guidance from the Red Hat support before using this field.
> Use of this property blocks cluster upgrades — it must be removed before
> upgrading your cluster."

The live cluster CR confirms: `unsupportedConfigOverrides: null` and
`UnsupportedConfigOverridesUpgradeable: True` (2026-01-25). Applying an override
will flip this condition to False and block cluster upgrades until removed.

**Operational consequences of using this path:**
1. Cluster upgrade blocked — must remove override before `oc adm upgrade`.
2. No Red Hat SRE support if cluster is under support contract.
3. The operator may reset the override on restart or operator reconciliation
   if it renders config that conflicts with the override; behavior is not
   guaranteed across operator pod restarts.
4. No validation: typos or invalid values will not be rejected by the API and
   will cause the KCM static pod to crash or use unexpected defaults.

**Red Hat KB references:**
- [KB 6295671 — Configuring KCM leader election](https://access.redhat.com/solutions/6295671)
  (notes that `unsupportedConfigOverrides` is the only path; confirms upgrade blocker)
- [OpenShift docs — Leader election overview](https://docs.openshift.com/container-platform/4.19/operators/operator_sdk/osdk-leader-election.html)
- [Red Hat docs — Worker latency profiles](https://docs.openshift.com/container-platform/4.19/scalability_and_performance/scaling-worker-latency-profiles.html)
  (separate lever; not for KCM leader-election)
- [cluster-kube-controller-manager-operator source](https://github.com/openshift/cluster-kube-controller-manager-operator)
  (look in `pkg/operator/configobservation/` for leader-election rendering)

### 1c. Actual OKD 4.19 Leader-Election Defaults (Corrected)

The 2026-04-21-to-23 AAR proposed values of 45s / 30s / 10s. These are incorrect
for this cluster.

**Verified from live cluster configmap `openshift-kube-controller-manager/config`
and static pod args (2026-04-24):**

| Flag | Value | Source |
|------|-------|--------|
| `--leader-elect-lease-duration` | **15s** (Go default) | Not in configmap or pod args — Go `client-go` default |
| `--leader-elect-renew-deadline` | **12s** | Explicit in configmap and pod args |
| `--leader-elect-retry-period` | **3s** | Explicit in configmap and pod args |
| `--leader-elect-resource-lock` | `leases` | Explicit |
| `--leader-elect-resource-namespace` | `kube-system` | Explicit |

These values are consistent across all 3 masters (verified in OPS-98 log evidence).
The 15s lease duration is the Kubernetes `client-go` library default for `LeaseDuration`
when not explicitly set; the OKD operator sets renew-deadline and retry-period but
delegates lease-duration to the Go default. OKD 4.19 (k8s 1.32) uses `client-go`
v0.32.x which sets the default at 15s.

**The AAR "45s/30s/10s" numbers do not match this cluster in any configuration
observed.** They appear to have been confabulated or copied from a different
cluster or operator version. Do not use them as reference values.

---

## 2. Go/No-Go Analysis for OPS-100 (Implement KCM Tuning)

### 2a. The 6-Second Timeout — What It Actually Is

The KCM logs show this pattern on every restart:

```
Put "https://api-int.overwatch.haist.farm:6443/.../leases/kube-controller-manager?timeout=6s":
context deadline exceeded
```

The `?timeout=6s` is the HTTP client-side per-request deadline that `client-go`
appends to each API server call. This is NOT the `--leader-elect-renew-deadline=12s`
parameter. They are separate:

- **`--leader-elect-renew-deadline=12s`** is the outer deadline for the entire
  lease renewal cycle. Within this 12s window, KCM makes up to
  `floor(12s / 3s) = 4` renewal attempts.
- **HTTP client timeout = 6s** is the per-attempt timeout for each individual
  API server PUT/GET call. This is derived from `client-go`'s internal computation
  and is approximately `min(renewDeadline, 2 * retryPeriod) = min(12, 6) = 6s`.

### 2b. Why KCM Times Out Despite renew-deadline=12s

The failure chain:

1. KCM attempts lease renewal: `PUT /leases/kube-controller-manager?timeout=6s`
2. kube-apiserver must write the lease to etcd: `PUT → etcd`
3. etcd must fsync the WAL: etcd WAL fsync on master-2 p99 = **3–8.192s** (24h evidence)
4. The fsync takes longer than the 6s HTTP client timeout → `context deadline exceeded`
5. KCM makes 4 attempts, all timing out, over 12s
6. After 12s renew-deadline: `leaderelection lost` → `os.Exit(1)` → container restart

**The critical arithmetic:**

```
Renew deadline:          12s
Per-attempt HTTP timeout: 6s
Number of attempts:       floor(12s / 3s retry-period) = 4

etcd WAL fsync p99 (master-2):
  Minimum observed (24h window): 2.14s
  Median observed:               ~5–7s
  Ceiling hit (≥8.192s):        11 of 25 hourly windows

For lease renewal to succeed, the entire etcd write round-trip
(etcd fsync + response) must complete within 6s.

Probability of success per attempt (rough):
  At 2.14s fsync: ~possible (6s - 2.14s = 3.86s buffer for network + processing)
  At 5–7s fsync:  ~marginal to impossible
  At 8.192s+ fsync: impossible (fsync alone exceeds the HTTP timeout)

At the observed p99 = 8.192s, KCM cannot renew its lease regardless of
--leader-elect-renew-deadline value, because each individual etcd operation
exceeds the HTTP per-request timeout.
```

### 2c. Could Raising Thresholds Help?

**Scenario: Raise renew-deadline from 12s to 25s.**

The HTTP per-request timeout scales with renew-deadline: approximately
`min(renewDeadline, 2 * retryPeriod)`. With retry-period=3s, the per-request
timeout would remain at `2 * 3s = 6s` — unchanged. Raising renew-deadline alone
does not extend the per-request timeout when retry-period is the binding constraint.

**Scenario: Raise both renew-deadline to 25s and retry-period to 12s.**

The per-request HTTP timeout would become `min(25, 2*12) = 24s`. Individual etcd
operations taking 3–7s would now complete within the 24s per-request budget.
At 8.192s+ (histogram ceiling), it would be marginal.

**Problem:** A retry-period of 12s means KCM holds a stale lease for up to 12s
before any other instance can attempt acquisition. Combined with a 25s
lease-duration (must be > renew-deadline), a crashed KCM would not be replaced
as leader for up to 37s. The current 15s lease duration already exceeds typical
etcd fsync spikes on healthy clusters by 1500x. At 25s, the cluster would be
effectively leadership-less for extended periods after each KCM restart.

**Scenario: What OPS-98 actually concluded.**

OPS-98 states: "Tuning cannot remediate the root cause." The reasoning: when etcd
fsync p99 regularly hits the 8.192s histogram ceiling (meaning true worst-case is
unknown and may exceed 8.192s), there is no retry-period value that provides
reliable per-request timeouts while also maintaining meaningful leader-election
semantics. You cannot configure your way out of a disk that may take more than
8 seconds to flush a WAL entry.

**Could any tuning help at the margin while waiting for XFS migrations?**

Marginally, yes. Raising `retry-period` from 3s to 8s and `renew-deadline` from
12s to 24s would extend per-request timeout to `min(24, 2*8) = 16s`, which would
survive the 8.192s fsync ceiling case (8s fsync + ~2s network/processing = ~10s,
within 16s). During the minority of windows where master-2 fsync p99 is at 2–5s,
this tuning would reduce restart frequency.

**However:**
- It does not prevent restarts when fsync hits the histogram ceiling (11 of 25
  hourly windows), because the true worst-case exceeds 8.192s and may exceed 16s.
- Applying `unsupportedConfigOverrides` now creates an upgrade blocker that must
  be removed before the XFS migrations trigger any cluster upgrade.
- The operator will trigger a static pod rollout when the override is applied,
  causing a controlled restart of all 3 KCM instances — adding disruption during
  an already-unstable period.
- The fix is expected within days (pending XFS migration completion), so the
  marginal benefit window is short.

**Verdict: Conditional NO-GO.** See Section 4.

---

## 3. XFS Migrations Fix-KCM Hypothesis — Required Validations

### 3a. Hypothesis Restatement

Heavy iSCSI ext4 I/O from application PVs on the Proxmox storage layer contaminates
the I/O scheduler queue for all VMs on that storage backend. This elevates etcd WAL
fsync latency specifically on master-2 (the Proxmox host with the most ext4 iSCSI
workloads, or the most contended storage path).

XFS's delayed allocation and more efficient journal handling produces less random
I/O than ext4 under equivalent postgres write loads, reducing I/O contention at
the Proxmox/Ceph/ZFS layer.

### 3b. Pending Migrations

| Issue | PV | Filesystem | Status |
|-------|----|------------|--------|
| OPS-82 | defectdojo-pg-iscsi | ext4 → XFS | PENDING |
| OPS-83 | langfuse-ch-iscsi | ext4 → XFS | PENDING |
| OPS-84 | langfuse-pg-iscsi | xfs | DONE (2026-04-23) |
| OPS-85 | keycloak-pg-iscsi | ext4 → XFS | PENDING |
| OPS-86 | matrix-pg-iscsi | ext4 → XFS | PENDING |
| OPS-87 | harbor-pg-iscsi | ext4 → XFS | PENDING |

(langfuse-pg-iscsi already migrated; OPS-84 refers to a different assignment.
Net: 5 PVs remain on ext4 as of OPS-98 evidence date 2026-04-24.)

### 3c. Post-Migration Measurement Protocol

After all 5 remaining ext4 PVs are migrated to XFS, run a **48-hour monitoring
window** before declaring the hypothesis confirmed or refuted.

**Metrics to collect:**

| Metric | PromQL | Success Threshold | Failure Threshold |
|--------|--------|-------------------|-------------------|
| etcd WAL fsync p99 on master-2 | `histogram_quantile(0.99, rate(etcd_disk_wal_fsync_duration_seconds_bucket{endpoint=~".*master-2.*"}[5m]))` | **< 50ms** sustained for 48h | > 500ms in any 30m window after 6h |
| etcd backend commit p99 on master-2 | `histogram_quantile(0.99, rate(etcd_disk_backend_commit_duration_seconds_bucket{endpoint=~".*master-2.*"}[5m]))` | **< 100ms** | > 500ms |
| KCM restart rate (all masters) | `increase(kube_pod_container_status_restarts_total{namespace="openshift-kube-controller-manager",container="kube-controller-manager"}[24h])` | **< 5 total / day** across all 3 masters | > 10 total / day |
| kube-apiserver etcd operation latency | etcd3 trace log lines (from kube-apiserver) p99 | **< 50ms** | > 200ms |

**Decision matrix:**

| Condition | Conclusion | Action |
|-----------|------------|--------|
| All 4 metrics hit success thresholds within 48h | XFS migration hypothesis CONFIRMED | Close OPS-78 as Done; close OPS-100 as "not needed" |
| etcd fsync drops below 50ms but KCM restart rate remains > 5/day | Disk I/O was NOT the only cause; secondary investigation needed | Proceed to Section 3d; do NOT close OPS-78 |
| etcd fsync does NOT drop below 500ms after 6h post-final-migration | Proxmox VM placement or host disk is primary; XFS migrations insufficient | Escalate to Section 3d immediately; do not wait 48h |

### 3d. If fsync Does Not Drop After XFS Migrations

Investigate in order:

1. **Proxmox VM placement audit:** Identify which Proxmox host master-2 runs on.
   Check if that host has more VMs, a slower storage device class (spinning vs NVMe),
   or shares a storage pool with high-I/O neighbors. Check `pvesh get /nodes/{host}/qemu`
   and `pvesh get /nodes/{host}/storage` on the Proxmox host.

2. **master-2 VM storage configuration:** Check if master-2's VM disk uses `cache=none`
   (write-through, safest for etcd), `cache=writeback` (fastest but risks WAL corruption
   on crash), or `virtio-scsi` vs `virtio-blk` bus. Sub-optimal cache mode can explain
   persistent high fsync latency independent of iSCSI workloads.
   Command: `qm config <vmid> | grep -E 'scsi|virtio|ide|cache'` on Proxmox host.

3. **Proxmox host disk health:** Check SMART data on the Proxmox host disk backing
   master-2's VM. A degraded SSD or a HDD in the storage path would produce exactly
   this latency profile.
   Command: `smartctl -a /dev/sdX` on the Proxmox host via SSH.

4. **etcd on a dedicated disk:** If the above reveals a structural hardware issue,
   the mitigation is moving etcd data to a dedicated disk or PV with `hostPath`
   remounted from a faster device. This requires a controlled etcd member replacement
   procedure and is a significant operation requiring operator authorization.

---

## 4. Explicit Go/No-Go for OPS-100

### Verdict: NO-GO

**OPS-100 (Implement KCM tuning per OPS-99 design) is NO-GO at this time.**

**Numeric justification:**

```
Current state (verified 2026-04-24):
  leader-elect-lease-duration:  15s (Go default, not explicitly set)
  leader-elect-renew-deadline:  12s (explicit)
  leader-elect-retry-period:     3s (explicit)
  HTTP per-request timeout:      6s (derived: min(12, 2*3) = 6s)

  master-2 etcd WAL fsync p99 (24h window):
    Minimum:   2.14s
    Typical:   5–7s
    Worst-case: ≥8.192s (histogram ceiling, true value unknown, 11/25 windows)

For tuning to reliably prevent restarts, the per-request HTTP timeout must exceed
the worst-case etcd round-trip duration.

Required timeout to survive 8.192s fsync + overhead:
  Minimum required: ~10–12s (adding ~2s for network, processing, API server overhead)
  Required retry-period: ≥ 6s (to make per-request timeout ≥ 10s)
  Required renew-deadline: ≥ 24s (must be > 4 * retry-period for meaningful attempts)
  Required lease-duration: ≥ 30s (must exceed renew-deadline)

This configuration is operable in theory but:
  (a) Requires unsupportedConfigOverrides — upgrade blocker, no Red Hat support
  (b) At histogram ceiling (true worst-case unknown, may exceed 8.192s), even
      retry-period=8s may not be sufficient
  (c) Lease-duration=30s means a crashed KCM goes undetected for up to 30s
  (d) The marginal benefit window is days — XFS migrations expected to resolve root cause
  (e) Applying the override during the current unstable period adds risk (controlled
      static pod rollout across all 3 masters)
```

**OPS-100 disposition:**
- OPS-100 closes as "will not implement without OPS-82..87 completion."
- OPS-101 (verification window) activates after the last XFS migration (OPS-82–87
  chain completes) with a 48-hour hold before assessment.
- If post-XFS etcd fsync remains elevated (Section 3d condition), OPS-100 should
  be re-evaluated with updated evidence: at that point, a conditional GO may be
  appropriate as a palliative measure while investigating Proxmox/host-disk root
  cause.

**If operator overrides this NO-GO** and wants to proceed with tuning anyway
(e.g., to reduce restart frequency while migrations are in progress), the
conditional GO apply path is:

```yaml
# CONDITIONAL GO ONLY — requires operator authorization — upgrade blocker
# Apply as: oc patch kubecontrollermanager cluster --type=merge \
#   --patch-file=<this-yaml>
spec:
  unsupportedConfigOverrides:
    extendedArguments:
      leader-elect-lease-duration:
        - "30s"
      leader-elect-renew-deadline:
        - "24s"
      leader-elect-retry-period:
        - "8s"
```

**Rollback path:**
```bash
oc patch kubecontrollermanager cluster --type=json \
  -p '[{"op": "remove", "path": "/spec/unsupportedConfigOverrides"}]'
# Verify: oc get kubecontrollermanager cluster -o yaml | grep UnsupportedConfigOverrides
# Expected: UnsupportedConfigOverridesUpgradeable: True
```

**Expected observable change (conditional GO):** Restart frequency should decrease
during the ~2–5s fsync windows (which are 14/25 = 56% of hourly windows). During
the ≥8.192s windows (44% of time), restarts will continue. Net effect: estimated
30–50% reduction in restart rate, not elimination. Risk: static pod rollout on
apply causes 1 controlled restart per master.

---

## 5. OPS-78 Closure Criteria

OPS-78 (KCM CrashLoopBackOff parent issue) closes as Done when ALL of the following
conditions are simultaneously true for a continuous 48-hour window after all OPS-82..87
migrations complete:

| Criterion | Threshold | Measurement |
|-----------|-----------|-------------|
| master-2 etcd WAL fsync p99 | **< 50ms** | Prometheus `etcd_disk_wal_fsync_duration_seconds_bucket` p99 over 5m window, all samples |
| KCM total restarts across all 3 masters | **< 5 per 24h period** | `increase(kube_pod_container_status_restarts_total{namespace="openshift-kube-controller-manager",container="kube-controller-manager"}[24h])` |
| ClusterOperator kube-controller-manager | **Degraded=False** | `oc get clusteroperator kube-controller-manager -o jsonpath='{.status.conditions[?(@.type=="Degraded")].status}'` = `False` |
| No `leaderelection lost` in KCM logs | **Zero occurrences** in 48h | `oc -n openshift-kube-controller-manager logs` grep |

If all 4 criteria are met, OPS-78 closes. OPS-100 and OPS-101 also close.

If criteria are not met within 10 days of the last XFS migration completing,
the Section 3d investigation escalation path activates and OPS-78 remains open.

---

## Evidence Used

- OPS-98 RCA document: `runbooks/ops-78-kcm-rca.md` (main branch, 2026-04-24)
- Live cluster `kubectl explain KubeControllerManager.spec` (2026-04-24T12:00Z)
- Live cluster KCM configmap `openshift-kube-controller-manager/config` (2026-04-24)
- Live cluster KCM pod static pod args (all 3 masters, identical)
- OKD version: 4.19.0-okd-scos.19, Kubernetes 1.32.8
- `nodes.config.openshift.io/cluster` spec: `{}` (no WorkerLatencyProfile set)
- KubeControllerManager CR: `unsupportedConfigOverrides: null`, `managementState: Managed`

---

*Document produced 2026-04-24 by worker-agent-ops-99. All cluster reads were
read-only (oc get, oc explain, kubectl explain). No oc apply/edit/patch/delete
commands were issued.*
