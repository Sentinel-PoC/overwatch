"""maintenance-lock helpers for sentinel-agent.

The operator creates /var/run/sentinel-agent.halt (symlink → /run/sentinel-agent.halt)
before doing hands-on infrastructure work.  The agent checks this file at the
start of every cycle; if present it exits 0 without polling or remediating.

File contents are optional.  Recommended format:
    echo '{"by":"jim","reason":"iSCSI recovery","at":"2026-04-19T17:00Z"}' \\
         > /var/run/sentinel-agent.halt

To release the lock:
    rm /var/run/sentinel-agent.halt

/var/run (→ /run) is tmpfs on all systemd hosts, so the file disappears on
reboot — a natural safety net if the operator forgets to remove it.

OPS-233
"""

import json
import logging
from pathlib import Path
from typing import Optional

LOCK_FILE = Path("/var/run/sentinel-agent.halt")


def is_locked() -> bool:
    """Return True if the maintenance-lock file is present."""
    return LOCK_FILE.exists()


def read_lock_info(log: Optional[logging.Logger] = None) -> dict:
    """Return parsed lock metadata dict (may be empty).

    Tries to JSON-parse the file contents.  Logs whatever it finds.
    Returns {} if file is empty or parse fails; caller should treat any
    non-empty dict as informational only.
    """
    try:
        raw = LOCK_FILE.read_text().strip()
    except OSError as exc:
        if log:
            log.warning(f"maintenance-lock: could not read {LOCK_FILE}: {exc}")
        return {}

    if not raw:
        if log:
            log.info(f"maintenance-lock: {LOCK_FILE} present but empty (no metadata)")
        return {}

    try:
        info = json.loads(raw)
        if log:
            log.info(
                f"maintenance-lock: held by={info.get('by', 'unknown')} "
                f"reason={info.get('reason', 'not given')} "
                f"at={info.get('at', 'unknown')}"
            )
        return info
    except json.JSONDecodeError:
        if log:
            log.info(f"maintenance-lock: {LOCK_FILE} contains non-JSON content: {raw!r}")
        return {"raw": raw}
