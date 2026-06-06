"""
l3_unifi_firewall.py — L3 Unifi firewall collector.

Collects: firewall policies + zones from the Unifi Integration API v1.
Registers layer "l3_unifi_firewall" on import.

Auth: Vault secret/unifi api_key. Falls back to UNIFI_API_KEY env var.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/unifi_firewall_sample.json.
Fixture capture: ARCH_AUDIT_CAPTURE_FIXTURE=1 writes fixtures/unifi_firewall_live.json.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "unifi_firewall_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "unifi_firewall_live.json"
)


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"Unifi firewall fixture not found: {_FIXTURE_SAMPLE}. "
            "Run with ARCH_AUDIT_CAPTURE_FIXTURE=1 against live controller first."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _save_fixture(data: dict) -> None:
    _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    logger.info("Unifi firewall: live fixture written to %s", _FIXTURE_LIVE)


def _get_api_key() -> str:
    from overwatch_gen.lib.vault_client import VaultClient
    try:
        vc = VaultClient()
        return vc.kv_read("secret/unifi", field="api_key")
    except Exception as vault_exc:
        key = os.environ.get("UNIFI_API_KEY")
        if not key:
            raise RuntimeError(
                f"L3 Firewall: Vault unavailable ({vault_exc}) and UNIFI_API_KEY not set"
            ) from vault_exc
        return key


def _get_site_id(session, base: str) -> str:
    resp = session.get(f"{base}/sites", params={"limit": 200}, timeout=15)
    resp.raise_for_status()
    sites = resp.json().get("data", [])
    if not sites:
        raise RuntimeError("L3 Firewall: no sites returned from Unifi controller")
    return sites[0].get("id") or sites[0].get("_id")


def _paginate(session, url: str) -> list:
    all_items: list = []
    offset = 0
    while True:
        resp = session.get(url, params={"limit": 200, "offset": offset}, timeout=15)
        resp.raise_for_status()
        page = resp.json().get("data", [])
        all_items.extend(page)
        if len(page) < 200:
            break
        offset += 200
    return all_items


def _collect_live() -> dict:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    api_key = _get_api_key()
    base = "https://192.168.12.1/proxy/network/integration/v1"
    session = requests.Session()
    session.headers.update({"X-API-Key": api_key, "Accept": "application/json"})
    session.verify = False

    site_id = _get_site_id(session, base)
    raw_policies = _paginate(session, f"{base}/sites/{site_id}/firewall/policies")
    raw_zones = _paginate(session, f"{base}/sites/{site_id}/firewall/zones")

    return _normalize(raw_policies, raw_zones)


def _normalize_policy(p: dict) -> dict:
    """Extract stable fields from a firewall policy."""

    def _simplify_endpoints(endpoints: list) -> list:
        result = []
        for ep in (endpoints or []):
            result.append({
                "network": ep.get("network", ""),
                "target": ep.get("matchingTarget", "ANY"),
                "target_type": ep.get("matchingTargetType", "ANY"),
            })
        return result

    return {
        "action": p.get("action", ""),
        "description": p.get("description", ""),
        "destinations": _simplify_endpoints(p.get("destinations", [])),
        "enabled": bool(p.get("enabled", True)),
        "id": p.get("id", ""),
        "name": p.get("name", ""),
        "sources": _simplify_endpoints(p.get("sources", [])),
    }


def _normalize(raw_policies: list, raw_zones: list) -> dict:
    policies = sorted(
        [_normalize_policy(p) for p in raw_policies],
        key=lambda x: x["name"],
    )
    zones = sorted(
        [
            {
                "id": z.get("id", ""),
                "name": z.get("name", ""),
                "network_ids": sorted(z.get("networkIds", [])),
            }
            for z in raw_zones
        ],
        key=lambda x: x["name"],
    )
    return {"firewall_policies": policies, "firewall_zones": zones}


def collect() -> dict:
    """
    Collect Unifi L3 firewall data.

    Returns dict with keys: firewall_policies, firewall_zones.
    Both lists sorted by name for deterministic output.
    """
    if _use_fixtures():
        logger.info("Unifi firewall: loading fixture from %s", _FIXTURE_SAMPLE)
        raw = _load_fixture()
        return _normalize(
            raw.get("firewall_policies", []),
            raw.get("firewall_zones", []),
        )

    data = _collect_live()

    if _capture_fixture():
        _save_fixture(data)

    return data


def render(data: dict) -> None:
    """Render L3 network layer page via l3_renderer (combined render)."""
    from overwatch_gen.renderers import l3_renderer
    from overwatch_gen.collectors.l3_netbox import _NETBOX_DATA_CACHE
    l3_renderer.render_combined(
        netbox=_NETBOX_DATA_CACHE,
        unifi=data,
    )


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l3_unifi_firewall", collect, render)
