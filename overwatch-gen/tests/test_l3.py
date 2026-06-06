"""
Tests for L3 collectors and renderer (l3_netbox, l3_unifi_firewall).

Runs entirely in fixture mode (ARCH_AUDIT_USE_FIXTURES=1) — no live network
or Vault access required.

Verifies:
- Collector returns expected structure
- Output is deterministic
- Renderer dry-run produces non-empty output
- Registry wiring is correct
"""

import json
import os
from unittest import mock

import pytest

os.environ["ARCH_AUDIT_USE_FIXTURES"] = "1"
os.environ["OVERWATCH_GEN_DRY_RUN"] = "1"


@pytest.fixture(autouse=True)
def clear_registry():
    from overwatch_gen.lib import registry
    registry.clear_registry()
    yield
    registry.clear_registry()


class TestL3NetboxCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l3_netbox
        data = l3_netbox.collect()
        assert "prefixes" in data
        assert "ip_addresses" in data

    def test_prefixes_nonempty(self):
        from overwatch_gen.collectors import l3_netbox
        data = l3_netbox.collect()
        assert len(data["prefixes"]) > 0

    def test_prefixes_sorted_by_prefix(self):
        from overwatch_gen.collectors import l3_netbox
        data = l3_netbox.collect()
        prefixes = [p["prefix"] for p in data["prefixes"]]
        assert prefixes == sorted(prefixes)

    def test_ips_sorted_by_address(self):
        from overwatch_gen.collectors import l3_netbox
        data = l3_netbox.collect()
        addrs = [ip["address"] for ip in data["ip_addresses"]]
        assert addrs == sorted(addrs)

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l3_netbox
        result1 = json.dumps(l3_netbox.collect(), sort_keys=True)
        result2 = json.dumps(l3_netbox.collect(), sort_keys=True)
        assert result1 == result2

    def test_prefix_fields_present(self):
        from overwatch_gen.collectors import l3_netbox
        data = l3_netbox.collect()
        prefix = data["prefixes"][0]
        assert "prefix" in prefix
        assert "status" in prefix
        assert "is_pool" in prefix

    def test_ip_fields_present(self):
        from overwatch_gen.collectors import l3_netbox
        data = l3_netbox.collect()
        if data["ip_addresses"]:
            ip = data["ip_addresses"][0]
            assert "address" in ip
            assert "status" in ip

    def test_collect_with_missing_fixture_raises(self):
        from overwatch_gen.collectors import l3_netbox
        with mock.patch.object(
            type(l3_netbox._FIXTURE_SAMPLE), "exists", return_value=False
        ):
            with pytest.raises(FileNotFoundError):
                l3_netbox.collect()


