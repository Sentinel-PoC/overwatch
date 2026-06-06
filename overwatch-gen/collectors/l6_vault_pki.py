"""
l6_vault_pki.py — L6 Vault PKI collector.

Collects: issued certificate list (serial, CN, SANs, issuer, not-after, revocation
status) and issuer hierarchy from Vault PKI secrets engine.

Registers layer "l6_vault_pki" on import.

Vault PKI endpoints used (read-only, requires pki-reader policy):
  GET <pki_mount>/certs            — list of serial numbers
  GET <pki_mount>/cert/<serial>    — certificate details
  GET <pki_mount>/issuers          — list of issuer keys
  GET <pki_mount>/issuer/<id>      — issuer details

Auth: uses VAULT_TOKEN env var (or VAULT_ADDR + VAULT_TOKEN).
Policy: pki-reader must be attached to the token in use.
If the token does NOT have pki-reader policy, the collector logs WARNING
and returns an empty stub (no Secrets fallback — hard stop).

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/pki_certs_sample.json.
Fixture capture: ARCH_AUDIT_CAPTURE_FIXTURE=1 writes fixtures/pki_certs_live.json.
"""

import datetime
import json
import logging
import os
from pathlib import Path

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "pki_certs_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "pki_certs_live.json"
)

_DEFAULT_PKI_MOUNT = os.environ.get("VAULT_PKI_MOUNT", "pki")
_DEFAULT_PKI_INT_MOUNT = os.environ.get("VAULT_PKI_INT_MOUNT", "pki_int")
_VAULT_ADDR = os.environ.get("VAULT_ADDR", "https://192.168.12.206:8200")
_VAULT_SKIP_VERIFY = os.environ.get("VAULT_SKIP_VERIFY", "true").lower() in ("1", "true", "yes")

