# overwatch-gen

L1-L7 architecture audit vault generator for the Overwatch platform.

Produces an Obsidian-openable vault (`architecture-vault/`) with per-layer
Markdown, tables, and `.canvas` diagrams sourced from live platform data via
Vault, Kubernetes API, and Unifi/Proxmox APIs.

## Architecture

```
overwatch-gen/
  main.py          Entry point (python3 -m overwatch_gen.main)
  lib/
    vault_client.py  Vault AppRole + KV read
    render.py        Jinja2 + deterministic file writes
    canvas.py        Obsidian .canvas JSON builder
    determinism.py   sorted_json, stable_hash, freeze_time
    registry.py      Layer plugin registry (Workers 1/2/3 hook here)
  layers/           (created by Workers 1/2/3)
    l1_physical.py
    l2_datalink.py
    ...
  tests/
    test_determinism.py
    test_registry.py
```

## Quick start

```bash
pip install -e ".[dev]"
python3 -m pytest tests/ -v
python3 -m overwatch_gen.main --help
python3 -m overwatch_gen.main --all --dry-run
```

## Environment variables

| Variable                 | Description                                      |
|--------------------------|--------------------------------------------------|
| `VAULT_ADDR`             | Vault server URL (default: https://192.168.12.206:8200) |
| `VAULT_TOKEN`            | Direct Vault token (skips AppRole)               |
| `VAULT_APPROLE_ROLE_ID`  | AppRole role_id                                  |
| `VAULT_APPROLE_SECRET_ID`| AppRole secret_id                                |
| `VAULT_SKIP_VERIFY`      | Set `true` to skip TLS verification (self-signed cert) |
| `OVERWATCH_GEN_DRY_RUN`  | Set `1` to suppress file writes (stdout only)    |

## Adding a layer (Workers 1/2/3)

1. Create `overwatch_gen/layers/lN_name.py`:

```python
from overwatch_gen.lib import registry
from overwatch_gen.lib.render import write_deterministic
from pathlib import Path

VAULT_PATH = "architecture-vault"

def collect() -> dict:
    """Fetch raw data. Return a dict."""
    return {"example": "data"}

def render(data: dict) -> None:
    """Write output files to architecture-vault/0N-LN-name/."""
    content = f"# L1 Physical\n\n{data}\n"
    write_deterministic(
        Path(VAULT_PATH) / "01-L1-physical" / "nodes.md",
        content,
    )

registry.register_layer("l1", collect, render)
```

2. Add one import line in `main.py` under the `# Workers: add your layer import here` comment:

```python
import overwatch_gen.layers.l1_physical   # registers "l1"
```

## d2 diagram generation — operator action required

The CI workflow installs the `d2` CLI binary from the Terrastruct GitHub
releases. If the CI runner does not have internet access, a one-time
operator action is needed:

```bash
# On the self-hosted runner (iac-control or equivalent):
curl -fsSL https://d2lang.com/install.sh | sh -s -- --tty-check
# Verify:
d2 --version
```

The workflow step caches the binary at `~/.local/bin/d2` after first install.
If the runner image pre-installs `d2`, remove the install step from the workflow.

## Determinism guarantees

All output files are written via `write_deterministic()` which:
- Normalizes line endings to `\n`
- Ensures trailing newline
- Skips the write if content is unchanged (no-op on no-change)

All JSON output uses `sorted_json()` (sort_keys=True, indent=2).

The only file expected to change every run is `00-meta/generated-at.md`.
All other files should produce identical bytes for identical platform state.
