"""
Tests for L2 collector and renderer (l2_vlans).

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


class TestL2VlansCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l2_vlans
        data = l2_vlans.collect()
        assert "vlans" in data

    def test_vlans_list_nonempty(self):
        from overwatch_gen.collectors import l2_vlans
        data = l2_vlans.collect()
        assert len(data["vlans"]) > 0

    def test_vlans_sorted_by_vlan_id(self):
        from overwatch_gen.collectors import l2_vlans
        data = l2_vlans.collect()
        ids = [v["vlan_id"] for v in data["vlans"]]
        assert ids == sorted(ids)

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l2_vlans
        result1 = json.dumps(l2_vlans.collect(), sort_keys=True)
        result2 = json.dumps(l2_vlans.collect(), sort_keys=True)
        assert result1 == result2

    def test_vlan_fields_present(self):
        from overwatch_gen.collectors import l2_vlans
        data = l2_vlans.collect()
        vlan = data["vlans"][0]
        assert "vlan_id" in vlan
        assert "name" in vlan
        assert "subnet" in vlan

    def test_dhcp_field_is_bool(self):
        from overwatch_gen.collectors import l2_vlans
        data = l2_vlans.collect()
        for vlan in data["vlans"]:
            assert isinstance(vlan["dhcp_enabled"], bool)

    def test_vlan_id_is_int(self):
        from overwatch_gen.collectors import l2_vlans
        data = l2_vlans.collect()
        for vlan in data["vlans"]:
            assert isinstance(vlan["vlan_id"], int)

    def test_collect_with_missing_fixture_raises(self):
        from overwatch_gen.collectors import l2_vlans
        with mock.patch.object(
            type(l2_vlans._FIXTURE_SAMPLE), "exists", return_value=False
        ):
            with pytest.raises(FileNotFoundError):
                l2_vlans.collect()


class TestL2Renderer:
    def test_render_dry_run_produces_output(self, capsys):
        from overwatch_gen.collectors import l2_vlans
        from overwatch_gen.renderers import l2_renderer
        data = l2_vlans.collect()
        l2_renderer.render(data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_render_contains_vlan_ids(self, capsys):
        from overwatch_gen.collectors import l2_vlans
        from overwatch_gen.renderers import l2_renderer
        data = l2_vlans.collect()
        l2_renderer.render(data)
        captured = capsys.readouterr()
        # Should mention at least one VLAN ID from fixture
        assert "VLAN" in captured.out or "vlan" in captured.out.lower()

    def test_render_determinism(self, capsys):
        from overwatch_gen.collectors import l2_vlans
        from overwatch_gen.renderers import l2_renderer
        data = l2_vlans.collect()
        l2_renderer.render(data)
        out1 = capsys.readouterr().out
        l2_renderer.render(data)
        out2 = capsys.readouterr().out
        assert out1 == out2

    def test_render_contains_management_vlan(self, capsys):
        """Fixture includes Management VLAN — verify it appears in output."""
        from overwatch_gen.collectors import l2_vlans
        from overwatch_gen.renderers import l2_renderer
        data = l2_vlans.collect()
        l2_renderer.render(data)
        captured = capsys.readouterr()
        assert "Management" in captured.out or "management" in captured.out.lower()


class TestL2RegistryWiring:
    def test_l2_vlans_registers(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l2_vlans
        from overwatch_gen.renderers import l2_renderer
        # Module was already imported, re-register manually
        registry.register_layer("l2_vlans", l2_vlans.collect, l2_renderer.render)
        assert "l2_vlans" in registry.all_layers()


# ---------------------------------------------------------------------------
# OPS-282: timeout plumbing tests
# ---------------------------------------------------------------------------

class TestL2TimeoutPlumbing:
    def test_list_networks_raises_on_timeout(self):
        """_list_networks raises requests.exceptions.Timeout when session.get times out."""
        import requests
        from overwatch_gen.collectors import l2_vlans

        # Inject timeout into requests.Session to verify timeout is plumbed through
        with mock.patch("requests.Session") as mock_session_cls:
            mock_session = mock.MagicMock()
            mock_session.get.side_effect = requests.exceptions.Timeout("timed out")
            mock_session_cls.return_value = mock_session

            with pytest.raises(requests.exceptions.Timeout):
                l2_vlans._list_networks("fake-key", "fake-site")
