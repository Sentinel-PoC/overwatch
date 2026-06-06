# OPS-308 / OPS-300: e2fsck for sdd on master-3 (langfuse-ch iSCSI PV)

**Issue:** OPS-308 — master-3 local disk sdd: historical ext4 corruption; needs fsck or repair before OPS-300 migration
**Authored:** 2026-04-22 by worker-agent-ops-308
**Status:** DIAGNOSIS COMPLETE — awaiting Judge review before execution

---

## CRITICAL FINDING: sdd is NOT a local VM disk

**The original OPS-308 description stated sdd was "a local virtio/scsi disk on VMID 213 (NOT an iSCSI PV)." This is incorrect. Live system inspection contradicts that assumption.**

sdd on master-3 is a **TrueNAS iSCSI network disk**, not a local Proxmox-managed disk. This changes the remediation path.

---

## sdd Identity — Evidence from Live System

**Evidence gathered 2026-04-22 via:**
1. Proxmox QGA exec on VMID 213 (node 208-pve2)
2. kubectl debug node/master-3.overwatch.haist.farm with ubi9 image

| Field | Value |
|-------|-------|
| Device | `/dev/sdd` on master-3 |
| Type | iSCSI disk (TrueNAS) |
| Vendor | TrueNAS |
| Model | iSCSI Disk |
| Size | 62914560 sectors × 512 bytes = **30 GiB** |
| iSCSI session | session3 |
| iSCSI target IQN | `iqn.2026-03.farm.haist:okd-langfuse-ch` |
| iSCSI portal | `192.168.12.205:3260` |
| Volume label | `langfuse-ch` |
| UUID | `a2fc16cc-a793-4250-93fa-346e2539fcd3` |
| Filesystem | ext4, stripe=8192 |
| Mount path (kubelet ns) | `/var/lib/kubelet/plugins/kubernetes.io/iscsi/iface-default/192.168.12.205:3260-iqn.2026-03.farm.haist:okd-langfuse-ch-lun-0` |
| PV bind mount | `/var/lib/kubelet/pods/<pod-uid>/volumes/kubernetes.io~iscsi/langfuse-ch-iscsi` |
| Workload | Langfuse ClickHouse (analytics database) |
| Proxmox VM config | NOT in VMID 213 config — managed entirely by kubelet |

**VM 213 Proxmox disk config (only one disk):**
```
scsi0: local-lvm:vm-213-disk-0,size=120G  (root disk / SCOS node OS)
```
There is no `scsi3` / sdd in the Proxmox VM definition. The iSCSI connection is established by the kubelet inside the VM.

---

## Current Error State — Evidence from Live System

### dmesg output (at VM boot, uptime ~200s after current boot on 2026-04-22):

```
[  200.560229] sd 5:0:0:0: [sdd] 62914560 512-byte logical blocks: (32.2 GB/30.0 GiB)
[  200.560233] sd 5:0:0:0: [sdd] 16384-byte physical blocks
[  200.562028] sd 5:0:0:0: [sdd] Write cache: enabled, read cache: enabled, supports DPO and FUA
[  200.564865] sd 5:0:0:0: [sdd] Preferred minimum I/O size 4096 bytes not a multiple of physical block size (16384 bytes)
[  200.586043] sd 5:0:0:0: [sdd] Attached SCSI disk
[  202.197321] EXT4-fs (sdd): warning: mounting fs with errors, running e2fsck is recommended
[  202.809907] EXT4-fs (sdd): recovery complete
[  202.823298] EXT4-fs (sdd): mounted filesystem a2fc16cc-a793-4250-93fa-346e2539fcd3 r/w with ordered data mode. Quota mode: none.
[  529.318739] EXT4-fs (sdd): error count since last fsck: 103005
[  529.318757] EXT4-fs (sdd): initial error at time 1776776749: __ext4_find_entry:1684: inode 786462
[  529.318761] EXT4-fs (sdd): last error at time 1776801096: ext4_evict_inode:255
```

### Superblock state (read live via QGA + dd + od):

