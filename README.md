# Overwatch — OKD 4.19 SCOS Deployment

[![pipeline status](http://192.168.12.68/admin1/overwatch/badges/main/pipeline.svg)](http://192.168.12.68/admin1/overwatch/-/commits/main)

## Overview
This configuration implements a "from scratch" deployment of OKD 4.19 using **CentOS Stream CoreOS (SCOS)** on Proxmox, following the official UPI guides.

## Architecture

### Network (10.0.0.0/24)
- **Gateway / DNS / LB / Web Server**: `10.0.0.1` (VM 200 - iac-control)
- **Bootstrap**: `10.0.0.220` (VM 210)
- **Master 0**: `10.0.0.221` (VM 211)
- **Master 1**: `10.0.0.222` (VM 212)
- **Master 2**: `10.0.0.223` (VM 213)
- **VIP (API/Ingress)**: `10.0.0.1`

### Components
1.  **OpenTofu**: Provisions Proxmox VMs (Bootstrap + Masters) and generates inventory.
2.  **Ansible**: Configures the Control Node (VM 200) with:
    -   **HAProxy**: Load balances API (6443), MachineConfig (22623), and Ingress (80/443).
    -   **Dnsmasq**: Provides DNS/DHCP for the cluster.
    -   **Nginx/Apache**: Serves Ignition files and SCOS artifacts (RootFS).
3.  **Shell Scripts**: Handle artifact extraction (SCOS Kernel/RootFS) and Ignition generation.

## Deployment Steps

### Phase 1: Preparation (Control Node)
1.  Install `openshift-install` (4.19) and `oc` client.
2.  Run `scripts/01_download_artifacts.sh` to fetch SCOS Kernel, Initramfs, and RootFS.
3.  Configure HAProxy and DNS on the Control Node using Ansible.

### Phase 2: Infrastructure (OpenTofu)
1.  Run OpenTofu to create empty VMs with correct MAC addresses.
2.  Update DHCP static leases on Control Node (via Ansible).

### Phase 3: Ignition Generation
1.  Populate `install-config.yaml`.
2.  Run `openshift-install create ignition-configs`.
3.  Host `bootstrap.ign`, `master.ign`, `worker.ign` on the Web Server.

### Phase 4: Boot
1.  Boot **Bootstrap Node** via PXE/iPXE with SCOS Kernel Args.
    -   Critical Arg: `coreos.live.rootfs_url=http://10.0.0.1:8080/scos-rootfs.img`
2.  Wait for Bootstrap completion.
3.  Boot **Master Nodes**.
4.  Approve CSRs.

## Prerequisites
- Proxmox Terraform Provider configured.
- SSH access to Proxmox Nodes and Control Node.

## CI/CD Pipeline

The CI pipeline runs on every push to `main` and on merge requests:

- **Lint**: yamllint, tflint
- **Security**: Trivy IaC scan, gitleaks secret detection
- **Generate**: Ignition config generation (manual)
- **Provision**: OpenTofu plan/apply (apply is manual)

## Related Repos

| Repo | Purpose |
|------|---------|
| [sentinel-iac](http://192.168.12.68/admin1/sentinel-iac) | Ansible playbooks, Terraform, Packer, compliance |
| [overwatch-gitops](http://192.168.12.68/admin1/overwatch-gitops) | K8s/OKD manifests, ArgoCD apps (push = deploy) |
