"""Track escalated signals across cycles to prevent duplicate issues.

Persists to /opt/sentinel-agent/state/escalation-tracker.json.
Maps signal source_ids to their Plane issue UUIDs so the agent
can comment on existing issues instead of creating new ones.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_STATE_PATH = "/opt/sentinel-agent/state/escalation-tracker.json"
MAX_AGE_DAYS = 30  # prune entries older than this


def load_tracker(config: dict) -> dict:
    """Load escalation tracker from state file.

    Returns dict: {source_id: {"issue_id": str, "last_seen": iso8601}}
    """
    path = _state_path(config)
    if not path.exists():
        return {}

    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_tracker(config: dict, tracker: dict,
                 log: Optional[logging.Logger] = None):
    """Save escalation tracker, pruning stale entries."""
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Prune entries not seen in MAX_AGE_DAYS
    now = datetime.now(timezone.utc)
    pruned = {}
    for source_id, entry in tracker.items():
        last_seen = entry.get("last_seen", "")
        try:
            dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            age_days = (now - dt).days
            if age_days <= MAX_AGE_DAYS:
                pruned[source_id] = entry
        except (ValueError, TypeError):
            pruned[source_id] = entry  # keep if unparseable

    try:
        with open(path, "w") as f:
            json.dump(pruned, f, indent=2)
    except OSError as e:
        if log:
            log.error(f"Failed to write escalation tracker: {e}")


def get_existing_issue(tracker: dict, source_id: str) -> str:
    """Return the Plane issue UUID for a previously escalated source_id.

    Uses exact match first, then falls back to normalized matching
    (strips FQDN suffixes and trailing numeric segments) so that
    slight variations in agent naming don't cause duplicate issues.

    Returns empty string if not tracked.
    """
    # Exact match — fast path
    entry = tracker.get(source_id, {})
    if entry:
        return entry.get("issue_id", "")

    # Normalized match — strip FQDN domain parts from agent names
    norm_key = _normalize_source_id(source_id)
    for tracked_id, tracked_entry in tracker.items():
        if _normalize_source_id(tracked_id) == norm_key:
            return tracked_entry.get("issue_id", "")

    return ""


def _normalize_source_id(source_id: str) -> str:
    """Normalize a source_id for fuzzy matching.

    Strips FQDN domain suffixes so 'wazuh-alert-5710-okd-worker-1.haist.farm'
    matches 'wazuh-alert-5710-okd-worker-1'.
    """
    import re
    # Strip domain suffixes (e.g. .haist.farm, .local, .example.com)
    return re.sub(r'\.[a-zA-Z][a-zA-Z0-9.-]+$', '', source_id)


def record_escalation(tracker: dict, source_id: str,
                      issue_id: str) -> dict:
    """Record that a source_id was escalated to a Plane issue."""
    tracker[source_id] = {
        "issue_id": issue_id,
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "escalation_count": tracker.get(source_id, {}).get(
            "escalation_count", 0) + 1,
    }
    return tracker


def _state_path(config: dict) -> Path:
    """Get state file path from config."""
    return Path(config.get("agent", {}).get(
        "state_dir", "/opt/sentinel-agent/state"
    )) / "escalation-tracker.json"
