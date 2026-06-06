"""
l4_renderer.py — L4 Transport layer renderer.

Renders NetworkPolicy + Kyverno + UFW data into:
  architecture-vault/04-L4-transport/INDEX.md    (Markdown tables)
  architecture-vault/04-L4-transport/transport-topology.d2  (D2 diagram)

Three entry points (one per sub-layer):
  render_netpol(data)  — called by l4_netpol collector
  render_kyverno(data) — called by l4_kyverno collector
  render_ufw(data)     — called by l4_ufw collector

Template: overwatch-gen/templates/l4_transport.md.j2
          overwatch-gen/templates/l4.d2.j2
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_VAULT_DIR = Path(__file__).parent.parent.parent / "architecture-vault"
_L4_DIR = _VAULT_DIR / "04-L4-transport"

_MD_TEMPLATE = _TEMPLATE_DIR / "l4_transport.md.j2"
_D2_TEMPLATE = _TEMPLATE_DIR / "l4.d2.j2"


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
        print("=== L4 Transport INDEX.md ===")
        print(md_content)
        print("=== L4 transport-topology.d2 ===")
        print(d2_content)
        return

    _L4_DIR.mkdir(parents=True, exist_ok=True)
    write_deterministic(_L4_DIR / "INDEX.md", md_content)

    d2_path = _L4_DIR / "transport-topology.d2"
    write_deterministic(d2_path, d2_content)

    svg_dir = _VAULT_DIR / "99-diagrams-svg"
    svg_dir.mkdir(parents=True, exist_ok=True)
    _run_d2(d2_path, svg_dir / "l4-transport-topology.svg")
    logger.info("L4 render complete: %s", _L4_DIR / "INDEX.md")


def render_netpol(data: dict) -> None:
    """
    Render L4 NetworkPolicy data.

    data keys expected: policies (list from l4_netpol.collect())
    Writes INDEX.md with empty Kyverno and UFW sections.
    """
    context = {
        "cluster_policies": [],
        "hosts": [],
        "namespace_policies": [],
        "netpol_policies": data.get("policies", []),
    }
    _write_output(context)


def render_kyverno(data: dict) -> None:
    """
    Render L4 Kyverno policy data.

    data keys expected: cluster_policies, namespace_policies (from l4_kyverno.collect())
    Writes INDEX.md with empty NetworkPolicy and UFW sections.
    """
    context = {
        "cluster_policies": data.get("cluster_policies", []),
        "hosts": [],
        "namespace_policies": data.get("namespace_policies", []),
        "netpol_policies": [],
    }
    _write_output(context)


def render_ufw(data: dict) -> None:
    """
    Render L4 UFW host firewall data.

    data keys expected: hosts (list from l4_ufw.collect())
    Writes INDEX.md with empty NetworkPolicy and Kyverno sections.
    """
    context = {
        "cluster_policies": [],
        "hosts": data.get("hosts", []),
        "namespace_policies": [],
        "netpol_policies": [],
    }
    _write_output(context)
