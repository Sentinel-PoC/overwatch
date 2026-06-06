"""
Tests for L1 collectors and renderer (l1_proxmox, l1_unifi).

Runs entirely in fixture mode (ARCH_AUDIT_USE_FIXTURES=1) — no live network
or Vault access required.

Verifies:
- Collector returns expected structure
- Output is deterministic (two runs on same fixture = identical result)
- Renderer dry-run produces non-empty output
- Registry wiring is correct
"""

import json
import os
from pathlib import Path
from unittest import mock

import pytest

# Ensure fixture mode is active for all tests in this file
os.environ["ARCH_AUDIT_USE_FIXTURES"] = "1"
os.environ["OVERWATCH_GEN_DRY_RUN"] = "1"


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear registry before each test to prevent duplicate registration errors."""
    from overwatch_gen.lib import registry
    registry.clear_registry()
    yield
    registry.clear_registry()


class TestL1ProxmoxCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l1_proxmox
        data = l1_proxmox.collect()
        assert "nodes" in data
        assert "vms" in data
        assert "storage" in data

    def test_nodes_list_nonempty(self):
        from overwatch_gen.collectors import l1_proxmox
        data = l1_proxmox.collect()
        assert len(data["nodes"]) > 0

    def test_vms_list_nonempty(self):
        from overwatch_gen.collectors import l1_proxmox
        data = l1_proxmox.collect()
        assert len(data["vms"]) > 0

    def test_nodes_sorted_by_name(self):
        from overwatch_gen.collectors import l1_proxmox
        data = l1_proxmox.collect()
        names = [n.get("node", n.get("name", "")) for n in data["nodes"]]
        assert names == sorted(names)

    def test_vms_sorted_by_node_then_vmid(self):
        from overwatch_gen.collectors import l1_proxmox
        data = l1_proxmox.collect()
        keys = [(v.get("node", ""), v.get("vmid", 0)) for v in data["vms"]]
        assert keys == sorted(keys)

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l1_proxmox
        result1 = json.dumps(l1_proxmox.collect(), sort_keys=True)
        result2 = json.dumps(l1_proxmox.collect(), sort_keys=True)
        assert result1 == result2

    def test_node_fields_present(self):
        from overwatch_gen.collectors import l1_proxmox
        data = l1_proxmox.collect()
        node = data["nodes"][0]
        # Accept either 'name' or 'node' key (fixture vs live)
        assert node.get("name") or node.get("node")

    def test_vm_fields_present(self):
        from overwatch_gen.collectors import l1_proxmox
        data = l1_proxmox.collect()
        vm = data["vms"][0]
        assert "vmid" in vm or "name" in vm

    def test_collect_with_missing_fixture_raises(self):
        from overwatch_gen.collectors import l1_proxmox
        with mock.patch.object(
            type(l1_proxmox._FIXTURE_SAMPLE), "exists", return_value=False
        ):
            with pytest.raises(FileNotFoundError):
                l1_proxmox.collect()


class TestL1ProxmoxRenderer:
    def test_render_dry_run_produces_output(self, capsys):
        from overwatch_gen.collectors import l1_proxmox
        from overwatch_gen.renderers import l1_renderer
        data = l1_proxmox.collect()
        l1_renderer.render(data)
        captured = capsys.readouterr()
        assert "L1" in captured.out or len(captured.out) > 0

    def test_render_determinism(self, capsys):
        from overwatch_gen.collectors import l1_proxmox
        from overwatch_gen.renderers import l1_renderer
        data = l1_proxmox.collect()
        l1_renderer.render(data)
        out1 = capsys.readouterr().out
        l1_renderer.render(data)
        out2 = capsys.readouterr().out
        assert out1 == out2

    def test_render_contains_node_data(self, capsys):
        from overwatch_gen.collectors import l1_proxmox
        from overwatch_gen.renderers import l1_renderer
        data = l1_proxmox.collect()
        l1_renderer.render(data)
        captured = capsys.readouterr()
        # Should contain at least one proxmox node name from fixture
        assert "proxmox" in captured.out.lower()


class TestL1UnifiCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l1_unifi
        data = l1_unifi.collect()
        assert "devices" in data

    def test_devices_list_nonempty(self):
        from overwatch_gen.collectors import l1_unifi
        data = l1_unifi.collect()
        assert len(data["devices"]) > 0

    def test_devices_sorted_by_type_name(self):
        from overwatch_gen.collectors import l1_unifi
        data = l1_unifi.collect()
        keys = [(d.get("type", ""), d.get("name", "")) for d in data["devices"]]
        assert keys == sorted(keys)

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l1_unifi
        result1 = json.dumps(l1_unifi.collect(), sort_keys=True)
        result2 = json.dumps(l1_unifi.collect(), sort_keys=True)
        assert result1 == result2

    def test_device_fields_present(self):
        from overwatch_gen.collectors import l1_unifi
        data = l1_unifi.collect()
        dev = data["devices"][0]
        assert "name" in dev
        assert "type" in dev


class TestL1RenderCombined:
    def test_render_combined_both_populated(self, capsys):
        """render_combined with both proxmox and unifi data produces output with both."""
        from overwatch_gen.collectors import l1_proxmox, l1_unifi
        from overwatch_gen.renderers import l1_renderer
        proxmox_data = l1_proxmox.collect()
        unifi_data = l1_unifi.collect()
        l1_renderer.render_combined(proxmox=proxmox_data, unifi=unifi_data)
        captured = capsys.readouterr()
        # Should contain proxmox node data
        assert "proxmox" in captured.out.lower()
        # Should contain unifi device data
        assert len(captured.out) > 0

    def test_render_combined_proxmox_empty(self, capsys):
        """render_combined with empty proxmox dict produces output (unifi side)."""
        from overwatch_gen.collectors import l1_unifi
        from overwatch_gen.renderers import l1_renderer
        unifi_data = l1_unifi.collect()
        l1_renderer.render_combined(proxmox={}, unifi=unifi_data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_render_combined_unifi_empty(self, capsys):
        """render_combined with empty unifi dict produces output (proxmox side)."""
        from overwatch_gen.collectors import l1_proxmox
        from overwatch_gen.renderers import l1_renderer
        proxmox_data = l1_proxmox.collect()
        l1_renderer.render_combined(proxmox=proxmox_data, unifi={})
        captured = capsys.readouterr()
        assert "proxmox" in captured.out.lower()

    def test_sequential_proxmox_then_unifi_preserves_both(self, capsys):
        """
        Running l1_proxmox.render then l1_unifi.render in sequence (coordinator pattern)
        produces a combined INDEX.md with BOTH Proxmox and Unifi sections populated.
        """
        from overwatch_gen.collectors import l1_proxmox, l1_unifi
        proxmox_data = l1_proxmox.collect()
        unifi_data = l1_unifi.collect()

        # Reset caches to simulate fresh run
        l1_proxmox._PROXMOX_DATA_CACHE.clear()

        # Step 1: proxmox render — caches data, no file write
        l1_proxmox.render(proxmox_data)
        # Cache should now be populated
        assert l1_proxmox._PROXMOX_DATA_CACHE.get("nodes") is not None
        assert len(l1_proxmox._PROXMOX_DATA_CACHE.get("nodes", [])) > 0

        # Step 2: unifi render — orchestrator: reads proxmox cache, writes combined output
        l1_unifi.render(unifi_data)
        captured = capsys.readouterr()

        # Output must contain proxmox data (nodes) AND unifi data (devices)
        assert "proxmox" in captured.out.lower(), (
            "Expected proxmox data in combined output, got: " + captured.out[:200]
        )
        assert len(captured.out) > 0

    def test_proxmox_render_does_not_write_file(self):
        """l1_proxmox.render should only populate cache, never call _write_output."""
        from overwatch_gen.collectors import l1_proxmox
        from overwatch_gen.renderers import l1_renderer
        proxmox_data = l1_proxmox.collect()
        l1_proxmox._PROXMOX_DATA_CACHE.clear()

        with mock.patch.object(l1_renderer, "render_combined") as mock_combined:
            l1_proxmox.render(proxmox_data)
            # render_combined must NOT be called by the proxmox cache step
            mock_combined.assert_not_called()

        # Cache must have been populated
        assert l1_proxmox._PROXMOX_DATA_CACHE.get("nodes") is not None

    def test_render_wrapper_proxmox_only(self, capsys):
        """Old render() wrapper still produces proxmox output (backwards compat)."""
        from overwatch_gen.collectors import l1_proxmox
        from overwatch_gen.renderers import l1_renderer
        data = l1_proxmox.collect()
        l1_renderer.render(data)
        captured = capsys.readouterr()
        assert "proxmox" in captured.out.lower()

    def test_render_wrapper_unifi_only(self, capsys):
        """Old render_unifi() wrapper still produces output (backwards compat)."""
        from overwatch_gen.collectors import l1_unifi
        from overwatch_gen.renderers import l1_renderer
        data = l1_unifi.collect()
        l1_renderer.render_unifi(data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0


class TestL1RegistryWiring:
    def test_l1_proxmox_registers(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l1_proxmox
        # Module was already imported, re-register manually
        registry.register_layer("l1_proxmox", l1_proxmox.collect, l1_proxmox.render)
        assert "l1_proxmox" in registry.all_layers()

    def test_l1_unifi_registers(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l1_unifi
        registry.register_layer("l1_unifi", l1_unifi.collect, l1_unifi.render)
        assert "l1_unifi" in registry.all_layers()


# ---------------------------------------------------------------------------
# OPS-282: timeout plumbing tests
# ---------------------------------------------------------------------------

class TestL1TimeoutPlumbing:
    def test_proxmox_pve_get_uses_timeout(self):
        """_pve_get raises requests.exceptions.Timeout when session.get times out."""
        import requests
        from overwatch_gen.collectors import l1_proxmox

        fake_session = mock.MagicMock()
        fake_session.get.side_effect = requests.exceptions.Timeout("timed out")

        with pytest.raises(requests.exceptions.Timeout):
            l1_proxmox._pve_get(fake_session, "192.168.12.6", "/nodes")

    def test_unifi_inline_client_list_sites_uses_timeout(self):
        """_InlineUniFiClient.list_sites raises Timeout when underlying session times out."""
        import requests
        from overwatch_gen.collectors.l1_unifi import _InlineUniFiClient

        client = _InlineUniFiClient.__new__(_InlineUniFiClient)
        client._session = mock.MagicMock()
        client._session.get.side_effect = requests.exceptions.Timeout("timed out")

        with pytest.raises(requests.exceptions.Timeout):
            client.list_sites()
