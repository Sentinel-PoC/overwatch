"""
l7_traefik.py — L7 Traefik collector.

Collects: IngressRoute CRs + Middleware CRs from Kubernetes.
Registers layer "l7_traefik" on import.

Kubernetes CRDs read (via kubeconfig, arch-vault-reader SA):
  ingressroutes.traefik.io    (all namespaces)
  middlewares.traefik.io      (all namespaces)

Also optionally reads Traefik dashboard API at traefik.208.haist.farm/api/
for live route validation (non-authoritative, used for cross-check only).

Auth: Vault path secret/forgejo-runner/arch-vault-kubeconfig.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/traefik_routes_sample.json.
Fixture capture: ARCH_AUDIT_CAPTURE_FIXTURE=1 writes fixtures/traefik_routes_live.json.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "traefik_routes_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "traefik_routes_live.json"
)

_TRAEFIK_DASHBOARD = os.environ.get(
    "TRAEFIK_DASHBOARD_URL", "https://traefik.208.haist.farm/api"
)


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"Traefik fixture not found: {_FIXTURE_SAMPLE}. "
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
    except Exception as exc:
        logger.warning("Failed to load kubeconfig from Vault: %s", exc)

    return None


def _parse_ingress_route(cr: dict) -> dict:
    """Parse a Traefik IngressRoute CR into a normalized dict."""
    spec = cr.get("spec", {})
    meta = cr.get("metadata", {})

    # Extract host from routes[*].match rule (look for Host() matcher)
    host = ""
    middlewares = []
    service = ""
    service_port = 0

    for route in spec.get("routes", []):
        match_str = route.get("match", "")
        # Parse Host(`hostname`) from the match string
        import re
        host_match = re.search(r"Host\(`([^`]+)`\)", match_str)
        if host_match and not host:
            host = host_match.group(1)

        # Collect middleware names
        for mw_ref in route.get("middlewares", []):
            mw_name = mw_ref.get("name", "")
            if mw_name and mw_name not in middlewares:
                middlewares.append(mw_name)

        # Collect first service
        for svc in route.get("services", []):
            if not service:
                service = svc.get("name", "")
                service_port = svc.get("port", 0)

    # TLS info
    tls = spec.get("tls", {})
    tls_secret = ""
    tls_issuer = ""
    if "secretName" in tls:
        tls_secret = tls["secretName"]
    cert_resolver = tls.get("certResolver", "")
    if cert_resolver:
        tls_issuer = cert_resolver

    # Try to infer TLS issuer from cert-manager annotation
    annotations = meta.get("annotations", {})
    if not tls_issuer:
        tls_issuer = annotations.get(
            "cert-manager.io/cluster-issuer",
            annotations.get("cert-manager.io/issuer", ""),
        )

    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "host": host,
        "tls_secret": tls_secret,
        "tls_issuer": tls_issuer,
        "middlewares": middlewares,
        "service": service,
        "service_port": service_port,
    }


def _parse_middleware(cr: dict) -> dict:
    """Parse a Traefik Middleware CR into a normalized dict."""
    spec = cr.get("spec", {})
    meta = cr.get("metadata", {})

    # Detect middleware type from spec keys
    mw_type = "unknown"
    settings = {}
    for key in ["forwardAuth", "headers", "rateLimit", "redirectScheme",
                "stripPrefix", "basicAuth", "compress", "buffering"]:
        if key in spec:
            mw_type = key
            settings = spec[key]
            break

    if mw_type == "forwardAuth":
        return {
            "name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "type": "forwardAuth",
            "address": settings.get("address", ""),
        }

    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "type": mw_type,
        "settings": {
            k: v for k, v in settings.items()
            if not isinstance(v, dict) or len(str(v)) < 200
        },
    }


def _collect_live() -> dict:
    """Collect Traefik IngressRoute + Middleware data from live Kubernetes."""
    kubeconfig = _load_kubeconfig()
    if kubeconfig is None:
        logger.warning(
            "Traefik collector: kubeconfig unavailable — returning empty stub"
        )
        return _empty_stub()

    try:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config

        k8s_config.load_kube_config(config_file=kubeconfig)
        custom_api = k8s_client.CustomObjectsApi()

        # IngressRoutes
        routes_resp = custom_api.list_cluster_custom_object(
            group="traefik.io",
            version="v1alpha1",
            plural="ingressroutes",
            _request_timeout=15,
        )
        ingress_routes = [
            _parse_ingress_route(item) for item in routes_resp.get("items", [])
        ]
        ingress_routes.sort(key=lambda r: r.get("host", r.get("name", "")))

        # Middlewares
        mw_resp = custom_api.list_cluster_custom_object(
            group="traefik.io",
            version="v1alpha1",
            plural="middlewares",
            _request_timeout=15,
        )
        middlewares = [
            _parse_middleware(item) for item in mw_resp.get("items", [])
        ]
        middlewares.sort(key=lambda m: (m.get("namespace", ""), m.get("name", "")))

        data = {
            "source": "traefik",
            "ingress_routes": ingress_routes,
            "middlewares": middlewares,
        }

        if _capture_fixture():
            _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
            with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            logger.info("Captured Traefik fixture to %s", _FIXTURE_LIVE)

        return data

    except Exception as exc:
        logger.warning("Traefik live collection failed: %s", exc)
        return _empty_stub()


def _empty_stub() -> dict:
    return {
        "source": "traefik",
        "ingress_routes": [],
        "middlewares": [],
        "_warning": "Traefik data unavailable: kubeconfig missing or cluster unreachable.",
    }


def collect() -> dict:
    """
    Collect Traefik IngressRoute and Middleware CR data.

    Returns dict with keys:
      source         — "traefik"
      ingress_routes — list of route dicts (name, namespace, host, tls_secret,
                       tls_issuer, middlewares, service, service_port)
      middlewares    — list of middleware dicts (name, namespace, type, ...)
      _warning       — (optional) warning if unavailable
    """
    if _use_fixtures():
        return _load_fixture()
    return _collect_live()


def render(data: dict) -> None:
    """Render L7 application layer page via l7_renderer (combined render)."""
    from overwatch_gen.renderers import l7_renderer
    from overwatch_gen.collectors.l7_istio import _ISTIO_DATA_CACHE
    from overwatch_gen.collectors.l7_okd_routes import _OKD_DATA_CACHE
    l7_renderer.render(
        traefik_data=data,
        istio_data=_ISTIO_DATA_CACHE,
        okd_data=_OKD_DATA_CACHE,
    )


# Module-level cache for combined render coordination
_TRAEFIK_DATA_CACHE: dict = {}


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l7_traefik", collect, render)
