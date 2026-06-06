"""
main.py — overwatch-gen entry point.

Run as: python3 -m overwatch_gen.main [options] [layer-name|--all]

Imports are lazy so --help does not require Vault credentials or network access.
Workers 1/2/3 register their layer plugins before this module runs them.

Environment variables (all overridable by CLI flags):
    VAULT_ADDR            Vault server URL
    VAULT_TOKEN           Direct Vault token (skips AppRole)
    VAULT_APPROLE_ROLE    AppRole role_id
    VAULT_APPROLE_SECRET  AppRole secret_id
    VAULT_SKIP_VERIFY     Set to "true" to skip TLS verification (self-signed)
"""

import argparse
import logging
import os
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m overwatch_gen.main",
        description=(
            "overwatch-gen: L1-L7 architecture audit generator.\n"
            "Registered layers are populated by layer plugin imports.\n"
            "See overwatch-gen/lib/registry.py for the extension-hook API."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "layer",
        nargs="*",
        metavar="LAYER",
        help=(
            "Name(s) of specific layer(s) to run (e.g. l1_proxmox l2_vlans). "
            "Multiple layer names accepted. Omit to use --all."
        ),
    )
    parser.add_argument(
        "--all",
        dest="run_all",
        action="store_true",
        default=False,
        help="Run all registered layers in sorted order.",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Render output to stdout instead of writing files.",
    )
    parser.add_argument(
        "--list-layers",
        dest="list_layers",
        action="store_true",
        default=False,
        help="Print all registered layer names and exit.",
    )
    parser.add_argument(
        "--vault-addr",
        default=os.environ.get("VAULT_ADDR", "https://192.168.12.206:8200"),
        metavar="URL",
        help="Vault server address (default: $VAULT_ADDR or https://192.168.12.206:8200)",
    )
    parser.add_argument(
        "--vault-token",
        default=os.environ.get("VAULT_TOKEN"),
        metavar="TOKEN",
        help="Vault token (direct; skips AppRole). Default: $VAULT_TOKEN",
    )
    parser.add_argument(
        "--approle-role",
        default=os.environ.get("VAULT_APPROLE_ROLE"),
        metavar="ROLE_ID",
        help="Vault AppRole role_id. Default: $VAULT_APPROLE_ROLE",
    )
    parser.add_argument(
        "--approle-secret",
        default=os.environ.get("VAULT_APPROLE_SECRET"),
        metavar="SECRET_ID",
        help="Vault AppRole secret_id. Default: $VAULT_APPROLE_SECRET",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )


def _set_vault_env(args: argparse.Namespace) -> None:
    """Push parsed vault args back to env so VaultClient picks them up."""
    if args.vault_addr:
        os.environ["VAULT_ADDR"] = args.vault_addr
    if args.vault_token:
        os.environ["VAULT_TOKEN"] = args.vault_token
    if args.approle_role:
        os.environ["VAULT_APPROLE_ROLE_ID"] = args.approle_role
    if args.approle_secret:
        os.environ["VAULT_APPROLE_SECRET_ID"] = args.approle_secret


def main(argv: list[str] | None = None) -> int:
    """
    Entry point. Returns exit code (0 = success, non-zero = error).

    Lazy imports: registry and layer plugins are loaded only after argument
    parsing, so --help exits without requiring Vault or Python deps.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    log = logging.getLogger(__name__)

    # --- Lazy import: registry (no network I/O yet) ---
    # Layer plugin modules are imported here so their register_layer()
    # side-effects populate the registry before we call run_layer().
    from overwatch_gen.lib import registry  # noqa: F401

    # Workers: add your layer import here, e.g.:
    #   import overwatch_gen.collectors.l1_proxmox   # registers "l1_proxmox"
    # (do not remove this comment block — it is the plugin hook guide)

    # OPS-272: L1-L3 collectors (Proxmox + Unifi + NetBox + VLANs + FW)
    import overwatch_gen.collectors.l1_proxmox  # noqa: F401
    import overwatch_gen.collectors.l1_unifi  # noqa: F401
    import overwatch_gen.collectors.l2_vlans  # noqa: F401
    import overwatch_gen.collectors.l3_netbox  # noqa: F401
    import overwatch_gen.collectors.l3_unifi_firewall  # noqa: F401
    # OPS-273: L4-L5 collectors (NetPol + Kyverno + UFW + Istio AuthzPolicy + PeerAuth + Keycloak)
    import overwatch_gen.collectors.l4_netpol  # noqa: F401 — registers "l4_netpol"
    import overwatch_gen.collectors.l4_kyverno  # noqa: F401 — registers "l4_kyverno"
    import overwatch_gen.collectors.l4_ufw  # noqa: F401 — registers "l4_ufw"
    import overwatch_gen.collectors.l5_istio_authz  # noqa: F401 — registers "l5_authz"
    import overwatch_gen.collectors.l5_istio_peerauth  # noqa: F401 — registers "l5_peerauth"
    import overwatch_gen.collectors.l5_keycloak  # noqa: F401 — registers "l5_keycloak"
    # OPS-274: L6-L7 collectors added by Worker-3 MR (Vault PKI + cert-manager + Traefik + Istio + OKD Routes)
    # Worker-3 (OPS-274): L6 PKI/cert-manager + L7 Traefik/Istio/OKD collectors
    import overwatch_gen.collectors.l6_vault_pki   # noqa: F401 — registers "l6_vault_pki"
    import overwatch_gen.collectors.l6_certmanager  # noqa: F401 — registers "l6_certmanager"
    import overwatch_gen.collectors.l7_traefik     # noqa: F401 — registers "l7_traefik"
    import overwatch_gen.collectors.l7_istio       # noqa: F401 — registers "l7_istio"
    import overwatch_gen.collectors.l7_okd_routes  # noqa: F401 — registers "l7_okd_routes"

    registered = registry.all_layers()

    if args.list_layers:
        if registered:
            print("Registered layers:")
            for name in registered:
                print(f"  {name}")
        else:
            print("No layers registered yet.")
            print(
                "Workers 1/2/3 add their layer imports in main.py "
                "under the '# Workers: add your layer import here' comment."
            )
        return 0

    if args.dry_run:
        log.info("Dry-run mode: output goes to stdout, no files written.")
        os.environ["OVERWATCH_GEN_DRY_RUN"] = "1"

    if args.run_all and args.layer:
        parser.error("Specify either layer name(s) or --all, not both.")

    if not args.run_all and not args.layer and not args.list_layers:
        print("No layer specified. Use --all to run all layers, or --list-layers.")
        print(f"Registered layers: {registered if registered else '(none)'}")
        parser.print_usage()
        return 1

    # --- Vault env setup (only needed when actually running layers) ---
    _set_vault_env(args)

    if args.run_all:
        layers_to_run = registered
    else:
        layers_to_run = args.layer  # list of layer names from positional args

    if not layers_to_run:
        log.warning("No layers registered. Nothing to do.")
        return 0

    log.info("Running %d layer(s): %s", len(layers_to_run), layers_to_run)
    errors = []
    for name in layers_to_run:
        if name not in registered:
            log.error(
                "Layer %r is not registered. Registered layers: %s",
                name,
                registered,
            )
            errors.append(name)
            continue
        try:
            registry.run_layer(name)
        except Exception as exc:
            log.error("Layer %r failed: %s", name, exc)
            errors.append(name)

    if errors:
        log.error("Failed/missing layers: %s", errors)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
