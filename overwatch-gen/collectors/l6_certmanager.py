"""
l6_certmanager.py — L6 cert-manager collector.

Collects: Certificate CRs and ClusterIssuer CRs from Kubernetes/OKD.
Registers layer "l6_certmanager" on import.

Kubernetes resources read (via kubeconfig):
  certificates.cert-manager.io  (all namespaces)
  clusterissuers.cert-manager.io

Auth: Vault path secret/forgejo-runner/arch-vault-kubeconfig
  SA: arch-vault-reader with view ClusterRole.
  Cannot read Secrets — uses Certificate CR status.notAfter + spec.dnsNames only.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/certmanager_sample.json.
Fixture capture: ARCH_AUDIT_CAPTURE_FIXTURE=1 writes fixtures/certmanager_live.json.
"""

import datetime
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "certmanager_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "certmanager_live.json"
)

_WARN_DAYS = 30


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"cert-manager fixture not found: {_FIXTURE_SAMPLE}. "
            "Run with ARCH_AUDIT_CAPTURE_FIXTURE=1 against live cluster first."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _load_kubeconfig() -> str | None:
    """
    Load kubeconfig from Vault or KUBECONFIG env var.

    Returns path to kubeconfig file, or None if unavailable.
    """
    # Check explicit env first
    kubeconfig_path = os.environ.get("KUBECONFIG")
    if kubeconfig_path and Path(kubeconfig_path).exists():
        return kubeconfig_path

    # Try to fetch from Vault
    vault_token = os.environ.get("VAULT_TOKEN", "")
    vault_addr = os.environ.get("VAULT_ADDR", "https://192.168.12.206:8200")
    vault_skip_verify = os.environ.get("VAULT_SKIP_VERIFY", "true").lower() in ("1", "true", "yes")

    if not vault_token:
        logger.warning("No VAULT_TOKEN and no KUBECONFIG — cannot load kubeconfig")
        return None

    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        resp = requests.get(
            f"{vault_addr.rstrip('/')}/v1/secret/data/forgejo-runner/arch-vault-kubeconfig",
            headers={"X-Vault-Token": vault_token},
            verify=not vault_skip_verify,
            timeout=15,
        )
        if resp.status_code == 200:
            kube_content = resp.json()["data"]["data"].get("kubeconfig", "")
            if kube_content:
                import tempfile
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".yaml", mode="w", delete=False, encoding="utf-8"
                )
                tmp.write(kube_content)
                tmp.flush()
                logger.info("Loaded kubeconfig from Vault to %s", tmp.name)
                return tmp.name
        else:
            logger.warning(
                "Could not fetch kubeconfig from Vault: HTTP %s", resp.status_code
            )
    except Exception as exc:
        logger.warning("Failed to load kubeconfig from Vault: %s", exc)

    return None


def _days_remaining(not_after_str: str | None) -> int:
    """Calculate days remaining from an ISO-8601 not_after string."""
    if not not_after_str:
        return -999
    try:
        not_after = datetime.datetime.fromisoformat(
            not_after_str.replace("Z", "+00:00")
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = not_after - now
        return delta.days
    except (ValueError, AttributeError):
        return -999


def _parse_cert_cr(cr: dict) -> dict:
    """Parse a Certificate CR into a normalized dict."""
    spec = cr.get("spec", {})
    status = cr.get("status", {})
    meta = cr.get("metadata", {})

    not_after = status.get("notAfter")
    days = _days_remaining(not_after)

    issuer_ref = spec.get("issuerRef", {})

    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "dns_names": sorted(spec.get("dnsNames", [])),
        "issuer_name": issuer_ref.get("name", ""),
        "issuer_kind": issuer_ref.get("kind", "Issuer"),
        "renewal_time": status.get("renewalTime"),
        "not_after": not_after,
        "days_remaining": days,
        "ready": any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in status.get("conditions", [])
        ),
    }


