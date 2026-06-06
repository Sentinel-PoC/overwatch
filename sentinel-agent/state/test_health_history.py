"""Unit tests for health_history.py — OPS-236 severity classification.

Run with: python -m pytest sentinel-agent/state/test_health_history.py -v
"""

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from health_history import (
    load_health_history,
    save_health_history,
    record_app_health,
    is_stuck,
    time_in_phase_seconds,
    get_progressing_severity,
    get_phase_started_at,
    clear_app,
    PROGRESSING_WARN_MINUTES,
    PROGRESSING_ALERT_MINUTES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config(tmp_path):
    """Config dict pointing state_dir at a temp directory."""
    return {"agent": {"state_dir": str(tmp_path)}}


# ---------------------------------------------------------------------------
# load_health_history / save_health_history
# ---------------------------------------------------------------------------

class TestLoadSave:
    def test_missing_file_returns_empty(self, tmp_config):
        assert load_health_history(tmp_config) == {}

    def test_corrupt_file_returns_empty(self, tmp_config):
        path = Path(tmp_config["agent"]["state_dir"]) / "app-health-history.json"
        path.write_text("NOT JSON")
        assert load_health_history(tmp_config) == {}

    def test_roundtrip_new_format(self, tmp_config):
        history = {}
        record_app_health(history, "my-app", "Progressing", "Synced")
        save_health_history(tmp_config, history)
        loaded = load_health_history(tmp_config)
        assert "my-app" in loaded
        assert loaded["my-app"]["entries"][0]["health"] == "Progressing"

    def test_old_format_upgraded_on_load(self, tmp_config):
        """Old format: {app: [list]} should be auto-upgraded to new dict format."""
        path = Path(tmp_config["agent"]["state_dir"]) / "app-health-history.json"
        old_data = {
            "my-app": [
                {"cycle": "2026-01-01T00:00:00+00:00", "health": "Progressing", "sync": "Synced"},
                {"cycle": "2026-01-01T00:05:00+00:00", "health": "Progressing", "sync": "Synced"},
            ]
        }
        path.write_text(json.dumps(old_data))
        history = load_health_history(tmp_config)
        assert "my-app" in history
        assert isinstance(history["my-app"], dict)
        assert len(history["my-app"]["entries"]) == 2
        # last_health should be set from last entry
        assert history["my-app"]["last_health"] == "Progressing"

    def test_prunes_to_max_entries(self, tmp_config):
        history = {}
        for i in range(10):
            record_app_health(history, "my-app", "Progressing", "Synced")
        save_health_history(tmp_config, history)
        loaded = load_health_history(tmp_config)
        assert len(loaded["my-app"]["entries"]) == 5  # MAX_ENTRIES_PER_APP


# ---------------------------------------------------------------------------
# record_app_health
# ---------------------------------------------------------------------------

class TestRecordAppHealth:
    def test_new_app_sets_phase_started_at(self):
        history = {}
        record_app_health(history, "app1", "Progressing", "Synced")
        assert history["app1"]["phase_started_at"] is not None
        assert history["app1"]["last_health"] == "Progressing"

    def test_same_health_does_not_reset_phase_timer(self):
        history = {}
        record_app_health(history, "app1", "Progressing", "Synced")
        original_start = history["app1"]["phase_started_at"]

        record_app_health(history, "app1", "Progressing", "Synced")
        assert history["app1"]["phase_started_at"] == original_start

    def test_health_change_resets_phase_timer(self):
        history = {}
        record_app_health(history, "app1", "Progressing", "Synced")
        original_start = history["app1"]["phase_started_at"]

        # Simulate time passing, then health changes
        record_app_health(history, "app1", "Degraded", "Synced")
        assert history["app1"]["phase_started_at"] != original_start
        assert history["app1"]["last_health"] == "Degraded"

    def test_case_insensitive_comparison(self):
        history = {}
        record_app_health(history, "app1", "Progressing", "Synced")
        original_start = history["app1"]["phase_started_at"]
        # Same status, different case — should NOT reset timer
        record_app_health(history, "app1", "progressing", "Synced")
        assert history["app1"]["phase_started_at"] == original_start

    def test_legacy_list_format_migrated(self):
        history = {
            "app1": [
                {"cycle": "2026-01-01T00:00:00+00:00", "health": "Progressing", "sync": "Synced"}
            ]
        }
        record_app_health(history, "app1", "Progressing", "Synced")
        assert isinstance(history["app1"], dict)
        assert len(history["app1"]["entries"]) == 2


# ---------------------------------------------------------------------------
# time_in_phase_seconds
# ---------------------------------------------------------------------------

class TestTimeInPhase:
    def test_returns_none_for_missing_app(self):
        assert time_in_phase_seconds({}, "no-app") is None

    def test_returns_none_for_legacy_list_format(self):
        history = {"app1": [{"cycle": "2026-01-01T00:00:00+00:00", "health": "Progressing", "sync": "Synced"}]}
        assert time_in_phase_seconds(history, "app1") is None

    def test_returns_elapsed_seconds(self):
        five_minutes_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        history = {
            "app1": {
                "entries": [],
                "phase_started_at": five_minutes_ago,
                "last_health": "Progressing",
            }
        }
        secs = time_in_phase_seconds(history, "app1")
        assert secs is not None
        assert 290 < secs < 310  # approximately 5 minutes

    def test_returns_none_when_phase_started_at_none(self):
        history = {"app1": {"entries": [], "phase_started_at": None, "last_health": "Progressing"}}
        assert time_in_phase_seconds(history, "app1") is None


# ---------------------------------------------------------------------------
# get_progressing_severity
# ---------------------------------------------------------------------------

class TestGetProgressingSeverity:
    def _history_with_age(self, minutes: float) -> dict:
        """Create history where app entered phase N minutes ago."""
        started = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        return {
            "app1": {
                "entries": [],
                "phase_started_at": started,
                "last_health": "Progressing",
            }
        }

    def test_suppress_under_warn_threshold(self):
        history = self._history_with_age(2)
        assert get_progressing_severity(history, "app1") == "suppress"

    def test_suppress_at_exactly_zero(self):
        history = self._history_with_age(0)
        assert get_progressing_severity(history, "app1") == "suppress"

    def test_warn_at_threshold(self):
        history = self._history_with_age(PROGRESSING_WARN_MINUTES + 0.1)
        assert get_progressing_severity(history, "app1") == "warn"

    def test_warn_between_thresholds(self):
        history = self._history_with_age(10)
        assert get_progressing_severity(history, "app1") == "warn"

    def test_alert_at_alert_threshold(self):
        history = self._history_with_age(PROGRESSING_ALERT_MINUTES + 0.1)
        assert get_progressing_severity(history, "app1") == "alert"

    def test_suppress_when_phase_unknown(self):
        history = {"app1": {"entries": [], "phase_started_at": None, "last_health": "Progressing"}}
        assert get_progressing_severity(history, "app1") == "suppress"

    def test_suppress_for_missing_app(self):
        assert get_progressing_severity({}, "no-app") == "suppress"

    def test_custom_thresholds(self):
        history = self._history_with_age(3)
        # With warn_minutes=2, 3 min should be "warn"
        assert get_progressing_severity(history, "app1", warn_minutes=2, alert_minutes=10) == "warn"
        # With warn_minutes=5 (default), 3 min should be "suppress"
        assert get_progressing_severity(history, "app1", warn_minutes=5, alert_minutes=15) == "suppress"


# ---------------------------------------------------------------------------
# is_stuck
# ---------------------------------------------------------------------------

class TestIsStuck:
    def test_not_stuck_after_one_cycle(self):
        history = {}
        record_app_health(history, "app1", "Progressing", "Synced")
        assert not is_stuck(history, "app1")

    def test_stuck_after_two_cycles(self):
        history = {}
        record_app_health(history, "app1", "Progressing", "Synced")
        record_app_health(history, "app1", "Progressing", "Synced")
        assert is_stuck(history, "app1")

    def test_not_stuck_if_recovered(self):
        history = {}
        record_app_health(history, "app1", "Progressing", "Synced")
        record_app_health(history, "app1", "Healthy", "Synced")
        assert not is_stuck(history, "app1")

    def test_not_stuck_missing_app(self):
        assert not is_stuck({}, "no-app")

    def test_stuck_with_legacy_list_format(self):
        history = {
            "app1": {
                "entries": [
                    {"cycle": "2026-01-01T00:00:00+00:00", "health": "Degraded", "sync": "Synced"},
                    {"cycle": "2026-01-01T00:05:00+00:00", "health": "Degraded", "sync": "Synced"},
                ],
                "phase_started_at": "2026-01-01T00:00:00+00:00",
                "last_health": "Degraded",
            }
        }
        assert is_stuck(history, "app1")


# ---------------------------------------------------------------------------
# get_phase_started_at
# ---------------------------------------------------------------------------

class TestGetPhaseStartedAt:
    def test_returns_none_for_missing_app(self):
        assert get_phase_started_at({}, "no-app") is None

    def test_returns_none_for_legacy_list(self):
        history = {"app1": [{"cycle": "x", "health": "Progressing", "sync": "Synced"}]}
        assert get_phase_started_at(history, "app1") is None

    def test_returns_iso_string(self):
        ts = "2026-01-01T00:00:00+00:00"
        history = {"app1": {"entries": [], "phase_started_at": ts, "last_health": "Progressing"}}
        assert get_phase_started_at(history, "app1") == ts


# ---------------------------------------------------------------------------
# clear_app
# ---------------------------------------------------------------------------

class TestClearApp:
    def test_clears_existing_app(self):
        history = {}
        record_app_health(history, "app1", "Progressing", "Synced")
        clear_app(history, "app1")
        assert "app1" not in history

    def test_clear_nonexistent_app_is_noop(self):
        history = {}
        clear_app(history, "nonexistent")  # should not raise
        assert history == {}
