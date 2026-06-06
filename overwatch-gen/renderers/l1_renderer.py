"""
l1_renderer.py — L1 Physical layer renderer.

Renders Proxmox + Unifi device data into:
  architecture-vault/01-L1-physical/INDEX.md    (Markdown table)
  architecture-vault/01-L1-physical/physical-topology.d2  (D2 diagram source)

Also invoked as render_unifi(data) for the l1_unifi layer standalone render.
Both render() and render_unifi() accept the collector's data dict and produce
the same output format — render_unifi just expects {"devices": [...]} while
render() expects full proxmox data. When run separately, they each write partial
content; the full INDEX.md is produced when both have run.

Template: overwatch-gen/templates/l1_physical.md.j2
          overwatch-gen/templates/l1.d2.j2
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_VAULT_DIR = Path(__file__).parent.parent.parent / "architecture-vault"
_L1_DIR = _VAULT_DIR / "01-L1-physical"

_MD_TEMPLATE = _TEMPLATE_DIR / "l1_physical.md.j2"
_D2_TEMPLATE = _TEMPLATE_DIR / "l1.d2.j2"


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
        logger.warning("d2 binary not found — install d2 for SVG output. Skipping %s", svg_path)
    except subprocess.TimeoutExpired:
        logger.warning("d2 timed out rendering %s", d2_path)


def render_combined(proxmox: dict, unifi: dict) -> None:
    """
    Render L1 physical layer combining Proxmox and Unifi data.

    proxmox keys expected: nodes, vms, storage (may be empty dict if layer not run)
    unifi keys expected:   devices           (may be empty dict if layer not run)

    Writes INDEX.md and physical-topology.d2 ONCE with all fields populated.
    This is the authoritative write — called by the l1_unifi orchestrator.
    """
    context = {
        "devices": unifi.get("devices", []),
        "nodes": proxmox.get("nodes", []),
        "storage": proxmox.get("storage", []),
        "vms": proxmox.get("vms", []),
    }

    md_content = _render_md(context)
    d2_content = _render_d2(context)

    if _dry_run():
        print("=== L1 Physical INDEX.md (combined) ===")
        print(md_content)
        print("=== L1 physical-topology.d2 (combined) ===")
        print(d2_content)
        return

    from overwatch_gen.lib.render import write_deterministic
    _L1_DIR.mkdir(parents=True, exist_ok=True)
    write_deterministic(_L1_DIR / "INDEX.md", md_content)

    d2_path = _L1_DIR / "physical-topology.d2"
    write_deterministic(d2_path, d2_content)

    svg_dir = _VAULT_DIR / "99-diagrams-svg"
    svg_dir.mkdir(parents=True, exist_ok=True)
    _run_d2(d2_path, svg_dir / "l1-physical-topology.svg")
    logger.info("L1 combined render complete: %s", _L1_DIR / "INDEX.md")


def render(data: dict) -> None:
    """
    Render L1 Proxmox data (standalone, backwards-compatible wrapper).

    data keys expected: nodes, vms, storage
    Calls render_combined with empty unifi side.
    """
    render_combined(proxmox=data, unifi={})


def render_unifi(data: dict) -> None:
    """
    Render L1 Unifi device data (standalone, backwards-compatible wrapper).

    data keys expected: devices
    Calls render_combined with empty proxmox side.
    """
    render_combined(proxmox={}, unifi=data)
