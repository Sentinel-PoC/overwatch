"""
vault_client.py — Vault AppRole login + KV read helper.

Priority resolution for credentials:
  1. VAULT_TOKEN env var (direct token override — for CI/dev)
  2. VAULT_APPROLE_ROLE_ID + VAULT_APPROLE_SECRET_ID env vars (AppRole)
  3. Explicit arguments to VaultClient.__init__

Internal Vault uses self-signed cert; VAULT_SKIP_VERIFY=true is supported.
Secret values are NEVER logged or printed — only presence is acknowledged.

Usage:
    from overwatch_gen.lib.vault_client import VaultClient
    vc = VaultClient()
    value = vc.kv_read("secret/plane/api-key", field="api_key")
"""

import os
import logging

import requests
import urllib3

logger = logging.getLogger(__name__)


def _skip_verify() -> bool:
    return os.environ.get("VAULT_SKIP_VERIFY", "false").lower() in ("true", "1", "yes")


class VaultError(Exception):
    """Raised when Vault returns an error or credential resolution fails."""


class VaultClient:
    """
    Thin Vault client using requests.

    Supports both KV-v1 (secret/<path>) and KV-v2 (secret/data/<path>)
    transparently: if the response contains a nested 'data' key it is
    unwrapped automatically.
    """

    def __init__(
        self,
        addr: str | None = None,
        token: str | None = None,
        role_id: str | None = None,
        secret_id: str | None = None,
    ):
        self.addr = (
            addr
            or os.environ.get("VAULT_ADDR")
            or "https://192.168.12.206:8200"
        )
        self.verify = not _skip_verify()

        if self.verify is False:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Token resolution order: explicit arg > env VAULT_TOKEN > AppRole
        resolved_token = token or os.environ.get("VAULT_TOKEN")
        if resolved_token:
            self._token = resolved_token
            logger.debug("Vault: using VAULT_TOKEN (length %d)", len(self._token))
        else:
            rid = role_id or os.environ.get("VAULT_APPROLE_ROLE_ID")
            sid = secret_id or os.environ.get("VAULT_APPROLE_SECRET_ID")
            if not rid or not sid:
                raise VaultError(
                    "No Vault credentials found. "
                    "Set VAULT_TOKEN or both VAULT_APPROLE_ROLE_ID and VAULT_APPROLE_SECRET_ID."
                )
            self._token = self._approle_login(rid, sid)
            logger.debug("Vault: AppRole login succeeded")

    def _approle_login(self, role_id: str, secret_id: str) -> str:
        """Perform AppRole authentication and return the client token."""
        url = f"{self.addr}/v1/auth/approle/login"
        resp = requests.post(
            url,
            json={"role_id": role_id, "secret_id": secret_id},
            verify=self.verify,
            timeout=15,
        )
        if resp.status_code != 200:
            raise VaultError(
                f"Vault AppRole login failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )
        data = resp.json()
        token = data.get("auth", {}).get("client_token")
        if not token:
            raise VaultError("Vault AppRole login response missing client_token")
        # Never log the token value itself
        logger.debug("Vault: AppRole token obtained (presence confirmed)")
        return token

    def _get(self, path: str) -> dict:
        """Raw GET against /v1/<path> with auth token."""
        url = f"{self.addr}/v1/{path.lstrip('/')}"
        resp = requests.get(
            url,
            headers={"X-Vault-Token": self._token},
            verify=self.verify,
            timeout=15,
        )
        if resp.status_code == 404:
            raise VaultError(f"Vault path not found: {path}")
        if resp.status_code != 200:
            raise VaultError(
                f"Vault GET {path} failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )
        return resp.json()

    def kv_read(self, path: str, field: str | None = None):
        """
        Read a KV secret at the given path.
        OPS-277: Normalizes KV-v1-style `secret/<x>` into KV-v2 `secret/data/<x>`
        on the overwatch Vault, where `secret/` is a KV-v2 mount.

        Handles KV-v1 and KV-v2 transparently:
          - KV-v1: response.data = {key: value, ...}
          - KV-v2: response.data.data = {key: value, ...}

        If field is None, returns the full dict.
        If field is given, returns only that field's value.

        Never logs or prints secret values.
        """
        # OPS-277: auto-transform for KV-v2 mount under `secret/`
        if path.startswith("secret/") and not path.startswith("secret/data/") and not path.startswith("secret/metadata/"):
            path = "secret/data/" + path[len("secret/"):]
        raw = self._get(path)
        payload = raw.get("data", {})

        # KV-v2 wraps secrets under a nested "data" key
        if "data" in payload and isinstance(payload["data"], dict):
            payload = payload["data"]

        logger.debug(
            "Vault: kv_read(%s) returned %d field(s) (values not logged)",
            path,
            len(payload),
        )

        if field is not None:
            if field not in payload:
                raise VaultError(
                    f"Vault path {path!r} does not contain field {field!r}. "
                    f"Available fields: {list(payload.keys())}"
                )
            return payload[field]

        return payload
