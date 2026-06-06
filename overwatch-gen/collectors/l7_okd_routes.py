"""
l7_okd_routes.py — L7 OpenShift Route collector.

Collects: OpenShift Route CRs from OKD cluster.
Registers layer "l7_okd_routes" on import.

Kubernetes CRDs read (via kubeconfig, arch-vault-reader SA):
  routes.route.openshift.io  (all namespaces)

Auth: Vault path secret/forgejo-runner/arch-vault-kubeconfig.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/okd_routes_sample.json.
Fixture capture: ARCH_AUDIT_CAPTURE_FIXTURE=1 writes fixtures/okd_routes_live.json.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "okd_routes_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "okd_routes_live.json"
)


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"OKD Routes fixture not found: {_FIXTURE_SAMPLE}. "
            "Run with ARCH_AUDIT_CAPTURE_FIXTURE=1 against live OKD cluster first."
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


def _parse_okd_route(cr: dict) -> dict:
    """Parse an OpenShift Route CR into a normalized dict."""
    spec = cr.get("spec", {})
    meta = cr.get("metadata", {})

    tls = spec.get("tls", {})
    tls_termination = tls.get("termination", "none") if tls else "none"
    tls_insecure_edge_policy = tls.get("insecureEdgeTerminationPolicy", "None") if tls else "None"

    to = spec.get("to", {})
    destination_service = to.get("name", "")

    port = spec.get("port", {})
    destination_port = port.get("targetPort", 0)

    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "host": spec.get("host", ""),
        "path": spec.get("path", "/"),
        "tls_termination": tls_termination,
        "tls_insecure_edge_policy": tls_insecure_edge_policy,
        "destination_service": destination_service,
        "destination_port": destination_port,
        "wildcard_policy": spec.get("wildcardPolicy", "None"),
    }


def _collect_live() -> dict:
    """Collect OKD Route data from live Kubernetes."""
    kubeconfig = _load_kubeconfig()
    if kubeconfig is None:
        logger.warning(
            "OKD Routes collector: kubeconfig unavailable — returning empty stub"
        )
        return _empty_stub()

    try:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config

        k8s_config.load_kube_config(config_file=kubeconfig)
        custom_api = k8s_client.CustomObjectsApi()

        routes_resp = custom_api.list_cluster_custom_object(
            group="route.openshift.io",
            version="v1",
            plural="routes",
            _request_timeout=15,
        )
        routes = [_parse_okd_route(item) for item in routes_resp.get("items", [])]
        routes.sort(key=lambda r: (r.get("namespace", ""), r.get("host", "")))

        data = {
            "source": "okd_routes",
            "routes": routes,
        }

        if _capture_fixture():
            _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
            with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            logger.info("Captured OKD Routes fixture to %s", _FIXTURE_LIVE)

        return data

    except Exception as exc:
        logger.warning("OKD Routes live collection failed: %s", exc)
        return _empty_stub()


def _empty_stub() -> dict:
    return {
        "source": "okd_routes",
        "routes": [],
        "_warning": "OKD Routes data unavailable: kubeconfig missing or cluster unreachable.",
    }


def collect() -> dict:
    """
    Collect OpenShift Route CR data.

    Returns dict with keys:
      source  — "okd_routes"
      routes  — list of Route dicts (name, namespace, host, path, tls_termination,
                tls_insecure_edge_policy, destination_service, destination_port,
                wildcard_policy)
      _warning — (optional) warning if unavailable
    """
    if _use_fixtures():
        return _load_fixture()
    return _collect_live()


def render(data: dict) -> None:
    """Cache OKD route data for combined L7 render."""
    logger.debug("l7_okd_routes.render: caching for combined L7 render")
    _OKD_DATA_CACHE.update(data)


# Module-level cache for combined render coordination
_OKD_DATA_CACHE: dict = {}


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l7_okd_routes", collect, render)
