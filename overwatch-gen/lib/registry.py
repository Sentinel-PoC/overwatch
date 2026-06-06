"""
registry.py — extension-hook registry for L1-L7 layer plugins.

Workers 1/2/3 import this module and call register_layer() to plug in
their collector and renderer without editing main.py.

Usage (in a layer module, e.g. overwatch_gen/layers/l1_physical.py):

    from overwatch_gen.lib import registry

    def collect():
        # Return a dict of raw data for this layer
        return {"interfaces": [...], "hardware": [...]}

    def render(data: dict) -> None:
        # Write Markdown/canvas files to architecture-vault/01-L1-physical/
        pass

    registry.register_layer("l1", collect, render)

Then in main.py (or any entry point):

    import overwatch_gen.layers.l1_physical  # side-effect: registers l1
    from overwatch_gen.lib import registry
    registry.run_layer("l1")
"""

import logging
from typing import Callable

logger = logging.getLogger(__name__)

# Module-global registry: {layer_name: {"collector": fn, "renderer": fn}}
_REGISTRY: dict[str, dict[str, Callable]] = {}


class RegistryError(Exception):
    """Raised on invalid registry operations."""


def register_layer(
    name: str,
    collector_fn: Callable,
    renderer_fn: Callable,
) -> None:
    """
    Register a collector and renderer for a named layer.

    Args:
        name:         Layer identifier, e.g. "l1", "l2", "l7".
                      Must be unique; re-registering the same name raises
                      RegistryError unless overwrite=True (not exposed —
                      duplicate registration is always a bug).
        collector_fn: Callable() -> dict. Fetches raw data for the layer.
                      Should raise on unrecoverable errors.
        renderer_fn:  Callable(data: dict) -> None. Writes output files.
                      Receives the return value of collector_fn.

    Raises:
        RegistryError: If name is already registered.
        TypeError:     If collector_fn or renderer_fn are not callable.
    """
    if not callable(collector_fn):
        raise TypeError(f"collector_fn for layer {name!r} is not callable")
    if not callable(renderer_fn):
        raise TypeError(f"renderer_fn for layer {name!r} is not callable")
    if name in _REGISTRY:
        raise RegistryError(
            f"Layer {name!r} is already registered. "
            "Each layer may only be registered once. "
            "Check for duplicate imports or double registration."
        )
    _REGISTRY[name] = {"collector": collector_fn, "renderer": renderer_fn}
    logger.debug("Registry: registered layer %r", name)


def run_layer(name: str) -> None:
    """
    Execute the collector then the renderer for a named layer.

    Args:
        name: Layer name as passed to register_layer().

    Raises:
        RegistryError: If name is not registered.
        Any exception raised by the collector or renderer propagates.
    """
    if name not in _REGISTRY:
        raise RegistryError(
            f"Layer {name!r} is not registered. "
            f"Registered layers: {all_layers()}"
        )
    entry = _REGISTRY[name]
    logger.info("Registry: collecting layer %r", name)
    data = entry["collector"]()
    logger.info("Registry: rendering layer %r", name)
    entry["renderer"](data)


def all_layers() -> list[str]:
    """
    Return a sorted list of all registered layer names.

    Sorted for deterministic output in --help and --all iteration.
    """
    return sorted(_REGISTRY.keys())


def clear_registry() -> None:
    """
    Remove all registered layers.

    Intended for use in tests only. Not exposed via CLI.
    """
    _REGISTRY.clear()
    logger.debug("Registry: cleared all layers")
