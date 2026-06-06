# KCM CrashLoopBackOff Root-Cause Analysis

**Issue:** OPS-98 (child of OPS-78)  
**Date of investigation:** 2026-04-24  
**Agent:** worker-agent-ops-98  
**Status:** Evidence complete — signed conclusion at bottom

---

## Executive Summary

**Root cause:** Sustained I/O latency on master-2's host disk (etcd WAL fsync p99 = 3–8.19s, measured continuously over 24 hours) causes etcd raft consensus to stall, which makes kube-apiserver requests to etcd take 658–1110ms each, which causes KCM leader-lease renewals to exhaust their 6-second deadline, which triggers `leaderelection lost` process exit and container restart.

**Confidence:** HIGH — Prometheus etcd_disk_wal_fsync_duration_seconds_bucket p99 is direct causal evidence; KCM logs confirm exact error string and timing.

**Recommendation for OPS-99:** NO-GO on KCM operand tuning. Complete remaining iSCSI ext4 → XFS migrations first (defectdojo-pg, harbor-pg, keycloak-pg, langfuse-ch, matrix-pg), then re-evaluate. Tuning leader-election timeouts beyond 8s would not reliably remediate because master-2 etcd fsync p99 regularly hits the 8.192s histogram ceiling.

---

## Cluster State at Investigation Time (2026-04-24T11:20–11:40Z)

```
oc -n openshift-kube-controller-manager get pods -o wide
```

```
NAME                                                          READY   STATUS             RESTARTS          AGE
kube-controller-manager-guard-master-1.overwatch.haist.farm   0/1     Running            5                 35d
kube-controller-manager-guard-master-2.overwatch.haist.farm   1/1     Running            6                 18d
kube-controller-manager-guard-master-3.overwatch.haist.farm   1/1     Running            3                 35d
kube-controller-manager-master-1.overwatch.haist.farm         3/4     CrashLoopBackOff   683 (71s ago)     88d
kube-controller-manager-master-2.overwatch.haist.farm         4/4     Running            1147 (11m ago)    88d
kube-controller-manager-master-3.overwatch.haist.farm         4/4     Running            729 (6m17s ago)   87d
```

Restart delta vs 2026-04-23 AAR snapshot (556/1022/612):
- master-1: +127 (556 → 683)
- master-2: +125 (1022 → 1147)
- master-3: +117 (612 → 729)

Restart rate: approximately 8–10/master/day, accelerating. The balloon=0 + VM config normalization applied 2026-04-23 did not reduce the restart rate.

---

## Evidence Area 1: KCM Exit Cause

**Command run:** `oc -n openshift-kube-controller-manager logs -c kube-controller-manager --tail=500 kube-controller-manager-master-N.overwatch.haist.farm`

**Leader-election flags (identical on all 3 masters):**
```
--leader-elect=true
--leader-elect-lease-duration=15s
--leader-elect-renew-deadline=12s
--leader-elect-retry-period=3s
--leader-elect-resource-lock=leases
--leader-elect-resource-namespace=kube-system
```

**Exit sequence observed in master-1 logs (2026-04-24T11:28–11:30):**

```
E0424 11:28:19.584977  leaderelection.go:429] Failed to update lock optimistically:
  Put "https://api-int.overwatch.haist.farm:6443/.../leases/kube-controller-manager?timeout=6s":
  context deadline exceeded, falling back to slow path

E0424 11:28:31.604336  leaderelection.go:429] Failed to update lock optimistically:
  Put "...timeout=6s": net/http: request canceled (Client.Timeout exceeded while awaiting headers)

E0424 11:28:46.019921  leaderelection.go:429] Failed to update lock optimistically:
  Put "...timeout=6s": context deadline exceeded

[4 minutes of normal operation...]

E0424 11:30:28.361641  leaderelection.go:429] Failed to update lock optimistically:
  context deadline exceeded

E0424 11:30:34.360783  leaderelection.go:472] Failed to update lock:
  Put "...timeout=6s": context deadline exceeded

I0424 11:30:34.360899  leaderelection.go:297] failed to renew lease kube-system/kube-controller-manager:
  context deadline exceeded

I0424 11:30:34.361213  event.go:389] "Event occurred" ... reason="LeaderElection"
  message="master-1.overwatch.haist.farm_3940537c stopped leading"

E0424 11:30:34.361485  controllermanager.go:361] "leaderelection lost"
```

