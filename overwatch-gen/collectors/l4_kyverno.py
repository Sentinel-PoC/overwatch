"""
l4_kyverno.py — Kyverno policy collector.

Live mode: uses the kubernetes Python client to list Kyverno ClusterPolicy
and Policy (namespace-scoped) CRDs across the cluster.

Kubeconfig fetched from Vault: secret/forgejo-runner/arch-vault-kubeconfig.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/kyverno_sample.json.

Returns a dict with keys:
  cluster_policies    list  — ClusterPolicy records:
    name                str   — policy name
    action              str   — validationFailureAction (Enforce/Audit)
    severity            str   — annotation-derived severity (high/medium/low/unknown)
    description         str   — annotation-derived description
    rule_count          int   — number of rules
    rule_names          list  — rule name strings (sorted)
  namespace_policies  list  — namespace-scoped Policy records (same shape + namespace key)

Registers layer "l4_kyverno".
"""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "kyverno_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "kyverno_live.json"
)

_KYVERNO_GROUP = "kyverno.io"
_CLUSTER_POLICY_VERSION = "v1"
_CLUSTER_POLICY_PLURAL = "clusterpolicies"
_POLICY_PLURAL = "policies"


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"Kyverno fixture not found: {_FIXTURE_SAMPLE}. "
            "Run with ARCH_AUDIT_CAPTURE_FIXTURE=1 against live cluster first."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _save_fixture(data: dict) -> None:
    _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    logger.info("Kyverno: live fixture written to %s", _FIXTURE_LIVE)


def _get_kubeconfig_content() -> str:
    from overwatch_gen.lib.vault_client import VaultClient
    vc = VaultClient()
    return vc.kv_read(
        "secret/forgejo-runner/arch-vault-kubeconfig",
        field="kubeconfig",
    )


def _normalize_policy(item: dict, namespace: str | None = None) -> dict:
    """Normalize a single Kyverno ClusterPolicy or Policy dict."""
    meta = item.get("metadata") or {}
    annotations = meta.get("annotations") or {}
    spec = item.get("spec") or {}

    rules = spec.get("rules") or []
    rule_names = sorted(r.get("name", "") for r in rules)

    record = {
        "action": spec.get("validationFailureAction", "Audit"),
        "description": annotations.get(
            "policies.kyverno.io/description", ""
        ),
        "name": meta.get("name", ""),
        "rule_count": len(rules),
        "rule_names": rule_names,
        "severity": annotations.get(
            "policies.kyverno.io/severity", "unknown"
        ),
    }
    if namespace is not None:
        record["namespace"] = namespace
    return record


def _normalize(raw: dict) -> dict:
    """Normalize raw fixture/live data into stable sorted dicts."""
    cluster_policies = sorted(
        [_normalize_policy(p) for p in raw.get("cluster_policies", [])],
        key=lambda p: p["name"],
    )
    namespace_policies = sorted(
        [
            _normalize_policy(
                p,
                namespace=(p.get("metadata") or {}).get("namespace", ""),
            )
            for p in raw.get("namespace_policies", [])
        ],
        key=lambda p: (p.get("namespace", ""), p["name"]),
    )
    return {
        "cluster_policies": cluster_policies,
        "namespace_policies": namespace_policies,
    }


def _collect_live() -> dict:
    """Collect Kyverno policy data from live cluster via CRD API."""
    import kubernetes
    from kubernetes.client.exceptions import ApiException

    kubeconfig_content = _get_kubeconfig_content()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write(kubeconfig_content)
        kubeconfig_path = tf.name

    try:
        kubernetes.config.load_kube_config(config_file=kubeconfig_path)
        custom = kubernetes.client.CustomObjectsApi()

        cluster_raw = []
        ns_raw = []
        warning = None

        try:
            cp_result = custom.list_cluster_custom_object(
                group=_KYVERNO_GROUP,
                version=_CLUSTER_POLICY_VERSION,
                plural=_CLUSTER_POLICY_PLURAL,
                _request_timeout=15,
            )
            cluster_raw = cp_result.get("items", [])
        except ApiException as exc:
            if exc.status == 404:
                logger.warning(
                    "Kyverno ClusterPolicy CRD not found (404) — Kyverno may not be installed"
                )
                warning = "Kyverno not installed in cluster (ClusterPolicy CRD returned 404)"
            else:
                raise

        try:
            p_result = custom.list_cluster_custom_object(
                group=_KYVERNO_GROUP,
                version=_CLUSTER_POLICY_VERSION,
                plural=_POLICY_PLURAL,
                _request_timeout=15,
            )
            ns_raw = p_result.get("items", [])
        except ApiException as exc:
            if exc.status == 404:
                logger.warning(
                    "Kyverno Policy CRD not found (404) — Kyverno may not be installed"
                )
                if warning is None:
                    warning = "Kyverno not installed in cluster (Policy CRD returned 404)"
            else:
                raise

        result = _normalize({"cluster_policies": cluster_raw, "namespace_policies": ns_raw})
        if warning:
            result["_warning"] = warning
        return result
    finally:
        os.unlink(kubeconfig_path)


def collect() -> dict:
    """
    Collect Kyverno ClusterPolicy and Policy data.

    Returns dict with keys: cluster_policies, namespace_policies.
    Each list sorted deterministically.
    """
    if _use_fixtures():
        logger.info("Kyverno: loading fixture from %s", _FIXTURE_SAMPLE)
        raw = _load_fixture()
        return _normalize(raw)

    data = _collect_live()

    if _capture_fixture():
        _save_fixture(data)

    return data


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402
from overwatch_gen.renderers import l4_renderer  # noqa: E402

registry.register_layer("l4_kyverno", collect, l4_renderer.render_kyverno)
