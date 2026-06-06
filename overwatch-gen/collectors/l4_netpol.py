"""
l4_netpol.py — Kubernetes NetworkPolicy collector.

Live mode: uses the kubernetes Python client to list NetworkPolicy resources
across all namespaces.  Requires KUBECONFIG or in-cluster configuration.
Kubeconfig is fetched from Vault path secret/forgejo-runner/arch-vault-kubeconfig,
field kubeconfig.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/netpol_sample.json.

Returns a dict with key:
  policies   list  — NetworkPolicy records, each as a dict with keys:
    name          str   — policy name
    namespace     str   — namespace
    pod_selector  dict  — spec.podSelector (matchLabels or {})
    ingress       list  — ingress rules (empty = deny all ingress)
    egress        list  — egress rules
    policy_types  list  — policyTypes from spec

Registers layer "l4_netpol".
"""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "netpol_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "netpol_live.json"
)


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"NetworkPolicy fixture not found: {_FIXTURE_SAMPLE}. "
            "Run with ARCH_AUDIT_CAPTURE_FIXTURE=1 against live cluster first."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _save_fixture(data: dict) -> None:
    _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    logger.info("NetworkPolicy: live fixture written to %s", _FIXTURE_LIVE)


def _get_kubeconfig_content() -> str:
    """Fetch kubeconfig from Vault."""
    from overwatch_gen.lib.vault_client import VaultClient
    vc = VaultClient()
    return vc.kv_read(
        "secret/forgejo-runner/arch-vault-kubeconfig",
        field="kubeconfig",
    )


def _collect_live() -> dict:
    """Collect NetworkPolicy data from live cluster."""
    import kubernetes

    kubeconfig_content = _get_kubeconfig_content()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write(kubeconfig_content)
        kubeconfig_path = tf.name

    try:
        kubernetes.config.load_kube_config(config_file=kubeconfig_path)
        v1_net = kubernetes.client.NetworkingV1Api()
        result = v1_net.list_network_policy_for_all_namespaces(_request_timeout=15)
        raw_items = [item.to_dict() for item in result.items]
        return _normalize(raw_items)
    finally:
        os.unlink(kubeconfig_path)


def _normalize(raw_items: list) -> dict:
    """Normalize raw NetworkPolicy items into stable sorted records."""
    policies = []
    for item in raw_items:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        policies.append({
            "egress": spec.get("egress") or [],
            "ingress": spec.get("ingress") or [],
            "name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "pod_selector": spec.get("pod_selector") or spec.get("podSelector") or {},
            "policy_types": sorted(spec.get("policy_types") or spec.get("policyTypes") or []),
        })

    policies = sorted(policies, key=lambda p: (p["namespace"], p["name"]))
    return {"policies": policies}


def collect() -> dict:
    """
    Collect Kubernetes NetworkPolicy data.

    Returns dict with key: policies (list of normalized records).
    Sorted by (namespace, name) for deterministic output.
    """
    if _use_fixtures():
        logger.info("NetworkPolicy: loading fixture from %s", _FIXTURE_SAMPLE)
        raw = _load_fixture()
        return _normalize(raw.get("items", []))

    data = _collect_live()

    if _capture_fixture():
        _save_fixture(data)

    return data


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402
from overwatch_gen.renderers import l4_renderer  # noqa: E402

registry.register_layer("l4_netpol", collect, l4_renderer.render_netpol)