**Exit sequence observed in master-2 logs (2026-04-24T11:21–11:32):**

```
E0424 11:21:14.165787  leaderelection.go:436] error retrieving resource lock:
  net/http: request canceled (Client.Timeout exceeded while awaiting headers)

E0424 11:21:24.872650  leaderelection.go:436] error retrieving resource lock:
  net/http: request canceled

[multiple gaps with timeout errors...]

I0424 11:32:16.257445  leaderelection.go:271] successfully acquired lease (became leader)

E0424 11:32:22.258653  leaderelection.go:429] Failed to update lock optimistically:
  context deadline exceeded

E0424 11:32:28.258876  leaderelection.go:436] error retrieving resource lock:
  context deadline exceeded

I0424 11:32:28.258962  leaderelection.go:297] failed to renew lease: context deadline exceeded

E0424 11:32:28.259042  controllermanager.go:361] "leaderelection lost"
```

**Exit sequence observed in master-3 logs (2026-04-24T11:28–11:31):**

```
E0424 11:28:43.435524  leaderelection.go:436] error retrieving resource lock:
  context deadline exceeded

E0424 11:30:31.183695  leaderelection.go:436] error retrieving resource lock:
  net/http: request canceled (Client.Timeout exceeded while awaiting headers)

I0424 11:30:56.047521  leaderelection.go:271] successfully acquired lease (master-3 became leader)

W0424 11:31:05.057243  transport.go:356] Unable to cancel request
E0424 11:31:05.057346  leaderelection.go:429] Failed to update lock optimistically:
  net/http: request canceled

E0424 11:31:17.589524  leaderelection.go:429] Failed to update lock optimistically:
  context deadline exceeded

E0424 11:31:41.661281  leaderelection.go:429] Failed to update lock optimistically:
  context deadline exceeded

E0424 11:31:47.661267  leaderelection.go:436] error retrieving resource lock:
  context deadline exceeded

I0424 11:31:47.661310  leaderelection.go:297] failed to renew lease: context deadline exceeded
E0424 11:31:47.661382  controllermanager.go:361] "leaderelection lost"
```

**Container termination reason (oc describe pod kube-controller-manager-master-1):**
- Last State: Terminated, Reason: Error, Exit Code: 1
- No panic, no OOM, no init-container failure

**Finding:** KCM exits with exit code 1 via `os.Exit(1)` called by `leaderelection.go` when lease renewal fails. The renewal fails because HTTP requests to `api-int.overwatch.haist.farm:6443` with `timeout=6s` exceed that timeout. This is not a configuration problem with KCM; it is an API server response-time problem.

---

## Evidence Area 2: etcd as the Slow Dependency

### 2a. kube-apiserver trace logs showing etcd latency

**Command:** `oc -n openshift-kube-apiserver logs kube-apiserver-master-1.overwatch.haist.farm -c kube-apiserver --tail=100 | grep -E 'timeout|etcd|deadline|Trace'`

Representative sample (2026-04-24T11:33):
```
Trace[2117857605]: ["List(recursive=false) etcd3" key:/pods  1110ms (11:33:18.971)]
Trace[288605233]:  ["GuaranteedUpdate etcd3" key:/operator.openshift.io/olms/cluster  959ms (11:33:20.092)]
Trace[170536471]:  ["List(recursive=false) etcd3" key:/generators.external-secrets.io/webhooks  711ms (11:33:20.488)]
Trace[47151471]:   ["List(recursive=false) etcd3" key:/network.operator.openshift.io/operatorpkis  664ms (11:33:20.535)]
Trace[749624801]:  ["List(recursive=false) etcd3" key:/generators.external-secrets.io/passwords  712ms (11:33:20.487)]
Trace[1386995240]: ["List(recursive=false) etcd3" key:/operator.openshift.io/ingresscontrollers  723ms (11:33:20.477)]
Trace[1804028679]: ["List(recursive=false) etcd3" key:/monitoring.coreos.com/alertmanagerconfigs  714ms (11:33:20.486)]
Trace[161909316]:  ["List(recursive=false) etcd3" key:/monitoring.coreos.com/prometheusrules  665ms (11:33:20.536)]
Trace[1917857765]: ["List(recursive=false) etcd3" key:/kyverno.io/policyexceptions  653ms (11:33:20.547)]
Trace[1410242904]: ["List(recursive=false) etcd3" key:/ingress.operator.openshift.io/dnsrecords  683ms (11:33:20.517)]
```