# Days-remaining threshold for expiry warnings
_WARN_DAYS = 30


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"Vault PKI fixture not found: {_FIXTURE_SAMPLE}. "
            "Run with ARCH_AUDIT_CAPTURE_FIXTURE=1 against live Vault first."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _days_remaining(not_after_str: str) -> int:
    """Calculate days remaining from an ISO-8601 not_after string."""
    try:
        not_after = datetime.datetime.fromisoformat(
            not_after_str.replace("Z", "+00:00")
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = not_after - now
        return delta.days
    except (ValueError, AttributeError):
        return -999


def _vault_request(path: str, token: str) -> dict | None:
    """Make a GET request to Vault API. Returns parsed JSON or None on error."""
    import requests

    url = f"{_VAULT_ADDR.rstrip('/')}/v1/{path.lstrip('/')}"
    try:
        resp = requests.get(
            url,
            headers={"X-Vault-Token": token},
            verify=not _VAULT_SKIP_VERIFY,
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Vault PKI request failed for %s: %s", path, exc)
        return None


def _check_pki_reader_policy(token: str) -> bool:
    """
    Check if the token has the pki-reader policy.
    Uses token self-lookup which any token can perform.
    """
    result = _vault_request("auth/token/lookup-self", token)
    if result is None:
        return False
    policies = result.get("data", {}).get("policies", [])
    return "pki-reader" in policies


def _mount_exists(mount: str, token: str) -> bool:
    """Check whether a Vault secrets mount exists by probing its certs list endpoint."""
    result = _vault_request(f"{mount}/certs", token)
    return result is not None


def _collect_live() -> dict:
    """Collect PKI certificate data from live Vault."""
    token = os.environ.get("VAULT_TOKEN", "")
    if not token:
        logger.warning(
            "VAULT_TOKEN not set — Vault PKI collector returning empty stub. "
            "Set VAULT_TOKEN with pki-reader policy to enable live collection."
        )
        return _empty_stub()

    if not _check_pki_reader_policy(token):
        logger.warning(
            "Current Vault token does NOT have pki-reader policy. "
            "Skipping Vault PKI collector — returning empty stub. "
            "Do NOT fall back to reading Secrets. Attach pki-reader policy to fix."
        )
        return _empty_stub()

    # Check whether any PKI mount exists before collecting
    mounts_to_try = [_DEFAULT_PKI_MOUNT, _DEFAULT_PKI_INT_MOUNT]
    existing_mounts = [m for m in mounts_to_try if _mount_exists(m, token)]

    if not existing_mounts:
        logger.warning(
            "No Vault PKI mount configured; collector skipped. "
            "Mounts checked: %s",
            ", ".join(mounts_to_try),
        )
        return _empty_stub(no_mount=True)

    issuers = _collect_issuers(token)
    certs = []

    for mount in existing_mounts:
        certs.extend(_collect_mount_certs(mount, token, issuers))

    certs.sort(key=lambda c: c.get("common_name", ""))

    data = {
        "source": "vault_pki",
        "issuers": issuers,
        "certs": certs,
    }

    if _capture_fixture():
        _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
        with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        logger.info("Captured Vault PKI fixture to %s", _FIXTURE_LIVE)

    return data


def _collect_issuers(token: str) -> list[dict]:
    """List and detail PKI issuers from all mounts."""
    issuers = []
    for mount in [_DEFAULT_PKI_MOUNT, _DEFAULT_PKI_INT_MOUNT]:
        resp = _vault_request(f"{mount}/issuers", token)
        if resp is None:
            continue
        for issuer_id in resp.get("data", {}).get("key_info", {}).keys():
            detail = _vault_request(f"{mount}/issuer/{issuer_id}", token)
            if detail and "data" in detail:
                d = detail["data"]
                issuers.append({
                    "name": f"{mount}/{issuer_id}",
                    "issuer_id": issuer_id,
                    "issuer_name": d.get("issuer_name", issuer_id),
                    "leaf_issuer": d.get("usage", "") != "root-sign-intermediate",
                })
    return sorted(issuers, key=lambda x: x.get("issuer_name", ""))


def _collect_mount_certs(mount: str, token: str, issuers: list[dict]) -> list[dict]:
    """List and detail certs from a PKI mount."""
    resp = _vault_request(f"{mount}/certs", token)
    if resp is None:
        return []

    certs = []
    serials = resp.get("data", {}).get("keys", [])
    for serial in serials:
        detail = _vault_request(f"{mount}/cert/{serial}", token)
        if detail is None or "data" not in detail:
            continue
        d = detail["data"]
        cert_data = d.get("certificate", "")
        not_after = d.get("expiration", "")
        # expiration is an int (Unix timestamp) in Vault PKI
        if isinstance(not_after, int):
            not_after_dt = datetime.datetime.utcfromtimestamp(not_after)
            not_after_str = not_after_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            not_after_str = str(not_after)

        days = _days_remaining(not_after_str)

        certs.append({
            "serial": serial,
            "common_name": d.get("common_name", d.get("subject", "")),
            "sans": d.get("sans", []),
            "issuer": _resolve_issuer_name(d.get("issuing_ca", ""), issuers),
            "not_after": not_after_str,
            "days_remaining": days,
            "revoked": d.get("revocation_time", 0) > 0,
        })
    return certs


def _resolve_issuer_name(issuing_ca: str, issuers: list[dict]) -> str:
    """Match issuing CA fingerprint to human-readable issuer name."""
    for issuer in issuers:
        if issuer.get("issuer_id") in issuing_ca or issuing_ca in issuer.get("issuer_name", ""):
            return issuer["issuer_name"]
    return issuing_ca or "Unknown"


def _empty_stub(no_mount: bool = False) -> dict:
    """Return an empty-but-valid struct when PKI is inaccessible."""
    if no_mount:
        warning = "No Vault PKI mount configured; collector skipped"
    else:
        warning = (
            "Vault PKI data unavailable: token missing or lacks pki-reader policy. "
            "Do NOT fall back to Secrets. Attach pki-reader policy and re-run."
        )
    return {
        "source": "vault_pki",
        "issuers": [],
        "certs": [],
        "_warning": warning,
    }


def collect() -> dict:
    """
    Collect Vault PKI certificate data.

    Returns dict with keys:
      source   — "vault_pki"
      issuers  — list of issuer dicts (name, issuer_id, issuer_name, leaf_issuer)
      certs    — list of cert dicts (serial, common_name, sans, issuer, not_after,
                 days_remaining, revoked)
      _warning — (optional) warning string if PKI is inaccessible
    """
    if _use_fixtures():
        return _load_fixture()
    return _collect_live()


def render(data: dict) -> None:
    """Render L6 presentation layer page via l6_renderer (combined render — orchestrator)."""
    from overwatch_gen.renderers import l6_renderer
    from overwatch_gen.collectors.l6_certmanager import _CERTMANAGER_DATA_CACHE
    l6_renderer.render_combined(
        certmanager=_CERTMANAGER_DATA_CACHE,
        vault_pki=data,
    )


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l6_vault_pki", collect, render)
