"""
l3_netbox.py — L3 NetBox collector.

Collects: IP prefixes + IP address assignments from NetBox.
Registers layer "l3_netbox" on import.

Auth: Vault secret/netbox admin_api_token. Falls back to NETBOX_API_TOKEN env var.
NetBox URL: https://netbox.208.haist.farm

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/netbox_prefixes_sample.json.
Fixture capture: ARCH_AUDIT_CAPTURE_FIXTURE=1 writes fixtures/netbox_prefixes_live.json.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "netbox_prefixes_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "netbox_prefixes_live.json"
)

_NETBOX_URL = os.environ.get("NETBOX_URL", "https://netbox.208.haist.farm")


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"NetBox fixture not found: {_FIXTURE_SAMPLE}. "
            "Run with ARCH_AUDIT_CAPTURE_FIXTURE=1 against live NetBox first."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _save_fixture(data: dict) -> None:
    _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    logger.info("NetBox: live fixture written to %s", _FIXTURE_LIVE)


def _get_token() -> str:
    from overwatch_gen.lib.vault_client import VaultClient
    try:
        vc = VaultClient()
        return vc.kv_read("secret/netbox", field="admin_api_token")
    except Exception as vault_exc:
        token = os.environ.get("NETBOX_API_TOKEN")
        if not token:
            raise RuntimeError(
                f"NetBox: Vault unavailable ({vault_exc}) and NETBOX_API_TOKEN not set"
            ) from vault_exc
        return token


def _netbox_get_all(token: str, path: str) -> list:
    """
    Paginated GET from NetBox REST API.

    Uses ?limit=0 to request all records in one shot (NetBox supports this).
    Falls back to manual pagination if needed.
    """
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Token {token}",
        "Accept": "application/json",
    })
    session.verify = False

    url = f"{_NETBOX_URL}/api{path}"
    params = {"limit": 0}  # 0 = no limit in NetBox
    resp = session.get(url, params=params, timeout=15)
    resp.raise_for_status()
    body = resp.json()

    # NetBox returns {"count": N, "results": [...]}
    results = body.get("results", body if isinstance(body, list) else [])

    # If server ignored limit=0, paginate manually
    while body.get("next"):
        resp = session.get(body["next"], timeout=15)
        resp.raise_for_status()
        body = resp.json()
        results.extend(body.get("results", []))

    return results


def _normalize_prefixes(raw: list) -> list:
    return sorted(
        [
            {
                "description": p.get("description", ""),
                "family": p.get("family", {}).get("value", 4) if isinstance(p.get("family"), dict) else p.get("family", 4),
                "id": p.get("id", 0),
                "is_pool": bool(p.get("is_pool", False)),
                "prefix": p.get("prefix", ""),
                "role": (p.get("role") or {}).get("slug", "") if isinstance(p.get("role"), dict) else "",
                "site": (p.get("site") or {}).get("name", "") if isinstance(p.get("site"), dict) else "",
                "status": (p.get("status") or {}).get("value", p.get("status", "")) if isinstance(p.get("status"), dict) else p.get("status", ""),
                "vlan": (p.get("vlan") or {}).get("vid", None) if isinstance(p.get("vlan"), dict) else None,
            }
            for p in raw
        ],
        key=lambda x: x["prefix"],
    )


def _normalize_ips(raw: list) -> list:
    return sorted(
        [
            {
                "address": ip.get("address", ""),
                "description": ip.get("description", ""),
                "dns_name": ip.get("dns_name", ""),
                "family": ip.get("family", {}).get("value", 4) if isinstance(ip.get("family"), dict) else ip.get("family", 4),
                "id": ip.get("id", 0),
                "role": (ip.get("role") or {}).get("value", "") if isinstance(ip.get("role"), dict) else (ip.get("role") or ""),
                "status": (ip.get("status") or {}).get("value", ip.get("status", "")) if isinstance(ip.get("status"), dict) else ip.get("status", ""),
            }
            for ip in raw
        ],
        key=lambda x: x["address"],
    )


def collect() -> dict:
    """
    Collect NetBox L3 data.

    Returns dict with keys: prefixes, ip_addresses.
    Both lists are sorted for deterministic output.
    """
    if _use_fixtures():
        logger.info("NetBox: loading fixture from %s", _FIXTURE_SAMPLE)
        raw = _load_fixture()
        return {
            "ip_addresses": _normalize_ips(raw.get("ip_addresses", [])),
            "prefixes": _normalize_prefixes(raw.get("prefixes", [])),
        }

    token = _get_token()
    raw_prefixes = _netbox_get_all(token, "/ipam/prefixes/")
    raw_ips = _netbox_get_all(token, "/ipam/ip-addresses/")

    data = {
        "ip_addresses": _normalize_ips(raw_ips),
        "prefixes": _normalize_prefixes(raw_prefixes),
    }

    if _capture_fixture():
        _save_fixture({"ip_addresses": raw_ips, "prefixes": raw_prefixes})

    return data


def render(data: dict) -> None:
    """Cache NetBox data for combined L3 render (triggered by l3_unifi_firewall)."""
    logger.debug("l3_netbox.render: caching netbox data for combined L3 render")
    _NETBOX_DATA_CACHE.update(data)


# Module-level cache for combined render coordination
_NETBOX_DATA_CACHE: dict = {}


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l3_netbox", collect, render)
