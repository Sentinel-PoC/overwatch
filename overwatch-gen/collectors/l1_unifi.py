"""
l1_unifi.py — L1 Unifi devices collector.

Collects: gateway, switches, APs from the Unifi Integration API v1.
Registers layer "l1_unifi" on import.

Auth: Uses VaultClient to fetch secret/unifi api_key field.
      Falls back to UNIFI_API_KEY env var if Vault unavailable.
      The sentinel-unifi UniFiClient handles the API-key header + TLS.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/unifi_devices_sample.json.
Fixture capture: ARCH_AUDIT_CAPTURE_FIXTURE=1 writes fixtures/unifi_devices_live.json.
"""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "unifi_devices_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "unifi_devices_live.json"
)

_SENTINEL_UNIFI_PATH = Path(__file__).parent.parent.parent.parent / "sentinel-unifi"


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"Unifi devices fixture not found: {_FIXTURE_SAMPLE}. "
            "Run with ARCH_AUDIT_CAPTURE_FIXTURE=1 against live controller first."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _save_fixture(data: dict) -> None:
    _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    logger.info("Unifi devices: live fixture written to %s", _FIXTURE_LIVE)


def _get_unifi_client():
    """
    Build a UniFiClient.

    Priority:
      1. Import sentinel_unifi.UniFiClient (if repo is adjacent to overwatch).
      2. Direct requests with VaultClient-fetched API key.
    """
    # Try to import from sentinel-unifi repo (may be on PYTHONPATH or adjacent)
    if str(_SENTINEL_UNIFI_PATH) not in sys.path and _SENTINEL_UNIFI_PATH.exists():
        sys.path.insert(0, str(_SENTINEL_UNIFI_PATH))

    try:
        from unifi import UniFiClient  # type: ignore
        # sentinel-unifi's from_vault uses subprocess vault CLI — that won't work
        # here without a live vault CLI. Use our VaultClient instead.
        from overwatch_gen.lib.vault_client import VaultClient
        try:
            vc = VaultClient()
            api_key = vc.kv_read("secret/unifi", field="api_key")
            logger.info("Unifi: API key fetched from Vault")
        except Exception as vault_exc:
            logger.warning("Unifi: Vault fetch failed (%s), trying env var", vault_exc)
            api_key = os.environ.get("UNIFI_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "Unifi: no API key available from Vault or UNIFI_API_KEY env var"
                ) from vault_exc
        return UniFiClient(api_key=api_key)
    except ImportError:
        logger.debug("sentinel-unifi not importable, using inline HTTP client")
        return None


class _InlineUniFiClient:
    """Minimal inline Unifi client when sentinel-unifi is not importable."""

    BASE = "https://192.168.12.1/proxy/network/integration/v1"

    def __init__(self, api_key: str):
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._session = requests.Session()
        self._session.headers.update({
            "X-API-Key": api_key,
            "Accept": "application/json",
        })
        self._session.verify = False

    def list_sites(self) -> list:
        resp = self._session.get(
            f"{self.BASE}/sites", params={"limit": 200}, timeout=15
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    def list_devices(self, site_id: str) -> list:
        all_items = []
        offset = 0
        while True:
            resp = self._session.get(
                f"{self.BASE}/sites/{site_id}/devices",
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


def _collect_live() -> dict:
    """Collect device data from live Unifi controller."""
    from overwatch_gen.lib.vault_client import VaultClient

    try:
        vc = VaultClient()
        api_key = vc.kv_read("secret/unifi", field="api_key")
    except Exception as vault_exc:
        api_key = os.environ.get("UNIFI_API_KEY")
        if not api_key:
            raise RuntimeError(
                f"Unifi: Vault unavailable ({vault_exc}) and UNIFI_API_KEY not set"
            ) from vault_exc

    # Try sentinel-unifi client first, fall back to inline
    client = _get_unifi_client()
    if client is None:
        client = _InlineUniFiClient(api_key)

    sites = client.list_sites()
    if not sites:
        raise RuntimeError("Unifi: no sites returned")
    site_id = sites[0].get("id") or sites[0].get("_id")
    raw_devices = client.list_devices(site_id)
    return _normalize(raw_devices)


def _normalize(raw_devices: list) -> dict:
    """Normalize device list into stable, sorted structure."""
    devices = sorted(
        [
            {
                "id": d.get("id") or d.get("macAddress", ""),
                "ip": d.get("ipAddress", ""),
                "mac": d.get("macAddress", ""),
                "model": d.get("model", ""),
                "name": d.get("name", ""),
                "state": d.get("state", "UNKNOWN"),
                "type": d.get("type", "unknown"),
            }
            for d in raw_devices
        ],
        key=lambda x: (x["type"], x["name"]),
    )
    return {"devices": devices}


def collect() -> dict:
    """
    Collect Unifi L1 device data.

    Returns dict with key: devices (list sorted by type, name).
    """
    if _use_fixtures():
        logger.info("Unifi devices: loading fixture from %s", _FIXTURE_SAMPLE)
        raw = _load_fixture()
        # Fixture files use raw API format — normalize through the same pipeline
        return _normalize(raw.get("devices", []))

    data = _collect_live()

    if _capture_fixture():
        _save_fixture(data)

    return data


def render(data: dict) -> None:
    """Render L1 physical layer page via l1_renderer (combined render)."""
    from overwatch_gen.renderers import l1_renderer
    from overwatch_gen.collectors.l1_proxmox import _PROXMOX_DATA_CACHE
    l1_renderer.render_combined(
        proxmox=_PROXMOX_DATA_CACHE,
        unifi=data,
    )


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l1_unifi", collect, render)
