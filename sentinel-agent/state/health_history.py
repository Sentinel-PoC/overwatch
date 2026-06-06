"""Track ArgoCD app health across cycles to detect stuck states.

Persists to /opt/sentinel-agent/state/app-health-history.json.
If an app has been Progressing or Degraded for 2+ consecutive cycles
(10+ minutes), it's stuck — not a normal rollout.

Each entry also tracks when the app *entered* its current health phase
so we can compute time-in-phase for severity grading:
  - Progressing < PROGRESSING_WARN_MINUTES  → suppress ntfy (normal rollout)
  - Progressing >= PROGRESSING_WARN_MINUTES  → warn (possibly stuck)
  - Progressing >= PROGRESSING_ALERT_MINUTES → alert (likely stuck)
  - Degraded / Missing                       → alert immediately (2+ cycles)
  - SyncFailed                               → alert immediately
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_STATE_PATH = "/opt/sentinel-agent/state/app-health-history.json"
MAX_ENTRIES_PER_APP = 5
STUCK_THRESHOLD = 2  # consecutive cycles before flagging

# Progressing thresholds (configurable via argocd config block)
PROGRESSING_WARN_MINUTES = 5    # >= this → warn-level
PROGRESSING_ALERT_MINUTES = 15  # >= this → alert-level


def load_health_history(config: dict) -> dict:
    """Load app health history from state file.

    Returns dict: {app_name: {entries: [{cycle, health, sync}...],
                               phase_started_at: ISO8601 or None,
                               last_health: str or None}}
    Handles missing/corrupt file gracefully.
    Backward-compatible: old format (list of entries) is upgraded transparently.
    """
    path = _state_path(config)
    if not path.exists():
        return {}

    try:
        with open(path) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    # Upgrade old format: {app: [list]} -> {app: {entries: [list], ...}}
    upgraded = {}
    for app_name, data in raw.items():
        if isinstance(data, list):
            upgraded[app_name] = {
                "entries": data,
                "phase_started_at": None,
                "last_health": data[-1]["health"] if data else None,
            }
        else:
            # Already new format
            upgraded[app_name] = data
    return upgraded


def save_health_history(config: dict, history: dict,
                        log: Optional[logging.Logger] = None):
    """Save app health history, pruning to MAX_ENTRIES_PER_APP."""
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Prune old entries (only the entries list, not phase metadata)
    pruned = {}
    for app, data in history.items():
        if isinstance(data, dict):
            pruned_data = dict(data)
            pruned_data["entries"] = data["entries"][-MAX_ENTRIES_PER_APP:]
            pruned[app] = pruned_data
        else:
            # Safety fallback for any unexpected format
            pruned[app] = data

    try:
        with open(path, "w") as f:
            json.dump(pruned, f, indent=2)
    except OSError as e:
        if log:
            log.error(f"Failed to write health history: {e}")


def record_app_health(history: dict, app_name: str,
                      health: str, sync: str) -> dict:
    """Record current cycle's health for an app.

    Tracks when the app entered its current health phase. If the health
    status changes from the previous cycle, phase_started_at is reset
    to now.
    """
    now = datetime.now(timezone.utc).isoformat()

    if app_name not in history:
        history[app_name] = {
            "entries": [],
            "phase_started_at": now,
            "last_health": health,
        }
    else:
        data = history[app_name]
        if not isinstance(data, dict):
            # Migrate legacy list format
            history[app_name] = {
                "entries": list(data) if data else [],
                "phase_started_at": now,
                "last_health": health,
            }
            data = history[app_name]

        # Reset phase timer if health status changed
        last_health = data.get("last_health", "")
        if last_health.lower() != health.lower():
            data["phase_started_at"] = now
            data["last_health"] = health
        elif data.get("phase_started_at") is None:
            # Initialize if missing (backward compat)
            data["phase_started_at"] = now
            data["last_health"] = health

    history[app_name]["entries"].append({
        "cycle": now,
        "health": health,
        "sync": sync,
    })

    return history


def time_in_phase_seconds(history: dict, app_name: str) -> Optional[float]:
    """Return seconds since app entered its current health phase.

    Returns None if phase_started_at is unavailable.
    """
    data = history.get(app_name)
    if not data or not isinstance(data, dict):
        return None

    phase_started_at = data.get("phase_started_at")
    if not phase_started_at:
        return None

    try:
        started = datetime.fromisoformat(phase_started_at)
        now = datetime.now(timezone.utc)
        return (now - started).total_seconds()
    except (ValueError, TypeError):
        return None


def get_progressing_severity(history: dict, app_name: str,
                              warn_minutes: int = PROGRESSING_WARN_MINUTES,
                              alert_minutes: int = PROGRESSING_ALERT_MINUTES) -> str:
    """Classify Progressing severity by time-in-phase.

    Returns:
        "suppress" -- < warn_minutes (normal rollout, no notification)
        "warn"     -- >= warn_minutes (possibly stuck)
        "alert"    -- >= alert_minutes (likely stuck)
    """
    secs = time_in_phase_seconds(history, app_name)
    if secs is None:
        # Unknown duration -- treat as suppress to avoid noise
        return "suppress"

    warn_secs = warn_minutes * 60
    alert_secs = alert_minutes * 60

    if secs >= alert_secs:
        return "alert"
    if secs >= warn_secs:
        return "warn"
    return "suppress"


def is_stuck(history: dict, app_name: str,
             statuses: set = None) -> bool:
    """Check if app has been in a problem state for 2+ consecutive cycles.

    Args:
        history: full health history dict
        app_name: app to check
        statuses: set of health statuses considered "stuck" (default: Progressing, Degraded)
    """
    if statuses is None:
        statuses = {"progressing", "degraded", "missing"}

    data = history.get(app_name)
    if not data:
        return False

    # Support both old list format and new dict format
    if isinstance(data, dict):
        entries = data.get("entries", [])
    else:
        entries = data

    if len(entries) < STUCK_THRESHOLD:
        return False

    # Check last N entries
    recent = entries[-STUCK_THRESHOLD:]
    return all(e["health"].lower() in statuses for e in recent)


def get_phase_started_at(history: dict, app_name: str) -> Optional[str]:
    """Return ISO8601 timestamp when app entered its current phase, or None."""
    data = history.get(app_name)
    if not data or not isinstance(data, dict):
        return None
    return data.get("phase_started_at")


def clear_app(history: dict, app_name: str) -> dict:
    """Clear history for an app that's recovered."""
    history.pop(app_name, None)
    return history


def _state_path(config: dict) -> Path:
    """Get state file path from config."""
    return Path(config.get("agent", {}).get(
        "state_dir", "/opt/sentinel-agent/state"
    )) / "app-health-history.json"
