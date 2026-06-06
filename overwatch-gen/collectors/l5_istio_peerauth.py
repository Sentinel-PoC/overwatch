"""
l5_istio_peerauth.py — Istio PeerAuthentication collector + mTLS flow matrix.

Live mode: uses the kubernetes Python client (CustomObjectsApi) to list
PeerAuthentication CRDs across all namespaces.
Kubeconfig fetched from Vault: secret/forgejo-runner/arch-vault-kubeconfig.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/peerauth_sample.json.

Returns a dict with keys:
  peer_auths    list  — PeerAuthentication records:
    name        str   — policy name
    namespace   str   — namespace
    mode        str   — STRICT | PERMISSIVE | DISABLE | UNSET
    selector    dict  — matchLabels (empty = namespace-wide default)
    scope       str   — "namespace-default" | "workload-specific"

  flow_matrix   list  — namespace-pair mTLS posture rows:
    from_ns     str   — source namespace
    to_ns       str   — destination namespace
    netpol      str   — "ALLOW" | "DENY" | "NO-POLICY"
    peerauth    str   — effective mode for destination namespace
    posture     str   — "STRICT" | "PERMISSIVE" | "NONE"
    flag        str   — "" if STRICT, "WARN" if PERMISSIVE, "CRITICAL" if NONE

NOTE: The flow_matrix is computed from PeerAuth data only (NetPol cross-reference
requires running both l4_netpol and l5_peerauth together).  When run standalone,
flow_matrix shows peerauth posture per namespace pair without NetworkPolicy gating.

Registers layer "l5_peerauth".
"""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "peerauth_sample.json"
)
_FIXTURE_LIVE = (
    Path(__file__).parent.parent / "fixtures" / "peerauth_live.json"
)

_ISTIO_GROUP = "security.istio.io"
_PEERAUTH_VERSION = "v1"
_PEERAUTH_PLURAL = "peerauthentications"


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _capture_fixture() -> bool:
    return os.environ.get("ARCH_AUDIT_CAPTURE_FIXTURE", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"PeerAuthentication fixture not found: {_FIXTURE_SAMPLE}."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _save_fixture(data: dict) -> None:
    _FIXTURE_LIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FIXTURE_LIVE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    logger.info("PeerAuth: live fixture written to %s", _FIXTURE_LIVE)


def _get_kubeconfig_content() -> str:
    from overwatch_gen.lib.vault_client import VaultClient
    vc = VaultClient()
    return vc.kv_read(
        "secret/forgejo-runner/arch-vault-kubeconfig",
        field="kubeconfig",
    )


def _normalize_peerauth(item: dict) -> dict:
    """Normalize a single PeerAuthentication dict."""
    meta = item.get("metadata") or {}
    spec = item.get("spec") or {}
    mtls = spec.get("mtls") or {}
    selector = spec.get("selector") or {}
    selector_labels = selector.get("matchLabels") or {}

    mode = mtls.get("mode", "UNSET")
    scope = "workload-specific" if selector_labels else "namespace-default"

    return {
        "mode": mode,
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "scope": scope,
        "selector": selector_labels,
    }


def _build_flow_matrix(peer_auths: list) -> list:
    """
    Build namespace-pair flow matrix from PeerAuth modes.

    Logic:
    - Collect namespace-default PeerAuth for each namespace.
    - If no default exists, mode = UNSET (treated as PERMISSIVE in practice).
    - For each (from_ns, to_ns) ordered pair where from != to:
        posture = effective mode of to_ns (destination controls mTLS requirement)
        STRICT   -> posture STRICT
        PERMISSIVE/UNSET -> posture PERMISSIVE (warn)
        DISABLE  -> posture NONE (critical)
    """
    # Collect namespace-default modes (scope=namespace-default)
    ns_modes: dict[str, str] = {}
    for pa in peer_auths:
        if pa["scope"] == "namespace-default":
            ns_modes[pa["namespace"]] = pa["mode"]

    namespaces = sorted(ns_modes.keys())

    matrix = []
    for from_ns in namespaces:
        for to_ns in namespaces:
            if from_ns == to_ns:
                continue
            mode = ns_modes.get(to_ns, "UNSET")
            if mode == "STRICT":
                posture = "STRICT"
                flag = ""
            elif mode == "DISABLE":
                posture = "NONE"
                flag = "CRITICAL"
            else:
                # PERMISSIVE or UNSET
                posture = "PERMISSIVE"
                flag = "WARN"

            matrix.append({
                "flag": flag,
                "from_ns": from_ns,
                "peerauth": mode,
                "posture": posture,
                "to_ns": to_ns,
            })

    return sorted(matrix, key=lambda r: (r["from_ns"], r["to_ns"]))


def _normalize(raw: dict) -> dict:
    """Normalize raw fixture/live items and compute flow matrix."""
    peer_auths = sorted(
        [_normalize_peerauth(item) for item in raw.get("items", [])],
        key=lambda p: (p["namespace"], p["name"]),
    )
    flow_matrix = _build_flow_matrix(peer_auths)
    return {"flow_matrix": flow_matrix, "peer_auths": peer_auths}


def _collect_live() -> dict:
    """Collect PeerAuthentication from live cluster."""
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
            version=_PEERAUTH_VERSION,
            plural=_PEERAUTH_PLURAL,
            _request_timeout=15,
        )
        return _normalize(result)
    finally:
        os.unlink(kubeconfig_path)


def collect() -> dict:
    """
    Collect Istio PeerAuthentication data + compute flow matrix.

    Returns dict with keys:
      peer_auths   list — normalized PeerAuthentication records
      flow_matrix  list — namespace-pair mTLS posture matrix
    """
    if _use_fixtures():
        logger.info("PeerAuth: loading fixture from %s", _FIXTURE_SAMPLE)
        raw = _load_fixture()
        return _normalize(raw)

    data = _collect_live()

    if _capture_fixture():
        _save_fixture(data)

    return data


def render(data: dict) -> None:
    """Render L5 session layer page via l5_renderer (combined render — orchestrator)."""
    from overwatch_gen.renderers import l5_renderer
    from overwatch_gen.collectors.l5_istio_authz import _AUTHZ_DATA_CACHE
    from overwatch_gen.collectors.l5_keycloak import _KEYCLOAK_DATA_CACHE
    l5_renderer.render_combined(
        authz=_AUTHZ_DATA_CACHE,
        keycloak=_KEYCLOAK_DATA_CACHE,
        peerauth=data,
    )


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402

registry.register_layer("l5_peerauth", collect, render)
