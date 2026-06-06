"""
Tests for L4 collectors and renderer (l4_netpol, l4_kyverno, l4_ufw).

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
from unittest import mock

import pytest

os.environ["ARCH_AUDIT_USE_FIXTURES"] = "1"
os.environ["OVERWATCH_GEN_DRY_RUN"] = "1"


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear registry before each test to prevent duplicate registration errors."""
    from overwatch_gen.lib import registry
    registry.clear_registry()
    yield
    registry.clear_registry()


# ---------------------------------------------------------------------------
# L4 NetworkPolicy
# ---------------------------------------------------------------------------

class TestL4NetpolCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l4_netpol
        data = l4_netpol.collect()
        assert "policies" in data

    def test_policies_nonempty(self):
        from overwatch_gen.collectors import l4_netpol
        data = l4_netpol.collect()
        assert len(data["policies"]) > 0

    def test_policies_sorted_by_namespace_then_name(self):
        from overwatch_gen.collectors import l4_netpol
        data = l4_netpol.collect()
        keys = [(p["namespace"], p["name"]) for p in data["policies"]]
        assert keys == sorted(keys)

    def test_policy_fields_present(self):
        from overwatch_gen.collectors import l4_netpol
        data = l4_netpol.collect()
        policy = data["policies"][0]
        assert "name" in policy
        assert "namespace" in policy
        assert "ingress" in policy
        assert "egress" in policy
        assert "pod_selector" in policy
        assert "policy_types" in policy

    def test_policy_types_sorted(self):
        from overwatch_gen.collectors import l4_netpol
        data = l4_netpol.collect()
        for p in data["policies"]:
            assert p["policy_types"] == sorted(p["policy_types"])

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l4_netpol
        result1 = json.dumps(l4_netpol.collect(), sort_keys=True)
        result2 = json.dumps(l4_netpol.collect(), sort_keys=True)
        assert result1 == result2

    def test_missing_fixture_raises(self):
        from overwatch_gen.collectors import l4_netpol
        with mock.patch.object(
            type(l4_netpol._FIXTURE_SAMPLE), "exists", return_value=False
        ):
            with pytest.raises(FileNotFoundError):
                l4_netpol.collect()


