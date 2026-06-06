"""
conftest.py — pytest path setup for overwatch-gen.

The project directory is named `overwatch-gen` (with hyphen) but the Python
package is `overwatch_gen` (with underscore). Python cannot import a directory
with a hyphen in its name, so we insert the project directory itself into
sys.path and register it under the canonical module name.

This allows tests to do:
    from overwatch_gen.lib.registry import register_layer

without requiring a separate `overwatch_gen/` subdirectory or an editable install.
"""

import sys
from pathlib import Path

# This file lives in overwatch-gen/. Add it to sys.path so that
# `overwatch-gen/` is importable — but since the name has a hyphen,
# we also register it under the underscore name.
_project_root = Path(__file__).parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Register `overwatch_gen` as an alias for the project root package.
# This makes `import overwatch_gen` resolve to this directory.
import importlib
import importlib.util
import types

if "overwatch_gen" not in sys.modules:
    # Create a package entry pointing to this directory
    spec = importlib.util.spec_from_file_location(
        "overwatch_gen",
        _project_root / "__init__.py",
        submodule_search_locations=[str(_project_root)],
    )
    if spec is not None:
        mod = importlib.util.module_from_spec(spec)
        mod.__path__ = [str(_project_root)]
        mod.__package__ = "overwatch_gen"
        sys.modules["overwatch_gen"] = mod
        spec.loader.exec_module(mod)
