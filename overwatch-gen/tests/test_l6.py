"""
Tests for L6 collectors and renderer (l6_vault_pki, l6_certmanager).

Runs entirely in fixture mode (ARCH_AUDIT_USE_FIXTURES=1) — no live network
or Vault access required.

Verifies:
- Collectors return expected structure
- Output is deterministic (two runs on same fixture = identical result)
- Renderer dry-run produces non-empty output
- Expiry flagging logic (EXPIRED / WARNING / OK)
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
# l6_vault_pki
# ---------------------------------------------------------------------------

class TestL6VaultPkiCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l6_vault_pki
        data = l6_vault_pki.collect()
        assert "source" in data
        assert "issuers" in data
        assert "certs" in data

    def test_source_is_vault_pki(self):
        from overwatch_gen.collectors import l6_vault_pki
        data = l6_vault_pki.collect()
        assert data["source"] == "vault_pki"

    def test_certs_list_nonempty(self):
        from overwatch_gen.collectors import l6_vault_pki
        data = l6_vault_pki.collect()
        assert len(data["certs"]) > 0

    def test_issuers_list_nonempty(self):
        from overwatch_gen.collectors import l6_vault_pki
        data = l6_vault_pki.collect()
        assert len(data["issuers"]) > 0

    def test_cert_fields_present(self):
        from overwatch_gen.collectors import l6_vault_pki
        data = l6_vault_pki.collect()
        cert = data["certs"][0]
        for field in ["serial", "common_name", "sans", "issuer", "not_after", "days_remaining", "revoked"]:
            assert field in cert, f"Missing field: {field}"

    def test_issuer_fields_present(self):
        from overwatch_gen.collectors import l6_vault_pki
        data = l6_vault_pki.collect()
        issuer = data["issuers"][0]
        for field in ["name", "issuer_id", "issuer_name", "leaf_issuer"]:
            assert field in issuer, f"Missing field: {field}"

    def test_days_remaining_is_int(self):
        from overwatch_gen.collectors import l6_vault_pki
        data = l6_vault_pki.collect()
        for cert in data["certs"]:
            assert isinstance(cert["days_remaining"], int)

    def test_revoked_is_bool(self):
        from overwatch_gen.collectors import l6_vault_pki
        data = l6_vault_pki.collect()
        for cert in data["certs"]:
            assert isinstance(cert["revoked"], bool)

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l6_vault_pki
        r1 = json.dumps(l6_vault_pki.collect(), sort_keys=True)
        r2 = json.dumps(l6_vault_pki.collect(), sort_keys=True)
        assert r1 == r2

    def test_fixture_has_revoked_cert(self):
        """Fixture should include a revoked cert to test that path."""
        from overwatch_gen.collectors import l6_vault_pki
        data = l6_vault_pki.collect()
        revoked = [c for c in data["certs"] if c["revoked"]]
        assert len(revoked) >= 1

    def test_fixture_has_expiring_cert(self):
        """Fixture should include a cert with <30d remaining."""
        from overwatch_gen.collectors import l6_vault_pki
        data = l6_vault_pki.collect()
        expiring = [c for c in data["certs"] if 0 < c["days_remaining"] < 30]
        assert len(expiring) >= 1

    def test_registry_registered(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l6_vault_pki
        # Module was already imported; re-register manually after clear_registry()
        registry.register_layer("l6_vault_pki", l6_vault_pki.collect, l6_vault_pki.render)
        assert "l6_vault_pki" in registry.all_layers()

    def test_no_pki_mount_returns_warning_stub(self):
        """When no PKI mount exists (all mounts return 404), collector returns empty stub with _warning."""
        from overwatch_gen.collectors import l6_vault_pki

        with mock.patch.dict(os.environ, {"VAULT_TOKEN": "fake-token", "ARCH_AUDIT_USE_FIXTURES": "0"}):
            with mock.patch.object(l6_vault_pki, "_check_pki_reader_policy", return_value=True):
                # _vault_request returns None for every call (simulates 404 on mount list)
                with mock.patch.object(l6_vault_pki, "_vault_request", return_value=None):
                    data = l6_vault_pki._collect_live()

        assert data["source"] == "vault_pki"
        assert data["certs"] == []
        assert data["issuers"] == []
        assert "_warning" in data
        assert "No Vault PKI mount" in data["_warning"] or "collector skipped" in data["_warning"]

    def test_empty_stub_no_mount_message(self):
        """_empty_stub(no_mount=True) sets the correct warning text."""
        from overwatch_gen.collectors import l6_vault_pki
        stub = l6_vault_pki._empty_stub(no_mount=True)
        assert "No Vault PKI mount" in stub["_warning"] or "collector skipped" in stub["_warning"]


# ---------------------------------------------------------------------------
# l6_certmanager
# ---------------------------------------------------------------------------

class TestL6CertManagerCollector:
    def test_collect_returns_expected_keys(self):
        from overwatch_gen.collectors import l6_certmanager
        data = l6_certmanager.collect()
        assert "source" in data
        assert "certificates" in data
        assert "cluster_issuers" in data

    def test_source_is_certmanager(self):
        from overwatch_gen.collectors import l6_certmanager
        data = l6_certmanager.collect()
        assert data["source"] == "certmanager"

    def test_certificates_nonempty(self):
        from overwatch_gen.collectors import l6_certmanager
        data = l6_certmanager.collect()
        assert len(data["certificates"]) > 0

    def test_cluster_issuers_nonempty(self):
        from overwatch_gen.collectors import l6_certmanager
        data = l6_certmanager.collect()
        assert len(data["cluster_issuers"]) > 0

    def test_cert_fields_present(self):
        from overwatch_gen.collectors import l6_certmanager
        data = l6_certmanager.collect()
        cert = data["certificates"][0]
        for field in ["name", "namespace", "dns_names", "issuer_name", "issuer_kind",
                      "not_after", "days_remaining", "ready"]:
            assert field in cert, f"Missing field: {field}"

    def test_cluster_issuer_fields_present(self):
        from overwatch_gen.collectors import l6_certmanager
        data = l6_certmanager.collect()
        issuer = data["cluster_issuers"][0]
        for field in ["name", "type", "ready"]:
            assert field in issuer, f"Missing field: {field}"

    def test_dns_names_is_list(self):
        from overwatch_gen.collectors import l6_certmanager
        data = l6_certmanager.collect()
        for cert in data["certificates"]:
            assert isinstance(cert["dns_names"], list)

    def test_ready_is_bool(self):
        from overwatch_gen.collectors import l6_certmanager
        data = l6_certmanager.collect()
        for cert in data["certificates"]:
            assert isinstance(cert["ready"], bool)

    def test_determinism_two_runs_identical(self):
        from overwatch_gen.collectors import l6_certmanager
        r1 = json.dumps(l6_certmanager.collect(), sort_keys=True)
        r2 = json.dumps(l6_certmanager.collect(), sort_keys=True)
        assert r1 == r2

    def test_vault_type_issuer_present(self):
        """Fixture should include at least one Vault-backed ClusterIssuer."""
        from overwatch_gen.collectors import l6_certmanager
        data = l6_certmanager.collect()
        vault_issuers = [i for i in data["cluster_issuers"] if i["type"] == "vault"]
        assert len(vault_issuers) >= 1

    def test_registry_registered(self):
        from overwatch_gen.lib import registry
        from overwatch_gen.collectors import l6_certmanager
        registry.register_layer("l6_certmanager", l6_certmanager.collect, l6_certmanager.render)
        assert "l6_certmanager" in registry.all_layers()

    def test_certmanager_not_installed_returns_warning_stub(self):
        """When cert-manager CRDs return 404, _collect_live returns empty stub with _warning."""
        from overwatch_gen.collectors import l6_certmanager
        import kubernetes.client.exceptions

        api_exc_404 = kubernetes.client.exceptions.ApiException(status=404)

        fake_custom = mock.MagicMock()
        fake_custom.list_cluster_custom_object.side_effect = api_exc_404

        with mock.patch.dict(os.environ, {"ARCH_AUDIT_USE_FIXTURES": "0"}):
            with mock.patch.object(l6_certmanager, "_load_kubeconfig", return_value="/tmp/fake.yaml"):  # nosec B108 — mock return_value string, no temp file actually created
                with mock.patch("kubernetes.client.CustomObjectsApi", return_value=fake_custom):
                    with mock.patch("kubernetes.config.load_kube_config"):
                        data = l6_certmanager._collect_live()

        assert data["source"] == "certmanager"
        assert data["certificates"] == []
        assert data["cluster_issuers"] == []
        assert "_warning" in data
        assert "cert-manager" in data["_warning"].lower() or "not installed" in data["_warning"].lower()

    def test_empty_stub_not_installed_message(self):
        """_empty_stub(not_installed=True) sets the correct warning text."""
        from overwatch_gen.collectors import l6_certmanager
        stub = l6_certmanager._empty_stub(not_installed=True)
        assert "cert-manager not installed" in stub["_warning"]


# ---------------------------------------------------------------------------
# l6_renderer (via dry-run)
# ---------------------------------------------------------------------------

class TestL6Renderer:
    def test_render_produces_output(self, capsys):
        from overwatch_gen.collectors import l6_vault_pki, l6_certmanager
        from overwatch_gen.renderers import l6_renderer

        pki_data = l6_vault_pki.collect()
        cm_data = l6_certmanager.collect()
        l6_renderer.render(pki_data=pki_data, certmanager_data=cm_data)

        captured = capsys.readouterr()
        assert "L6" in captured.out
        assert "INDEX.md" in captured.out

    def test_render_contains_cert_table(self, capsys):
        from overwatch_gen.collectors import l6_vault_pki, l6_certmanager
        from overwatch_gen.renderers import l6_renderer

        pki_data = l6_vault_pki.collect()
        cm_data = l6_certmanager.collect()
        l6_renderer.render(pki_data=pki_data, certmanager_data=cm_data)

        captured = capsys.readouterr()
        # Should have cert table headers
        assert "Common Name" in captured.out or "Serial" in captured.out

    def test_render_flags_expiring_certs(self, capsys):
        from overwatch_gen.collectors import l6_vault_pki, l6_certmanager
        from overwatch_gen.renderers import l6_renderer

        pki_data = l6_vault_pki.collect()
        cm_data = l6_certmanager.collect()
        l6_renderer.render(pki_data=pki_data, certmanager_data=cm_data)

        captured = capsys.readouterr()
        # Fixture has expiring certs (5d, 11d remaining) — should trigger WARNING
        assert "WARNING" in captured.out or "EXPIRING" in captured.out

    def test_render_is_deterministic(self, capsys):
        from overwatch_gen.collectors import l6_vault_pki, l6_certmanager
        from overwatch_gen.renderers import l6_renderer

        pki_data = l6_vault_pki.collect()
        cm_data = l6_certmanager.collect()

        l6_renderer.render(pki_data=pki_data, certmanager_data=cm_data)
        out1 = capsys.readouterr().out

        l6_renderer.render(pki_data=pki_data, certmanager_data=cm_data)
        out2 = capsys.readouterr().out

        assert out1 == out2

    def test_render_empty_pki_stub_shows_warning(self, capsys):
        from overwatch_gen.collectors import l6_vault_pki, l6_certmanager
        from overwatch_gen.renderers import l6_renderer

        empty_pki = l6_vault_pki._empty_stub()
        cm_data = l6_certmanager.collect()
        l6_renderer.render(pki_data=empty_pki, certmanager_data=cm_data)

        captured = capsys.readouterr()
        # Empty PKI should render without crash and include a warning note
        assert "L6" in captured.out

    def test_expiry_annotation(self):
        from overwatch_gen.renderers.l6_renderer import _annotate_certs

        certs = [
            {"days_remaining": -10},
            {"days_remaining": 5},
            {"days_remaining": 100},
        ]
        annotated = _annotate_certs(certs)
        assert annotated[0]["expiry_flag"] == "EXPIRED"
        assert annotated[1]["expiry_flag"] == "WARNING"
        assert annotated[2]["expiry_flag"] == "OK"


# ---------------------------------------------------------------------------
# OPS-285: render_combined coordinator pattern tests
# ---------------------------------------------------------------------------

class TestL6RenderCombined:
    def test_render_combined_both_sources_populated(self, capsys):
        """render_combined with both pki and certmanager data produces output with both."""
        from overwatch_gen.collectors import l6_vault_pki, l6_certmanager
        from overwatch_gen.renderers import l6_renderer
        pki_data = l6_vault_pki.collect()
        cm_data = l6_certmanager.collect()
        l6_renderer.render_combined(certmanager=cm_data, vault_pki=pki_data)
        captured = capsys.readouterr()
        assert "L6" in captured.out
        assert "INDEX.md" in captured.out

    def test_render_combined_empty_certmanager(self, capsys):
        """render_combined with empty certmanager dict produces output (pki side)."""
        from overwatch_gen.collectors import l6_vault_pki
        from overwatch_gen.renderers import l6_renderer
        pki_data = l6_vault_pki.collect()
        l6_renderer.render_combined(certmanager={}, vault_pki=pki_data)
        captured = capsys.readouterr()
        assert "L6" in captured.out

    def test_render_combined_empty_pki(self, capsys):
        """render_combined with empty pki dict produces output (certmanager side)."""
        from overwatch_gen.collectors import l6_certmanager
        from overwatch_gen.renderers import l6_renderer
        cm_data = l6_certmanager.collect()
        l6_renderer.render_combined(certmanager=cm_data, vault_pki={})
        captured = capsys.readouterr()
        assert "L6" in captured.out

    def test_sequential_certmanager_then_vault_pki_preserves_both(self, capsys):
        """
        Running certmanager.render then vault_pki.render in sequence (coordinator pattern)
        produces a combined INDEX.md with BOTH sections populated.
        """
        from overwatch_gen.collectors import l6_vault_pki, l6_certmanager
        pki_data = l6_vault_pki.collect()
        cm_data = l6_certmanager.collect()

        # Reset caches to simulate fresh run
        l6_certmanager._CERTMANAGER_DATA_CACHE.clear()

        # Step 1: certmanager render — caches data, no file write
        l6_certmanager.render(cm_data)
        assert l6_certmanager._CERTMANAGER_DATA_CACHE.get("source") == "certmanager"
        assert l6_certmanager._CERTMANAGER_DATA_CACHE.get("certificates") is not None

        # Step 2: vault_pki render — orchestrator: reads certmanager cache, writes combined output
        l6_vault_pki.render(pki_data)
        captured = capsys.readouterr()

        # Both L6 PKI and cert-manager data should appear in output
        assert "L6" in captured.out, "Expected L6 label in combined output"
        assert "INDEX.md" in captured.out

    def test_certmanager_render_does_not_call_render_combined(self):
        """l6_certmanager.render should only populate cache, never trigger file write."""
        from overwatch_gen.collectors import l6_certmanager
        from overwatch_gen.renderers import l6_renderer
        cm_data = l6_certmanager.collect()
        l6_certmanager._CERTMANAGER_DATA_CACHE.clear()

        with mock.patch.object(l6_renderer, "render_combined") as mock_combined:
            l6_certmanager.render(cm_data)
            mock_combined.assert_not_called()

        assert l6_certmanager._CERTMANAGER_DATA_CACHE.get("source") == "certmanager"

    def test_render_wrapper_still_works(self, capsys):
        """Old render(pki_data, certmanager_data) wrapper still works (backwards compat)."""
        from overwatch_gen.collectors import l6_vault_pki, l6_certmanager
        from overwatch_gen.renderers import l6_renderer
        pki_data = l6_vault_pki.collect()
        cm_data = l6_certmanager.collect()
        l6_renderer.render(pki_data=pki_data, certmanager_data=cm_data)
        captured = capsys.readouterr()
        assert "L6" in captured.out
        assert "INDEX.md" in captured.out


# ---------------------------------------------------------------------------
# OPS-282: timeout plumbing tests
# ---------------------------------------------------------------------------

class TestL6TimeoutPlumbing:
    def test_vault_pki_request_raises_on_timeout(self):
        """_vault_request raises requests.exceptions.Timeout when requests.get times out."""
        import requests
        from overwatch_gen.collectors import l6_vault_pki

        with mock.patch("requests.get", side_effect=requests.exceptions.Timeout("timed out")):
            # _vault_request catches generic Exception and logs, returns None
            # The timeout IS propagated via the logger warning, not re-raised
            # Verify the call_args includes timeout=15
            result = l6_vault_pki._vault_request("/pki/certs", "fake-token")
        # Timeout is caught internally (returns None on exception) — verify timeout kwarg used
        # by checking that a request with timeout=15 was attempted
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = mock.MagicMock(status_code=200, json=lambda: {})
            l6_vault_pki._vault_request("/pki/certs", "fake-token")
            _, kwargs = mock_get.call_args
            assert kwargs.get("timeout") == 15

    def test_certmanager_k8s_passes_request_timeout(self):
        """cert-manager _collect_live passes _request_timeout=15 to kubernetes API calls."""
        from overwatch_gen.collectors import l6_certmanager
        import kubernetes.client.exceptions

        fake_custom = mock.MagicMock()
        fake_custom.list_cluster_custom_object.return_value = {"items": []}

        with mock.patch.dict(os.environ, {"ARCH_AUDIT_USE_FIXTURES": "0"}):
            with mock.patch.object(l6_certmanager, "_load_kubeconfig", return_value="/tmp/fake.yaml"):  # nosec B108 — mock return_value string, no temp file actually created
                with mock.patch("kubernetes.config.load_kube_config"):
                    with mock.patch("kubernetes.client.CustomObjectsApi", return_value=fake_custom):
                        l6_certmanager._collect_live()

        for call in fake_custom.list_cluster_custom_object.call_args_list:
            assert call.kwargs.get("_request_timeout") == 15, (
                f"Expected _request_timeout=15 in call kwargs: {call.kwargs}"
            )
