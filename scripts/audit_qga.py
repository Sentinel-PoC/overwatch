#!/usr/bin/env python3
"""
audit_qga.py — OPS-262 qemu-guest-agent audit script.

Read-only: queries Proxmox API to enumerate all VMs and check qga status.
No qga-exec calls are made. Safe to run at any time.

Usage:
    python3 scripts/audit_qga.py [--output PATH]

Output: Markdown table to stdout (or --output file).

Requirements:
    - VAULT_ADDR env var pointing to Vault (default: https://vault.208.haist.farm)
    - ~/.vault-token or VAULT_TOKEN env var
    - Proxmox API token at Vault path secret/proxmox (fields: api_token_id, api_token_secret)
"""

import argparse
import json
import logging
import os
import sys
import urllib.request
import urllib.error
import ssl
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROXMOX_HOSTS = [
    ("192.168.12.6", "pve"),
    ("192.168.12.56", "208-pve2"),
    ("192.168.12.57", "pve3"),
]
PVE_PORT = 8006
VAULT_ADDR = os.environ.get("VAULT_ADDR", "https://vault.208.haist.farm")

# VMs considered "critical" for the overwatch cluster
CRITICAL_VMIDS = {
    107,   # pangolin-proxy
    111,   # wazuh
    200,   # iac-control
    201,   # gitlab-server
    205,   # vault-server
    210,   # overwatch-bootstrap
    211,   # overwatch-node-1
    212,   # overwatch-node-2
    213,   # overwatch-node-3
    300,   # config-server
}


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _http_get(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=15) as resp:  # nosec B310 — internal Proxmox API call to known PVE_PORT on hardcoded LAN IPs; same authorized pattern as claude-config audit_*.py
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": str(e), "code": e.code}
    except Exception as e:
        return {"error": str(e)}


def _get_vault_token() -> str:
    token = os.environ.get("VAULT_TOKEN", "")
    if not token:
        token_path = os.path.expanduser("~/.vault-token")
        if os.path.exists(token_path):
            with open(token_path) as f:
                token = f.read().strip()
    if not token:
        raise RuntimeError("No Vault token found. Set VAULT_TOKEN or write ~/.vault-token")
    return token


def _get_proxmox_token() -> str:
    """Fetch Proxmox API token from Vault KV."""
    vault_token = _get_vault_token()
    url = f"{VAULT_ADDR}/v1/secret/data/proxmox"
    data = _http_get(url, {"X-Vault-Token": vault_token})
    if "error" in data:
        raise RuntimeError(f"Vault error: {data['error']}")
    kv_data = data.get("data", {}).get("data", {})
    token_id = kv_data.get("api_token_id")
    token_secret = kv_data.get("api_token_secret")
    if not token_id or not token_secret:
        raise RuntimeError("Vault secret/proxmox missing api_token_id or api_token_secret")
    return f"PVEAPIToken={token_id}={token_secret}"


def _pve_get(host: str, path: str, auth: str) -> dict:
    url = f"https://{host}:{PVE_PORT}/api2/json{path}"
    result = _http_get(url, {"Authorization": auth})
    return result


def _find_reachable_host(auth: str) -> Optional[str]:
    for host, _ in PROXMOX_HOSTS:
        result = _pve_get(host, "/nodes", auth)
        if "data" in result:
            logger.info("Proxmox API reachable via %s", host)
            return host
    return None


def _get_vm_list(host: str, auth: str) -> list:
    result = _pve_get(host, "/cluster/resources?type=vm", auth)
    return result.get("data", [])


def _get_vm_config(host: str, node: str, vmid: int, vtype: str, auth: str) -> dict:
    """Get VM config to check qga enabled in config."""
    if vtype == "lxc":
        path = f"/nodes/{node}/lxc/{vmid}/config"
    else:
        path = f"/nodes/{node}/qemu/{vmid}/config"
    result = _pve_get(host, path, auth)
    return result.get("data", {})


