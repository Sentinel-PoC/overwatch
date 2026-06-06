"""
l5_keycloak.py — Keycloak realm client + scope collector.

Live mode: authenticates with Keycloak admin-cli, then:
  GET /admin/realms/{realm}/clients?first=0&max=200  (paginated)
  GET /admin/realms/{realm}/client-scopes

Keycloak admin creds fetched from Vault: secret/keycloak/admin.
  Fields: username (admin username), password (admin password), realm (realm name).
Keycloak base URL: https://auth.208.haist.farm

Realm resolution order:
  1. Use realm name from Vault field (defaults to "sentinel" if empty).
  2. If GET /admin/realms/{realm}/clients returns 0 clients OR raises 404,
     enumerate GET /admin/realms to discover all realms.
  3. Pick the first non-master realm found; if none, fall back to "master".

Pagination:
  Queries include first=0&max=200 to avoid Keycloak server-side truncation.
  For deployments with >200 clients, pagination iterates until the page is
  smaller than max (i.e., last page).

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/keycloak_clients_sample.json.

Returns a dict with keys:
  clients       list  — client records:
    client_id             str   — clientId
    name                  str   — display name
    root_url              str   — rootUrl
    redirect_uris         list  — redirectUris (sorted)
    protocol              str   — openid-connect / saml
    public_client         bool  — publicClient
    direct_access_grants  bool  — directAccessGrantsEnabled
    pkce_required         bool  — pkce.code.challenge.method is set
  client_scopes list  — scope records:
    name      str   — scope name
    protocol  str   — openid-connect / saml

Registers layer "l5_keycloak".
"""

import json
import logging
import os
from pathlib import Path

import requests
import urllib3

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "keycloak_clients_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "keycloak_clients_live.json"
)

_KEYCLOAK_BASE = "https://auth.208.haist.farm"
_DEFAULT_REALM = "sentinel"
_PAGE_SIZE = 200


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"Keycloak fixture not found: {_FIXTURE_SAMPLE}."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _save_fixture(data: dict) -> None:
    _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    logger.info("Keycloak: live fixture written to %s", _FIXTURE_LIVE)


def _get_admin_creds() -> tuple[str, str, str]:
    """
    Fetch Keycloak admin credentials from Vault.

    Returns (username, password, realm).
    Vault path: secret/keycloak/admin
    Fields: username, password, realm (defaults to _DEFAULT_REALM if absent).
    """
    from overwatch_gen.lib.vault_client import VaultClient
    vc = VaultClient()
    username = vc.kv_read("secret/keycloak/admin", field="username")
    password = vc.kv_read("secret/keycloak/admin", field="password")
    realm = vc.kv_read("secret/keycloak/admin", field="realm") or _DEFAULT_REALM
    return username, password, realm


def _get_admin_token(session: requests.Session, admin_username: str, admin_password: str) -> str:
    """Obtain Keycloak admin token via master realm."""
    url = f"{_KEYCLOAK_BASE}/realms/master/protocol/openid-connect/token"
    resp = session.post(
        url,
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": admin_username,
            "password": admin_password,
        },
        timeout=15,
        verify=False,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _list_realms(session: requests.Session) -> list[str]:
    """
    Enumerate all realms via GET /admin/realms.

    Returns list of realm names. Logs a warning if the request fails.
    """
    url = f"{_KEYCLOAK_BASE}/admin/realms"
    try:
        resp = session.get(url, timeout=15, verify=False)
        resp.raise_for_status()
        realms = [r.get("realm", "") for r in resp.json() if r.get("realm")]
        logger.info("Keycloak: discovered realms: %s", realms)
        return realms
    except requests.HTTPError as exc:
        logger.warning("Keycloak: failed to enumerate realms: %s", exc)
        return []