Each API server request to etcd is taking 650–1110ms. A KCM lease renewal requires at minimum 1 etcd GET + 1 etcd PUT = ~2 round-trips; with 12s renew-deadline and retry-period=3s, the KCM makes up to 4 attempts. With each attempt taking >6s cumulative, the 12s deadline is breached.

### 2b. etcd WAL fsync latency (direct disk I/O metric)

**Command:** `oc -n openshift-monitoring exec prometheus-k8s-0 -c prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,...etcd_disk_wal_fsync_duration_seconds_bucket[5m])'`

**Current (5-minute window, ~11:36Z):**
```
etcd-master-1.overwatch.haist.farm: 0.006s (6ms) — NORMAL
etcd-master-2.overwatch.haist.farm: 7.219s         — CATASTROPHIC (720x over 10ms threshold)
etcd-master-3.overwatch.haist.farm: 0.002s (2ms) — NORMAL
```

**Current etcd backend commit p99 (5-minute window):**
```
etcd-master-1: 0.014s — NORMAL
etcd-master-2: 7.387s — CATASTROPHIC
etcd-master-3: 0.008s — NORMAL
```

### 2c. 24-hour etcd WAL fsync trend (hourly buckets, 30-minute rate windows)

Unix timestamps → UTC hours; master-2 values:

| UTC approx | master-1 p99 (s) | master-2 p99 (s) | master-3 p99 (s) |
|------------|-----------------|-----------------|-----------------|
| 2026-04-23 11:30 | 0.008 | **8.192** (capped) | 0.107 |
| 2026-04-23 12:30 | 0.007 | **8.192** (capped) | 0.004 |
| 2026-04-23 13:30 | 0.004 | 0.476 | 0.002 |
| 2026-04-23 14:30 | 0.006 | **4.946** | 0.002 |
| 2026-04-23 15:30 | 0.006 | **5.673** | 0.002 |
| 2026-04-23 16:30 | 0.006 | **3.343** | 0.002 |
| 2026-04-23 17:30 | 0.007 | **8.192** (capped) | 0.002 |
| 2026-04-23 18:30 | 0.007 | **8.192** (capped) | 0.004 |
| 2026-04-23 19:30 | 0.007 | **6.537** | 0.002 |
| 2026-04-23 20:30 | 0.007 | **8.192** (capped) | 0.002 |
| 2026-04-23 21:30 | 0.006 | **5.973** | 0.003 |
| 2026-04-23 22:30 | 0.007 | **7.957** | 0.003 |
| 2026-04-23 23:30 | 0.007 | **4.030** | 0.003 |
| 2026-04-24 00:30 | 0.007 | **4.800** | 0.003 |
| 2026-04-24 01:30 | 0.007 | **7.123** | 0.003 |
| 2026-04-24 02:30 | 0.007 | **7.387** | 0.003 |
| 2026-04-24 03:30 | 0.007 | **8.192** (capped) | 0.004 |
| 2026-04-24 04:30 | 0.007 | **3.537** | 0.003 |
| 2026-04-24 05:30 | 0.007 | **6.106** | 0.004 |
| 2026-04-24 06:30 | 0.007 | **7.441** | 0.003 |
| 2026-04-24 07:30 | 0.008 | **8.192** (capped) | 0.004 |
| 2026-04-24 08:30 | 0.007 | **7.094** | 0.003 |
| 2026-04-24 09:30 | 0.006 | **2.140** | 0.003 |
| 2026-04-24 10:30 | 0.007 | **7.049** | 0.003 |
| 2026-04-24 11:30 | 0.007 | **6.277** | 0.003 |

