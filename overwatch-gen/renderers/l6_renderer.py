"""
l6_renderer.py — L6 Presentation layer renderer.

Renders Vault PKI + cert-manager data into:
  architecture-vault/06-L6-presentation/INDEX.md    (PKI trust chain + cert table)
  architecture-vault/06-L6-presentation/pki-trust-chain.d2  (D2 diagram)

Primary entry point:
  render_combined(certmanager, vault_pki) — writes INDEX.md ONCE with both sections.
  Called by the l6_vault_pki orchestrator (alphabetically last).

Backwards-compatible alias:
  render(pki_data, certmanager_data) — kept for existing callers; delegates to render_combined.

Template: overwatch-gen/templates/l6_presentation.md.j2
          overwatch-gen/templates/l6.d2.j2
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_VAULT_DIR = Path(__file__).parent.parent.parent / "architecture-vault"
_L6_DIR = _VAULT_DIR / "06-L6-presentation"

_MD_TEMPLATE = _TEMPLATE_DIR / "l6_presentation.md.j2"
_D2_TEMPLATE = _TEMPLATE_DIR / "l6.d2.j2"

_WARN_DAYS = 30


def _dry_run() -> bool:
    return os.environ.get("OVERWATCH_GEN_DRY_RUN", "").lower() in ("1", "true", "yes")


def _render_md(context: dict) -> str:
    from overwatch_gen.lib.render import render_markdown
    return render_markdown(_MD_TEMPLATE, context)


def _render_d2(context: dict) -> str:
    from overwatch_gen.lib.render import render_markdown
    return render_markdown(_D2_TEMPLATE, context)


def _run_d2(d2_path: Path, svg_path: Path) -> None:
    """Run d2 binary to produce SVG. Logs warning if d2 not installed."""
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
        logger.warning(
            "d2 binary not found — install d2 for SVG output. Skipping %s", svg_path
        )
    except subprocess.TimeoutExpired:
        logger.warning("d2 timed out rendering %s", d2_path)


def _annotate_certs(certs: list[dict]) -> list[dict]:
    """Add expiry_flag to each cert for template rendering."""
    annotated = []
    for cert in certs:
        days = cert.get("days_remaining", 0)
        cert = dict(cert)  # shallow copy
        cert["expiry_flag"] = "EXPIRED" if days < 0 else ("WARNING" if days < _WARN_DAYS else "OK")
        annotated.append(cert)
    return annotated


def _build_pki_context(pki_data: dict, certmanager_data: dict) -> dict:
    """Build Jinja2 context from PKI + cert-manager data."""
    pki_warning = pki_data.get("_warning", "")
    cm_warning = certmanager_data.get("_warning", "")

    all_warnings = []
    if pki_warning:
        all_warnings.append(pki_warning)
    if cm_warning:
        all_warnings.append(cm_warning)

    # PKI certs from Vault
    pki_certs = _annotate_certs(pki_data.get("certs", []))
    pki_issuers = pki_data.get("issuers", [])

    # cert-manager certificates
    cm_certs = _annotate_certs(certmanager_data.get("certificates", []))
    cluster_issuers = certmanager_data.get("cluster_issuers", [])

    # Expiry summary counts
    expired_count = sum(1 for c in pki_certs + cm_certs if c.get("expiry_flag") == "EXPIRED")
    warning_count = sum(1 for c in pki_certs + cm_certs if c.get("expiry_flag") == "WARNING")

    return {
        "pki_certs": pki_certs,
        "pki_issuers": pki_issuers,
        "cm_certs": cm_certs,
        "cluster_issuers": cluster_issuers,
        "expired_count": expired_count,
        "warning_count": warning_count,
        "warnings": all_warnings,
        "warn_days": _WARN_DAYS,
        "has_pki_data": bool(pki_certs or pki_issuers),
        "has_cm_data": bool(cm_certs or cluster_issuers),
    }


def render_combined(certmanager: dict, vault_pki: dict) -> None:
    """
    Render L6 Presentation layer combining cert-manager and Vault PKI data.

    certmanager keys expected: source, certificates, cluster_issuers (may be empty)
    vault_pki keys expected:   source, issuers, certs              (may be empty)

    Writes INDEX.md and pki-trust-chain.d2 ONCE with all sections populated.
    This is the authoritative write — called by the l6_vault_pki orchestrator.
    """
    context = _build_pki_context(pki_data=vault_pki, certmanager_data=certmanager)

    md_content = _render_md(context)
    d2_content = _render_d2(context)

    if _dry_run():
        print("=== L6 INDEX.md ===")
        print(md_content)
        print("=== L6 pki-trust-chain.d2 ===")
        print(d2_content)
        return

    from overwatch_gen.lib.render import write_deterministic

    _L6_DIR.mkdir(parents=True, exist_ok=True)
    md_path = _L6_DIR / "INDEX.md"
    d2_path = _L6_DIR / "pki-trust-chain.d2"
    svg_path = _L6_DIR / "pki-trust-chain.svg"

    wrote_md = write_deterministic(md_path, md_content)
    wrote_d2 = write_deterministic(d2_path, d2_content)

    if wrote_md:
        logger.info("L6: wrote %s", md_path)
    if wrote_d2:
        logger.info("L6: wrote %s", d2_path)
        _run_d2(d2_path, svg_path)
    logger.info("L6 combined render complete: %s", md_path)


def render(pki_data: dict, certmanager_data: dict) -> None:
    """
    Render L6 Presentation layer page (backwards-compatible wrapper).

    Args:
        pki_data:         Output of l6_vault_pki.collect()
        certmanager_data: Output of l6_certmanager.collect()

    Delegates to render_combined.
    """
    render_combined(certmanager=certmanager_data, vault_pki=pki_data)
