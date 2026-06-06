"""
render.py — Jinja2 template rendering + deterministic file writes.

- render_markdown(template_path, context) -> str
    Renders a Jinja2 template file with strict undefined (missing vars = error).

- write_deterministic(path, content)
    Normalizes line endings, ensures trailing newline, skips write if unchanged.
"""

import logging
import os
from pathlib import Path

import jinja2
from jinja2 import select_autoescape

logger = logging.getLogger(__name__)


def _make_jinja_env(template_dir: str | Path) -> jinja2.Environment:
    """Create a Jinja2 Environment with strict undefined from a directory."""
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=select_autoescape(["html", "htm", "xml"]),
    )


def render_markdown(template_path: str | Path, context: dict) -> str:
    """
    Render a Jinja2 template file with the given context dict.

    Uses StrictUndefined: any variable referenced in the template that is
    not present in context raises jinja2.UndefinedError immediately.

    Args:
        template_path: Absolute or relative path to the .md.j2 (or .md) template.
        context:       Dict of variables available in the template.

    Returns:
        Rendered string with normalized line endings (\n) and trailing newline.

    Raises:
        jinja2.UndefinedError: If a template variable is missing from context.
        FileNotFoundError:     If template_path does not exist.
    """
    template_path = Path(template_path)
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    env = _make_jinja_env(template_path.parent)
    template = env.get_template(template_path.name)
    rendered = template.render(**context)

    # Normalize endings and ensure trailing newline
    rendered = rendered.replace("\r\n", "\n").replace("\r", "\n")
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def write_deterministic(path: str | Path, content: str) -> bool:
    """
    Write content to path only if it differs from the existing file.

    Normalizes line endings to \\n and ensures a trailing newline before
    comparing. This makes the function a no-op when the logical content
    has not changed, which keeps git diffs clean and avoids spurious
    timestamp updates.

    Args:
        path:    Destination file path. Parent directories must exist.
        content: String content to write.

    Returns:
        True  if the file was written (content changed or file was new).
        False if the file was skipped (content identical to existing).
    """
    path = Path(path)

    # Normalize the incoming content
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"

    # Check existing content
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == normalized:
            logger.debug("write_deterministic: no-op (unchanged) %s", path)
            return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8")
    logger.debug("write_deterministic: wrote %d bytes to %s", len(normalized), path)
    return True
