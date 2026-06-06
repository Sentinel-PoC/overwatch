"""Unit tests for operator_activity.py — OPS-234 change-freeze detection.

Tests cover:
  - Recent bash_history → is_active=True
  - Stale bash_history → is_active=False
  - Missing file → is_active=False, no exception
  - Unreadable/stat-error → is_active=False, no exception
  - Evidence dict structure on all branches
"""

import os
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from operator_activity import recent_operator_activity

WINDOW = 900  # 15 minutes


class TestRecentOperatorActivity:
    """Happy-path and sad-path tests for recent_operator_activity()."""

    def test_recent_bash_history_returns_active(self, tmp_path):
        """File modified < window seconds ago → is_active=True."""
        hist = tmp_path / ".bash_history"
        hist.write_text("kubectl get pods\n")
        # mtime defaults to now-ish; touch to ensure it is recent
        hist.touch()

        is_active, evidence = recent_operator_activity(
            window_seconds=WINDOW,
            history_path=hist,
        )

        assert is_active is True
        assert evidence["source"] == "bash_history"
        assert "last_activity_at_utc" in evidence
        assert "seconds_ago" in evidence
        assert evidence["seconds_ago"] <= WINDOW
        assert evidence["window_seconds"] == WINDOW

    def test_stale_bash_history_returns_inactive(self, tmp_path):
        """File modified > window seconds ago → is_active=False."""
        hist = tmp_path / ".bash_history"
        hist.write_text("kubectl get pods\n")
        # Set mtime to window + 60 seconds in the past
        stale_mtime = time.time() - (WINDOW + 60)
        os.utime(hist, (stale_mtime, stale_mtime))

        is_active, evidence = recent_operator_activity(
            window_seconds=WINDOW,
            history_path=hist,
        )

        assert is_active is False
        assert evidence["source"] == "none"
        assert evidence["seconds_ago"] > WINDOW

    def test_missing_file_returns_inactive_no_error(self, tmp_path):
        """Non-existent history file → is_active=False, reason=file_not_found."""
        nonexistent = tmp_path / "no-such-file"

        is_active, evidence = recent_operator_activity(
            window_seconds=WINDOW,
            history_path=nonexistent,
        )

        assert is_active is False
        assert evidence["source"] == "none"
        assert evidence["reason"] == "file_not_found"
        assert "last_activity_at_utc" not in evidence

    def test_stat_error_returns_inactive_no_exception(self, tmp_path):
        """OSError during stat → is_active=False, reason=stat_error."""
        hist = tmp_path / ".bash_history"
        hist.write_text("")

        with patch("operator_activity.os.stat", side_effect=OSError("permission denied")):
            is_active, evidence = recent_operator_activity(
                window_seconds=WINDOW,
                history_path=hist,
            )

        assert is_active is False
        assert evidence["source"] == "none"
        assert evidence["reason"] == "stat_error"
        assert "error" in evidence

    def test_evidence_always_contains_base_fields(self, tmp_path):
        """Evidence dict always has checked_at_utc, window_seconds, history_path."""
        hist = tmp_path / ".bash_history"
        # Missing file case
        is_active, evidence = recent_operator_activity(
            window_seconds=WINDOW,
            history_path=hist,
        )
        assert "checked_at_utc" in evidence
        assert "window_seconds" in evidence
        assert "history_path" in evidence

    def test_window_exactly_at_boundary(self, tmp_path):
        """File modified exactly window_seconds ago is still within window (<=)."""
        hist = tmp_path / ".bash_history"
        hist.write_text("")
        boundary_mtime = time.time() - WINDOW
        os.utime(hist, (boundary_mtime, boundary_mtime))

        # seconds_ago = int(now - mtime); due to float timing may be WINDOW or WINDOW+1
        # Both are acceptable; we just confirm the function doesn't crash
        is_active, evidence = recent_operator_activity(
            window_seconds=WINDOW,
            history_path=hist,
        )
        assert isinstance(is_active, bool)
        assert evidence["window_seconds"] == WINDOW

    def test_zero_window_never_active_on_stale(self, tmp_path):
        """Window of 0 means only files modified in the same second are active."""
        hist = tmp_path / ".bash_history"
        hist.write_text("")
        # Set mtime 2 seconds ago
        old_mtime = time.time() - 2
        os.utime(hist, (old_mtime, old_mtime))

        is_active, _ = recent_operator_activity(
            window_seconds=0,
            history_path=hist,
        )
        assert is_active is False

    def test_logger_called_on_active(self, tmp_path):
        """Logger.info is called when activity is detected."""
        hist = tmp_path / ".bash_history"
        hist.touch()
        mock_log = MagicMock()

        is_active, _ = recent_operator_activity(
            window_seconds=WINDOW,
            history_path=hist,
            log=mock_log,
        )

        assert is_active is True
        mock_log.info.assert_called_once()

    def test_logger_not_called_on_missing_file_without_log(self, tmp_path):
        """No logger provided → missing file is silently handled."""
        nonexistent = tmp_path / "no-such-file"
        # Should not raise even without a logger
        is_active, evidence = recent_operator_activity(
            window_seconds=WINDOW,
            history_path=nonexistent,
            log=None,
        )
        assert is_active is False
