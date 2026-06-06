"""
l1_proxmox.py — L1 Proxmox collector.

Collects: nodes, VMs, storage pools.
Registers layer "l1_proxmox" on import.

Proxmox API endpoints used (read-only):
  GET /api2/json/nodes
  GET /api2/json/cluster/resources?type=vm
  GET /api2/json/cluster/resources?type=storage

Auth: Vault path secret/proxmox, fields api_token_id + api_token_secret.
Token format: "PVEAPIToken=<api_token_id>=<api_token_secret>"

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/proxmox_sample.json.
Fixture capture: ARCH_AUDIT_CAPTURE_FIXTURE=1 writes fixtures/proxmox_live.json.
"""

import json
import logging
import os
from pathlib import Path

import requests
import urllib3

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "proxmox_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "proxmox_live.json"
)

# Default Proxmox cluster IPs (3 nodes)
_DEFAULT_HOSTS = [
    "192.168.12.6",
    "192.168.12.56",
    "192.168.12.57",
]
_PVE_PORT = 8006


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"Proxmox fixture not found: {_FIXTURE_SAMPLE}. "
            "Run with ARCH_AUDIT_CAPTURE_FIXTURE=1 against live cluster first."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _save_fixture(data: dict) -> None:
    _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    logger.info("Proxmox: live fixture written to %s", _FIXTURE_LIVE)


def _get_auth_header() -> str:
    """Fetch Proxmox API token from Vault and return Authorization header value."""
    from overwatch_gen.lib.vault_client import VaultClient
    vc = VaultClient()
    token_id = vc.kv_read("secret/proxmox", field="api_token_id")
    token_secret = vc.kv_read("secret/proxmox", field="api_token_secret")
    return f"PVEAPIToken={token_id}={token_secret}"


def _pve_get(session: requests.Session, host: str, path: str) -> list | dict:
    """HTTP GET against a Proxmox API endpoint. Suppress TLS warnings."""
    url = f"https://{host}:{_PVE_PORT}/api2/json{path}"
    resp = session.get(url, timeout=15, verify=False)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("data", payload)


def _collect_live() -> dict:
    """Collect data from live Proxmox cluster."""
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    auth_header = _get_auth_header()
    session = requests.Session()
    session.headers.update({"Authorization": auth_header})

    # Try each host until one responds
    last_exc: Exception | None = None
    for host in _DEFAULT_HOSTS:
        try:
            raw_nodes = _pve_get(session, host, "/nodes")
            raw_vms = _pve_get(session, host, "/cluster/resources?type=vm")
            raw_storage = _pve_get(session, host, "/cluster/resources?type=storage")
            return _normalize(raw_nodes, raw_vms, raw_storage)
        except Exception as exc:
            logger.warning("Proxmox: host %s unreachable (%s)", host, exc)
            last_exc = exc

    raise RuntimeError(
        f"Proxmox: all hosts unreachable: {_DEFAULT_HOSTS}. Last error: {last_exc}"
    )


def _normalize(raw_nodes: list, raw_vms: list, raw_storage: list) -> dict:
    """Normalize raw API responses into a stable, sorted dict."""
    nodes = sorted(
        [
            {
                "cpu_usage": round(float(n.get("cpu", 0)), 4),
                "maxcpu": int(n.get("maxcpu", 0)),
                "maxmem": int(n.get("maxmem", 0)),
                "mem_used": int(n.get("mem", 0)),
                "name": n.get("node", ""),
                "status": n.get("status", "unknown"),
                "uptime": int(n.get("uptime", 0)),
            }
            for n in raw_nodes
        ],
        key=lambda x: x["name"],
    )

    vms = sorted(
        [
            {
                "cpus": int(v.get("cpus", 0)),
                "maxmem": int(v.get("maxmem", 0)),
                "mem_used": int(v.get("mem", 0)),
                "name": v.get("name", ""),
                "node": v.get("node", ""),
                "status": v.get("status", "unknown"),
                "type": v.get("type", "qemu"),
                "vmid": int(v.get("vmid", 0)),
            }
            for v in raw_vms
        ],
        key=lambda x: (x["node"], x["vmid"]),
    )

    storage = sorted(
        [
            {
                "avail": int(s.get("avail", 0)),
                "content": s.get("content", ""),
                "maxdisk": int(s.get("maxdisk", 0)),
                "node": s.get("node", ""),
                "shared": bool(s.get("shared", 0)),
                "status": s.get("status", "unknown"),
                "storage": s.get("storage", ""),
                "type": s.get("type", ""),
                "used": int(s.get("used", 0)),
            }
            for s in raw_storage
        ],
        key=lambda x: (x["node"], x["storage"]),
    )

    return {"nodes": nodes, "storage": storage, "vms": vms}


def collect() -> dict:
    """
    Collect Proxmox L1 data.

    Returns dict with keys: nodes, vms, storage.
    Each list is sorted for deterministic output.
    """
    if _use_fixtures():
        logger.info("Proxmox: loading fixture from %s", _FIXTURE_SAMPLE)
        raw = _load_fixture()
        # Fixture files use raw API format — normalize through the same pipeline
        return _normalize(
            raw.get("nodes", []),
            raw.get("vms", []),
            raw.get("storage", []),
        )

    data = _collect_live()

    if _capture_fixture():
        _save_fixture(data)

    return data


def render(data: dict) -> None:
    """Cache Proxmox data for combined L1 render (triggered by l1_unifi)."""
    logger.debug("l1_proxmox.render: caching proxmox data for combined L1 render")
    _PROXMOX_DATA_CACHE.update(data)


# Module-level cache for combined render coordination
_PROXMOX_DATA_CACHE: dict = {}


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l1_proxmox", collect, render)
