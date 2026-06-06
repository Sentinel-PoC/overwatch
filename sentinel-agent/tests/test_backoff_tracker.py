"""Unit tests for state/backoff_tracker.py.

Covers:
- Backoff schedule formula (skip cycles computation)
- First attempt fires immediately (no prior history)
- Backoff gates correctly at each failure tier
- Success resets counters
- Escalation flag set after ESCALATE_AFTER_FAILURES consecutive failures
- Cycle counter load/save roundtrip
- Tracker load/save roundtrip via temp directory
"""

import json
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state.backoff_tracker import (
    BACKOFF_TABLE,
    ESCALATE_AFTER_FAILURES,
    _skip_cycles_for_failure_count,
    load_backoff_tracker,
    save_backoff_tracker,
    load_cycle_count,
    save_cycle_count,
    should_skip_for_backoff,
    record_tier2_success,
    record_tier2_failure,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_config(tmp_path: Path) -> dict:
    """Return a minimal config dict pointing state_dir at tmp_path."""
    return {"agent": {"state_dir": str(tmp_path)}}


# ---------------------------------------------------------------------------
# Backoff formula tests
# ---------------------------------------------------------------------------

class TestSkipCyclesFormula:
    """_skip_cycles_for_failure_count returns correct wait per BACKOFF_TABLE."""

    def test_no_failures_fires_immediately(self):
        # failure_count=0 → no prior failures → next attempt on same cycle
        # (skip_cycles=1 means: fire on current+1 but we still fire this cycle
        #  because next_eligible = current + skip — tested via integration below)
        # For formula alone: threshold 0 → skip 1 cycle
        assert _skip_cycles_for_failure_count(0) == 1

    def test_first_tier_failures(self):
        # failures 1-2 → threshold 2 in BACKOFF_TABLE → skip 2 cycles (10 min)
        # But failure_count=1 doesn't yet hit threshold 2 so it falls back
        # to the threshold=0 entry → skip 1 cycle
        # Let's verify against actual table
        assert _skip_cycles_for_failure_count(1) == 1

    def test_second_tier_entry(self):
        # failure_count=2 → matches threshold 2 → skip 2 cycles
        assert _skip_cycles_for_failure_count(2) == 2
        assert _skip_cycles_for_failure_count(3) == 2

    def test_third_tier_entry(self):
        # failure_count=4-5 → matches threshold 4 → skip 6 cycles
        # failure_count=6 hits the next threshold (6) → 24 cycles
        assert _skip_cycles_for_failure_count(4) == 6
        assert _skip_cycles_for_failure_count(5) == 6

    def test_final_tier_entry(self):
        # failure_count=7+ → matches threshold 6 → skip 24 cycles
        assert _skip_cycles_for_failure_count(7) == 24
        assert _skip_cycles_for_failure_count(100) == 24


# ---------------------------------------------------------------------------
# should_skip_for_backoff tests
# ---------------------------------------------------------------------------

class TestShouldSkip:
    """should_skip_for_backoff gate logic."""

    def test_no_history_fires(self):
        """Source with no history: fire immediately, no escalation."""
        skip, escalate = should_skip_for_backoff({}, "srv-a", current_cycle=1)
        assert skip is False
        assert escalate is False

    def test_backoff_gate_skips_when_before_next_eligible(self):
        tracker = {
            "srv-a": {
                "failure_count": 2,
                "consecutive_failures": 2,
                "cycle_last_attempted": 5,
                "next_eligible_cycle": 10,
                "should_escalate": False,
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
        }
        skip, escalate = should_skip_for_backoff(tracker, "srv-a", current_cycle=7)
        assert skip is True
        assert escalate is False

    def test_backoff_gate_fires_when_at_next_eligible(self):
        tracker = {
            "srv-a": {
                "failure_count": 2,
                "consecutive_failures": 2,
                "cycle_last_attempted": 5,
                "next_eligible_cycle": 10,
                "should_escalate": False,
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
        }
        skip, escalate = should_skip_for_backoff(tracker, "srv-a", current_cycle=10)
        assert skip is False
        assert escalate is False

    def test_escalation_flag_returns_no_skip_but_escalate_true(self):
        """When should_escalate=True the caller escalates; we don't skip."""
        tracker = {
            "srv-a": {
                "failure_count": 10,
                "consecutive_failures": ESCALATE_AFTER_FAILURES,
                "cycle_last_attempted": 5,
                "next_eligible_cycle": 10,
                "should_escalate": True,
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
        }
        skip, escalate = should_skip_for_backoff(tracker, "srv-a", current_cycle=15)
        assert skip is False
        assert escalate is True


# ---------------------------------------------------------------------------
# record_tier2_failure tests
# ---------------------------------------------------------------------------

class TestRecordFailure:
    """record_tier2_failure increments counters and computes next_eligible."""

    def test_first_failure_recorded(self):
        tracker = {}
        tracker = record_tier2_failure(tracker, "srv-a", current_cycle=1)
        entry = tracker["srv-a"]
        assert entry["failure_count"] == 1
        assert entry["consecutive_failures"] == 1
        assert entry["cycle_last_attempted"] == 1
        # failure_count=1 → skip 1 cycle → next_eligible = 1 + 1 = 2
        assert entry["next_eligible_cycle"] == 2
        assert entry["should_escalate"] is False

    def test_second_tier_failure(self):
        """At failure_count=2 the skip jumps to 2 cycles."""
        tracker = {}
        # Simulate 2 failures
        tracker = record_tier2_failure(tracker, "srv-a", current_cycle=1)
        tracker = record_tier2_failure(tracker, "srv-a", current_cycle=2)
        entry = tracker["srv-a"]
        assert entry["failure_count"] == 2
        # failure_count=2 → skip 2 cycles → next_eligible = 2 + 2 = 4
        assert entry["next_eligible_cycle"] == 4

    def test_escalation_triggered_after_threshold(self):
        """After ESCALATE_AFTER_FAILURES consecutive failures, flag is set."""
        tracker = {}
        for i in range(ESCALATE_AFTER_FAILURES):
            tracker = record_tier2_failure(tracker, "srv-a", current_cycle=i)
        entry = tracker["srv-a"]
        assert entry["consecutive_failures"] == ESCALATE_AFTER_FAILURES
        assert entry["should_escalate"] is True


# ---------------------------------------------------------------------------
# record_tier2_success tests
# ---------------------------------------------------------------------------

class TestRecordSuccess:
    """record_tier2_success resets counters."""

    def test_success_resets_failure_count(self):
        tracker = {
            "srv-a": {
                "failure_count": 5,
                "consecutive_failures": 5,
                "cycle_last_attempted": 10,
                "next_eligible_cycle": 34,
                "should_escalate": True,
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
        }
        tracker = record_tier2_success(tracker, "srv-a")
        entry = tracker["srv-a"]
        assert entry["failure_count"] == 0
        assert entry["consecutive_failures"] == 0
        assert entry["should_escalate"] is False

    def test_success_on_unknown_source_is_noop(self):
        """Success for an unknown source_id should not crash."""
        tracker = {}
        tracker = record_tier2_success(tracker, "srv-unknown")
        assert "srv-unknown" not in tracker


# ---------------------------------------------------------------------------
# Persistence roundtrip tests
# ---------------------------------------------------------------------------

class TestPersistence:
    """Tracker and cycle counter load/save via temp files."""

    def test_backoff_tracker_roundtrip(self, tmp_path):
        config = _tmp_config(tmp_path)
        tracker = {
            "srv-a": {
                "failure_count": 3,
                "consecutive_failures": 3,
                "cycle_last_attempted": 7,
                "next_eligible_cycle": 13,
                "should_escalate": False,
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
        }
        save_backoff_tracker(config, tracker)
        loaded = load_backoff_tracker(config)
        assert loaded == tracker

    def test_backoff_tracker_missing_file_returns_empty(self, tmp_path):
        config = _tmp_config(tmp_path)
        loaded = load_backoff_tracker(config)
        assert loaded == {}

    def test_backoff_tracker_corrupt_file_returns_empty(self, tmp_path):
        config = _tmp_config(tmp_path)
        path = tmp_path / "backoff-tracker.json"
        path.write_text("not json {{{")
        loaded = load_backoff_tracker(config)
        assert loaded == {}

    def test_cycle_counter_roundtrip(self, tmp_path):
        config = _tmp_config(tmp_path)
        save_cycle_count(config, 42)
        count = load_cycle_count(config)
        assert count == 42

    def test_cycle_counter_missing_returns_zero(self, tmp_path):
        config = _tmp_config(tmp_path)
        count = load_cycle_count(config)
        assert count == 0

    def test_cycle_counter_corrupt_returns_zero(self, tmp_path):
        config = _tmp_config(tmp_path)
        path = tmp_path / "cycle-counter.json"
        path.write_text("bad data")
        count = load_cycle_count(config)
        assert count == 0


# ---------------------------------------------------------------------------
# Integration: full failure→skip→fire cycle
# ---------------------------------------------------------------------------

class TestIntegration:
    """End-to-end simulation of several consecutive failure cycles."""

    def test_backoff_progression(self, tmp_path):
        """Simulate failure sequence and verify skip/fire decisions match schedule."""
        config = _tmp_config(tmp_path)
        tracker = {}
        source = "argocd-defectdojo"

        # Cycle 1: first attempt, no history → fire
        skip, esc = should_skip_for_backoff(tracker, source, current_cycle=1)
        assert skip is False
        # Fire fails
        tracker = record_tier2_failure(tracker, source, current_cycle=1)
        save_backoff_tracker(config, tracker)

        # Cycle 2: failure_count=1, next_eligible=2 → fire
        tracker = load_backoff_tracker(config)
        skip, esc = should_skip_for_backoff(tracker, source, current_cycle=2)
        assert skip is False
        # Fire fails again
        tracker = record_tier2_failure(tracker, source, current_cycle=2)
        save_backoff_tracker(config, tracker)

        # Cycle 3: failure_count=2, next_eligible=4 → skip (current=3 < 4)
        tracker = load_backoff_tracker(config)
        skip, esc = should_skip_for_backoff(tracker, source, current_cycle=3)
        assert skip is True

        # Cycle 4: failure_count=2, next_eligible=4 → fire (current=4 >= 4)
        skip, esc = should_skip_for_backoff(tracker, source, current_cycle=4)
        assert skip is False