class TestL4NetpolRenderer:
    def test_render_dry_run_produces_output(self, capsys):
        from overwatch_gen.collectors import l4_netpol
        from overwatch_gen.renderers import l4_renderer
        data = l4_netpol.collect()
        l4_renderer.render_netpol(data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_render_contains_namespace_data(self, capsys):
        from overwatch_gen.collectors import l4_netpol
        from overwatch_gen.renderers import l4_renderer
        data = l4_netpol.collect()
        l4_renderer.render_netpol(data)
        captured = capsys.readouterr()
        # Fixture has sentinel and monitoring namespaces
        assert "sentinel" in captured.out

    def test_render_determinism(self, capsys):
        from overwatch_gen.collectors import l4_netpol
        from overwatch_gen.renderers import l4_renderer
        data = l4_netpol.collect()
        l4_renderer.render_netpol(data)
        out1 = capsys.readouterr().out
        l4_renderer.render_netpol(data)
        out2 = capsys.readouterr().out
        assert out1 == out2

    def test_render_contains_l4_header(self, capsys):
        from overwatch_gen.collectors import l4_netpol
        from overwatch_gen.renderers import l4_renderer
        data = l4_netpol.collect()
        l4_renderer.render_netpol(data)
        captured = capsys.readouterr()
        assert "L4" in captured.out or "Transport" in captured.out


# ---------------------------------------------------------------------------
# L4 Kyverno
# ---------------------------------------------------------------------------

class TestL4KyvernoCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l4_kyverno
        data = l4_kyverno.collect()
        assert "cluster_policies" in data
        assert "namespace_policies" in data

    def test_cluster_policies_nonempty(self):
        from overwatch_gen.collectors import l4_kyverno
        data = l4_kyverno.collect()
        assert len(data["cluster_policies"]) > 0

    def test_cluster_policies_sorted_by_name(self):
        from overwatch_gen.collectors import l4_kyverno
        data = l4_kyverno.collect()
        names = [p["name"] for p in data["cluster_policies"]]
        assert names == sorted(names)

    def test_namespace_policies_sorted(self):
        from overwatch_gen.collectors import l4_kyverno
        data = l4_kyverno.collect()
        keys = [(p.get("namespace", ""), p["name"]) for p in data["namespace_policies"]]
        assert keys == sorted(keys)

    def test_policy_fields_present(self):
        from overwatch_gen.collectors import l4_kyverno
        data = l4_kyverno.collect()
        policy = data["cluster_policies"][0]
        assert "name" in policy
        assert "action" in policy
        assert "severity" in policy
        assert "rule_count" in policy
        assert "rule_names" in policy
        assert "description" in policy

    def test_rule_names_sorted(self):
        from overwatch_gen.collectors import l4_kyverno
        data = l4_kyverno.collect()
        for p in data["cluster_policies"]:
            assert p["rule_names"] == sorted(p["rule_names"])

    def test_action_is_enforce_or_audit(self):
        from overwatch_gen.collectors import l4_kyverno
        data = l4_kyverno.collect()
        valid_actions = {"Enforce", "Audit"}
        for p in data["cluster_policies"]:
            assert p["action"] in valid_actions, f"Unexpected action: {p['action']}"

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l4_kyverno
        result1 = json.dumps(l4_kyverno.collect(), sort_keys=True)
        result2 = json.dumps(l4_kyverno.collect(), sort_keys=True)
        assert result1 == result2

    def test_missing_fixture_raises(self):
        from overwatch_gen.collectors import l4_kyverno
        with mock.patch.object(
            type(l4_kyverno._FIXTURE_SAMPLE), "exists", return_value=False
        ):
            with pytest.raises(FileNotFoundError):
                l4_kyverno.collect()

    def test_live_kyverno_not_installed_returns_warning(self):
        """When Kyverno CRDs return 404, _collect_live returns empty result with _warning."""
        from overwatch_gen.collectors import l4_kyverno
        import kubernetes.client.exceptions

        api_exc_404 = kubernetes.client.exceptions.ApiException(status=404)

        fake_custom = mock.MagicMock()
        fake_custom.list_cluster_custom_object.side_effect = api_exc_404

        with mock.patch.dict(os.environ, {"ARCH_AUDIT_USE_FIXTURES": "0"}):
            with mock.patch.object(l4_kyverno, "_get_kubeconfig_content", return_value="fake-kubeconfig"):
                with mock.patch("kubernetes.config.load_kube_config"):
                    with mock.patch("kubernetes.client.CustomObjectsApi", return_value=fake_custom):
                        with mock.patch("os.unlink"):
                            data = l4_kyverno._collect_live()

        assert "cluster_policies" in data
        assert "namespace_policies" in data
        assert data["cluster_policies"] == []
        assert data["namespace_policies"] == []
        assert "_warning" in data
        assert "404" in data["_warning"] or "not installed" in data["_warning"].lower()


class TestL4KyvernoRenderer:
    def test_render_dry_run_produces_output(self, capsys):
        from overwatch_gen.collectors import l4_kyverno
        from overwatch_gen.renderers import l4_renderer
        data = l4_kyverno.collect()
        l4_renderer.render_kyverno(data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_render_contains_policy_name(self, capsys):
        from overwatch_gen.collectors import l4_kyverno
        from overwatch_gen.renderers import l4_renderer
        data = l4_kyverno.collect()
        l4_renderer.render_kyverno(data)
        captured = capsys.readouterr()
        # Fixture has disallow-latest-tag
        assert "disallow-latest-tag" in captured.out

    def test_render_determinism(self, capsys):
        from overwatch_gen.collectors import l4_kyverno
        from overwatch_gen.renderers import l4_renderer
        data = l4_kyverno.collect()
        l4_renderer.render_kyverno(data)
        out1 = capsys.readouterr().out
        l4_renderer.render_kyverno(data)
        out2 = capsys.readouterr().out
        assert out1 == out2


# ---------------------------------------------------------------------------
# L4 UFW
# ---------------------------------------------------------------------------

class TestL4UfwCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l4_ufw
        data = l4_ufw.collect()
        assert "hosts" in data

    def test_hosts_nonempty(self):
        from overwatch_gen.collectors import l4_ufw
        data = l4_ufw.collect()
        assert len(data["hosts"]) > 0

    def test_hosts_sorted_by_hostname(self):
        from overwatch_gen.collectors import l4_ufw
        data = l4_ufw.collect()
        hostnames = [h["hostname"] for h in data["hosts"]]
        assert hostnames == sorted(hostnames)

    def test_rules_sorted_by_rule_num(self):
        from overwatch_gen.collectors import l4_ufw
        data = l4_ufw.collect()
        for host in data["hosts"]:
            nums = [r["rule_num"] for r in host["rules"]]
            assert nums == sorted(nums)

    def test_host_fields_present(self):
        from overwatch_gen.collectors import l4_ufw
        data = l4_ufw.collect()
        host = data["hosts"][0]
        assert "hostname" in host
        assert "status" in host
        assert "rules" in host

    def test_rule_fields_present(self):
        from overwatch_gen.collectors import l4_ufw
        data = l4_ufw.collect()
        host = data["hosts"][0]
        rule = host["rules"][0]
        assert "rule_num" in rule
        assert "action" in rule
        assert "from" in rule
        assert "to" in rule
        assert "protocol" in rule
        assert "port" in rule
        assert "comment" in rule

    def test_rule_num_is_int(self):
        from overwatch_gen.collectors import l4_ufw
        data = l4_ufw.collect()
        for host in data["hosts"]:
            for rule in host["rules"]:
                assert isinstance(rule["rule_num"], int)

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l4_ufw
        result1 = json.dumps(l4_ufw.collect(), sort_keys=True)
        result2 = json.dumps(l4_ufw.collect(), sort_keys=True)
        assert result1 == result2

    def test_live_mode_raises_not_implemented(self):
        from overwatch_gen.collectors import l4_ufw
        with mock.patch.dict(os.environ, {"ARCH_AUDIT_USE_FIXTURES": "0"}):
            with pytest.raises(NotImplementedError):
                l4_ufw.collect()

    def test_missing_fixture_raises(self):
        from overwatch_gen.collectors import l4_ufw
        with mock.patch.object(
            type(l4_ufw._FIXTURE_SAMPLE), "exists", return_value=False
        ):
            with pytest.raises(FileNotFoundError):
                l4_ufw.collect()


class TestL4UfwRenderer:
    def test_render_dry_run_produces_output(self, capsys):
        from overwatch_gen.collectors import l4_ufw
        from overwatch_gen.renderers import l4_renderer
        data = l4_ufw.collect()
        l4_renderer.render_ufw(data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_render_contains_hostname(self, capsys):
        from overwatch_gen.collectors import l4_ufw
        from overwatch_gen.renderers import l4_renderer
        data = l4_ufw.collect()
        l4_renderer.render_ufw(data)
        captured = capsys.readouterr()
        assert "iac-control" in captured.out

    def test_render_determinism(self, capsys):
        from overwatch_gen.collectors import l4_ufw
        from overwatch_gen.renderers import l4_renderer
        data = l4_ufw.collect()
        l4_renderer.render_ufw(data)
        out1 = capsys.readouterr().out
        l4_renderer.render_ufw(data)
        out2 = capsys.readouterr().out
        assert out1 == out2


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# OPS-282: timeout plumbing tests
# ---------------------------------------------------------------------------

class TestL4TimeoutPlumbing:
    def test_netpol_collect_live_passes_request_timeout(self):
        """_collect_live passes _request_timeout=15 to list_network_policy_for_all_namespaces."""
        import kubernetes
        from overwatch_gen.collectors import l4_netpol

        fake_v1_net = mock.MagicMock()
        # Return a mock result with empty items
        fake_result = mock.MagicMock()
        fake_result.items = []
        fake_v1_net.list_network_policy_for_all_namespaces.return_value = fake_result

        with mock.patch.dict(os.environ, {"ARCH_AUDIT_USE_FIXTURES": "0"}):
            with mock.patch.object(l4_netpol, "_get_kubeconfig_content", return_value="fake"):
                with mock.patch("kubernetes.config.load_kube_config"):
                    with mock.patch("kubernetes.client.NetworkingV1Api", return_value=fake_v1_net):
                        with mock.patch("os.unlink"):
                            l4_netpol._collect_live()

        fake_v1_net.list_network_policy_for_all_namespaces.assert_called_once_with(
            _request_timeout=15
        )

    def test_kyverno_collect_live_passes_request_timeout(self):
        """_collect_live passes _request_timeout=15 to list_cluster_custom_object for Kyverno."""
        import kubernetes.client.exceptions
        from overwatch_gen.collectors import l4_kyverno

        fake_custom = mock.MagicMock()
        fake_custom.list_cluster_custom_object.return_value = {"items": []}

        with mock.patch.dict(os.environ, {"ARCH_AUDIT_USE_FIXTURES": "0"}):
            with mock.patch.object(l4_kyverno, "_get_kubeconfig_content", return_value="fake"):
                with mock.patch("kubernetes.config.load_kube_config"):
                    with mock.patch("kubernetes.client.CustomObjectsApi", return_value=fake_custom):
                        with mock.patch("os.unlink"):
                            l4_kyverno._collect_live()

        # Verify _request_timeout=15 was in every call
        for call in fake_custom.list_cluster_custom_object.call_args_list:
            assert call.kwargs.get("_request_timeout") == 15, (
                f"Expected _request_timeout=15 in call kwargs: {call.kwargs}"
            )


class TestL4Registry:
    def test_netpol_registers_correctly(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l4_netpol
        from overwatch_gen.renderers import l4_renderer
        # Registry is cleared by autouse fixture; re-register manually
        registry.register_layer("l4_netpol", l4_netpol.collect, l4_renderer.render_netpol)
        assert "l4_netpol" in registry.all_layers()

    def test_kyverno_registers_correctly(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l4_kyverno
        from overwatch_gen.renderers import l4_renderer
        registry.register_layer("l4_kyverno", l4_kyverno.collect, l4_renderer.render_kyverno)
        assert "l4_kyverno" in registry.all_layers()

    def test_ufw_registers_correctly(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l4_ufw
        from overwatch_gen.renderers import l4_renderer
        registry.register_layer("l4_ufw", l4_ufw.collect, l4_renderer.render_ufw)
        assert "l4_ufw" in registry.all_layers()
