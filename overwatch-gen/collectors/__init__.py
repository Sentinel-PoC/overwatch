"""
collectors — layer data collector modules for overwatch-gen.

Each collector module exposes a collect() function that returns a dict
of raw data for that layer.  Collectors are imported for their side-effects
(registry.register_layer) by the layer plugin modules.

Available collectors (registered at import):
  l1_proxmox       — Proxmox nodes, VMs, storage (registers "l1_proxmox")
  l1_unifi         — Unifi devices / ports (registers "l1_unifi")
  l2_vlans         — Unifi VLAN/network definitions (registers "l2_vlans")
  l3_netbox        — NetBox prefixes + IP assignments (registers "l3_netbox")
  l3_unifi_firewall — Unifi firewall policies + zones (registers "l3_unifi_firewall")
  l6_vault_pki      — Vault PKI issued certificates and issuers (registers "l6_vault_pki")
  l6_certmanager    — cert-manager Certificate + ClusterIssuer CRs (registers "l6_certmanager")
  l7_traefik        — Traefik IngressRoute + Middleware CRs (registers "l7_traefik")
  l7_istio          — Istio VirtualService + DestinationRule + AuthorizationPolicy (registers "l7_istio")
  l7_okd_routes     — OpenShift Route CRs (registers "l7_okd_routes")

Fixture mode: set ARCH_AUDIT_USE_FIXTURES=1 to load data from
overwatch-gen/fixtures/ instead of hitting live cluster/APIs.

Fixture capture: set ARCH_AUDIT_CAPTURE_FIXTURE=1 to write live API
responses to fixtures/<name>_live.json (gitignored).
"""
