#!/usr/bin/env python3
"""
strength-weighted-postproc.py — Strength-weighted compliance post-processor
OPS-133 / HONESTY-5

Consumes:
  - nist-compliance-latest.json  (authoritative compliance run output)
  - check-strength.yaml          (strength registry — read-only)

Emits:
  - nist-compliance-strength-weighted-latest.json  (sidecar with honest pass rates)

Strength weight table (documented here per issue spec):
  trivial    = 0.0   (always passes on default installs; no signal)
  weak       = 0.25  (file/directory existence only; low evidentiary value)
  proxy      = 0.5   (falls back to SCA score, redundant with other checks)
  misleading = 0.0   (check claims more than it verifies; excluded from rate)
  strong     = 1.0   (runtime/API probe of actual operational state)
  untagged   = 0.7   (not in registry; treat as moderate-presumed)

Two complementary "honest" pass-rate metrics:
  strength_weighted_pass_rate_pct:
      (Σ weight × is_pass) / (Σ weight × 1_per_check) × 100, rounded 1 decimal.
      Gives full credit only to strong checks; trivial/misleading contribute nothing.

  evidentiary_pass_rate_pct:
      (PASSes NOT tagged trivial/weak/proxy/misleading) /
      (checks NOT tagged trivial/weak/proxy/misleading) × 100, rounded 1 decimal.
      Binary view: only checks with real evidentiary value are counted.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_COMPLIANCE = Path.home() / "repos/sentinel-cache/config-cache/nist-compliance-latest.json"
DEFAULT_STRENGTH   = Path.home() / "repos/overwatch/check-strength.yaml"
DEFAULT_OUTPUT     = Path.home() / "repos/sentinel-cache/config-cache/nist-compliance-strength-weighted-latest.json"

# ---------------------------------------------------------------------------
# Strength weight table
# ---------------------------------------------------------------------------

STRENGTH_WEIGHTS = {
    "trivial":    0.0,
    "weak":       0.25,
    "proxy":      0.5,
    "misleading": 0.0,
    "strong":     1.0,
    "untagged":   0.7,
}

# Strengths that have no evidentiary value for the evidentiary pass rate
NON_EVIDENTIARY = {"trivial", "weak", "proxy", "misleading"}


# ---------------------------------------------------------------------------
# YAML thin parser (handles the flat list structure of check-strength.yaml)
# ---------------------------------------------------------------------------

def _parse_yaml_thin(text: str) -> dict:
    """
    Minimal YAML parser for check-strength.yaml.
    Handles:
      - Comment lines (# ...)
      - Top-level key: value (e.g. "checks:")
      - List items starting with "  - key: value"
      - Continuation / extra keys indented under a list item
      - Quoted string values
    Returns a dict with key "checks" → list of dicts.
    """
    checks = []
    current: dict | None = None
    in_checks = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        # Skip blank lines and full-line comments
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level key detection
        if not line.startswith(" ") and ":" in line:
            key_part = line.split(":", 1)[0].strip()
            if key_part == "checks":
                in_checks = True
            continue

        if not in_checks:
            continue

        # New list item
        if re.match(r"^\s+- ", line):
            if current is not None:
                checks.append(current)
            current = {}
            # Extract first key-value from the "- key: value" line
            inner = re.sub(r"^\s+- ", "", line)
            if ":" in inner:
                k, _, v = inner.partition(":")
                current[k.strip()] = _unquote(v.strip())
            continue

        # Continuation key inside list item
        if current is not None and ":" in line:
            # Only accept simple scalar lines (no nested block items like data-sources)
            if not re.match(r"^\s+- ", line):
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip()
                if v and not v.startswith("-") and k:
                    current[k.strip()] = _unquote(v)

    if current is not None:
        checks.append(current)

    return {"checks": checks}


def _unquote(s: str) -> str:
    """Remove surrounding quotes from a YAML scalar value."""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


# ---------------------------------------------------------------------------
# YAML loader (prefer PyYAML, fall back to thin parser)
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        return _parse_yaml_thin(text)


# ---------------------------------------------------------------------------
# SHA-256 helper
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Registry audit date extractor
# ---------------------------------------------------------------------------

def extract_registry_audit_date(path: Path) -> str | None:
    """
    Look for a comment like '# Generated from research audit YYYY-MM-DD'
    in the YAML file header.
    """
    pattern = re.compile(r"#.*audit.*?(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
    try:
        with open(path, encoding="utf-8") as fh:
            for _ in range(30):  # Only scan first 30 lines
                line = fh.readline()
                if not line:
                    break
                m = pattern.search(line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Core processor
# ---------------------------------------------------------------------------

def process(compliance_path: Path, strength_path: Path, output_path: Path) -> None:
    # --- Load inputs ---
    if not compliance_path.exists():
        print(f"ERROR: compliance JSON not found: {compliance_path}", file=sys.stderr)
        sys.exit(1)
    if not strength_path.exists():
        print(f"ERROR: strength YAML not found: {strength_path}", file=sys.stderr)
        sys.exit(1)

    try:
        compliance_data = json.loads(compliance_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: compliance JSON parse failed: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        strength_data = load_yaml(strength_path)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: strength YAML parse failed: {exc}", file=sys.stderr)
        sys.exit(1)

    # --- Build registry lookup: (control, check_id) → {strength, reason} ---
    registry: dict[tuple[str, str], dict] = {}
    raw_registry_entries = strength_data.get("checks") or []
    for entry in raw_registry_entries:
        ctrl = entry.get("control", "").strip()
        chk  = entry.get("check_id", "").strip()
        if ctrl and chk:
            registry[(ctrl, chk)] = {
                "strength": entry.get("strength", "untagged").strip(),
                "reason":   entry.get("reason", ""),
            }

    # --- Annotate compliance checks ---
    raw_checks = compliance_data.get("checks", [])
    annotated_checks = []
    for raw in raw_checks:
        control = raw.get("control", "").strip()
        check_id = raw.get("check", "").strip()
        status   = raw.get("status", "UNKNOWN").strip()
        detail   = raw.get("detail", "")

        reg_entry = registry.get((control, check_id))
        if reg_entry:
            strength = reg_entry["strength"]
            reason   = reg_entry["reason"]
            item = {
                "control":          control,
                "check":            check_id,
                "status":           status,
                "detail":           detail,
                "strength":         strength,
                "strength_reason":  reason,
            }
        else:
            strength = "untagged"
            item = {
                "control": control,
                "check":   check_id,
                "status":  status,
                "detail":  detail,
                "strength": "untagged",
            }

        annotated_checks.append(item)

    # --- Tally by strength ---
    strength_tallies: dict[str, dict[str, int]] = {
        s: {"total": 0, "pass": 0, "fail": 0, "warn": 0}
        for s in STRENGTH_WEIGHTS
    }

    raw_pass = raw_fail = raw_warn = 0

    for item in annotated_checks:
        status   = item["status"]
        strength = item["strength"]

        if strength not in strength_tallies:
            strength_tallies[strength] = {"total": 0, "pass": 0, "fail": 0, "warn": 0}

        strength_tallies[strength]["total"] += 1

        if status == "PASS":
            raw_pass += 1
            strength_tallies[strength]["pass"] += 1
        elif status == "FAIL":
            raw_fail += 1
            strength_tallies[strength]["fail"] += 1
        elif status == "WARN":
            raw_warn += 1
            strength_tallies[strength]["warn"] += 1

    total_checks = len(annotated_checks)

    # --- Compute strength-weighted pass rate ---
    weighted_pass_sum   = 0.0
    weighted_total_sum  = 0.0

    for item in annotated_checks:
        w = STRENGTH_WEIGHTS.get(item["strength"], STRENGTH_WEIGHTS["untagged"])
        weighted_total_sum += w
        if item["status"] == "PASS":
            weighted_pass_sum += w

    if weighted_total_sum > 0:
        sw_rate = round(weighted_pass_sum / weighted_total_sum * 100, 1)
    else:
        sw_rate = 0.0

    # --- Compute evidentiary pass rate ---
    evid_pass  = sum(1 for c in annotated_checks
                     if c["status"] == "PASS" and c["strength"] not in NON_EVIDENTIARY)
    evid_total = sum(1 for c in annotated_checks
                     if c["strength"] not in NON_EVIDENTIARY)

    if evid_total > 0:
        evid_rate = round(evid_pass / evid_total * 100, 1)
    else:
        evid_rate = 0.0

    # --- Registry tagged vs. untagged ---
    registry_tagged = sum(1 for c in annotated_checks if c["strength"] != "untagged")
    untagged_count  = total_checks - registry_tagged

    # --- Build by_strength summary (only non-zero categories in canonical order) ---
    by_strength = {}
    for s in STRENGTH_WEIGHTS:  # canonical order
        t = strength_tallies.get(s, {"total": 0, "pass": 0, "fail": 0, "warn": 0})
        by_strength[s] = {
            "total": t["total"],
            "pass":  t["pass"],
            "fail":  t["fail"],
            "warn":  t["warn"],
        }

    # --- Assemble output ---
    audit_date = extract_registry_audit_date(strength_path)
    registry_audit_date_field = audit_date if audit_date else "UNKNOWN"

    output = {
        "source_jsons": {
            "compliance": {
                "path":      str(compliance_path.resolve()),
                "timestamp": compliance_data.get("timestamp", "UNKNOWN"),
                "sha256":    sha256_file(compliance_path),
            },
            "strength_registry": {
                "path":                str(strength_path.resolve()),
                "sha256":              sha256_file(strength_path),
                "registry_audit_date": registry_audit_date_field,
            },
        },
        "generated_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_by":  "strength-weighted-postproc.py (OPS-133)",

        "summary": {
            "total_checks":   total_checks,
            "raw_pass":       raw_pass,
            "raw_fail":       raw_fail,
            "raw_warn":       raw_warn,
            "raw_pass_rate_pct": round(raw_pass / total_checks * 100) if total_checks else 0,

            "registry_tagged_checks": registry_tagged,
            "untagged_checks":        untagged_count,
            "by_strength":            by_strength,

            "strength_weighted_pass_rate_pct": sw_rate,
            "evidentiary_pass_rate_pct":        evid_rate,
        },

        "checks": annotated_checks,
    }

    # --- Write output ---
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")

    print(f"Wrote {output_path}")
    print(f"  total_checks:                    {total_checks}")
    print(f"  raw_pass / raw_fail / raw_warn:  {raw_pass} / {raw_fail} / {raw_warn}")
    print(f"  registry_tagged_checks:          {registry_tagged}")
    print(f"  strength_weighted_pass_rate_pct: {sw_rate}")
    print(f"  evidentiary_pass_rate_pct:       {evid_rate}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strength-weighted compliance post-processor (OPS-133)",
    )
    parser.add_argument(
        "--compliance-json",
        type=Path,
        default=DEFAULT_COMPLIANCE,
        help="Path to nist-compliance-latest.json",
    )
    parser.add_argument(
        "--strength-yaml",
        type=Path,
        default=DEFAULT_STRENGTH,
        help="Path to check-strength.yaml",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path for output sidecar JSON",
    )
    args = parser.parse_args()

    process(args.compliance_json, args.strength_yaml, args.output)


if __name__ == "__main__":
    main()