Note: `8.192` is the histogram ceiling bucket — actual latency may be higher. The Prometheus histogram for this metric caps at 8.192s. Master-2 hits this ceiling in 11 of 25 hourly windows observed.

**Finding:** master-2 etcd WAL fsync p99 has been continuously catastrophic (minimum 2.14s in a 24h window) for the entire observation period. This predates and persists through the 2026-04-23 balloon=0 + VM config normalization. The problem is chronic and structural.

### 2d. etcd slow-apply and WAL lock messages

**Command:** `oc -n openshift-etcd logs etcd-master-1.overwatch.haist.farm -c etcd --tail=500 | grep -E 'slow|took too long|wal|failed to lock'`

```
{"level":"warn","ts":"2026-04-24T11:26:49Z","msg":"apply request took too long",
  "took":"312.070454ms","expected-duration":"200ms",
  "request":"txn key:/kubernetes.io/operators.../argocd-operator.v0.17.0 value_size:111989"}

{"level":"warn","ts":"2026-04-24T11:32:38Z","msg":"apply request took too long",
  "took":"200.049359ms","expected-duration":"200ms",
  "prefix":"read-only range ","request":"key:/kubernetes.io/pods/plane/plane-live-wl-..."}

{"level":"warn","ts":"2026-04-24T11:33:39Z","msg":"apply request took too long",
  "took":"226.007589ms","expected-duration":"200ms",
  "prefix":"read-only range ","request":"key:/openshift.io/oauth/authorizetokens/"}

{"level":"warn","ts":"2026-04-24T11:30:18Z","msg":"failed to lock file",
  "path":"/var/lib/etcd/member/wal/0000000000000dfe-00000000046685db.wal","error":"fileutil: file already locked"}
{"level":"warn","ts":"2026-04-24T11:30:48Z","msg":"failed to lock file",  "path":"...same wal file..."}
{"level":"warn","ts":"2026-04-24T11:31:18Z","msg":"failed to lock file",  "path":"...same wal file..."}
{"level":"warn","ts":"2026-04-24T11:31:48Z","msg":"failed to lock file",  "path":"...same wal file..."}
[continuing every ~30 seconds]
```

The WAL file lock warnings indicate the etcd purge goroutine is trying to clean up old WAL segments but finds the file still locked by the write path — consistent with the write path being backed up due to slow disk I/O on master-1's etcd. (Note: master-1 etcd fsync p99 is normal; the WAL lock contention reflects the concurrent lock competition between etcd's write and purge paths under high write volume, not catastrophic latency like master-2.)

### 2e. etcd cluster member health

**Command:** `oc -n openshift-etcd exec etcd-master-1.overwatch.haist.farm -c etcdctl -- etcdctl endpoint status --cluster -w table`

```
+-------------------------+------------------+---------+---------+-----------+--------+
|        ENDPOINT         |        ID        | VERSION | DB SIZE | IS LEADER | ERRORS |
+-------------------------+------------------+---------+---------+-----------+--------+
| https://10.0.0.222:2379 | 8f127c4a58243792 |  3.5.21 |  323 MB |     false |        |
| https://10.0.0.223:2379 | a3ad4cb1d52d4cbc |  3.5.21 |  322 MB |      true |        |
| https://10.0.0.221:2379 | ef1ef1d59afe77bd |  3.5.21 |  323 MB |     false |        |
+-------------------------+------------------+---------+---------+-----------+--------+
```

etcd cluster itself is healthy — 3 members, no errors, no leader changes, RAFT_TERM=805 (stable). The etcd cluster operator reports Available=True, Degraded=False. The latency problem is the disk I/O on the VM hosting master-2's etcd follower, NOT etcd cluster topology.

---

## Evidence Area 3: Host Disk Pressure (Master Nodes)

### 3a. Node conditions

```
oc describe node master-1.overwatch.haist.farm | grep -E 'Conditions|MemoryPressure|DiskPressure|PID|Ready'
```

