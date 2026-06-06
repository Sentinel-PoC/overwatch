"""Research event emitter for sentinel-agent.

Appends structured JSONL events to research-log.jsonl.
Used by the weekly research publisher to generate BSides data.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def emit_research_event(config: dict, event_type: str, data: dict,
                        plane_issue: Optional[str] = None,
                        narrative: str = "",
                        log: Optional[logging.Logger] = None):
    """Append a research event to research-log.jsonl.

    Each event is a single JSON line (atomic write under pipe buffer).
    """
    log_dir = Path(config["agent"].get("log_dir", "/var/log/sentinel-agent"))
    log_path = log_dir / "research-log.jsonl"

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "source": "sentinel-agent",
        "data": data,
        "plane_issue": plane_issue,
        "narrative": narrative,
    }

    line = json.dumps(event, separators=(",", ":")) + "\n"

    try:
        with open(log_path, "a") as f:
            f.write(line)
    except Exception as e:
        if log:
            log.error(f"Failed to write research event: {e}")
