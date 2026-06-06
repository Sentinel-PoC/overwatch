"""
Tests for L5 collectors and renderer (l5_istio_authz, l5_istio_peerauth, l5_keycloak).

Runs entirely in fixture mode (ARCH_AUDIT_USE_FIXTURES=1) — no live network
or Vault access required.

Verifies:
- Collector returns expected structure
- Output is deterministic (two runs on same fixture = identical result)
- Renderer dry-run produces non-empty output
- Flow matrix is computed correctly from PeerAuth modes
- Registry wiring is correct
"""

import json
import os
from unittest import mock

import pytest
import requests

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
# L5 PeerAuthentication + flow matrix
# ---------------------------------------------------------------------------

class TestL5PeerauthCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        data = l5_istio_peerauth.collect()
        assert "peer_auths" in data
        assert "flow_matrix" in data

    def test_peer_auths_nonempty(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        data = l5_istio_peerauth.collect()
        assert len(data["peer_auths"]) > 0

    def test_peer_auths_sorted_by_namespace_then_name(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        data = l5_istio_peerauth.collect()
        keys = [(pa["namespace"], pa["name"]) for pa in data["peer_auths"]]
        assert keys == sorted(keys)

    def test_peerauth_fields_present(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        data = l5_istio_peerauth.collect()
        pa = data["peer_auths"][0]
        assert "name" in pa
        assert "namespace" in pa
        assert "mode" in pa
        assert "scope" in pa
        assert "selector" in pa

    def test_scope_values_valid(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        data = l5_istio_peerauth.collect()
        valid_scopes = {"namespace-default", "workload-specific"}
        for pa in data["peer_auths"]:
            assert pa["scope"] in valid_scopes

    def test_flow_matrix_nonempty(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        data = l5_istio_peerauth.collect()
        # Flow matrix requires at least 2 namespaces
        assert len(data["flow_matrix"]) > 0

    def test_flow_matrix_sorted_by_from_to(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        data = l5_istio_peerauth.collect()
        keys = [(r["from_ns"], r["to_ns"]) for r in data["flow_matrix"]]
        assert keys == sorted(keys)

    def test_flow_matrix_fields_present(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        data = l5_istio_peerauth.collect()
        row = data["flow_matrix"][0]
        assert "from_ns" in row
        assert "to_ns" in row
        assert "peerauth" in row
        assert "posture" in row
        assert "flag" in row

    def test_flow_matrix_no_self_pairs(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        data = l5_istio_peerauth.collect()
        for row in data["flow_matrix"]:
            assert row["from_ns"] != row["to_ns"]

    def test_strict_mode_yields_strict_posture(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        data = l5_istio_peerauth.collect()
        # Fixture has sentinel namespace as STRICT; rows with to_ns=sentinel should be STRICT
        strict_rows = [r for r in data["flow_matrix"] if r["to_ns"] == "sentinel"]
        for row in strict_rows:
            assert row["posture"] == "STRICT", f"Expected STRICT for sentinel row: {row}"
            assert row["flag"] == ""

    def test_permissive_mode_yields_permissive_posture(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        data = l5_istio_peerauth.collect()
        # Fixture has keycloak as PERMISSIVE
        permissive_rows = [r for r in data["flow_matrix"] if r["to_ns"] == "keycloak"]
        for row in permissive_rows:
            assert row["posture"] == "PERMISSIVE"
            assert row["flag"] == "WARN"

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        result1 = json.dumps(l5_istio_peerauth.collect(), sort_keys=True)
        result2 = json.dumps(l5_istio_peerauth.collect(), sort_keys=True)
        assert result1 == result2

    def test_missing_fixture_raises(self):
        from overwatch_gen.collectors import l5_istio_peerauth
        with mock.patch.object(
            type(l5_istio_peerauth._FIXTURE_SAMPLE), "exists", return_value=False
        ):
            with pytest.raises(FileNotFoundError):
                l5_istio_peerauth.collect()


class TestL5PeerauthRenderer:
    def test_render_dry_run_produces_output(self, capsys):
        from overwatch_gen.collectors import l5_istio_peerauth
        from overwatch_gen.renderers import l5_renderer
        data = l5_istio_peerauth.collect()
        l5_renderer.render_peerauth(data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_render_contains_flow_matrix(self, capsys):
        from overwatch_gen.collectors import l5_istio_peerauth
        from overwatch_gen.renderers import l5_renderer
        data = l5_istio_peerauth.collect()
        l5_renderer.render_peerauth(data)
        captured = capsys.readouterr()
        assert "Flow Matrix" in captured.out or "flow" in captured.out.lower()

    def test_render_contains_strict_label(self, capsys):
        from overwatch_gen.collectors import l5_istio_peerauth
        from overwatch_gen.renderers import l5_renderer
        data = l5_istio_peerauth.collect()
        l5_renderer.render_peerauth(data)
        captured = capsys.readouterr()
        assert "STRICT" in captured.out

    def test_render_determinism(self, capsys):
        from overwatch_gen.collectors import l5_istio_peerauth
        from overwatch_gen.renderers import l5_renderer
        data = l5_istio_peerauth.collect()
        l5_renderer.render_peerauth(data)
        out1 = capsys.readouterr().out
        l5_renderer.render_peerauth(data)
        out2 = capsys.readouterr().out
        assert out1 == out2


# ---------------------------------------------------------------------------
# L5 AuthorizationPolicy
# ---------------------------------------------------------------------------

class TestL5AuthzCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l5_istio_authz
        data = l5_istio_authz.collect()
        assert "policies" in data

    def test_policies_nonempty(self):
        from overwatch_gen.collectors import l5_istio_authz
        data = l5_istio_authz.collect()
        assert len(data["policies"]) > 0

    def test_policies_sorted_by_namespace_then_name(self):
        from overwatch_gen.collectors import l5_istio_authz
        data = l5_istio_authz.collect()
        keys = [(p["namespace"], p["name"]) for p in data["policies"]]
        assert keys == sorted(keys)

    def test_policy_fields_present(self):
        from overwatch_gen.collectors import l5_istio_authz
        data = l5_istio_authz.collect()
        policy = data["policies"][0]
        assert "name" in policy
        assert "namespace" in policy
        assert "action" in policy
        assert "selector" in policy
        assert "sources" in policy
        assert "rule_count" in policy

    def test_sources_sorted(self):
        from overwatch_gen.collectors import l5_istio_authz
        data = l5_istio_authz.collect()
        for p in data["policies"]:
            assert p["sources"] == sorted(p["sources"])

    def test_action_values_valid(self):
        from overwatch_gen.collectors import l5_istio_authz
        data = l5_istio_authz.collect()
        valid_actions = {"ALLOW", "DENY", "AUDIT", "CUSTOM"}
        for p in data["policies"]:
            assert p["action"] in valid_actions

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l5_istio_authz
        result1 = json.dumps(l5_istio_authz.collect(), sort_keys=True)
        result2 = json.dumps(l5_istio_authz.collect(), sort_keys=True)
        assert result1 == result2

    def test_missing_fixture_raises(self):
        from overwatch_gen.collectors import l5_istio_authz
        with mock.patch.object(
            type(l5_istio_authz._FIXTURE_SAMPLE), "exists", return_value=False
        ):
            with pytest.raises(FileNotFoundError):
                l5_istio_authz.collect()

    def test_crd_query_constants_match_l7(self):
        """OPS-283: l5 must query security.istio.io/v1beta1/authorizationpolicies.

        l7_istio.py uses v1beta1 and returns 28 policies; l5 was using v1 and
        returned 0.  Verify both group and version match the working collector.
        """
        from overwatch_gen.collectors import l5_istio_authz
        assert l5_istio_authz._ISTIO_GROUP == "security.istio.io"
        assert l5_istio_authz._AUTHZ_VERSION == "v1beta1", (
            f"Version must be v1beta1 (matches l7_istio.py) — got {l5_istio_authz._AUTHZ_VERSION!r}"
        )
        assert l5_istio_authz._AUTHZ_PLURAL == "authorizationpolicies"


class TestL5AuthzRenderer:
    def test_render_dry_run_produces_output(self, capsys):
        from overwatch_gen.collectors import l5_istio_authz
        from overwatch_gen.renderers import l5_renderer
        data = l5_istio_authz.collect()
        l5_renderer.render_authz(data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_render_contains_authz_data(self, capsys):
        from overwatch_gen.collectors import l5_istio_authz
        from overwatch_gen.renderers import l5_renderer
        data = l5_istio_authz.collect()
        l5_renderer.render_authz(data)
        captured = capsys.readouterr()
        # Fixture has monitoring namespace
        assert "monitoring" in captured.out

    def test_render_determinism(self, capsys):
        from overwatch_gen.collectors import l5_istio_authz
        from overwatch_gen.renderers import l5_renderer
        data = l5_istio_authz.collect()
        l5_renderer.render_authz(data)
        out1 = capsys.readouterr().out
        l5_renderer.render_authz(data)
        out2 = capsys.readouterr().out
        assert out1 == out2


# ---------------------------------------------------------------------------
# L5 Keycloak
# ---------------------------------------------------------------------------

class TestL5KeycloakCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l5_keycloak
        data = l5_keycloak.collect()
        assert "clients" in data
        assert "client_scopes" in data

    def test_clients_nonempty(self):
        from overwatch_gen.collectors import l5_keycloak
        data = l5_keycloak.collect()
        assert len(data["clients"]) > 0

    def test_clients_sorted_by_client_id(self):
        from overwatch_gen.collectors import l5_keycloak
        data = l5_keycloak.collect()
        ids = [c["client_id"] for c in data["clients"]]
        assert ids == sorted(ids)

    def test_client_scopes_sorted_by_name(self):
        from overwatch_gen.collectors import l5_keycloak
        data = l5_keycloak.collect()
        names = [s["name"] for s in data["client_scopes"]]
        assert names == sorted(names)

    def test_client_fields_present(self):
        from overwatch_gen.collectors import l5_keycloak
        data = l5_keycloak.collect()
        client = data["clients"][0]
        assert "client_id" in client
        assert "public_client" in client
        assert "direct_access_grants" in client
        assert "pkce_required" in client
        assert "redirect_uris" in client
        assert "root_url" in client
        assert "protocol" in client

    def test_public_client_is_bool(self):
        from overwatch_gen.collectors import l5_keycloak
        data = l5_keycloak.collect()
        for c in data["clients"]:
            assert isinstance(c["public_client"], bool)

    def test_pkce_required_is_bool(self):
        from overwatch_gen.collectors import l5_keycloak
        data = l5_keycloak.collect()
        for c in data["clients"]:
            assert isinstance(c["pkce_required"], bool)

    def test_redirect_uris_sorted(self):
        from overwatch_gen.collectors import l5_keycloak
        data = l5_keycloak.collect()
        for c in data["clients"]:
            assert c["redirect_uris"] == sorted(c["redirect_uris"])

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l5_keycloak
        result1 = json.dumps(l5_keycloak.collect(), sort_keys=True)
        result2 = json.dumps(l5_keycloak.collect(), sort_keys=True)
        assert result1 == result2

    def test_missing_fixture_raises(self):
        from overwatch_gen.collectors import l5_keycloak
        with mock.patch.object(
            type(l5_keycloak._FIXTURE_SAMPLE), "exists", return_value=False
        ):
            with pytest.raises(FileNotFoundError):
                l5_keycloak.collect()

    def test_get_admin_creds_reads_correct_vault_path(self):
        """_get_admin_creds must read secret/keycloak/admin, not secret/keycloak."""
        from overwatch_gen.collectors import l5_keycloak

        def fake_kv_read(path, field=None):
            assert path == "secret/keycloak/admin", f"Wrong Vault path: {path}"
            mapping = {"username": "admin", "password": "secret", "realm": "sentinel"}
            return mapping.get(field, "")

        fake_vc = mock.MagicMock()
        fake_vc.kv_read.side_effect = fake_kv_read

        with mock.patch("overwatch_gen.lib.vault_client.VaultClient", return_value=fake_vc):
            username, password, realm = l5_keycloak._get_admin_creds()

        assert username == "admin"
        assert password == "secret"
        assert realm == "sentinel"

    def test_get_admin_creds_defaults_realm_to_sentinel(self):
        """When realm field is absent/empty, defaults to _DEFAULT_REALM."""
        from overwatch_gen.collectors import l5_keycloak

        def fake_kv_read(path, field=None):
            mapping = {"username": "admin", "password": "secret", "realm": ""}
            return mapping.get(field, "")

        fake_vc = mock.MagicMock()
        fake_vc.kv_read.side_effect = fake_kv_read

        with mock.patch("overwatch_gen.lib.vault_client.VaultClient", return_value=fake_vc):
            username, password, realm = l5_keycloak._get_admin_creds()

        assert realm == l5_keycloak._DEFAULT_REALM

    def test_discover_realm_returns_configured_when_clients_found(self):
        """_discover_realm returns configured realm when probe yields >= 1 client."""
        from overwatch_gen.collectors import l5_keycloak

        session = mock.MagicMock()
        probe_resp = mock.MagicMock()
        probe_resp.json.return_value = [{"clientId": "some-client"}]
        probe_resp.raise_for_status.return_value = None
        session.get.return_value = probe_resp

        result = l5_keycloak._discover_realm(session, "sentinel")
        assert result == "sentinel"
        # Only the probe should be called; no realm enumeration needed
        assert session.get.call_count == 1

    def test_discover_realm_falls_back_when_probe_returns_zero(self):
        """_discover_realm enumerates realms when probe returns 0 clients."""
        from overwatch_gen.collectors import l5_keycloak

        session = mock.MagicMock()

        def fake_get(url, **kwargs):
            resp = mock.MagicMock()
            resp.raise_for_status.return_value = None
            if "/clients" in url:
                # Probe: return 0 clients
                resp.json.return_value = []
            else:
                # Realm list: return master + haist
                resp.json.return_value = [{"realm": "master"}, {"realm": "haist"}]
            return resp

        session.get.side_effect = fake_get

        result = l5_keycloak._discover_realm(session, "sentinel")
        assert result == "haist"

    def test_discover_realm_falls_back_to_master_when_no_non_master_realm(self):
        """_discover_realm falls back to 'master' when all discovered realms are master."""
        from overwatch_gen.collectors import l5_keycloak

        session = mock.MagicMock()

        def fake_get(url, **kwargs):
            resp = mock.MagicMock()
            resp.raise_for_status.return_value = None
            if "/clients" in url:
                resp.json.return_value = []
            else:
                # Only master realm in the list
                resp.json.return_value = [{"realm": "master"}]
            return resp

        session.get.side_effect = fake_get

        result = l5_keycloak._discover_realm(session, "sentinel")
        assert result == "master"

    def test_discover_realm_on_404_triggers_discovery(self):
        """_discover_realm handles 404 from probe by falling back to realm enumeration."""
        from overwatch_gen.collectors import l5_keycloak

        session = mock.MagicMock()

        probe_call_count = [0]

        def fake_get(url, **kwargs):
            resp = mock.MagicMock()
            resp.raise_for_status.return_value = None
            if "/clients" in url:
                probe_call_count[0] += 1
                # Simulate 404 on probe
                http_err = requests.HTTPError("404 Not Found")
                http_err.response = mock.MagicMock()
                http_err.response.status_code = 404
                resp.raise_for_status.side_effect = http_err
                return resp
            else:
                # Realm enumeration returns haist
                resp.json.return_value = [{"realm": "master"}, {"realm": "haist"}]
            return resp

        session.get.side_effect = fake_get

        result = l5_keycloak._discover_realm(session, "nonexistent-realm")
        assert result == "haist"

    def test_list_clients_paginated_single_page(self):
        """_list_clients_paginated returns all clients when response fits in one page."""
        from overwatch_gen.collectors import l5_keycloak

        session = mock.MagicMock()
        resp = mock.MagicMock()
        resp.raise_for_status.return_value = None
        # Return 3 clients — less than _PAGE_SIZE, so stops after one page
        resp.json.return_value = [
            {"clientId": "a"}, {"clientId": "b"}, {"clientId": "c"}
        ]
        session.get.return_value = resp

        clients = l5_keycloak._list_clients_paginated(session, "sentinel")
        assert len(clients) == 3
        # Verify pagination params were passed
        call_kwargs = session.get.call_args
        assert call_kwargs.kwargs["params"]["first"] == 0
        assert call_kwargs.kwargs["params"]["max"] == l5_keycloak._PAGE_SIZE

    def test_list_clients_paginated_multiple_pages(self):
        """_list_clients_paginated iterates pages until last page smaller than max."""
        from overwatch_gen.collectors import l5_keycloak

        page_size = l5_keycloak._PAGE_SIZE
        page1 = [{"clientId": f"c{i}"} for i in range(page_size)]
        page2 = [{"clientId": f"c{page_size+i}"} for i in range(3)]

        session = mock.MagicMock()
        call_count = [0]

        def fake_get(url, params=None, **kwargs):
            resp = mock.MagicMock()
            resp.raise_for_status.return_value = None
            if call_count[0] == 0:
                resp.json.return_value = page1
            else:
                resp.json.return_value = page2
            call_count[0] += 1
            return resp

        session.get.side_effect = fake_get

        clients = l5_keycloak._list_clients_paginated(session, "sentinel")
        assert len(clients) == page_size + 3
        assert call_count[0] == 2  # Two page requests made


class TestL5KeycloakRenderer:
    def test_render_dry_run_produces_output(self, capsys):
        from overwatch_gen.collectors import l5_keycloak
        from overwatch_gen.renderers import l5_renderer
        data = l5_keycloak.collect()
        l5_renderer.render_keycloak(data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_render_contains_client_data(self, capsys):
        from overwatch_gen.collectors import l5_keycloak
        from overwatch_gen.renderers import l5_renderer
        data = l5_keycloak.collect()
        l5_renderer.render_keycloak(data)
        captured = capsys.readouterr()
        # Fixture has sentinel-dashboard client
        assert "sentinel-dashboard" in captured.out

    def test_render_contains_keycloak_section(self, capsys):
        from overwatch_gen.collectors import l5_keycloak
        from overwatch_gen.renderers import l5_renderer
        data = l5_keycloak.collect()
        l5_renderer.render_keycloak(data)
        captured = capsys.readouterr()
        assert "Keycloak" in captured.out or "sentinel" in captured.out

    def test_render_determinism(self, capsys):
        from overwatch_gen.collectors import l5_keycloak
        from overwatch_gen.renderers import l5_renderer
        data = l5_keycloak.collect()
        l5_renderer.render_keycloak(data)
        out1 = capsys.readouterr().out
        l5_renderer.render_keycloak(data)
        out2 = capsys.readouterr().out
        assert out1 == out2


# ---------------------------------------------------------------------------
# OPS-282: timeout plumbing tests
# ---------------------------------------------------------------------------

class TestL5TimeoutPlumbing:
    def test_authz_collect_live_passes_request_timeout(self):
        """_collect_live passes _request_timeout=15 to list_cluster_custom_object for AuthzPolicy."""
        from overwatch_gen.collectors import l5_istio_authz

        fake_custom = mock.MagicMock()
        fake_custom.list_cluster_custom_object.return_value = {"items": []}

        with mock.patch.dict(os.environ, {"ARCH_AUDIT_USE_FIXTURES": "0"}):
            with mock.patch.object(l5_istio_authz, "_get_kubeconfig_content", return_value="fake"):
                with mock.patch("kubernetes.config.load_kube_config"):
                    with mock.patch("kubernetes.client.CustomObjectsApi", return_value=fake_custom):
                        with mock.patch("os.unlink"):
                            l5_istio_authz._collect_live()

        fake_custom.list_cluster_custom_object.assert_called_once_with(
            group=l5_istio_authz._ISTIO_GROUP,
            version=l5_istio_authz._AUTHZ_VERSION,
            plural=l5_istio_authz._AUTHZ_PLURAL,
            _request_timeout=15,
        )

    def test_peerauth_collect_live_passes_request_timeout(self):
        """_collect_live passes _request_timeout=15 to list_cluster_custom_object for PeerAuth."""
        from overwatch_gen.collectors import l5_istio_peerauth

        fake_custom = mock.MagicMock()
        fake_custom.list_cluster_custom_object.return_value = {"items": []}

        with mock.patch.dict(os.environ, {"ARCH_AUDIT_USE_FIXTURES": "0"}):
            with mock.patch.object(l5_istio_peerauth, "_get_kubeconfig_content", return_value="fake"):
                with mock.patch("kubernetes.config.load_kube_config"):
                    with mock.patch("kubernetes.client.CustomObjectsApi", return_value=fake_custom):
                        with mock.patch("os.unlink"):
                            l5_istio_peerauth._collect_live()

        fake_custom.list_cluster_custom_object.assert_called_once_with(
            group=l5_istio_peerauth._ISTIO_GROUP,
            version=l5_istio_peerauth._PEERAUTH_VERSION,
            plural=l5_istio_peerauth._PEERAUTH_PLURAL,
            _request_timeout=15,
        )

    def test_keycloak_get_admin_token_raises_on_timeout(self):
        """_get_admin_token raises requests.exceptions.Timeout when session.post times out."""
        import requests
        from overwatch_gen.collectors import l5_keycloak

        fake_session = mock.MagicMock()
        fake_session.post.side_effect = requests.exceptions.Timeout("timed out")

        with pytest.raises(requests.exceptions.Timeout):
            l5_keycloak._get_admin_token(fake_session, "admin", "password")


# ---------------------------------------------------------------------------
# OPS-285: render_combined coordinator pattern tests
# ---------------------------------------------------------------------------

class TestL5RenderCombined:
    def test_render_combined_all_sections_populated(self, capsys):
        """render_combined with all three data sources produces output containing each section."""
        from overwatch_gen.collectors import l5_istio_authz, l5_keycloak, l5_istio_peerauth
        from overwatch_gen.renderers import l5_renderer
        authz_data = l5_istio_authz.collect()
        keycloak_data = l5_keycloak.collect()
        peerauth_data = l5_istio_peerauth.collect()
        l5_renderer.render_combined(authz=authz_data, keycloak=keycloak_data, peerauth=peerauth_data)
        captured = capsys.readouterr()
        # Should contain data from all three sub-layers
        assert "monitoring" in captured.out          # from authz fixture (monitoring namespace)
        assert "sentinel-dashboard" in captured.out  # from keycloak fixture
        assert "STRICT" in captured.out              # from peerauth fixture (flow matrix)

    def test_render_combined_empty_authz(self, capsys):
        """render_combined with empty authz still produces keycloak + peerauth output."""
        from overwatch_gen.collectors import l5_keycloak, l5_istio_peerauth
        from overwatch_gen.renderers import l5_renderer
        keycloak_data = l5_keycloak.collect()
        peerauth_data = l5_istio_peerauth.collect()
        l5_renderer.render_combined(authz={}, keycloak=keycloak_data, peerauth=peerauth_data)
        captured = capsys.readouterr()
        assert len(captured.out) > 0
        assert "STRICT" in captured.out

    def test_render_combined_empty_keycloak(self, capsys):
        """render_combined with empty keycloak still produces authz + peerauth output."""
        from overwatch_gen.collectors import l5_istio_authz, l5_istio_peerauth
        from overwatch_gen.renderers import l5_renderer
        authz_data = l5_istio_authz.collect()
        peerauth_data = l5_istio_peerauth.collect()
        l5_renderer.render_combined(authz=authz_data, keycloak={}, peerauth=peerauth_data)
        captured = capsys.readouterr()
        assert "monitoring" in captured.out
        assert "STRICT" in captured.out

    def test_render_combined_empty_peerauth(self, capsys):
        """render_combined with empty peerauth still produces authz + keycloak output."""
        from overwatch_gen.collectors import l5_istio_authz, l5_keycloak
        from overwatch_gen.renderers import l5_renderer
        authz_data = l5_istio_authz.collect()
        keycloak_data = l5_keycloak.collect()
        l5_renderer.render_combined(authz=authz_data, keycloak=keycloak_data, peerauth={})
        captured = capsys.readouterr()
        assert "monitoring" in captured.out
        assert "sentinel-dashboard" in captured.out

    def test_sequential_authz_keycloak_peerauth_preserves_all(self, capsys):
        """
        Running authz.render, keycloak.render, peerauth.render in sequence (coordinator pattern)
        produces a combined INDEX.md with ALL three sections populated.
        """
        from overwatch_gen.collectors import l5_istio_authz, l5_keycloak, l5_istio_peerauth
        authz_data = l5_istio_authz.collect()
        keycloak_data = l5_keycloak.collect()
        peerauth_data = l5_istio_peerauth.collect()

        # Reset caches to simulate fresh run
        l5_istio_authz._AUTHZ_DATA_CACHE.clear()
        l5_keycloak._KEYCLOAK_DATA_CACHE.clear()

        # Step 1: authz render — caches data, no file write
        l5_istio_authz.render(authz_data)
        assert l5_istio_authz._AUTHZ_DATA_CACHE.get("policies") is not None
        assert len(l5_istio_authz._AUTHZ_DATA_CACHE["policies"]) > 0

        # Step 2: keycloak render — caches data, no file write
        l5_keycloak.render(keycloak_data)
        assert l5_keycloak._KEYCLOAK_DATA_CACHE.get("clients") is not None
        assert len(l5_keycloak._KEYCLOAK_DATA_CACHE["clients"]) > 0

        # Step 3: peerauth render — orchestrator: reads both caches, writes combined output
        l5_istio_peerauth.render(peerauth_data)
        captured = capsys.readouterr()

        # Output must contain data from all three sub-layers
        assert "monitoring" in captured.out, (
            "Expected authz data (monitoring namespace) in combined output"
        )
        assert "sentinel-dashboard" in captured.out, (
            "Expected keycloak data (sentinel-dashboard client) in combined output"
        )
        assert "STRICT" in captured.out, (
            "Expected peerauth data (STRICT flow matrix) in combined output"
        )

    def test_authz_render_does_not_call_write_output(self):
        """l5_istio_authz.render should only populate cache, never trigger file write."""
        from overwatch_gen.collectors import l5_istio_authz
        from overwatch_gen.renderers import l5_renderer
        authz_data = l5_istio_authz.collect()
        l5_istio_authz._AUTHZ_DATA_CACHE.clear()

        with mock.patch.object(l5_renderer, "render_combined") as mock_combined:
            l5_istio_authz.render(authz_data)
            mock_combined.assert_not_called()

        assert l5_istio_authz._AUTHZ_DATA_CACHE.get("policies") is not None

    def test_keycloak_render_does_not_call_write_output(self):
        """l5_keycloak.render should only populate cache, never trigger file write."""
        from overwatch_gen.collectors import l5_keycloak
        from overwatch_gen.renderers import l5_renderer
        keycloak_data = l5_keycloak.collect()
        l5_keycloak._KEYCLOAK_DATA_CACHE.clear()

        with mock.patch.object(l5_renderer, "render_combined") as mock_combined:
            l5_keycloak.render(keycloak_data)
            mock_combined.assert_not_called()

        assert l5_keycloak._KEYCLOAK_DATA_CACHE.get("clients") is not None

    def test_render_wrapper_peerauth_standalone(self, capsys):
        """render_peerauth() wrapper still produces peerauth output (backwards compat)."""
        from overwatch_gen.collectors import l5_istio_peerauth
        from overwatch_gen.renderers import l5_renderer
        data = l5_istio_peerauth.collect()
        l5_renderer.render_peerauth(data)
        captured = capsys.readouterr()
        assert "STRICT" in captured.out

    def test_render_wrapper_authz_standalone(self, capsys):
        """render_authz() wrapper still produces authz output (backwards compat)."""
        from overwatch_gen.collectors import l5_istio_authz
        from overwatch_gen.renderers import l5_renderer
        data = l5_istio_authz.collect()
        l5_renderer.render_authz(data)
        captured = capsys.readouterr()
        assert "monitoring" in captured.out

    def test_render_wrapper_keycloak_standalone(self, capsys):
        """render_keycloak() wrapper still produces keycloak output (backwards compat)."""
        from overwatch_gen.collectors import l5_keycloak
        from overwatch_gen.renderers import l5_renderer
        data = l5_keycloak.collect()
        l5_renderer.render_keycloak(data)
        captured = capsys.readouterr()
        assert "sentinel-dashboard" in captured.out


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

class TestL5Registry:
    def test_peerauth_registers_correctly(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l5_istio_peerauth
        registry.register_layer("l5_peerauth", l5_istio_peerauth.collect, l5_istio_peerauth.render)
        assert "l5_peerauth" in registry.all_layers()

    def test_authz_registers_correctly(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l5_istio_authz
        registry.register_layer("l5_authz", l5_istio_authz.collect, l5_istio_authz.render)
        assert "l5_authz" in registry.all_layers()

    def test_keycloak_registers_correctly(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l5_keycloak
        registry.register_layer("l5_keycloak", l5_keycloak.collect, l5_keycloak.render)
        assert "l5_keycloak" in registry.all_layers()
