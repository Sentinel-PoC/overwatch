"""Operator activity detection for sentinel-agent change-freeze.

Detects recent operator kubectl/oc activity by examining bash_history
file modification time on the ubuntu user's home directory.

If the history file was modified within the configured window, we treat
it as a signal that a human operator is actively working and Tier 2
autonomous remediation should pause to avoid collisions.

Design notes:
- Uses mtime of ~/.bash_history, NOT content parsing.  This is
  conservative (any shell command triggers it, not just kubectl/oc)
  but safe-fail and zero-overhead.
- File absence → is_active=False, never raises.
- ProtectHome=true in the service unit blocks /home/* by default.
  sentinel-agent.service adds ReadOnlyPaths=/home/ubuntu/.bash_history
  so this path is accessible despite the sandbox.

OPS-234
"""

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Default path — ubuntu user on iac-control
_DEFAULT_BASH_HISTORY = Path("/home/ubuntu/.bash_history")


def recent_operator_activity(
    window_seconds: int,
    history_path: Optional[Path] = None,
    log: Optional[logging.Logger] = None,
) -> tuple[bool, dict]:
    """Check whether an operator was recently active on this host.

    Returns (is_active, evidence_dict).

    is_active is True when the bash_history file mtime is within
    window_seconds of now.  Evidence dict always contains:
      - source: which signal source fired (or "none")
      - checked_at_utc: ISO 8601 timestamp of this check
      - window_seconds: the configured window
    On positive detection, additionally includes:
      - last_activity_at_utc: ISO 8601 of the history file mtime
      - seconds_ago: int seconds since last modification
    On absent file or error:
      - reason: "file_not_found" or "stat_error"
    """
    if history_path is None:
        history_path = _DEFAULT_BASH_HISTORY

    now = time.time()
    checked_at = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()

    evidence_base: dict = {
        "checked_at_utc": checked_at,
        "window_seconds": window_seconds,
        "history_path": str(history_path),
    }

    # --- bash_history mtime check ---
    try:
        stat = os.stat(history_path)
    except FileNotFoundError:
        if log:
            log.debug(
                f"operator_activity: {history_path} not found — no freeze signal"
            )
        return False, {**evidence_base, "source": "none", "reason": "file_not_found"}
    except OSError as exc:
        if log:
            log.warning(
                f"operator_activity: cannot stat {history_path}: {exc} — "
                "defaulting to no freeze"
            )
        return False, {
            **evidence_base,
            "source": "none",
            "reason": "stat_error",
            "error": str(exc),
        }

    mtime = stat.st_mtime
    seconds_ago = int(now - mtime)
    last_activity_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    if seconds_ago <= window_seconds:
        if log:
            log.info(
                f"operator_activity: bash_history modified {seconds_ago}s ago "
                f"(window={window_seconds}s) — change-freeze active"
            )
        return True, {
            **evidence_base,
            "source": "bash_history",
            "last_activity_at_utc": last_activity_at,
            "seconds_ago": seconds_ago,
        }

    if log:
        log.debug(
            f"operator_activity: bash_history modified {seconds_ago}s ago "
            f"(window={window_seconds}s) — no freeze"
        )
    return False, {
        **evidence_base,
        "source": "none",
        "last_activity_at_utc": last_activity_at,
        "seconds_ago": seconds_ago,
    }
