"""
l5_renderer.py — L5 Session layer renderer.

Renders PeerAuthentication + AuthorizationPolicy + Keycloak data into:
  architecture-vault/05-L5-session/INDEX.md    (Markdown tables)
  architecture-vault/05-L5-session/session-topology.d2  (D2 diagram)

Primary entry point:
  render_combined(authz, keycloak, peerauth) — writes INDEX.md ONCE with all sections.
  Called by the l5_peerauth orchestrator (alphabetically last).

Backwards-compatible thin wrappers (standalone layer execution):
  render_peerauth(data) — calls render_combined with empty authz/keycloak sections
  render_authz(data)    — calls render_combined with empty peerauth/keycloak sections
  render_keycloak(data) — calls render_combined with empty peerauth/authz sections

Template: overwatch-gen/templates/l5_session.md.j2
          overwatch-gen/templates/l5.d2.j2
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_VAULT_DIR = Path(__file__).parent.parent.parent / "architecture-vault"
_L5_DIR = _VAULT_DIR / "05-L5-session"

_MD_TEMPLATE = _TEMPLATE_DIR / "l5_session.md.j2"
_D2_TEMPLATE = _TEMPLATE_DIR / "l5.d2.j2"


def _dry_run() -> bool:
    return os.environ.get("OVERWATCH_GEN_DRY_RUN", "").lower() in ("1", "true", "yes")


def _run_d2(d2_path: Path, svg_path: Path) -> None:
    try:
        result = subprocess.run(
            ["d2", str(d2_path), str(svg_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("d2 render failed for %s: %s", d2_path, result.stderr[:300])
        else:
            logger.info("d2: rendered %s", svg_path)
    except FileNotFoundError:
        logger.warning("d2 binary not found — install d2 for SVG output. Skipping %s", svg_path)
    except subprocess.TimeoutExpired:
        logger.warning("d2 timed out rendering %s", d2_path)


def _write_output(context: dict) -> None:
    from overwatch_gen.lib.render import render_markdown, write_deterministic

    md_content = render_markdown(_MD_TEMPLATE, context)
    d2_content = render_markdown(_D2_TEMPLATE, context)

    if _dry_run():
        print("=== L5 Session INDEX.md ===")
        print(md_content)
        print("=== L5 session-topology.d2 ===")
        print(d2_content)
        return

    _L5_DIR.mkdir(parents=True, exist_ok=True)
    write_deterministic(_L5_DIR / "INDEX.md", md_content)

    d2_path = _L5_DIR / "session-topology.d2"
    write_deterministic(d2_path, d2_content)

    svg_dir = _VAULT_DIR / "99-diagrams-svg"
    svg_dir.mkdir(parents=True, exist_ok=True)
    _run_d2(d2_path, svg_dir / "l5-session-topology.svg")
    logger.info("L5 render complete: %s", _L5_DIR / "INDEX.md")


def render_combined(authz: dict, keycloak: dict, peerauth: dict) -> None:
    """
    Render L5 session layer combining AuthzPolicy + Keycloak + PeerAuth data.

    authz keys expected:    policies           (from l5_istio_authz.collect())
    keycloak keys expected: clients, client_scopes (from l5_keycloak.collect())
    peerauth keys expected: peer_auths, flow_matrix (from l5_istio_peerauth.collect())

    Any section may be an empty dict if that layer was not run.

    Writes INDEX.md and session-topology.d2 ONCE with all fields populated.
    This is the authoritative write — called by the l5_peerauth orchestrator.
    """
    context = {
        "authz_policies": authz.get("policies", []),
        "client_scopes": keycloak.get("client_scopes", []),
        "clients": keycloak.get("clients", []),
        "flow_matrix": peerauth.get("flow_matrix", []),
        "peer_auths": peerauth.get("peer_auths", []),
    }
    _write_output(context)


def render_peerauth(data: dict) -> None:
    """
    Render L5 PeerAuthentication + flow matrix data (standalone wrapper).

    data keys expected: peer_auths, flow_matrix (from l5_peerauth.collect())
    Calls render_combined with empty authz and keycloak sections.
    """
    render_combined(authz={}, keycloak={}, peerauth=data)


def render_authz(data: dict) -> None:
    """
    Render L5 AuthorizationPolicy data (standalone wrapper).

    data keys expected: policies (from l5_authz.collect())
    Calls render_combined with empty peerauth and keycloak sections.
    """
    render_combined(authz=data, keycloak={}, peerauth={})


def render_keycloak(data: dict) -> None:
    """
    Render L5 Keycloak client data (standalone wrapper).

    data keys expected: clients, client_scopes (from l5_keycloak.collect())
    Calls render_combined with empty peerauth and authz sections.
    """
    render_combined(authz={}, keycloak=data, peerauth={})
