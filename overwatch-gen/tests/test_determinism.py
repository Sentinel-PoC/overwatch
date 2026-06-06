"""
Tests for lib/determinism.py

Covers:
- sorted_json: key ordering, indent, nested structures
- stable_hash: length, hex chars only, stability, known value
- freeze_time: patches datetime.now() and datetime.utcnow()
"""

import datetime
import json

import pytest

from overwatch_gen.lib.determinism import freeze_time, sorted_json, stable_hash


class TestSortedJson:
    def test_sorts_top_level_keys(self):
        obj = {"z": 1, "a": 2, "m": 3}
        result = sorted_json(obj)
        parsed = json.loads(result)
        assert list(parsed.keys()) == ["a", "m", "z"]

    def test_sorts_nested_keys(self):
        obj = {"outer": {"z": 1, "a": 2}}
        result = sorted_json(obj)
        parsed = json.loads(result)
        assert list(parsed["outer"].keys()) == ["a", "z"]

    def test_two_space_indent(self):
        obj = {"key": "value"}
        result = sorted_json(obj)
        # With 2-space indent, the key line starts with 2 spaces
        assert '  "key"' in result

    def test_identical_objects_produce_identical_output(self):
        obj1 = {"b": [3, 1, 2], "a": {"y": 99, "x": 0}}
        obj2 = {"a": {"x": 0, "y": 99}, "b": [3, 1, 2]}
        # Keys sorted, but list order is preserved (lists are not sorted)
        assert sorted_json(obj1) == sorted_json(obj2)

    def test_list_order_preserved(self):
        """Lists must not be reordered — only dict keys are sorted."""
        obj = {"items": [3, 1, 2]}
        result = sorted_json(obj)
        parsed = json.loads(result)
        assert parsed["items"] == [3, 1, 2]

    def test_empty_dict(self):
        assert sorted_json({}) == "{}"

    def test_returns_string(self):
        assert isinstance(sorted_json({"x": 1}), str)


class TestStableHash:
    def test_returns_16_hex_chars(self):
        h = stable_hash("hello")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        assert stable_hash("hello") == stable_hash("hello")

    def test_different_inputs_differ(self):
        assert stable_hash("hello") != stable_hash("world")

    def test_known_value(self):
        # SHA-256 of "overwatch" = first 16 hex of:
        # 5f4dcc3b5aa765d61d8327deb882cf99... no, let's compute actual
        import hashlib
        expected = hashlib.sha256(b"overwatch").hexdigest()[:16]
        assert stable_hash("overwatch") == expected

    def test_empty_string(self):
        h = stable_hash("")
        assert len(h) == 16

    def test_unicode(self):
        h = stable_hash("test-\u0041\u0042\u0043")
        assert len(h) == 16


class TestFreezeTime:
    def test_now_returns_frozen_value(self):
        fixed = datetime.datetime(2026, 4, 20, 0, 0, 0)
        with freeze_time(fixed) as frozen:
            assert frozen.now() == fixed

    def test_utcnow_returns_frozen_value(self):
        fixed = datetime.datetime(2026, 1, 1, 12, 0, 0)
        with freeze_time(fixed) as frozen:
            assert frozen.utcnow() == fixed

    def test_multiple_calls_return_same_value(self):
        fixed = datetime.datetime(2026, 6, 15, 8, 30, 0)
        with freeze_time(fixed) as frozen:
            t1 = frozen.now()
            t2 = frozen.now()
            assert t1 == t2 == fixed

    def test_context_manager_restores_after_exit(self):
        fixed = datetime.datetime(2026, 4, 20, 0, 0, 0)
        before = datetime.datetime.now()
        with freeze_time(fixed) as frozen:
            assert frozen.now() == fixed
        # After context, real datetime.now() should work
        after = datetime.datetime.now()
        # Real now should be close to before (within a few seconds)
        assert (after - before).total_seconds() < 10

    def test_frozen_datetime_accepts_constructor_args(self):
        """freeze_time should not break datetime() constructor calls."""
        import datetime as _dt_module
        _real_datetime = _dt_module.datetime
        fixed = _real_datetime(2026, 4, 20, 0, 0, 0)
        with freeze_time(fixed):
            # Should not raise — datetime constructor still works via side_effect
            dt = _dt_module.datetime(2025, 1, 1)
            # Check the returned object is a real datetime (not a Mock)
            # Use the captured real class for isinstance to avoid comparing to mock
            assert isinstance(dt, _real_datetime)
            assert dt.year == 2025
