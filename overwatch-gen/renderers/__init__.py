"""
renderers — layer output renderer modules for overwatch-gen.

Each renderer module exposes render functions that take collector output
and write Markdown + d2 files to architecture-vault/.

Available renderers:
  l1_renderer  — Physical layer (Proxmox nodes/VMs, Unifi devices)
  l2_renderer  — Data link layer (VLAN table)
  l3_renderer  — Network layer (NetBox prefixes, firewall rules)
  l6_renderer  — Presentation layer (Vault PKI + cert-manager certs)
  l7_renderer  — Application layer (Traefik routes + Istio VS + OKD Routes)

Dry-run mode: set OVERWATCH_GEN_DRY_RUN=1 (or pass --dry-run to main.py)
to print output to stdout instead of writing files.
"""
