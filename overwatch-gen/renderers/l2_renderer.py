"""
l2_renderer.py — L2 Data Link layer renderer.

Renders VLAN data into:
  architecture-vault/02-L2-datalink/INDEX.md    (Markdown table)
  architecture-vault/02-L2-datalink/vlan-topology.d2  (D2 diagram source)

Template: overwatch-gen/templates/l2_datalink.md.j2
          overwatch-gen/templates/l2.d2.j2
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_VAULT_DIR = Path(__file__).parent.parent.parent / "architecture-vault"
_L2_DIR = _VAULT_DIR / "02-L2-datalink"

_MD_TEMPLATE = _TEMPLATE_DIR / "l2_datalink.md.j2"
_D2_TEMPLATE = _TEMPLATE_DIR / "l2.d2.j2"


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


def render(data: dict) -> None:
    """
    Render L2 VLAN data.

    data keys expected: vlans
    Writes INDEX.md and vlan-topology.d2 to architecture-vault/02-L2-datalink/.
    """
    from overwatch_gen.lib.render import render_markdown

    vlans = data.get("vlans", [])
    context = {"vlans": vlans}

    md_content = render_markdown(_MD_TEMPLATE, context)
    d2_content = render_markdown(_D2_TEMPLATE, context)

    if _dry_run():
        print("=== L2 Data Link INDEX.md ===")
        print(md_content)
        print("=== L2 vlan-topology.d2 ===")
        print(d2_content)
        return

    from overwatch_gen.lib.render import write_deterministic
    _L2_DIR.mkdir(parents=True, exist_ok=True)
    write_deterministic(_L2_DIR / "INDEX.md", md_content)

    d2_path = _L2_DIR / "vlan-topology.d2"
    write_deterministic(d2_path, d2_content)

    svg_dir = _VAULT_DIR / "99-diagrams-svg"
    svg_dir.mkdir(parents=True, exist_ok=True)
    _run_d2(d2_path, svg_dir / "l2-vlan-topology.svg")
    logger.info("L2 render complete: %s", _L2_DIR / "INDEX.md")
