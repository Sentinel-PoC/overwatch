# overwatch/tools

Post-processing and analysis utilities for the Overwatch platform.
These tools consume outputs from platform checks and produce derivative artifacts.
None of these tools write to compliance-vault or modify authoritative check scripts.

---

## strength-weighted-postproc.py

**Purpose:** Produces a strength-weighted sidecar for the NIST compliance run output,
surfacing two "honest" pass-rate metrics alongside the raw 121/125 count.

**OPS-133 / HONESTY-5**

### Inputs

| File | Role |
|------|------|
| `~/repos/sentinel-cache/config-cache/nist-compliance-latest.json` | Authoritative compliance run (read-only) |
| `~/repos/overwatch/check-strength.yaml` | Strength registry (read-only) |

### Output

`~/repos/sentinel-cache/config-cache/nist-compliance-strength-weighted-latest.json`

Sidecar JSON that contains:
- `source_jsons` — provenance block with paths, timestamps, SHA-256 of both inputs
- `summary` — raw pass/fail/warn counts plus two honest pass-rate metrics
- `checks` — all 125 checks annotated with their strength and registry reason

### Honest pass-rate metrics

**`strength_weighted_pass_rate_pct`**

Each check is multiplied by its strength weight before summing:

| Strength | Weight | Rationale |
|----------|--------|-----------|
| trivial | 0.0 | Always passes on default installs; no signal |
| weak | 0.25 | File/directory existence only; low evidentiary value |
| proxy | 0.5 | Falls back to SCA score; redundant with other checks |
| misleading | 0.0 | Claims more than it verifies; excluded |
| strong | 1.0 | Runtime/API probe of actual operational state |
| untagged | 0.7 | Not in registry; treated as moderate-presumed |

Formula: `(Σ weight × is_pass) / (Σ weight × 1_per_check) × 100`

**`evidentiary_pass_rate_pct`**

Binary view: only checks NOT tagged trivial/weak/proxy/misleading are counted.
Formula: `PASSes_evidentiary / total_evidentiary × 100`

### Usage

```bash
# Run with defaults
python3 tools/strength-weighted-postproc.py

# Run with explicit paths
python3 tools/strength-weighted-postproc.py \
    --compliance-json /path/to/nist-compliance-latest.json \
    --strength-yaml   /path/to/check-strength.yaml \
    --output          /path/to/output.json
```

Exit codes: 0 on success, non-zero with stderr message on input-missing or parse failure.

### Do not run this script on iac-control
This is a post-processor that runs on the workstation against cached JSON.
The authoritative compliance check runs on iac-control via the systemd timer.
