"""Route signals to the appropriate LLM or rules-only diagnosis."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models import Signal, Tier, LLMResult
from triage import rules_only_diagnosis
from research import emit_research_event
from llm.client import query_gemini, query_claude
from llm.prompt import load_system_prompt


def get_diagnosis(signal: Signal, config: dict, secrets: dict,
                  log: logging.Logger) -> Tier:
    """Route signal to appropriate diagnosis method.

    Routing table (architecture doc Section 4.6):
    - Triage/classification: rules-based (no LLM) — pattern match first
    - Tier 2 (operational): Gemini API (free tier) → rules-only fallback
    - Tier 3 (Git changes): Claude API → escalate to Jim fallback
    """
    # Step 1: Try rules-only first — handles known patterns without LLM
    rules_tier = rules_only_diagnosis(signal)

    # SKIP is always trusted (ArgoCD auto-sync handles it)
    if rules_tier == Tier.SKIP:
        return rules_tier

    # Known OPERATIONAL patterns don't need LLM confirmation
    if rules_tier == Tier.OPERATIONAL and _is_known_pattern(signal):
        return rules_tier

    # Step 2: For anything rules can't confidently handle, try LLM
    # This includes: unknown signals (rules default → ESCALATE),
    # ambiguous OPERATIONAL, and GIT_CHANGE signals

    if rules_tier in (Tier.OPERATIONAL, Tier.ESCALATE):
        # OPS-199 short-circuit: if config has no Gemini models, skip the
        # call entirely (avoids noise rows in llm-decisions.jsonl with
        # model="none" latency_ms=0). Falls through to rules-only.
        # Set gemini.models: [] in config.yaml to opt out.
        gemini_models = config.get("gemini", {}).get("models", [])
        if not gemini_models:
            log.debug(f"Gemini disabled (gemini.models=[]); using rules-only "
                      f"({rules_tier.value}) for signal {signal.source_id}")
            return rules_tier

        # Try Gemini — it may recognize an actionable pattern rules missed,
        # or confirm the escalation is warranted
        prompt = _build_diagnosis_prompt(signal)
        llm_result = query_gemini(prompt, config, secrets, log)
        tier = _parse_llm_tier(llm_result.response_text, log) if llm_result.success else None

        _log_llm_decision(config, signal, llm_result, prompt,
                          parsed_tier=tier, rules_tier=rules_tier, log=log)

        if tier is not None:
            log.info(f"Gemini classified signal as {tier.value} "
                     f"(rules said {rules_tier.value})")
            return tier

        # Gemini failed — fall back to rules result
        emit_research_event(config, "gemini_fallback", {
            "signal": signal.summary,
            "rules_result": rules_tier.value,
        }, signal.plane_issue_id, log=log)
        log.warning(f"Gemini unavailable, using rules-only ({rules_tier.value})")
        return rules_tier

    if rules_tier == Tier.GIT_CHANGE:
        # OPS-199 short-circuit: if no claude_api_key in secrets, skip the
        # call entirely (avoids noise row in llm-decisions.jsonl with
        # model="none" latency_ms=0). Falls through to ESCALATE — Tier 3
        # Git-change signals end up as raw Plane issues for operator triage.
        if not secrets.get("claude_api_key"):
            log.debug(f"Claude disabled (claude_api_key absent); escalating "
                      f"Tier 3 signal {signal.source_id} for operator triage")
            return Tier.ESCALATE

        # Try Claude for Tier 3 diagnosis
        system_prompt = load_system_prompt()
        prompt = _build_diagnosis_prompt(signal)
        llm_result = query_claude(prompt, system_prompt, config, secrets, log)
        tier = _parse_llm_tier(llm_result.response_text, log) if llm_result.success else None

        _log_llm_decision(config, signal, llm_result, prompt,
                          parsed_tier=tier, rules_tier=rules_tier, log=log)

        if tier is not None:
            return tier

        # Claude failed — escalate (don't guess on Git changes)
        log.warning("Claude unavailable, escalating Tier 3 signal")
        return Tier.ESCALATE

    return rules_tier


def _is_known_pattern(signal: Signal) -> bool:
    """Check if the signal matches a well-understood pattern.

    Uses word-set matching for multi-word patterns so that
    interpolated names (e.g. 'Wazuh agent gitlab-server (ID 008)
    is disconnected') still match 'wazuh agent disconnected'.
    """
    # Single-token patterns: exact substring match
    single_token = [
        "crashloopbackoff",
        "imagepullbackoff",
        "outofsync",
        "degraded",
    ]
    # Multi-word patterns: all words must appear (order-independent)
    multi_word = [
        ["wazuh", "agent", "offline"],
        ["wazuh", "agent", "disconnected"],
        ["wazuh", "agent", "never", "connected"],
        ["vault", "sealed"],
        ["brute", "force"],
        ["authentication", "failure"],
        ["multiple", "auth"],
    ]
    summary_lower = signal.summary.lower()
    if any(p in summary_lower for p in single_token):
        return True
    summary_words = set(summary_lower.split())
    return any(all(w in summary_words for w in words) for words in multi_word)


def _build_diagnosis_prompt(signal: Signal) -> str:
    """Build a diagnosis prompt for an LLM."""
    return f"""\
Diagnose this infrastructure signal and recommend an action tier.

Signal source: {signal.source.value}
Signal ID: {signal.source_id}
Summary: {signal.summary}
Severity: {signal.severity}
Raw data: {json.dumps(signal.raw_data, indent=2)}

Respond with JSON:
{{"tier": "tier2"|"tier3"|"escalate", "diagnosis": "...", "action": "..."}}
"""


def _parse_llm_tier(response: str, log: logging.Logger) -> Optional[Tier]:
    """Parse tier from LLM JSON response."""
    if not response:
        return None
    try:
        text = response.strip()
        if "```" in text:
            start = text.index("```") + 3
            if text[start:start + 4] == "json":
                start += 4
            # Handle truncated responses where closing ``` is missing
            try:
                end = text.index("```", start)
            except ValueError:
                end = len(text)
            text = text[start:end].strip()

        # Try to parse JSON — if truncated, extract just the tier field
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Response may be truncated — try to find tier field
            import re
            tier_match = re.search(r'"tier"\s*:\s*"(tier2|tier3|escalate|skip)"', text)
            if tier_match:
                data = {"tier": tier_match.group(1)}
            else:
                raise
        tier_str = data.get("tier", "").lower()
        tier_map = {
            "tier2": Tier.OPERATIONAL,
            "tier3": Tier.GIT_CHANGE,
            "escalate": Tier.ESCALATE,
            "skip": Tier.SKIP,
        }
        return tier_map.get(tier_str)
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        log.warning(f"Failed to parse LLM response as JSON: {e}")
        return None


def _extract_llm_reasoning(response: Optional[str]) -> dict:
    """Extract diagnosis and action from LLM response for logging."""
    if not response:
        return {}
    try:
        text = response.strip()
        if "```" in text:
            start = text.index("```") + 3
            if text[start:start + 4] == "json":
                start += 4
            try:
                end = text.index("```", start)
            except ValueError:
                end = len(text)
            text = text[start:end].strip()
        data = json.loads(text)
        return {
            "diagnosis": data.get("diagnosis", ""),
            "action": data.get("action", ""),
        }
    except Exception:
        return {"raw_excerpt": response[:500] if response else ""}


def _log_llm_decision(config: dict, signal: Signal, llm_result: LLMResult,
                      prompt: str, parsed_tier: Optional[Tier],
                      rules_tier: Tier, log: logging.Logger):
    """Write a structured record to llm-decisions.jsonl.

    Every LLM call — success or failure — gets logged here.
    """
    log_dir = Path(config["agent"].get("log_dir", "/var/log/sentinel-agent"))
    log_path = log_dir / "llm-decisions.jsonl"

    reasoning = _extract_llm_reasoning(llm_result.response_text)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signal_id": signal.source_id,
        "signal_source": signal.source.value,
        "signal_summary": signal.summary,
        "signal_severity": signal.severity,
        "llm_provider": llm_result.provider,
        "llm_model": llm_result.model,
        "prompt_text": prompt,
        "response_text": llm_result.response_text,
        "parsed_tier": parsed_tier.value if parsed_tier else None,
        "rules_tier": rules_tier.value,
        "agreed_with_rules": (parsed_tier == rules_tier) if parsed_tier else None,
        "diagnosis": reasoning.get("diagnosis", ""),
        "recommended_action": reasoning.get("action", ""),
        "latency_ms": llm_result.latency_ms,
        "success": llm_result.success,
    }

    line = json.dumps(entry, separators=(",", ":")) + "\n"

    try:
        with open(log_path, "a") as f:
            f.write(line)
    except Exception as e:
        if log:
            log.error(f"Failed to write LLM decision log: {e}")