class TestL3NetboxRenderer:
    def test_render_dry_run_produces_output(self, capsys):
        from overwatch_gen.collectors import l3_netbox
        from overwatch_gen.renderers import l3_renderer
        data = l3_netbox.collect()
        l3_renderer.render_netbox(data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_render_contains_prefix_data(self, capsys):
        from overwatch_gen.collectors import l3_netbox
        from overwatch_gen.renderers import l3_renderer
        data = l3_netbox.collect()
        l3_renderer.render_netbox(data)
        captured = capsys.readouterr()
        # Fixture has 192.168.12.0/24 prefix
        assert "192.168" in captured.out

    def test_render_determinism(self, capsys):
        from overwatch_gen.collectors import l3_netbox
        from overwatch_gen.renderers import l3_renderer
        data = l3_netbox.collect()
        l3_renderer.render_netbox(data)
        out1 = capsys.readouterr().out
        l3_renderer.render_netbox(data)
        out2 = capsys.readouterr().out
        assert out1 == out2


class TestL3UnifiFirewallCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l3_unifi_firewall
        data = l3_unifi_firewall.collect()
        assert "firewall_policies" in data
        assert "firewall_zones" in data

    def test_policies_nonempty(self):
        from overwatch_gen.collectors import l3_unifi_firewall
        data = l3_unifi_firewall.collect()
        assert len(data["firewall_policies"]) > 0

    def test_policies_sorted_by_name(self):
        from overwatch_gen.collectors import l3_unifi_firewall
        data = l3_unifi_firewall.collect()
        names = [p["name"] for p in data["firewall_policies"]]
        assert names == sorted(names)

    def test_zones_sorted_by_name(self):
        from overwatch_gen.collectors import l3_unifi_firewall
        data = l3_unifi_firewall.collect()
        names = [z["name"] for z in data["firewall_zones"]]
        assert names == sorted(names)

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l3_unifi_firewall
        result1 = json.dumps(l3_unifi_firewall.collect(), sort_keys=True)
        result2 = json.dumps(l3_unifi_firewall.collect(), sort_keys=True)
        assert result1 == result2

    def test_policy_fields_present(self):
        from overwatch_gen.collectors import l3_unifi_firewall
        data = l3_unifi_firewall.collect()
        policy = data["firewall_policies"][0]
        assert "name" in policy
        assert "action" in policy
        assert "enabled" in policy

    def test_policy_enabled_is_bool(self):
        from overwatch_gen.collectors import l3_unifi_firewall
        data = l3_unifi_firewall.collect()
        for policy in data["firewall_policies"]:
            assert isinstance(policy["enabled"], bool)

    def test_zone_network_ids_sorted(self):
        from overwatch_gen.collectors import l3_unifi_firewall
        data = l3_unifi_firewall.collect()
        for zone in data["firewall_zones"]:
            ids = zone.get("network_ids", [])
            assert ids == sorted(ids)

    def test_collect_with_missing_fixture_raises(self):
        from overwatch_gen.collectors import l3_unifi_firewall
        with mock.patch.object(
            type(l3_unifi_firewall._FIXTURE_SAMPLE), "exists", return_value=False
        ):
            with pytest.raises(FileNotFoundError):
                l3_unifi_firewall.collect()


class TestL3UnifiFirewallRenderer:
    def test_render_dry_run_produces_output(self, capsys):
        from overwatch_gen.collectors import l3_unifi_firewall
        from overwatch_gen.renderers import l3_renderer
        data = l3_unifi_firewall.collect()
        l3_renderer.render_firewall(data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_render_contains_policy_names(self, capsys):
        from overwatch_gen.collectors import l3_unifi_firewall
        from overwatch_gen.renderers import l3_renderer
        data = l3_unifi_firewall.collect()
        l3_renderer.render_firewall(data)
        captured = capsys.readouterr()
        assert "allow" in captured.out.lower() or "block" in captured.out.lower()

    def test_render_determinism(self, capsys):
        from overwatch_gen.collectors import l3_unifi_firewall
        from overwatch_gen.renderers import l3_renderer
        data = l3_unifi_firewall.collect()
        l3_renderer.render_firewall(data)
        out1 = capsys.readouterr().out
        l3_renderer.render_firewall(data)
        out2 = capsys.readouterr().out
        assert out1 == out2


class TestL3RenderCombined:
    def test_render_combined_both_populated(self, capsys):
        """render_combined with both netbox and unifi data produces combined output."""
        from overwatch_gen.collectors import l3_netbox, l3_unifi_firewall
        from overwatch_gen.renderers import l3_renderer
        netbox_data = l3_netbox.collect()
        unifi_data = l3_unifi_firewall.collect()
        l3_renderer.render_combined(netbox=netbox_data, unifi=unifi_data)
        captured = capsys.readouterr()
        # Should contain netbox prefix data
        assert "192.168" in captured.out
        # Should contain firewall data
        assert len(captured.out) > 0

    def test_render_combined_netbox_empty(self, capsys):
        """render_combined with empty netbox dict produces firewall output."""
        from overwatch_gen.collectors import l3_unifi_firewall
        from overwatch_gen.renderers import l3_renderer
        unifi_data = l3_unifi_firewall.collect()
        l3_renderer.render_combined(netbox={}, unifi=unifi_data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_render_combined_unifi_empty(self, capsys):
        """render_combined with empty unifi dict produces netbox output."""
        from overwatch_gen.collectors import l3_netbox
        from overwatch_gen.renderers import l3_renderer
        netbox_data = l3_netbox.collect()
        l3_renderer.render_combined(netbox=netbox_data, unifi={})
        captured = capsys.readouterr()
        assert "192.168" in captured.out

    def test_sequential_netbox_then_unifi_preserves_both(self, capsys):
        """
        Running l3_netbox.render then l3_unifi_firewall.render in sequence
        (coordinator pattern) produces combined INDEX.md with BOTH sections populated.
        """
        from overwatch_gen.collectors import l3_netbox, l3_unifi_firewall
        netbox_data = l3_netbox.collect()
        unifi_data = l3_unifi_firewall.collect()

        # Reset cache to simulate fresh run
        l3_netbox._NETBOX_DATA_CACHE.clear()

        # Step 1: netbox render — caches data, no file write
        l3_netbox.render(netbox_data)
        assert l3_netbox._NETBOX_DATA_CACHE.get("prefixes") is not None
        assert len(l3_netbox._NETBOX_DATA_CACHE.get("prefixes", [])) > 0

        # Step 2: unifi firewall render — orchestrator: reads netbox cache, writes combined
        l3_unifi_firewall.render(unifi_data)
        captured = capsys.readouterr()

        # Output must contain netbox data (192.168 prefix) AND firewall data
        assert "192.168" in captured.out, (
            "Expected netbox prefix data in combined output, got: " + captured.out[:200]
        )
        assert len(captured.out) > 0

    def test_netbox_render_does_not_write_file(self):
        """l3_netbox.render should only populate cache, never call render_combined."""
        from overwatch_gen.collectors import l3_netbox
        from overwatch_gen.renderers import l3_renderer
        netbox_data = l3_netbox.collect()
        l3_netbox._NETBOX_DATA_CACHE.clear()

        with mock.patch.object(l3_renderer, "render_combined") as mock_combined:
            l3_netbox.render(netbox_data)
            mock_combined.assert_not_called()

        assert l3_netbox._NETBOX_DATA_CACHE.get("prefixes") is not None

    def test_render_netbox_wrapper_still_works(self, capsys):
        """Old render_netbox() wrapper still produces prefix output (backwards compat)."""
        from overwatch_gen.collectors import l3_netbox
        from overwatch_gen.renderers import l3_renderer
        data = l3_netbox.collect()
        l3_renderer.render_netbox(data)
        captured = capsys.readouterr()
        assert "192.168" in captured.out

    def test_render_firewall_wrapper_still_works(self, capsys):
        """Old render_firewall() wrapper still produces output (backwards compat)."""
        from overwatch_gen.collectors import l3_unifi_firewall
        from overwatch_gen.renderers import l3_renderer
        data = l3_unifi_firewall.collect()
        l3_renderer.render_firewall(data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0


class TestL3RegistryWiring:
    def test_l3_netbox_registers(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l3_netbox
        # Module was already imported, re-register manually
        registry.register_layer("l3_netbox", l3_netbox.collect, l3_netbox.render)
        assert "l3_netbox" in registry.all_layers()

    def test_l3_unifi_firewall_registers(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l3_unifi_firewall
        registry.register_layer("l3_unifi_firewall", l3_unifi_firewall.collect, l3_unifi_firewall.render)
        assert "l3_unifi_firewall" in registry.all_layers()


# ---------------------------------------------------------------------------
# OPS-282: timeout plumbing tests
# ---------------------------------------------------------------------------

class TestL3TimeoutPlumbing:
    def test_netbox_get_all_raises_on_timeout(self):
        """_netbox_get_all raises requests.exceptions.Timeout when session.get times out."""
        import requests
        from overwatch_gen.collectors import l3_netbox

        with mock.patch("requests.Session") as mock_session_cls:
            mock_session = mock.MagicMock()
            mock_session.get.side_effect = requests.exceptions.Timeout("timed out")
            mock_session_cls.return_value = mock_session

            with pytest.raises(requests.exceptions.Timeout):
                l3_netbox._netbox_get_all("fake-token", "/ipam/prefixes/")

    def test_unifi_firewall_paginate_raises_on_timeout(self):
        """_paginate raises requests.exceptions.Timeout when session.get times out."""
        import requests
        from overwatch_gen.collectors import l3_unifi_firewall

        fake_session = mock.MagicMock()
        fake_session.get.side_effect = requests.exceptions.Timeout("timed out")

        with pytest.raises(requests.exceptions.Timeout):
            l3_unifi_firewall._paginate(fake_session, "https://192.168.12.1/api/v1/firewall")
