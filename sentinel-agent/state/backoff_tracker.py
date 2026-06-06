"""Per-source exponential backoff tracker for Tier 2 failures.

Prevents the agent from hammering a persistently-broken service every cycle.
On Tier 2 failure, the source_id is backed off geometrically:
  - Attempts 1:  fire immediately (no skip)
  - Attempts 2-3: skip every 2nd cycle  (10-min gap at 5-min cadence)
  - Attempts 4-6: skip every 6th cycle  (30-min gap)
  - Attempts 7+:  skip every 24th cycle (2h gap)

After ESCALATE_AFTER_FAILURES consecutive Tier 2 failures the entry is
flagged for escalation on the next cycle regardless of the skip schedule.

State is persisted to /opt/sentinel-agent/state/backoff-tracker.json.
Cycle counters are persisted to /opt/sentinel-agent/state/cycle-counter.json.

Key invariant: a cycle_count that starts at 0 and monotonically increases
is the single shared clock.  agent.py loads it, increments it, and passes
it into run_cycle / into the backoff helpers.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ----- tunables ---------------------------------------------------------------

# Number of cycles to skip per failure tier
BACKOFF_TABLE = [
    (0, 1),   # failure 0 — fire immediately (first attempt)
    (2, 2),   # failures 1-2 → skip every 2nd cycle
    (4, 6),   # failures 3-5 → skip every 6th cycle
    (6, 24),  # failures 6+  → skip every 24th cycle
]
# After this many consecutive Tier 2 failures, force Tier.ESCALATE
ESCALATE_AFTER_FAILURES = 7

DEFAULT_BACKOFF_PATH = "/opt/sentinel-agent/state/backoff-tracker.json"
DEFAULT_COUNTER_PATH = "/opt/sentinel-agent/state/cycle-counter.json"

# ------------------------------------------------------------------------------


def _backoff_path(config: dict) -> Path:
    state_dir = Path(config.get("agent", {}).get(
        "state_dir", "/opt/sentinel-agent/state"
    ))
    return state_dir / "backoff-tracker.json"


def _counter_path(config: dict) -> Path:
    state_dir = Path(config.get("agent", {}).get(
        "state_dir", "/opt/sentinel-agent/state"
    ))
    return state_dir / "cycle-counter.json"


# ----- cycle counter ----------------------------------------------------------

def load_cycle_count(config: dict) -> int:
    """Load the persistent cycle counter.  Returns 0 if missing/corrupt."""
    path = _counter_path(config)
    if not path.exists():
        return 0
    try:
        with open(path) as f:
            data = json.load(f)
        return int(data.get("cycle_count", 0))
    except (json.JSONDecodeError, OSError, ValueError):
        return 0


def save_cycle_count(config: dict, cycle_count: int,
                     log: Optional[logging.Logger] = None) -> None:
    """Persist the cycle counter."""
    path = _counter_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w") as f:
            json.dump({"cycle_count": cycle_count,
                       "updated_at": datetime.now(timezone.utc).isoformat()},
                      f, indent=2)
    except OSError as e:
        if log:
            log.error(f"Failed to write cycle counter: {e}")


# ----- backoff tracker --------------------------------------------------------

def load_backoff_tracker(config: dict) -> dict:
    """Load backoff tracker from state file.

    Returns dict: {
        source_id: {
            "failure_count": int,
            "consecutive_failures": int,
            "cycle_last_attempted": int,
            "next_eligible_cycle": int,
            "should_escalate": bool,
            "last_updated": iso8601
        }
    }
    """
    path = _backoff_path(config)
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_backoff_tracker(config: dict, tracker: dict,
                         log: Optional[logging.Logger] = None) -> None:
    """Persist the backoff tracker to state file."""
    path = _backoff_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w") as f:
            json.dump(tracker, f, indent=2)
    except OSError as e:
        if log:
            log.error(f"Failed to write backoff tracker: {e}")


def _skip_cycles_for_failure_count(failure_count: int) -> int:
    """Return how many cycles to wait before retrying given failure_count.

    failure_count is the number of failures BEFORE the next attempt
    (so failure_count=0 means no prior failures → fire immediately).
    """
    skip = 1  # default: last table entry
    for threshold, cycles in reversed(BACKOFF_TABLE):
        if failure_count >= threshold:
            skip = cycles
            break
    return skip


def should_skip_for_backoff(
    tracker: dict,
    source_id: str,
    current_cycle: int,
    log: Optional[logging.Logger] = None,
) -> tuple[bool, bool]:
    """Check if this source_id should be skipped this cycle due to backoff.

    Returns (skip: bool, should_escalate: bool).
      skip=True  → do not execute Tier 2 this cycle
      should_escalate=True → caller must convert signal to Tier.ESCALATE
        (this is returned even when skip=False so caller can act immediately)
    """
    entry = tracker.get(source_id)
    if entry is None:
        # No history — fire normally
        return False, False

    if entry.get("should_escalate", False):
        return False, True  # don't skip, but do escalate — caller handles

    next_eligible = entry.get("next_eligible_cycle", 0)
    if current_cycle < next_eligible:
        if log:
            log.info(
                f"Backoff: skipping {source_id} this cycle "
                f"(failures={entry.get('failure_count', 0)}, "
                f"consecutive={entry.get('consecutive_failures', 0)}, "
                f"next_eligible={next_eligible}, current={current_cycle})"
            )
        return True, False

    return False, False


def record_tier2_success(tracker: dict, source_id: str) -> dict:
    """Reset backoff counters for a source_id after a successful Tier 2 action."""
    if source_id in tracker:
        tracker[source_id]["failure_count"] = 0
        tracker[source_id]["consecutive_failures"] = 0
        tracker[source_id]["should_escalate"] = False
        tracker[source_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
    # If source_id not present, nothing to reset
    return tracker


def record_tier2_failure(
    tracker: dict,
    source_id: str,
    current_cycle: int,
    log: Optional[logging.Logger] = None,
) -> dict:
    """Increment failure counter and compute next eligible cycle.

    Updates tracker in-place and returns it.
    """
    entry = tracker.get(source_id, {
        "failure_count": 0,
        "consecutive_failures": 0,
        "cycle_last_attempted": current_cycle,
        "next_eligible_cycle": current_cycle + 1,
        "should_escalate": False,
        "last_updated": "",
    })

    failure_count = entry.get("failure_count", 0) + 1
    consecutive = entry.get("consecutive_failures", 0) + 1

    skip_n = _skip_cycles_for_failure_count(failure_count)
    next_eligible = current_cycle + skip_n
    should_escalate = consecutive >= ESCALATE_AFTER_FAILURES

    entry.update({
        "failure_count": failure_count,
        "consecutive_failures": consecutive,
        "cycle_last_attempted": current_cycle,
        "next_eligible_cycle": next_eligible,
        "should_escalate": should_escalate,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    })
    tracker[source_id] = entry

    if log:
        log.warning(
            f"Backoff: recorded failure for {source_id} "
            f"(total={failure_count}, consecutive={consecutive}, "
            f"next_eligible_cycle={next_eligible}, "
            f"should_escalate={should_escalate})"
        )

    return tracker
