"""Data models for sentinel-agent.

Shared across all modules — no imports from agent.py.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class Tier(str, Enum):
    SKIP = "skip"          # Tier 1: ArgoCD auto-sync handles it
    OPERATIONAL = "tier2"  # Tier 2: autonomous fix, no code change
    GIT_CHANGE = "tier3"   # Tier 3: requires Git branch + PR
    ESCALATE = "escalate"  # Outside authority


class SignalSource(str, Enum):
    PLANE = "plane"
    WAZUH = "wazuh"
    ARGOCD = "argocd"


@dataclass
class Signal:
    """A detected problem from any input source."""
    source: SignalSource
    source_id: str              # unique ID from the source system
    summary: str                # human-readable one-liner
    severity: int = 0           # 0-15 (Wazuh scale, mapped for others)
    tier: Optional[Tier] = None # set by triage
    raw_data: dict = field(default_factory=dict)
    plane_issue_id: Optional[str] = None  # if from Plane or linked


@dataclass
class LLMResult:
    """Result of an LLM query — captures everything for audit."""
    provider: str               # "gemini" or "claude"
    model: str                  # specific model name
    response_text: Optional[str]  # raw response (None on failure)
    latency_ms: int             # round-trip time
    success: bool               # got a usable response


@dataclass
class ActionResult:
    """Result of an attempted remediation action."""
    signal: Signal
    action_taken: str
    success: bool
    evidence: str = ""
    error: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
