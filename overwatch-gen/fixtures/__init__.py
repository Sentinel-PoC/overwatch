"""
fixtures/__init__.py — Sample data fixtures for offline/test mode.

Live fixture capture: set ARCH_AUDIT_CAPTURE_FIXTURE=1 before running.
The collector will write fixtures/<name>_live.json (gitignored).
Operator manually copies the live file to the sample file after first run.
fixtures — sample JSON data for fixture-mode testing.

Files in this directory are loaded by collectors when ARCH_AUDIT_USE_FIXTURES=1.
They represent sanitized snapshots of live data for deterministic CI testing.

Available fixtures:
  pki_certs_sample.json         — Vault PKI issued certificates
  certmanager_sample.json       — cert-manager Certificate CRs
  traefik_routes_sample.json    — Traefik IngressRoute + Middleware CRs
  virtualservice_sample.json    — Istio VirtualService CRs
  okd_routes_sample.json        — OpenShift Route CRs
"""
