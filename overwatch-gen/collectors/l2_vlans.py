"""
l2_vlans.py — L2 Unifi VLAN/network collector.

Collects: VLAN definitions from the Unifi Integration API v1.
Registers layer "l2_vlans" on import.

Auth: Vault secret/unifi api_key. Falls back to UNIFI_API_KEY env var.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/unifi_networks_sample.json.
Fixture capture: ARCH_AUDIT_CAPTURE_FIXTURE=1 writes fixtures/unifi_networks_live.json.
"""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "unifi_networks_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "unifi_networks_live.json"
)

_SENTINEL_UNIFI_PATH = Path(__file__).parent.parent.parent.parent / "sentinel-unifi"


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"Unifi networks fixture not found: {_FIXTURE_SAMPLE}. "
            "Run with ARCH_AUDIT_CAPTURE_FIXTURE=1 against live controller first."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _save_fixture(data: dict) -> None:
    _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    logger.info("Unifi networks: live fixture written to %s", _FIXTURE_LIVE)


def _get_api_key() -> str:
    from overwatch_gen.lib.vault_client import VaultClient
    try:
        vc = VaultClient()
        return vc.kv_read("secret/unifi", field="api_key")
    except Exception as vault_exc:
        key = os.environ.get("UNIFI_API_KEY")
        if not key:
            raise RuntimeError(
                f"L2 VLANs: Vault unavailable ({vault_exc}) and UNIFI_API_KEY not set"
            ) from vault_exc
        return key


def _list_networks(api_key: str, site_id: str) -> list:
    """Fetch all networks for a site via Integration API v1."""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    base = "https://192.168.12.1/proxy/network/integration/v1"
    session = requests.Session()
    session.headers.update({"X-API-Key": api_key, "Accept": "application/json"})
    session.verify = False

    all_items: list = []
    offset = 0
    while True:
        resp = session.get(
            f"{base}/sites/{site_id}/networks",
            params={"limit": 200, "offset": offset},
            timeout=15,
        )
        resp.raise_for_status()
        page = resp.json().get("data", [])
        all_items.extend(page)
        if len(page) < 200:
            break
        offset += 200
    return all_items


def _get_site_id(api_key: str) -> str:
    """Auto-detect primary site ID."""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    base = "https://192.168.12.1/proxy/network/integration/v1"
    session = requests.Session()
    session.headers.update({"X-API-Key": api_key, "Accept": "application/json"})
    session.verify = False

    resp = session.get(f"{base}/sites", params={"limit": 200}, timeout=15)
    resp.raise_for_status()
    sites = resp.json().get("data", [])
    if not sites:
        raise RuntimeError("L2 VLANs: no sites returned from Unifi controller")
    return sites[0].get("id") or sites[0].get("_id")


def _normalize(raw_networks: list) -> dict:
    """Normalize network list into stable, sorted VLAN table."""
    vlans = sorted(
        [
            {
                "dhcp_enabled": bool(n.get("dhcpEnabled", False)),
                "dhcp_start": n.get("dhcpRangeStart") or "",
                "dhcp_stop": n.get("dhcpRangeStop") or "",
                "id": n.get("id") or n.get("_id", ""),
                "name": n.get("name", ""),
                "purpose": n.get("purpose", ""),
                "subnet": n.get("ipSubnet", ""),
                "vlan_id": int(n.get("vlanId", 1)),
            }
            for n in raw_networks
        ],
        key=lambda x: x["vlan_id"],
    )
    return {"vlans": vlans}


def collect() -> dict:
    """
    Collect Unifi L2 VLAN data.

    Returns dict with key: vlans (list sorted by vlan_id).
    """
    if _use_fixtures():
        logger.info("Unifi networks: loading fixture from %s", _FIXTURE_SAMPLE)
        raw = _load_fixture()
        return _normalize(raw.get("networks", []))

    api_key = _get_api_key()
    site_id = _get_site_id(api_key)
    raw_networks = _list_networks(api_key, site_id)
    data = _normalize(raw_networks)

    if _capture_fixture():
        _save_fixture({"networks": raw_networks})

    return data


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402
from overwatch_gen.renderers import l2_renderer  # noqa: E402

registry.register_layer("l2_vlans", collect, l2_renderer.render)
