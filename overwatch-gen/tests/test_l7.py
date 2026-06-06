"""
Tests for L7 collectors and renderer (l7_traefik, l7_istio, l7_okd_routes).

Runs entirely in fixture mode (ARCH_AUDIT_USE_FIXTURES=1) — no live network
or Vault access required.

Verifies:
- Collectors return expected structure
- Output is deterministic
- App routing table builds correctly (one row per hostname)
- Renderer dry-run produces non-empty output including the routing table
- Registry wiring is correct
"""

import json
import os
from pathlib import Path
from unittest import mock

import pytest

# Ensure fixture mode and dry-run active for all tests
os.environ["ARCH_AUDIT_USE_FIXTURES"] = "1"
os.environ["OVERWATCH_GEN_DRY_RUN"] = "1"

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear registry before each test to prevent duplicate registration errors."""
    from overwatch_gen.lib import registry
    registry.clear_registry()
    yield
    registry.clear_registry()


# ---------------------------------------------------------------------------
# l7_traefik
# ---------------------------------------------------------------------------

class TestL7TraefikCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l7_traefik
        data = l7_traefik.collect()
        assert "source" in data
        assert "ingress_routes" in data
        assert "middlewares" in data

    def test_source_is_traefik(self):
        from overwatch_gen.collectors import l7_traefik
        data = l7_traefik.collect()
        assert data["source"] == "traefik"

    def test_ingress_routes_nonempty(self):
        from overwatch_gen.collectors import l7_traefik
        data = l7_traefik.collect()
        assert len(data["ingress_routes"]) > 0

    def test_middlewares_nonempty(self):
        from overwatch_gen.collectors import l7_traefik
        data = l7_traefik.collect()
        assert len(data["middlewares"]) > 0

    def test_route_fields_present(self):
        from overwatch_gen.collectors import l7_traefik
        data = l7_traefik.collect()
        route = data["ingress_routes"][0]
        for field in ["name", "namespace", "host", "tls_secret", "tls_issuer",
                      "middlewares", "service", "service_port"]:
            assert field in route, f"Missing field: {field}"

    def test_middleware_fields_present(self):
        from overwatch_gen.collectors import l7_traefik
        data = l7_traefik.collect()
        mw = data["middlewares"][0]
        for field in ["name", "namespace", "type"]:
            assert field in mw, f"Missing field: {field}"

    def test_routes_sorted_by_host(self):
        from overwatch_gen.collectors import l7_traefik
        data = l7_traefik.collect()
        hosts = [r.get("host", "") for r in data["ingress_routes"]]
        assert hosts == sorted(hosts)

    def test_middlewares_list_is_list(self):
        from overwatch_gen.collectors import l7_traefik
        data = l7_traefik.collect()
        for route in data["ingress_routes"]:
            assert isinstance(route["middlewares"], list)

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l7_traefik
        r1 = json.dumps(l7_traefik.collect(), sort_keys=True)
        r2 = json.dumps(l7_traefik.collect(), sort_keys=True)
        assert r1 == r2

    def test_forward_auth_middleware_present(self):
        """Fixture should include at least one forwardAuth middleware."""
        from overwatch_gen.collectors import l7_traefik
        data = l7_traefik.collect()
        fa = [mw for mw in data["middlewares"] if mw["type"] == "forwardAuth"]
        assert len(fa) >= 1

    def test_registry_registered(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l7_traefik
        registry.register_layer("l7_traefik", l7_traefik.collect, l7_traefik.render)
        assert "l7_traefik" in registry.all_layers()


# ---------------------------------------------------------------------------
# l7_istio
# ---------------------------------------------------------------------------

class TestL7IstioCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l7_istio
        data = l7_istio.collect()
        assert "source" in data
        assert "virtual_services" in data
        assert "destination_rules" in data
        assert "authorization_policies" in data

    def test_source_is_istio(self):
        from overwatch_gen.collectors import l7_istio
        data = l7_istio.collect()
        assert data["source"] == "istio"

    def test_virtual_services_nonempty(self):
        from overwatch_gen.collectors import l7_istio
        data = l7_istio.collect()
        assert len(data["virtual_services"]) > 0

    def test_vs_fields_present(self):
        from overwatch_gen.collectors import l7_istio
        data = l7_istio.collect()
        vs = data["virtual_services"][0]
        for field in ["name", "namespace", "hosts", "gateways", "http_routes"]:
            assert field in vs, f"Missing field: {field}"

    def test_hosts_is_list(self):
        from overwatch_gen.collectors import l7_istio
        data = l7_istio.collect()
        for vs in data["virtual_services"]:
            assert isinstance(vs["hosts"], list)

    def test_destination_rules_nonempty(self):
        from overwatch_gen.collectors import l7_istio
        data = l7_istio.collect()
        assert len(data["destination_rules"]) > 0

    def test_authz_policies_nonempty(self):
        from overwatch_gen.collectors import l7_istio
        data = l7_istio.collect()
        assert len(data["authorization_policies"]) > 0

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l7_istio
        r1 = json.dumps(l7_istio.collect(), sort_keys=True)
        r2 = json.dumps(l7_istio.collect(), sort_keys=True)
        assert r1 == r2

    def test_registry_registered(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l7_istio
        registry.register_layer("l7_istio", l7_istio.collect, l7_istio.render)
        assert "l7_istio" in registry.all_layers()


# ---------------------------------------------------------------------------
# l7_okd_routes
# ---------------------------------------------------------------------------

class TestL7OkdRoutesCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l7_okd_routes
        data = l7_okd_routes.collect()
        assert "source" in data
        assert "routes" in data

    def test_source_is_okd_routes(self):
        from overwatch_gen.collectors import l7_okd_routes
        data = l7_okd_routes.collect()
        assert data["source"] == "okd_routes"

    def test_routes_nonempty(self):
        from overwatch_gen.collectors import l7_okd_routes
        data = l7_okd_routes.collect()
        assert len(data["routes"]) > 0

    def test_route_fields_present(self):
        from overwatch_gen.collectors import l7_okd_routes
        data = l7_okd_routes.collect()
        route = data["routes"][0]
        for field in ["name", "namespace", "host", "path", "tls_termination",
                      "tls_insecure_edge_policy", "destination_service",
                      "destination_port", "wildcard_policy"]:
            assert field in route, f"Missing field: {field}"

    def test_tls_termination_valid_values(self):
        """TLS termination should be edge, reencrypt, passthrough, or none."""
        from overwatch_gen.collectors import l7_okd_routes
        data = l7_okd_routes.collect()
        valid = {"edge", "reencrypt", "passthrough", "none"}
        for route in data["routes"]:
            assert route["tls_termination"] in valid, \
                f"Unexpected tls_termination: {route['tls_termination']}"

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l7_okd_routes
        r1 = json.dumps(l7_okd_routes.collect(), sort_keys=True)
        r2 = json.dumps(l7_okd_routes.collect(), sort_keys=True)
        assert r1 == r2

    def test_registry_registered(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l7_okd_routes
        registry.register_layer("l7_okd_routes", l7_okd_routes.collect, l7_okd_routes.render)
        assert "l7_okd_routes" in registry.all_layers()


# ---------------------------------------------------------------------------
# l7_renderer (app routing table)
# ---------------------------------------------------------------------------

class TestL7Renderer:
    def test_render_produces_output(self, capsys):
        from overwatch_gen.collectors import l7_traefik, l7_istio, l7_okd_routes
        from overwatch_gen.renderers import l7_renderer

        t_data = l7_traefik.collect()
        i_data = l7_istio.collect()
        o_data = l7_okd_routes.collect()
        l7_renderer.render(traefik_data=t_data, istio_data=i_data, okd_data=o_data)

        captured = capsys.readouterr()
        assert "L7" in captured.out
        assert "INDEX.md" in captured.out

    def test_render_contains_routing_table(self, capsys):
        from overwatch_gen.collectors import l7_traefik, l7_istio, l7_okd_routes
        from overwatch_gen.renderers import l7_renderer

        t_data = l7_traefik.collect()
        i_data = l7_istio.collect()
        o_data = l7_okd_routes.collect()
        l7_renderer.render(traefik_data=t_data, istio_data=i_data, okd_data=o_data)

        captured = capsys.readouterr()
        assert "Hostname" in captured.out
        assert "Destination Service" in captured.out

    def test_routing_table_has_all_hostnames(self, capsys):
        """Every IngressRoute + OKD Route hostname should appear in output."""
        from overwatch_gen.collectors import l7_traefik, l7_istio, l7_okd_routes
        from overwatch_gen.renderers import l7_renderer

        t_data = l7_traefik.collect()
        i_data = l7_istio.collect()
        o_data = l7_okd_routes.collect()
        l7_renderer.render(traefik_data=t_data, istio_data=i_data, okd_data=o_data)

        captured = capsys.readouterr()
        for route in t_data["ingress_routes"]:
            host = route.get("host", "")
            if host:
                assert host in captured.out, f"Host {host} not found in L7 output"
        for route in o_data["routes"]:
            host = route.get("host", "")
            if host:
                assert host in captured.out, f"OKD host {host} not found in L7 output"

    def test_routing_table_sorted_by_hostname(self):
        """App routing table rows should be sorted by hostname."""
        from overwatch_gen.collectors import l7_traefik, l7_istio, l7_okd_routes
        from overwatch_gen.renderers.l7_renderer import _build_app_routing_table

        t_data = l7_traefik.collect()
        i_data = l7_istio.collect()
        o_data = l7_okd_routes.collect()

        rows = _build_app_routing_table(t_data, i_data, o_data)
        hostnames = [r["hostname"] for r in rows]
        assert hostnames == sorted(hostnames)

    def test_routing_table_row_has_all_columns(self):
        """Every row in the routing table must have all required column keys."""
        from overwatch_gen.collectors import l7_traefik, l7_istio, l7_okd_routes
        from overwatch_gen.renderers.l7_renderer import _build_app_routing_table

        t_data = l7_traefik.collect()
        i_data = l7_istio.collect()
        o_data = l7_okd_routes.collect()

        rows = _build_app_routing_table(t_data, i_data, o_data)
        required = {"hostname", "source", "source_name", "tls_issuer",
                    "middleware_chain", "istio_vs", "authz_policy", "destination_service"}
        for row in rows:
            missing = required - set(row.keys())
            assert not missing, f"Row missing columns: {missing}"

    def test_render_is_deterministic(self, capsys):
        from overwatch_gen.collectors import l7_traefik, l7_istio, l7_okd_routes
        from overwatch_gen.renderers import l7_renderer

        t_data = l7_traefik.collect()
        i_data = l7_istio.collect()
        o_data = l7_okd_routes.collect()

        l7_renderer.render(traefik_data=t_data, istio_data=i_data, okd_data=o_data)
        out1 = capsys.readouterr().out

        l7_renderer.render(traefik_data=t_data, istio_data=i_data, okd_data=o_data)
        out2 = capsys.readouterr().out

        assert out1 == out2

    def test_istio_vs_cross_reference(self):
        """Routes matching an Istio VS host should have the VS name in the row."""
        from overwatch_gen.collectors import l7_traefik, l7_istio, l7_okd_routes
        from overwatch_gen.renderers.l7_renderer import _build_app_routing_table

        t_data = l7_traefik.collect()
        i_data = l7_istio.collect()
        o_data = l7_okd_routes.collect()

        rows = _build_app_routing_table(t_data, i_data, o_data)

        # vault.208.haist.farm has both IngressRoute and VirtualService in fixtures
        vault_rows = [r for r in rows if "vault.208.haist.farm" in r.get("hostname", "")]
        assert vault_rows, "Expected vault route in routing table"
        # Should have Istio VS cross-reference
        assert any(r["istio_vs"] for r in vault_rows), \
            "Expected Istio VS reference for vault hostname"

    def test_authz_policy_cross_reference(self):
        """Routes in namespaces with AuthzPolicy should show the policy name."""
        from overwatch_gen.collectors import l7_traefik, l7_istio, l7_okd_routes
        from overwatch_gen.renderers.l7_renderer import _build_app_routing_table

        t_data = l7_traefik.collect()
        i_data = l7_istio.collect()
        o_data = l7_okd_routes.collect()

        rows = _build_app_routing_table(t_data, i_data, o_data)
        # vault namespace has vault-authz policy in fixtures
        vault_rows = [r for r in rows if "vault" in r.get("source_name", "")]
        assert any(r["authz_policy"] for r in vault_rows), \
            "Expected authz policy for vault namespace routes"

    def test_tls_boundary_has_internet_to_traefik(self):
        """TLS boundaries must always include internet -> traefik segment."""
        from overwatch_gen.collectors import l7_traefik, l7_istio
        from overwatch_gen.renderers.l7_renderer import _build_tls_boundaries

        t_data = l7_traefik.collect()
        i_data = l7_istio.collect()

        boundaries = _build_tls_boundaries(t_data, i_data)
        segments = [(b["from"], b["to"]) for b in boundaries]
        assert ("internet", "traefik") in segments


# ---------------------------------------------------------------------------
# OPS-282: timeout plumbing tests
# ---------------------------------------------------------------------------

class TestL7TimeoutPlumbing:
    def test_traefik_collect_live_passes_request_timeout(self):
        """l7_traefik _collect_live passes _request_timeout=15 to all kubernetes API calls."""
        from overwatch_gen.collectors import l7_traefik

        fake_custom = mock.MagicMock()
        fake_custom.list_cluster_custom_object.return_value = {"items": []}

        with mock.patch.dict(os.environ, {"ARCH_AUDIT_USE_FIXTURES": "0"}):
            with mock.patch.object(l7_traefik, "_load_kubeconfig", return_value="/tmp/fake.yaml"):  # nosec B108 — mock return_value string, no temp file actually created
                with mock.patch("kubernetes.config.load_kube_config"):
                    with mock.patch("kubernetes.client.CustomObjectsApi", return_value=fake_custom):
                        l7_traefik._collect_live()

        for call in fake_custom.list_cluster_custom_object.call_args_list:
            assert call.kwargs.get("_request_timeout") == 15, (
                f"Expected _request_timeout=15 in call kwargs: {call.kwargs}"
            )

    def test_istio_collect_live_passes_request_timeout(self):
        """l7_istio _collect_live passes _request_timeout=15 to all kubernetes API calls."""
        from overwatch_gen.collectors import l7_istio

        fake_custom = mock.MagicMock()
        fake_custom.list_cluster_custom_object.return_value = {"items": []}

        with mock.patch.dict(os.environ, {"ARCH_AUDIT_USE_FIXTURES": "0"}):
            with mock.patch.object(l7_istio, "_load_kubeconfig", return_value="/tmp/fake.yaml"):  # nosec B108 — mock return_value string, no temp file actually created
                with mock.patch("kubernetes.config.load_kube_config"):
                    with mock.patch("kubernetes.client.CustomObjectsApi", return_value=fake_custom):
                        l7_istio._collect_live()

        for call in fake_custom.list_cluster_custom_object.call_args_list:
            assert call.kwargs.get("_request_timeout") == 15, (
                f"Expected _request_timeout=15 in call kwargs: {call.kwargs}"
            )

    def test_okd_routes_collect_live_passes_request_timeout(self):
        """l7_okd_routes _collect_live passes _request_timeout=15 to kubernetes API call."""
        from overwatch_gen.collectors import l7_okd_routes

        fake_custom = mock.MagicMock()
        fake_custom.list_cluster_custom_object.return_value = {"items": []}

        with mock.patch.dict(os.environ, {"ARCH_AUDIT_USE_FIXTURES": "0"}):
            with mock.patch.object(l7_okd_routes, "_load_kubeconfig", return_value="/tmp/fake.yaml"):  # nosec B108 — mock return_value string, no temp file actually created
                with mock.patch("kubernetes.config.load_kube_config"):
                    with mock.patch("kubernetes.client.CustomObjectsApi", return_value=fake_custom):
                        l7_okd_routes._collect_live()

        fake_custom.list_cluster_custom_object.assert_called_once()
        call = fake_custom.list_cluster_custom_object.call_args
        assert call.kwargs.get("_request_timeout") == 15, (
            f"Expected _request_timeout=15 in call kwargs: {call.kwargs}"
        )