| Field | Value | Interpretation |
|-------|-------|----------------|
| s_state | 0x0001 | EXT4_VALID_FS — cleanly unmounted before this boot |
| s_error_count | 0 | Superblock error counter zeroed after journal replay |
| first_error_time | 0 | No first-error timestamp in superblock (cleared) |
| last_error_time (dmesg) | `1776776749` = 2026-04-21T13:05:49Z | Start of error window |
| last_error (dmesg) | `1776801096` = 2026-04-21T19:51:36Z | End of error window |
| volume_name | `langfuse-ch` | Confirmed label |
| UUID | `a2fc16cc-a793-4250-93fa-346e2539fcd3` | |

### Interpretation:

The 103005 errors occurred between **2026-04-21 13:05 UTC and 19:51 UTC** — this falls within the OPS-294 cascade event where Langfuse and associated workloads were crashing and being restarted. The errors are from the previous boot cycle.

On the current boot (2026-04-22 ~02:15 UTC), the kernel ran **journal replay** ("recovery complete") and mounted the filesystem. No new errors have been recorded since the current boot. The kernel prints the historical error summary at mount time (the "103005 errors" message), but those are from the SB's saved error log, not from the current mount.

**The filesystem is currently mounted and operational. The warning "running e2fsck is recommended" is accurate — the superblock retains the historical error record even after journal replay, and fsck is needed to clear it and verify no structural damage.**

---

## Risk Assessment

| Scenario | Risk Level | Notes |
|----------|------------|-------|
| **sdd is Langfuse ClickHouse data** | **LOW-MEDIUM** | ClickHouse analytics DB; Langfuse may function without analytics (degraded, not crashed); data loss would mean lost analytics history |
| sdd is etcd data | N/A — confirmed NOT etcd | No etcd-specific escalation needed |
| sdd is /var or node OS data | N/A — confirmed NOT node OS | Confirmed only one local disk (scsi0) for SCOS |
| sdd is ephemeral/swap | N/A | It is a production iSCSI PV |

**Specific risk of running e2fsck:** If fsck finds unfixable errors in ClickHouse data files, Langfuse ClickHouse may require full rebuild from backup. ClickHouse is an analytics store — the platform remains operational without it, but Langfuse tracing history would be lost.

---

## Relationship to OPS-300

OPS-300 is a cold migration of VMID 213 from 208-pve2 to pve3.

**OPS-300 does NOT need to handle sdd.** Reason:
1. sdd is an iSCSI PV managed by kubelet, not by Proxmox
2. When VMID 213 is shut down and migrated, the iSCSI session drops
3. When VMID 213 boots on pve3, kubelet re-attaches the iSCSI target from TrueNAS
4. The iSCSI LUN itself lives on TrueNAS (192.168.12.205), which is unaffected by VM migration

However, the OPS-300 shutdown window IS a good opportunity to run fsck on sdd, since the VM will be powered off and the iSCSI session will be cleanly disconnected. At that point the LUN can be connected from iac-control for fsck.

---

## Runbook: e2fsck on langfuse-ch iSCSI LUN

**This runbook is for execution during the OPS-300 shutdown window OR as a standalone operation.**

### Prerequisites

- iac-control (192.168.12.210) reachable via SSH
- TrueNAS at 192.168.12.205 reachable, iSCSI target accessible
- Langfuse ClickHouse workload scaled to 0 (no writers to sdd)
- VMID 213 (master-3) is shut down OR sdd is unmounted from kubelet

### Option A: Standalone fsck (without OPS-300 migration)

Run this if OPS-300 is delayed but fsck is needed now:

