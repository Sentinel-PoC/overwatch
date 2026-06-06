"""
l7_istio.py — L7 Istio collector.

Collects: VirtualService, DestinationRule, AuthorizationPolicy CRs.
Registers layer "l7_istio" on import.

Kubernetes CRDs read (via kubeconfig, arch-vault-reader SA):
  virtualservices.networking.istio.io      (all namespaces)
  destinationrules.networking.istio.io     (all namespaces)
  authorizationpolicies.security.istio.io  (all namespaces)

Auth: Vault path secret/forgejo-runner/arch-vault-kubeconfig.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/virtualservice_sample.json.
Fixture capture: ARCH_AUDIT_CAPTURE_FIXTURE=1 writes fixtures/virtualservice_live.json.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "virtualservice_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "virtualservice_live.json"
)


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"Istio fixture not found: {_FIXTURE_SAMPLE}. "
            "Run with ARCH_AUDIT_CAPTURE_FIXTURE=1 against live cluster first."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _load_kubeconfig() -> str | None:
    """Load kubeconfig from Vault or KUBECONFIG env var."""
    kubeconfig_path = os.environ.get("KUBECONFIG")
    if kubeconfig_path and Path(kubeconfig_path).exists():
        return kubeconfig_path

    vault_token = os.environ.get("VAULT_TOKEN", "")
    vault_addr = os.environ.get("VAULT_ADDR", "https://192.168.12.206:8200")
    vault_skip_verify = os.environ.get("VAULT_SKIP_VERIFY", "true").lower() in ("1", "true", "yes")

    if not vault_token:
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
                return tmp.name
    except Exception as exc:
        logger.warning("Failed to load kubeconfig from Vault: %s", exc)

    return None


def _parse_virtual_service(cr: dict) -> dict:
    """Parse an Istio VirtualService CR."""
    spec = cr.get("spec", {})
    meta = cr.get("metadata", {})

    # Normalize http routes to a simple list
    http_routes = []
    for route in spec.get("http", []):
        matches = route.get("match", [])
        destinations = [
            {
                "host": d.get("destination", {}).get("host", ""),
                "port": d.get("destination", {}).get("port", {}).get("number", 0),
                "weight": d.get("weight", 100),
            }
            for d in route.get("route", [])
        ]
        http_routes.append({
            "match": matches,
            "route": destinations,
        })

    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "hosts": spec.get("hosts", []),
        "gateways": spec.get("gateways", []),
        "http_routes": http_routes,
        "tls_routes": spec.get("tls", []),
    }


def _parse_destination_rule(cr: dict) -> dict:
    """Parse an Istio DestinationRule CR."""
    spec = cr.get("spec", {})
    meta = cr.get("metadata", {})

    traffic_policy = spec.get("trafficPolicy", {})
    tls_mode = (
        traffic_policy.get("tls", {}).get("mode", "")
        if traffic_policy
        else ""
    )

    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "host": spec.get("host", ""),
        "traffic_policy": {"tls": {"mode": tls_mode}} if tls_mode else {},
    }


def _parse_authz_policy(cr: dict) -> dict:
    """Parse an Istio AuthorizationPolicy CR."""
    spec = cr.get("spec", {})
    meta = cr.get("metadata", {})

    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "action": spec.get("action", "ALLOW"),
        "rules": spec.get("rules", []),
    }


def _collect_live() -> dict:
    """Collect Istio CR data from live Kubernetes."""
    kubeconfig = _load_kubeconfig()
    if kubeconfig is None:
        logger.warning(
            "Istio collector: kubeconfig unavailable — returning empty stub"
        )
        return _empty_stub()

    try:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config

        k8s_config.load_kube_config(config_file=kubeconfig)
        custom_api = k8s_client.CustomObjectsApi()

        # VirtualServices
        vs_resp = custom_api.list_cluster_custom_object(
            group="networking.istio.io",
            version="v1beta1",
            plural="virtualservices",
            _request_timeout=15,
        )
        virtual_services = [
            _parse_virtual_service(item) for item in vs_resp.get("items", [])
        ]
        virtual_services.sort(key=lambda x: (x["namespace"], x["name"]))

        # DestinationRules
        dr_resp = custom_api.list_cluster_custom_object(
            group="networking.istio.io",
            version="v1beta1",
            plural="destinationrules",
            _request_timeout=15,
        )
        destination_rules = [
            _parse_destination_rule(item) for item in dr_resp.get("items", [])
        ]
        destination_rules.sort(key=lambda x: (x["namespace"], x["name"]))

        # AuthorizationPolicies
        ap_resp = custom_api.list_cluster_custom_object(
            group="security.istio.io",
            version="v1beta1",
            plural="authorizationpolicies",
            _request_timeout=15,
        )
        authz_policies = [
            _parse_authz_policy(item) for item in ap_resp.get("items", [])
        ]
        authz_policies.sort(key=lambda x: (x["namespace"], x["name"]))

        data = {
            "source": "istio",
            "virtual_services": virtual_services,
            "destination_rules": destination_rules,
            "authorization_policies": authz_policies,
        }

        if _capture_fixture():
            _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
            with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            logger.info("Captured Istio fixture to %s", _FIXTURE_LIVE)

        return data

    except Exception as exc:
        logger.warning("Istio live collection failed: %s", exc)
        return _empty_stub()


def _empty_stub() -> dict:
    return {
        "source": "istio",
        "virtual_services": [],
        "destination_rules": [],
        "authorization_policies": [],
        "_warning": "Istio data unavailable: kubeconfig missing or CRDs not found.",
    }


def collect() -> dict:
    """
    Collect Istio VirtualService, DestinationRule, AuthorizationPolicy data.

    Returns dict with keys:
      source                 — "istio"
      virtual_services       — list of VirtualService dicts
      destination_rules      — list of DestinationRule dicts
      authorization_policies — list of AuthorizationPolicy dicts
      _warning               — (optional) warning if unavailable
    """
    if _use_fixtures():
        return _load_fixture()
    return _collect_live()


def render(data: dict) -> None:
    """Cache Istio data for combined L7 render (triggered by l7_traefik)."""
    logger.debug("l7_istio.render: caching istio data for combined L7 render")
    _ISTIO_DATA_CACHE.update(data)


# Module-level cache for combined render coordination
_ISTIO_DATA_CACHE: dict = {}


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l7_istio", collect, render)