def _discover_realm(session: requests.Session, configured_realm: str) -> str:
    """
    Discover the realm to use.

    Strategy:
    1. Probe configured_realm with GET /admin/realms/{realm}/clients?first=0&max=1.
    2. If it returns >= 1 result, use it — we have access.
    3. If it returns 0 OR raises 404, enumerate all realms.
    4. Pick first non-master realm; fall back to "master" if none found.

    Returns the realm name to use.
    """
    probe_url = f"{_KEYCLOAK_BASE}/admin/realms/{configured_realm}/clients"
    try:
        resp = session.get(
            probe_url,
            params={"first": 0, "max": 1},
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
        count = len(resp.json())
        logger.info(
            "Keycloak: probe realm=%r returned %d client(s)", configured_realm, count
        )
        if count > 0:
            return configured_realm
    except requests.HTTPError as exc:
        logger.warning(
            "Keycloak: probe of realm=%r failed (%s); attempting realm discovery",
            configured_realm,
            exc,
        )

    # Probe returned 0 clients or 404 — try to discover the real realm
    logger.info(
        "Keycloak: realm=%r probe returned 0 clients; enumerating realms for discovery",
        configured_realm,
    )
    all_realms = _list_realms(session)
    non_master = [r for r in all_realms if r != "master"]
    if non_master:
        chosen = non_master[0]
        logger.info(
            "Keycloak: using discovered realm=%r (configured=%r had 0 clients)",
            chosen,
            configured_realm,
        )
        return chosen

    # Last resort: if configured realm exists in the list, use it; else master
    if configured_realm in all_realms:
        logger.warning(
            "Keycloak: no non-master realm found; sticking with configured realm=%r",
            configured_realm,
        )
        return configured_realm

    logger.warning(
        "Keycloak: realm discovery found no usable realm; falling back to 'master'"
    )
    return "master"


def _list_clients_paginated(session: requests.Session, realm: str) -> list[dict]:
    """
    Fetch all clients from a realm using explicit pagination.

    Uses first=0&max=_PAGE_SIZE and iterates until the page is smaller than
    _PAGE_SIZE (indicating the last page has been received).

    Returns combined list of raw client dicts.
    """
    all_clients: list[dict] = []
    first = 0
    while True:
        url = f"{_KEYCLOAK_BASE}/admin/realms/{realm}/clients"
        resp = session.get(
            url,
            params={"first": first, "max": _PAGE_SIZE},
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
        page = resp.json()
        all_clients.extend(page)
        logger.info(
            "Keycloak: realm=%r clients page first=%d got %d (total so far: %d)",
            realm,
            first,
            len(page),
            len(all_clients),
        )
        if len(page) < _PAGE_SIZE:
            break
        first += _PAGE_SIZE
    return all_clients


def _normalize_client(client: dict) -> dict:
    """Normalize a single Keycloak client dict."""
    attributes = client.get("attributes") or {}
    pkce_required = bool(attributes.get("pkce.code.challenge.method"))

    return {
        "client_id": client.get("clientId", ""),
        "direct_access_grants": bool(client.get("directAccessGrantsEnabled", False)),
        "name": client.get("name", ""),
        "pkce_required": pkce_required,
        "protocol": client.get("protocol", "openid-connect"),
        "public_client": bool(client.get("publicClient", False)),
        "redirect_uris": sorted(client.get("redirectUris") or []),
        "root_url": client.get("rootUrl", ""),
    }


def _normalize_scope(scope: dict) -> dict:
    """Normalize a single Keycloak client scope dict."""
    return {
        "name": scope.get("name", ""),
        "protocol": scope.get("protocol", "openid-connect"),
    }


def _normalize(raw: dict) -> dict:
    """Normalize raw fixture/live data into stable sorted structure."""
    clients = sorted(
        [_normalize_client(c) for c in raw.get("clients", [])],
        key=lambda c: c["client_id"],
    )
    client_scopes = sorted(
        [_normalize_scope(s) for s in raw.get("client_scopes", [])],
        key=lambda s: s["name"],
    )
    return {"client_scopes": client_scopes, "clients": clients}


def _collect_live() -> dict:
    """Collect Keycloak client and scope data from live instance."""
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    admin_username, admin_password, configured_realm = _get_admin_creds()
    logger.info(
        "Keycloak: configured realm=%r from Vault (base=%s)",
        configured_realm,
        _KEYCLOAK_BASE,
    )

    session = requests.Session()
    token = _get_admin_token(session, admin_username, admin_password)
    session.headers.update({"Authorization": f"Bearer {token}"})

    # Realm discovery: probe configured realm; fall back if it yields 0 clients
    realm = _discover_realm(session, configured_realm)
    logger.info("Keycloak: collecting from realm=%r", realm)

    raw_clients = _list_clients_paginated(session, realm)
    logger.info("Keycloak: total clients fetched from realm=%r: %d", realm, len(raw_clients))

    scopes_url = f"{_KEYCLOAK_BASE}/admin/realms/{realm}/client-scopes"
    resp = session.get(scopes_url, timeout=15, verify=False)
    resp.raise_for_status()
    raw_scopes = resp.json()
    logger.info("Keycloak: total client-scopes fetched from realm=%r: %d", realm, len(raw_scopes))

    return _normalize({"clients": raw_clients, "client_scopes": raw_scopes})


def collect() -> dict:
    """
    Collect Keycloak sentinel realm clients and scopes.

    Returns dict with keys: clients, client_scopes.
    Each list sorted deterministically by client_id / name.
    """
    if _use_fixtures():
        logger.info("Keycloak: loading fixture from %s", _FIXTURE_SAMPLE)
        raw = _load_fixture()
        return _normalize(raw)

    data = _collect_live()

    if _capture_fixture():
        _save_fixture(data)

    return data


def render(data: dict) -> None:
    """Cache Keycloak data for combined L5 render (triggered by l5_peerauth orchestrator)."""
    logger.debug("l5_keycloak.render: caching keycloak data for combined L5 render")
    _KEYCLOAK_DATA_CACHE.update(data)


# Module-level cache for combined render coordination
_KEYCLOAK_DATA_CACHE: dict = {}


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l5_keycloak", collect, render)