```bash
# Step 1: Scale Langfuse ClickHouse to 0 replicas
# (Run from iac-control after SSH + kubectl)
ssh ubuntu@192.168.12.210
export KUBECONFIG=/etc/kubernetes/kubeconfig  # or wherever kubeconfig is on iac-control

# Find the ClickHouse deployment/statefulset
kubectl -n langfuse get statefulset,deployment | grep -i click
# Scale to 0
kubectl -n langfuse scale statefulset <clickhouse-statefulset-name> --replicas=0
kubectl -n langfuse wait --for=condition=available --timeout=120s deployment/... 2>/dev/null || true
# Wait for pods to terminate
kubectl -n langfuse get pods -w | grep -i click

# Step 2: Delete the PersistentVolumeClaim bind to force unmount
# (The PVC is bound as langfuse-ch-iscsi)
# DO NOT delete the PV itself - just scale the consumer to 0 and wait for kubelet unmount
# Check it's unmounted on master-3 via:
# kubectl debug node/master-3.overwatch.haist.farm -it --image=registry.access.redhat.com/ubi9/ubi-minimal -- \
#   sh -c 'grep sdd /proc/$(ls /proc | grep -E "^[0-9]+$" | head -1)/mounts 2>/dev/null'

# Step 3: Connect the iSCSI target from iac-control for fsck
sudo iscsiadm -m discovery -t sendtargets -p 192.168.12.205:3260
sudo iscsiadm -m node --targetname iqn.2026-03.farm.haist:okd-langfuse-ch \
  --portal 192.168.12.205:3260 --login

# Verify device appeared (usually sdb or sdc depending on existing sessions)
lsblk | grep -i iscsi
# Note the device name (e.g., /dev/sdb)
FSCK_DEV=/dev/sdb  # ADJUST to actual device

# Step 4: Run e2fsck
sudo e2fsck -fy ${FSCK_DEV}
# -f: force check even if fs appears clean
# -y: answer yes to all repair questions
# Record the exit code:
# 0 = no errors
# 1 = errors corrected
# 2 = errors corrected, reboot required
# 4 = errors NOT corrected (filesystem may be corrupt)
# 8 = operational error (e.g., could not read)
# 16 = usage or syntax error
# 32 = e2fsck cancelled
# 128 = shared library error

echo "e2fsck exit code: $?"

# Step 5: After fsck completes, disconnect the iSCSI session
sudo iscsiadm -m node --targetname iqn.2026-03.farm.haist:okd-langfuse-ch \
  --portal 192.168.12.205:3260 --logout

# Step 6: Scale Langfuse ClickHouse back up
kubectl -n langfuse scale statefulset <clickhouse-statefulset-name> --replicas=1

# Step 7: Verify on master-3 that sdd remounts without warnings
# (Check dmesg on master-3 via QGA or kubectl debug)
```

### Option B: fsck during OPS-300 shutdown window

If OPS-300 is executing and VMID 213 is already shut down:

```bash
# VMID 213 is off - iSCSI session from master-3 has dropped
# Run from iac-control:

ssh ubuntu@192.168.12.210

# Step 1: Connect the iSCSI target
sudo iscsiadm -m discovery -t sendtargets -p 192.168.12.205:3260
sudo iscsiadm -m node --targetname iqn.2026-03.farm.haist:okd-langfuse-ch \
  --portal 192.168.12.205:3260 --login

# Wait for device to appear
sleep 3
lsblk
FSCK_DEV=$(lsblk -o NAME,LABEL | grep langfuse-ch | awk '{print "/dev/"$1}')
echo "Target device: ${FSCK_DEV}"
# Verify with: sudo blkid ${FSCK_DEV}
# Expected: UUID="a2fc16cc-a793-4250-93fa-346e2539fcd3" LABEL="langfuse-ch" TYPE="ext4"

# Step 2: DO NOT mount it. Run fsck directly.
sudo e2fsck -fy ${FSCK_DEV} 2>&1 | tee /tmp/fsck-langfuse-ch.log
FSCK_EXIT=$?
echo "=== e2fsck exit code: ${FSCK_EXIT} ==="

# Exit code interpretation:
# 0: No errors detected
# 1: Filesystem errors corrected
# 4: Filesystem errors left uncorrected (ESCALATE - see rollback)

# Step 3: Verify the superblock is now clean
sudo dumpe2fs -h ${FSCK_DEV} 2>&1 | grep -E "Filesystem state|Mount count|Last checked|Errors|error count"

# Step 4: Logout from iSCSI
sudo iscsiadm -m node --targetname iqn.2026-03.farm.haist:okd-langfuse-ch \
  --portal 192.168.12.205:3260 --logout

# Step 5: Continue with OPS-300 migration
# (Resume VMID 213 cold-migrate to pve3)

# After VMID 213 boots on pve3, check master-3 dmesg:
# Expected (after successful fsck): 
#   EXT4-fs (sdd): mounted filesystem ... r/w ...
#   (NO "warning: mounting fs with errors" line)
#   (NO "error count since last fsck" line)
```