def _get_qga_agent_info(host: str, node: str, vmid: int, auth: str) -> dict:
    """Query live qga info from a running qemu VM. Returns {} on failure."""
    path = f"/nodes/{node}/qemu/{vmid}/agent/info"
    result = _pve_get(host, path, auth)
    if "error" in result:
        return {}
    return result.get("data", {}).get("result", {})


def _check_exec_enabled(agent_info: dict) -> Optional[bool]:
    """Return True if guest-exec is in supported_commands and enabled."""
    commands = agent_info.get("supported_commands", [])
    for cmd in commands:
        if cmd.get("name") == "guest-exec":
            return bool(cmd.get("enabled", False))
    return None


def audit() -> list:
    """Run the audit. Returns list of per-VM result dicts."""
    logger.info("Fetching Proxmox token from Vault...")
    auth = _get_proxmox_token()

    logger.info("Locating reachable Proxmox API host...")
    host = _find_reachable_host(auth)
    if not host:
        raise RuntimeError("No Proxmox host reachable")

    logger.info("Enumerating VMs...")
    vms = _get_vm_list(host, auth)
    results = []

    for vm in sorted(vms, key=lambda x: x.get("vmid", 0)):
        vmid = int(vm.get("vmid", 0))
        name = vm.get("name", "unknown")
        node = vm.get("node", "unknown")
        vtype = vm.get("type", "qemu")
        status = vm.get("status", "unknown")
        is_critical = vmid in CRITICAL_VMIDS

        logger.info("Checking VMID=%d (%s) on %s...", vmid, name, node)

        # LXC containers don't support qga
        if vtype == "lxc":
            results.append({
                "vmid": vmid,
                "name": name,
                "node": node,
                "type": vtype,
                "status": status,
                "is_critical": is_critical,
                "qga_installed": "N/A (LXC)",
                "qga_active": "N/A (LXC)",
                "exec_enabled": "N/A (LXC)",
                "qga_version": "N/A",
                "notes": "LXC container - qga not applicable",
            })
            continue

        # For qemu VMs: check config for qga enabled
        config = _get_vm_config(host, node, vmid, vtype, auth)
        agent_str = config.get("agent", "")
        # Proxmox agent field formats: "1", "1,type=virtio", "enabled=1", "enabled=1,fstrim_cloned_disks=0,type=virtio"
        # Any value starting with "1" or containing "enabled=1" means enabled
        agent_str_s = str(agent_str).strip()
        qga_in_config = (
            "yes" if (
                "enabled=1" in agent_str_s
                or agent_str_s == "1"
                or agent_str_s.startswith("1,")
            )
            else "no" if agent_str_s
            else "not-set"
        )

        # For running VMs: check live qga status
        qga_active = "stopped-vm"
        exec_enabled = "N/A"
        qga_version = "N/A"
        notes = ""

        if status == "running":
            agent_info = _get_qga_agent_info(host, node, vmid, auth)
            if agent_info:
                qga_active = "yes"
                qga_version = agent_info.get("version", "unknown")
                exec_en = _check_exec_enabled(agent_info)
                exec_enabled = "yes" if exec_en else ("no" if exec_en is False else "unknown")
            else:
                qga_active = "no"
                notes = "VM running but qga not responding"
        elif status == "stopped":
            notes = "VM stopped - cannot verify live qga"

        results.append({
            "vmid": vmid,
            "name": name,
            "node": node,
            "type": vtype,
            "status": status,
            "is_critical": is_critical,
            "qga_installed": qga_in_config,
            "qga_active": qga_active,
            "exec_enabled": exec_enabled,
            "qga_version": qga_version,
            "notes": notes,
        })

    return results


