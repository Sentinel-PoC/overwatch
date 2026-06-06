"""LLM client abstraction — Gemini (Tier 2) and Claude (Tier 3)."""

import json
import logging
import time
from typing import Optional

import requests

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models import LLMResult


def query_gemini(prompt: str, config: dict, secrets: dict,
                 log: logging.Logger) -> LLMResult:
    """Call Gemini API for Tier 2 diagnosis.

    Tries models in order from config. If a model returns 429
    (rate limited), tries the next one. This spreads free-tier
    quota across multiple models (~60 RPD combined).

    Returns LLMResult with provider/model/response/latency.
    """
    api_key = secrets.get("gemini_api_key")
    if not api_key:
        api_key = config.get("gemini", {}).get("api_key", "")
    if not api_key:
        log.warning("No Gemini API key available")
        return LLMResult(provider="gemini", model="none",
                         response_text=None, latency_ms=0, success=False)

    gemini_cfg = config.get("gemini", {})
    models = gemini_cfg.get("models", [gemini_cfg.get("model", "gemini-2.5-flash")])
    timeout = gemini_cfg.get("timeout_sec", 30)

    for model in models:
        result = _try_gemini_model(prompt, model, api_key, timeout, log)
        if result.success:
            return result
        # result failed (429 or error) — try next model

    log.warning(f"All Gemini models exhausted or failed ({len(models)} tried)")
    return LLMResult(provider="gemini", model=models[-1] if models else "none",
                     response_text=None, latency_ms=0, success=False)


def _try_gemini_model(prompt: str, model: str, api_key: str,
                      timeout: int, log: logging.Logger) -> LLMResult:
    """Try a single Gemini model. Returns LLMResult."""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={api_key}")

    t0 = time.monotonic()
    try:
        resp = requests.post(
            url,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 1024},
            },
            timeout=timeout,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        if resp.status_code == 429:
            log.info(f"Gemini {model}: rate limited, trying next model")
            return LLMResult(provider="gemini", model=model,
                             response_text=None, latency_ms=latency_ms, success=False)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            log.warning(f"Gemini {model}: no candidates returned")
            return LLMResult(provider="gemini", model=model,
                             response_text=None, latency_ms=latency_ms, success=False)
        parts = candidates[0].get("content", {}).get("parts", [])
        text = parts[0].get("text", "") if parts else None
        if text:
            log.info(f"Gemini {model}: responded ({len(text)} chars)")
            return LLMResult(provider="gemini", model=model,
                             response_text=text, latency_ms=latency_ms, success=True)
        return LLMResult(provider="gemini", model=model,
                         response_text=None, latency_ms=latency_ms, success=False)
    except requests.ConnectionError:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.warning("Gemini API unreachable")
        return LLMResult(provider="gemini", model=model,
                         response_text=None, latency_ms=latency_ms, success=False)
    except requests.Timeout:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.warning(f"Gemini {model}: timed out after {timeout}s")
        return LLMResult(provider="gemini", model=model,
                         response_text=None, latency_ms=latency_ms, success=False)
    except requests.HTTPError as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.warning(f"Gemini {model}: HTTP error {e}")
        return LLMResult(provider="gemini", model=model,
                         response_text=None, latency_ms=latency_ms, success=False)
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.warning(f"Gemini {model}: unexpected error {e}")
        return LLMResult(provider="gemini", model=model,
                         response_text=None, latency_ms=latency_ms, success=False)


def query_claude(prompt: str, system_prompt: str, config: dict,
                 secrets: dict, log: logging.Logger) -> LLMResult:
    """Send prompt to Claude API, return LLMResult.

    Used for Tier 3 (Git changes) where frontier reasoning is needed.
    """
    api_key = secrets.get("claude_api_key")
    if not api_key:
        log.warning("No Claude API key available")
        return LLMResult(provider="claude", model="none",
                         response_text=None, latency_ms=0, success=False)

    claude_cfg = config.get("claude", {})
    model = claude_cfg.get("model", "claude-sonnet-4-5-20241022")
    max_tokens = claude_cfg.get("max_tokens", 4096)

    t0 = time.monotonic()
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        resp.raise_for_status()
        data = resp.json()
        content_blocks = data.get("content", [])
        text = "".join(
            b.get("text", "") for b in content_blocks if b.get("type") == "text"
        )
        return LLMResult(provider="claude", model=model,
                         response_text=text, latency_ms=latency_ms, success=True)
    except requests.ConnectionError as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.error(f"Claude API unreachable: {e}")
        return LLMResult(provider="claude", model=model,
                         response_text=None, latency_ms=latency_ms, success=False)
    except requests.Timeout:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.error("Claude API timed out after 120s")
        return LLMResult(provider="claude", model=model,
                         response_text=None, latency_ms=latency_ms, success=False)
    except requests.HTTPError as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.error(f"Claude API error: {e}")
        return LLMResult(provider="claude", model=model,
                         response_text=None, latency_ms=latency_ms, success=False)
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.error(f"Claude API unexpected error: {e}")
        return LLMResult(provider="claude", model=model,
                         response_text=None, latency_ms=latency_ms, success=False)