def _parse_cluster_issuer(cr: dict) -> dict:
    """Parse a ClusterIssuer CR into a normalized dict."""
    spec = cr.get("spec", {})
    status = cr.get("status", {})
    meta = cr.get("metadata", {})

    # Determine issuer type
    if "vault" in spec:
        issuer_type = "vault"
        vault_path = spec["vault"].get("path", "")
    elif "acme" in spec:
        issuer_type = "acme"
        vault_path = ""
    elif "selfSigned" in spec:
        issuer_type = "selfSigned"
        vault_path = ""
    elif "ca" in spec:
        issuer_type = "ca"
        vault_path = ""
    else:
        issuer_type = "unknown"
        vault_path = ""

    return {
        "name": meta.get("name", ""),
        "type": issuer_type,
        "vault_path": vault_path,
        "ready": any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in status.get("conditions", [])
        ),
    }


def _collect_live() -> dict:
    """Collect cert-manager CR data from live Kubernetes."""
    kubeconfig = _load_kubeconfig()
    if kubeconfig is None:
        logger.warning(
            "cert-manager collector: kubeconfig unavailable — returning empty stub"
        )
        return _empty_stub()

    try:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config
        from kubernetes.client.exceptions import ApiException

        k8s_config.load_kube_config(config_file=kubeconfig)
        custom_api = k8s_client.CustomObjectsApi()

        # Collect Certificate CRs (all namespaces)
        try:
            certs_resp = custom_api.list_cluster_custom_object(
                group="cert-manager.io",
                version="v1",
                plural="certificates",
                _request_timeout=15,
            )
            certs = [_parse_cert_cr(item) for item in certs_resp.get("items", [])]
            certs.sort(key=lambda c: (c["namespace"], c["name"]))
        except ApiException as exc:
            if exc.status == 404:
                logger.warning(
                    "cert-manager Certificate CRD not found (404) — cert-manager may not be installed"
                )
                return _empty_stub(not_installed=True)
            raise

        # Collect ClusterIssuer CRs
        try:
            issuers_resp = custom_api.list_cluster_custom_object(
                group="cert-manager.io",
                version="v1",
                plural="clusterissuers",
                _request_timeout=15,
            )
            cluster_issuers = [
                _parse_cluster_issuer(item) for item in issuers_resp.get("items", [])
            ]
            cluster_issuers.sort(key=lambda x: x["name"])
        except ApiException as exc:
            if exc.status == 404:
                logger.warning(
                    "cert-manager ClusterIssuer CRD not found (404) — cert-manager may not be installed"
                )
                return _empty_stub(not_installed=True)
            raise

        data = {
            "source": "certmanager",
            "certificates": certs,
            "cluster_issuers": cluster_issuers,
        }

        if _capture_fixture():
            _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
            with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            logger.info("Captured cert-manager fixture to %s", _FIXTURE_LIVE)

        return data

    except Exception as exc:
        logger.warning("cert-manager live collection failed: %s", exc)
        return _empty_stub()


def _empty_stub(not_installed: bool = False) -> dict:
    if not_installed:
        warning = "cert-manager not installed in cluster"
    else:
        warning = "cert-manager data unavailable: kubeconfig missing or cluster unreachable."
    return {
        "source": "certmanager",
        "certificates": [],
        "cluster_issuers": [],
        "_warning": warning,
    }


def collect() -> dict:
    """
    Collect cert-manager Certificate CR and ClusterIssuer data.

    Returns dict with keys:
      source          — "certmanager"
      certificates    — list of Certificate CR dicts
      cluster_issuers — list of ClusterIssuer dicts
      _warning        — (optional) warning if cluster unreachable
    """
    if _use_fixtures():
        return _load_fixture()
    return _collect_live()


def render(data: dict) -> None:
    """Cache cert-manager data for combined L6 render (triggered by l6_vault_pki orchestrator)."""
    logger.debug("l6_certmanager.render: caching certmanager data for combined L6 render")
    _CERTMANAGER_DATA_CACHE.update(data)


# Module-level cache for combined render coordination
_CERTMANAGER_DATA_CACHE: dict = {}


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l6_certmanager", collect, render)
