"""
determinism.py — helpers for stable, reproducible output.

- sorted_json(obj) -> str       : JSON with sorted keys, 2-space indent
- stable_hash(s) -> str         : sha256 first 16 hex chars of a string
- freeze_time(dt)               : context manager for deterministic timestamps

freeze_time patches datetime.datetime.now() and datetime.datetime.utcnow()
within the `with` block. Import datetime from this module to get the patchable
version, or patch the target module's datetime reference in tests.
"""

import contextlib
import datetime
import hashlib
import json
import unittest.mock as mock


def sorted_json(obj) -> str:
    """
    Serialize obj to JSON with sorted keys and 2-space indentation.

    This guarantees byte-identical output across runs for the same logical
    object regardless of dict insertion order (Python 3.7+ guarantees order
    preservation, but source data may not be stable).
    """
    return json.dumps(obj, sort_keys=True, indent=2)


def stable_hash(s: str) -> str:
    """
    Return the first 16 hex characters of the SHA-256 digest of s.

    Useful for generating stable, short identifiers from strings (e.g.,
    Obsidian canvas node IDs derived from hostnames or service names).

    Example:
        stable_hash("iac-control.208.haist.farm") -> "3a9f1c8e7b204d56"
    """
    digest = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return digest[:16]


@contextlib.contextmanager
def freeze_time(dt: datetime.datetime):
    """
    Context manager that freezes datetime.datetime.now() and .utcnow()
    to the given dt for the duration of the block.

    Usage in tests:
        from overwatch_gen.lib.determinism import freeze_time
        import datetime

        fixed = datetime.datetime(2026, 1, 1, 0, 0, 0)
        with freeze_time(fixed):
            result = my_function_that_calls_datetime_now()
            assert "2026-01-01" in result

    Note: this patches `datetime.datetime` in the `datetime` module.
    If the target code does `from datetime import datetime` you must also
    patch the target module's `datetime` reference separately.
    """
    # Capture the real datetime class BEFORE patching to avoid recursion
    _real_datetime = datetime.datetime

    frozen = mock.MagicMock(wraps=_real_datetime)
    frozen.now = mock.MagicMock(return_value=dt)
    frozen.utcnow = mock.MagicMock(return_value=dt)
    # Allow datetime() constructor calls to still work by delegating to real class
    frozen.side_effect = lambda *args, **kwargs: _real_datetime(*args, **kwargs)

    with mock.patch("datetime.datetime", frozen):
        yield frozen
