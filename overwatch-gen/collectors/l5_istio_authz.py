"""
l5_istio_authz.py — Istio AuthorizationPolicy collector.

Live mode: uses the kubernetes Python client (CustomObjectsApi) to list
AuthorizationPolicy CRDs across all namespaces.
Kubeconfig fetched from Vault: secret/forgejo-runner/arch-vault-kubeconfig.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/authzpolicy_sample.json.

Returns a dict with key:
  policies  list  — AuthorizationPolicy records:
    name        str   — policy name
    namespace   str   — namespace
    action      str   — ALLOW / DENY / AUDIT / CUSTOM (default ALLOW)
    selector    dict  — matchLabels selector (empty = all pods)
    rule_count  int   — number of rules in the spec
    sources     list  — flattened source.principals from rules[].from[].source

Registers layer "l5_authz".
"""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "authzpolicy_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "authzpolicy_live.json"
)

_ISTIO_GROUP = "security.istio.io"
_AUTHZ_VERSION = "v1beta1"
_AUTHZ_PLURAL = "authorizationpolicies"


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"AuthorizationPolicy fixture not found: {_FIXTURE_SAMPLE}."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _save_fixture(data: dict) -> None:
    _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    logger.info("AuthzPolicy: live fixture written to %s", _FIXTURE_LIVE)


def _get_kubeconfig_content() -> str:
    from overwatch_gen.lib.vault_client import VaultClient
    vc = VaultClient()
    return vc.kv_read(
        "secret/forgejo-runner/arch-vault-kubeconfig",
        field="kubeconfig",
    )


def _extract_sources(rules: list) -> list[str]:
    """Flatten rule[].from[].source.principals into a sorted list."""
    principals = set()
    for rule in rules:
        for frm in rule.get("from") or []:
            src = frm.get("source") or {}
            for p in src.get("principals") or []:
                principals.add(p)
    return sorted(principals)


def _normalize_policy(item: dict) -> dict:
    """Normalize a single AuthorizationPolicy dict."""
    meta = item.get("metadata") or {}
    spec = item.get("spec") or {}
    rules = spec.get("rules") or []
    selector_labels = {}
    sel = spec.get("selector") or {}
    selector_labels = sel.get("matchLabels") or {}

    return {
        "action": spec.get("action", "ALLOW"),
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "rule_count": len(rules),
        "selector": selector_labels,
        "sources": _extract_sources(rules),
    }


def _normalize(raw: dict) -> dict:
    """Normalize raw fixture/live items."""
    policies = sorted(
        [_normalize_policy(item) for item in raw.get("items", [])],
        key=lambda p: (p["namespace"], p["name"]),
    )
    return {"policies": policies}


def _collect_live() -> dict:
    """Collect AuthorizationPolicy from live cluster."""
    import kubernetes

    kubeconfig_content = _get_kubeconfig_content()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write(kubeconfig_content)
        kubeconfig_path = tf.name

    try:
        kubernetes.config.load_kube_config(config_file=kubeconfig_path)
        custom = kubernetes.client.CustomObjectsApi()

        result = custom.list_cluster_custom_object(
            group=_ISTIO_GROUP,
            version=_AUTHZ_VERSION,
            plural=_AUTHZ_PLURAL,
            _request_timeout=15,
        )
        return _normalize(result)
    finally:
        os.unlink(kubeconfig_path)


def collect() -> dict:
    """
    Collect Istio AuthorizationPolicy data.

    Returns dict with key: policies (list of normalized records).
    Sorted by (namespace, name) for deterministic output.
    """
    if _use_fixtures():
        logger.info("AuthzPolicy: loading fixture from %s", _FIXTURE_SAMPLE)
        raw = _load_fixture()
        return _normalize(raw)

    data = _collect_live()

    if _capture_fixture():
        _save_fixture(data)

    return data


def render(data: dict) -> None:
    """Cache AuthzPolicy data for combined L5 render (triggered by l5_peerauth orchestrator)."""
    logger.debug("l5_istio_authz.render: caching authz data for combined L5 render")
    _AUTHZ_DATA_CACHE.update(data)


# Module-level cache for combined render coordination
_AUTHZ_DATA_CACHE: dict = {}


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l5_authz", collect, render)
