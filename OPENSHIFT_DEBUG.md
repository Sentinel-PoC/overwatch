# OpenShift Deployment Debug - Issue Tracking

**Status:** Critical / Fails Deployment
**Date:** 2026-01-20
**Scope:** Provisioning `overwatch` cluster (VMs 210, 211, 212, 213) on Proxmox.

## Environment Context
- **Proxmox Node 1:** `192.168.12.6` (pve)
- **Proxmox Node 2:** `192.168.12.56` (208-pve2)
- **Terraform/Tofu Controller:** `192.168.12.210` (IaC Control Node)
- **Target Storage:** `local-lvm` (LVM-Thin)
- **Boot Media:** `local:iso/fedora-coreos.iso` (Verified present on both nodes)

## Errors Observed (Pipeline 66)

### 1. State/Cleanup Conflict (Node 1)
```text
Error: error creating VM: All attempts fail:
#1: received an HTTP 500 response - Reason: unable to create VM 210 - VM 210 already exists on node 'pve'
```
*Cause:* Previous failed pipelines left zombie VMs. OpenTofu state might be out of sync.
*Fix:* Manual force destroy of VMs 210 & 211 on Node 1.

### 2. Storage Format Mismatch (Node 2)
```text
Error: error waiting for VM creation... unable to create VM 212 - unsupported format 'qcow2' at /usr/share/perl5/PVE/Storage/LvmThinPlugin.pm line 101.
```
*Cause:* The `disk` block in `main.tf` is attempting to create a `qcow2` image.
*Technical Detail:* The target datastore is `local-lvm`. LVM-Thin storage **only** supports `raw` block devices. It does not support `qcow2` files.
*Fix:* Explicitly set `file_format = "raw"` in the `disk` configuration block in `main.tf`.

## Remediation Plan
1.  **Cleanup:** SSH into both Proxmox nodes and forcefully destroy VMs 210, 211, 212, 213.
2.  **Code Fix:** Update `overwatch-work/infrastructure/main.tf`:
    - Add `file_format = "raw"` to all `disk` blocks.
3.  **Deployment:** Push changes and trigger a new pipeline.