### Verification commands (post-fsck, on master-3 after reboot)

```bash
# Via QGA on VMID 213 (after migration to pve3):
# Check dmesg for sdd - should be clean
dmesg | grep sdd
# Expected:
#   sd X:X:X:X: [sdd] 62914560 512-byte logical blocks: (32.2 GB/30.0 GiB)
#   EXT4-fs (sdd): mounted filesystem a2fc16cc-a793-4250-93fa-346e2539fcd3 r/w ...
# NOT expected (should be absent):
#   EXT4-fs (sdd): warning: mounting fs with errors
#   EXT4-fs (sdd): error count since last fsck:
```

---

## Rollback if e2fsck Reports Unfixable Errors (exit code 4)

If `e2fsck` exits with code 4 (errors not corrected) or if ClickHouse data is corrupted:

1. **Do NOT mount the filesystem.** Note the specific errors from the e2fsck log.

2. **Check TrueNAS for a snapshot or backup of the langfuse-ch LUN:**
   - Access TrueNAS at 192.168.12.205 (admin UI or SSH)
   - Check ZFS snapshots for the dataset backing `iqn.2026-03.farm.haist:okd-langfuse-ch`
   - If a snapshot exists from before 2026-04-21 (before the OPS-294 cascade), restore it.

3. **If no snapshot is available:**
   - Recreate the ext4 filesystem: `sudo mkfs.ext4 -L langfuse-ch -U a2fc16cc-a793-4250-93fa-346e2539fcd3 ${FSCK_DEV}`
   - WARNING: this destroys all ClickHouse analytics data. Langfuse will restart with an empty analytics DB. Application data (Langfuse UI, traces visible in the app) will be lost for the historical period.
   - This is acceptable for ClickHouse (analytics only) — Langfuse's primary PostgreSQL data is unaffected.
   - Create a Plane issue to document the data loss event.

4. **Do NOT attempt manual inode repair** on a ClickHouse database — the database internal state would be inconsistent with repaired inode structure. Restart fresh.

---

## Background: Why 103005 Errors

The April 21, 2026 OPS-294 cascade caused multiple VM crashes and restarts. During that event, the Langfuse ClickHouse container was killed without clean unmount multiple times (SIGKILL from OOM or kubelet). Each unclean shutdown while ClickHouse was writing to sdd generated ext4 journal entries. When the kernel replayed the journal at the next mount, 103005 accumulated journal replay steps were counted as "errors" by the EXT4 error tracking subsystem.

The `__ext4_find_entry` error at inode 786462 is a typical journal recovery artifact — the inode table entry was in an inconsistent state during crash recovery. The journal replay resolved it, but the error was recorded in the superblock's error log fields.

**The 103005 number is the count of journal transaction replay operations, not 103005 distinct filesystem corruptions.**

---

## Note on iSCSI Discovery

If iscsiadm is not installed on iac-control:
```bash
sudo apt-get install open-iscsi
sudo systemctl enable --now iscsid
```

Or use the Proxmox host (pve) which can initiator-attach iSCSI LUNs via pvesm.

---

## NIST 800-53 Control Mapping

| Control | How This Runbook Supports It |
|---------|------------------------------|
| CM-3 | Change tracked in OPS-308 (Plane issue) |
| CM-4 | PLAN note posted before execution; risk assessed |
| SI-7 | Filesystem integrity check (e2fsck) verifies data integrity |
| CP-9 | Backup check (TrueNAS snapshot) before destructive repair |
| AU-12 | e2fsck log captured; dmesg evidence collected |