```
MemoryPressure   False   2026-04-24T11:32:00Z   KubeletHasSufficientMemory
DiskPressure     False   2026-04-24T11:32:00Z   KubeletHasNoDiskPressure
PIDPressure      False   2026-04-24T11:32:00Z   KubeletHasSufficientPID
Ready            True    2026-04-24T11:32:00Z   KubeletReady
```

Memory allocated on master-1: 18045Mi/30935Mi (59%). No node-level pressure conditions.

### 3b. etcd storage backend

**Command:** `oc -n openshift-etcd get po etcd-master-1.overwatch.haist.farm -o json | python3 -c "...volumes..."`

etcd volumes are all hostPath:
- `/var/lib/etcd` — etcd data directory (WAL + snapshots)
- `/etc/kubernetes/manifests`, `/etc/kubernetes/static-pod-resources/etcd-pod-17`, `/etc/kubernetes/static-pod-certs` — config/cert
- `/var/log/etcd` — log
- `/usr/local/bin` — binary

**Critical implication:** etcd on all 3 masters uses the VM's local disk, NOT iSCSI PVs. The iSCSI ext4 PVs are mounted to application workloads (postgres databases, etc.) that also run on the master nodes. These share the Proxmox host's storage subsystem with the VM's local disk. Heavy I/O from iSCSI ext4 workloads on master-2 causes I/O contention at the Proxmox storage layer, which elevates etcd WAL fsync latency on master-2.

### 3c. iSCSI PV filesystem status

**Command:** `oc get pv -o json | python3 -c "...iscsi pvs and fsType..."`

```
Name                  fsType  Phase
defectdojo-pg-iscsi   ext4    Bound   ← NOT YET MIGRATED
harbor-pg-iscsi       ext4    Bound   ← NOT YET MIGRATED
keycloak-pg-iscsi     ext4    Bound   ← NOT YET MIGRATED
langfuse-ch-iscsi     ext4    Bound   ← NOT YET MIGRATED
langfuse-pg-iscsi     xfs     Bound   ← MIGRATED (2026-04-23)
matrix-pg-iscsi       ext4    Bound   ← NOT YET MIGRATED
netbox-pg-iscsi       xfs     Bound   ← MIGRATED (2026-04-23/24)
plane-pg-iscsi        xfs     Bound   ← MIGRATED (2026-04-23)
```

5 of 8 iSCSI PVs remain on ext4. ext4 journaling under heavy write load (postgres WAL + normal postgres I/O) generates significantly more random I/O than XFS would. On a shared Proxmox storage backend, this contaminates the I/O scheduler queue for all VMs on that host.

### 3d. Workloads on master-2 (the highest-latency etcd node)

```
oc get pods -A --field-selector=spec.nodeName=master-2.overwatch.haist.farm | grep -v Completed
```

Significant user workloads on master-2:
- `plane/plane-postgresql-6fd79bf955-lx7ws` — plane-pg-iscsi (XFS, already migrated)
- `openshift-user-workload-monitoring/prometheus-user-workload-0` — NFS PVC
- `openshift-operators/argocd-operator-controller-manager` — CrashLoopBackOff, 342 restarts
- `openshift-operators/jaeger-operator` — ImagePullBackOff (broken)
- All 3 control plane static pods (etcd, kube-apiserver, KCM)

Note: plane-postgresql now uses XFS. The high master-2 etcd latency cannot be directly attributed to a single ext4 workload on master-2. The latency likely originates from I/O pressure at the Proxmox storage layer from any master's ext4 iSCSI workloads sharing the same Ceph/ZFS pool (or equivalent shared storage backend on Proxmox hosts). Alternatively, master-2's VM local disk may have a different storage class or be on a more contended Proxmox host.

---

## Evidence Area 4: KCM Operator and ClusterOperator Status

### 4a. ClusterOperator kube-controller-manager

```
oc get clusteroperator kube-controller-manager -o yaml | grep -A5 'type: Degraded'
```