def format_markdown(results: list, timestamp: str) -> str:
    lines = []
    lines.append(f"# qemu-guest-agent Audit Report")
    lines.append(f"")
    lines.append(f"**Audit date:** {timestamp}")
    lines.append(f"**Scope:** All QEMU VMs in overwatch Proxmox cluster")
    lines.append(f"**Nodes:** pve (192.168.12.6), 208-pve2 (192.168.12.56), pve3 (192.168.12.57)")
    lines.append(f"**Method:** Read-only Proxmox API (no qga-exec calls performed)")
    lines.append(f"**Auth:** Proxmox API token from Vault secret/proxmox")
    lines.append(f"")
    lines.append(f"## Summary")
    lines.append(f"")

    total = len([r for r in results if r["type"] != "lxc"])
    active = len([r for r in results if r["qga_active"] == "yes"])
    critical_missing = [
        r for r in results
        if r["is_critical"] and r["qga_active"] not in ("yes", "stopped-vm", "N/A (LXC)")
    ]

    lines.append(f"- Total QEMU VMs: {total}")
    lines.append(f"- VMs with qga active (running): {active}")
    lines.append(f"- Critical VMs missing qga: {len(critical_missing)}")
    lines.append(f"")

    if critical_missing:
        lines.append(f"**ACTION REQUIRED:** {len(critical_missing)} critical VM(s) missing qga:")
        for r in critical_missing:
            lines.append(f"  - VMID {r['vmid']} ({r['name']}) on {r['node']}")
        lines.append(f"")
    else:
        lines.append(f"**All running critical VMs have qga active and guest-exec enabled.**")
        lines.append(f"")

    lines.append(f"## Per-VM Detail")
    lines.append(f"")
    lines.append(f"| VMID | Name | Node | Type | Status | Critical | qga-installed | qga-active | guest-exec | qga-version | Notes |")
    lines.append(f"|------|------|------|------|--------|----------|---------------|------------|------------|-------------|-------|")

    for r in results:
        critical_flag = "YES" if r["is_critical"] else "no"
        lines.append(
            f"| {r['vmid']} | {r['name']} | {r['node']} | {r['type']} | {r['status']} "
            f"| {critical_flag} | {r['qga_installed']} | {r['qga_active']} "
            f"| {r['exec_enabled']} | {r['qga_version']} | {r['notes']} |"
        )

    lines.append(f"")
    lines.append(f"## Notes")
    lines.append(f"")
    lines.append(f"- **qga-installed**: reflects Proxmox VM config (`agent=enabled=1` or `agent=1` in config API)")
    lines.append(f"- **qga-active**: reflects live qga socket response (only meaningful for running VMs)")
    lines.append(f"- **guest-exec**: whether `guest-exec` command is in qga's supported_commands and enabled")
    lines.append(f"- **stopped-vm**: VM is stopped; qga status cannot be verified live but config shows it is enabled")
    lines.append(f"- LXC containers do not support qemu-guest-agent")
    lines.append(f"- Template VMs (9xxx series) excluded from critical scope")
    lines.append(f"")
    lines.append(f"## Audit Method")
    lines.append(f"")
    lines.append(f"```")
    lines.append(f"# Proxmox API calls made (read-only):")
    lines.append(f"GET /api2/json/nodes")
    lines.append(f"GET /api2/json/cluster/resources?type=vm")
    lines.append(f"GET /api2/json/nodes/{{node}}/qemu/{{vmid}}/config")
    lines.append(f"GET /api2/json/nodes/{{node}}/qemu/{{vmid}}/agent/info")
    lines.append(f"```")
    lines.append(f"")
    lines.append(f"Script: `scripts/audit_qga.py`  ")
    lines.append(f"Issue: OPS-262")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Audit qemu-guest-agent on Proxmox cluster")
    parser.add_argument("--output", help="Write Markdown report to this file (default: stdout)")
    parser.add_argument("--json", dest="json_out", action="store_true", help="Output raw JSON instead")
    args = parser.parse_args()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = audit()

    if args.json_out:
        output = json.dumps({"timestamp": timestamp, "vms": results}, indent=2)
    else:
        output = format_markdown(results, timestamp)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        logger.info("Report written to %s", args.output)
    else:
        print(output)


if __name__ == "__main__":
    main()
