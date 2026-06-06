"""
Tests for lib/registry.py

Covers:
- register_layer: happy path, duplicate rejection, type validation
- run_layer: collector-to-renderer data flow, unknown layer error
- all_layers: sorted order, empty case
- clear_registry: test isolation utility
"""

import pytest

from overwatch_gen.lib.registry import (
    RegistryError,
    all_layers,
    clear_registry,
    register_layer,
    run_layer,
)


@pytest.fixture(autouse=True)
def isolated_registry():
    """
    Clear the registry before and after each test.
    autouse=True means every test in this file gets a clean registry.
    """
    clear_registry()
    yield
    clear_registry()


class TestRegisterLayer:
    def test_registers_successfully(self):
        register_layer("l1", lambda: {}, lambda d: None)
        assert "l1" in all_layers()

    def test_duplicate_name_raises(self):
        register_layer("l1", lambda: {}, lambda d: None)
        with pytest.raises(RegistryError, match="already registered"):
            register_layer("l1", lambda: {}, lambda d: None)

    def test_non_callable_collector_raises(self):
        with pytest.raises(TypeError, match="not callable"):
            register_layer("l2", "not_a_function", lambda d: None)

    def test_non_callable_renderer_raises(self):
        with pytest.raises(TypeError, match="not callable"):
            register_layer("l2", lambda: {}, 42)

    def test_multiple_layers_allowed(self):
        register_layer("l1", lambda: {}, lambda d: None)
        register_layer("l2", lambda: {}, lambda d: None)
        register_layer("l7", lambda: {}, lambda d: None)
        assert all_layers() == ["l1", "l2", "l7"]


class TestRunLayer:
    def test_collector_output_passed_to_renderer(self):
        received = {}

        def collect():
            return {"hosts": ["a", "b"], "count": 2}

        def render(data):
            received.update(data)

        register_layer("l3", collect, render)
        run_layer("l3")
        assert received == {"hosts": ["a", "b"], "count": 2}

    def test_unknown_layer_raises(self):
        with pytest.raises(RegistryError, match="not registered"):
            run_layer("l99")

    def test_collector_exception_propagates(self):
        def bad_collect():
            raise RuntimeError("network failure")

        register_layer("l4", bad_collect, lambda d: None)
        with pytest.raises(RuntimeError, match="network failure"):
            run_layer("l4")

    def test_renderer_exception_propagates(self):
        def bad_render(data):
            raise ValueError("bad data")

        register_layer("l5", lambda: {}, bad_render)
        with pytest.raises(ValueError, match="bad data"):
            run_layer("l5")

    def test_renderer_receives_none_from_collector(self):
        """Collector returning None should not cause TypeError in registry."""
        received = [None]

        def collect():
            return None

        def render(data):
            received[0] = data

        register_layer("l6", collect, render)
        run_layer("l6")
        assert received[0] is None


class TestAllLayers:
    def test_empty_when_no_layers(self):
        assert all_layers() == []

    def test_returns_sorted_list(self):
        register_layer("l7", lambda: {}, lambda d: None)
        register_layer("l1", lambda: {}, lambda d: None)
        register_layer("l3", lambda: {}, lambda d: None)
        assert all_layers() == ["l1", "l3", "l7"]

    def test_returns_list_type(self):
        assert isinstance(all_layers(), list)


class TestClearRegistry:
    def test_clear_removes_all_layers(self):
        register_layer("l1", lambda: {}, lambda d: None)
        register_layer("l2", lambda: {}, lambda d: None)
        clear_registry()
        assert all_layers() == []

    def test_can_re_register_after_clear(self):
        register_layer("l1", lambda: {}, lambda d: None)
        clear_registry()
        # Should not raise RegistryError
        register_layer("l1", lambda: {}, lambda d: None)
        assert "l1" in all_layers()