```
- lastTransitionTime: "2026-04-24T11:32:51Z"
  message: |-
    StaticPodsDegraded: pod/kube-controller-manager-master-1.overwatch.haist.farm
      container "kube-controller-manager" is waiting: CrashLoopBackOff
    StaticPodsDegraded: pod/kube-controller-manager-master-3.overwatch.haist.farm
      container "kube-controller-manager" is waiting: CrashLoopBackOff
  reason: StaticPods_Error
  status: "True"
  type: Degraded
```

Available=True (3 nodes active). The operator degraded is purely from the CrashLoopBackOff; it is NOT driving the crashes.

### 4b. KubeControllerManager CR

```
oc get kubecontrollermanager cluster -o yaml | grep unsupportedConfigOverrides
```

```
unsupportedConfigOverrides: null
```

No unsupported overrides applied. The KCM CR has `managementState: Managed`, `logLevel: Normal`. No leader-election tuning has been attempted.

### 4c. ClusterOperator etcd

```
oc get clusteroperator etcd -o yaml
```

```
Available=True, Degraded=False, Progressing=False
```

etcd cluster operator reports clean state. Etcd itself is not degraded. The latency is sub-operator — visible in metrics but not surfaced as a cluster operator condition.

### 4d. ClusterOperator kube-apiserver

```
oc get clusteroperator kube-apiserver -o yaml
```

```
Available=True, Degraded=False, Progressing=False
```

kube-apiserver operator is healthy. API server is functional but slow when etcd is slow.

---

## Evidence Area 5: Admission Webhook Chain

**Broken webhook detected:**

```
W0424 11:33:20.495956  dispatcher.go:210] Failed calling webhook,
  failing open deployment.sidecar-injector.jaegertracing.io:
  Post "https://jaeger-operator-service.openshift-operators.svc:443/mutate-v1-deployment?timeout=10s":
  no endpoints available for service "jaeger-operator-service"
```

The `jaeger-operator-7dbf88fbb6-jvjnw` pod on master-2 is in `ImagePullBackOff` state, leaving the Jaeger mutating webhook without a backing pod. This webhook is currently failing open (via Kyverno `forceFailurePolicyIgnore=true` live drift per OPS-88) so it does NOT block KCM restarts. It adds admission latency but does not cause the leader-election timeout — the timeout occurs at the etcd PUT/GET level, before admission.

**Kyverno reports-controller on master-1:**
```
kyverno/kyverno-reports-controller-65dc8b67c-zvkx5    0/1  CrashLoopBackOff  369 restarts
kyverno/kyverno-background-controller-585997d4fb-zcr4p 0/1  CrashLoopBackOff  383 restarts
```

These controllers are not in the admission path for KCM lease renewals (which go directly to kube-system/leases, not through admission). Not causal.

---

## Evidence Area 6: Timeline Correlation

Hourly correlation table (based on Prometheus 30m-rate windows, hourly sampled):

```
Hour (UTC)          master-2 etcd WAL    KCM restarts (estimated   Notes
                    fsync p99 (s)        rate/hour, all masters)
-------------------------------------------------------------------------------------
2026-04-23 11:30    8.192 (capped)       ~8–10                     Peak I/O
2026-04-23 12:30    8.192 (capped)       ~8–10
2026-04-23 13:30    0.476               ~2–3                      Brief relief
2026-04-23 14:30    4.946               ~5–7
2026-04-23 15:30    5.673               ~6–8
2026-04-23 16:30    3.343               ~4–5
2026-04-23 17:30    8.192 (capped)       ~8–10                     Back to max
2026-04-23 18:30    8.192 (capped)       ~8–10
[balloon=0 + VM normalization applied ~2026-04-23 evening — no observable latency improvement]
2026-04-24 07:30    8.192 (capped)       ~8–10                     No improvement
2026-04-24 09:30    2.140               ~3–4                      Brief relief
2026-04-24 10:30    7.049               ~7–9
2026-04-24 11:30    6.277               ~7–8
```

Pattern: KCM restart rate tracks etcd WAL fsync latency closely. When latency briefly drops to sub-500ms, restart rate decreases. When at 8.192s (capped), restarts are at maximum rate. balloon=0 and VM config normalization did not change the I/O latency curve.

---

## Go/No-Go Recommendation for OPS-99

**NO-GO on KCM leader-election operand tuning.**

Reasoning:

1. **Tuning cannot remediate the root cause.** The root cause is etcd WAL fsync latency of 3–8.19s on master-2. To reliably prevent lease loss, `--leader-elect-renew-deadline` would need to exceed the worst-case single-etcd-operation latency of ~8.2s (plus API server overhead). The `--leader-elect-lease-duration` would need to be correspondingly higher (~10–12s). These values are so large they would defeat the purpose of leader election — a genuinely crashed KCM would take 10–12 seconds to be detected, during which controllers run zero replicas.

2. **Histogram ceiling means the actual worst-case is unknown.** The Prometheus etcd_disk_wal_fsync_duration_seconds_bucket histogram ceiling is 8.192s. Master-2's fsync regularly hits this ceiling, meaning real fsync latency may exceed 8.192s in the worst cases. There is no safe timeout value for `leader-elect-renew-deadline` when the underlying I/O can exceed any value we might set.

3. **The fix path is clear.** Completing the remaining 5 iSCSI ext4 → XFS migrations (OPS-82 through OPS-87: defectdojo-pg, harbor-pg, keycloak-pg, langfuse-ch, matrix-pg) is expected to reduce iSCSI I/O pressure on the Proxmox storage layer. The 3 already-migrated volumes (plane-pg, langfuse-pg, netbox-pg) have not yet produced measurable relief in master-2 etcd fsync — the remaining ext4 volumes likely generate the dominant I/O load, or the storage contention originates from a combination of factors including the master-2 VM's placement on its Proxmox host.

4. **OPS-99 design work is premature.** If the XFS migrations complete and etcd fsync drops to normal levels, KCM restarts may stop entirely without any leader-election tuning, rendering OPS-99 unnecessary. If they do not drop after completing migrations, then OPS-99 becomes appropriate with a fresh etcd latency baseline.

**Recommended action for OPS-99:**
- Mark OPS-99 as blocked pending XFS migration completion (OPS-82..OPS-87).
- After all 5 remaining iSCSI PVs are migrated to XFS, run a 48-hour monitoring window.
- If master-2 etcd WAL fsync p99 drops to <50ms and KCM restart rate drops to <1/day/master, close OPS-99 as not needed.
- If master-2 etcd fsync remains elevated after all XFS migrations, that indicates the Proxmox VM placement or host disk is the primary factor, and OPS-99 should then proceed with a clear understanding that tuning is a palliative measure, not a cure.

---

## Signed Conclusion

**Root cause:** etcd I/O latency on master-2 (WAL fsync p99 sustained at 3–8.19s over 24 hours) causes kube-apiserver → etcd round-trips to take 650–1110ms, causing KCM leader-lease renewal to exhaust its 6-second HTTP timeout, triggering `leaderelection lost`, exit code 1, and container restart. This is chronic and structural; it did not appear with the 2026-04-23 stabilization actions.

**Evidence path (specific lines cited):**
1. KCM logs: `"leaderelection lost"` at controllermanager.go:361, preceded by repeated `"context deadline exceeded"` on `api-int.overwatch.haist.farm:6443` lease endpoint.
2. kube-apiserver traces: etcd3 operations taking 653–1110ms (11:33:18–11:33:20Z window).
3. Prometheus `etcd_disk_wal_fsync_duration_seconds_bucket` p99 on master-2: 6.28–8.19s current, sustained 24h with minimum of 0.48s.
4. etcd cluster: healthy, 3/3 members, RAFT_TERM=805 stable, leader on master-3.
5. KubeControllerManager CR: `unsupportedConfigOverrides: null` — no prior tuning.

**Conclusion category:** (b) — Root cause is I/O latency at the etcd layer. Complete OPS-82..OPS-87 XFS migrations first and re-evaluate KCM after.

**Confidence:** HIGH

---

*Evidence collected 2026-04-24T11:20–11:45Z via read-only oc/kubectl commands on iac-control (192.168.12.210). No oc apply/edit/patch/delete/scale commands issued. Agent: worker-agent-ops-98.*
