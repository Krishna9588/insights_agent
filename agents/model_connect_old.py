"""
model_connect.py
================
Unified LLM Gateway — single file to control which model all agents use.

Supports:
  - Gemini  (Google GenAI)
  - Claude  (Anthropic)
  - ChatGPT (OpenAI)

Public API:
  from model_connect import model_connect

  response = model_connect(
      prompt="Your prompt here",
      system="Optional system prompt",
      json_mode=True,           # Wrap output instructions for clean JSON
      model_override=None,      # Force a specific model for one call
  )

Change the DEFAULT_PROVIDER / DEFAULT_MODEL below to switch the
entire project to a different LLM in one edit.
"""

from __future__ import annotations

import os
import json
import time
import logging
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("model_connect")

# ─────────────────────────────────────────────────────────────────────────────
# ✏️  CHANGE THESE TO SWITCH THE ENTIRE PROJECT'S LLM
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_PROVIDER = "gemini"          # "gemini" | "claude" | "openai"
DEFAULT_MODEL    = "gemini-2.5-flash"

# Per-provider model shortcuts  (used when model_override is just a short name)
MODEL_MAP = {
    "gemini": {
        "fast"    : "gemini-2.0-flash",
        "default" : "gemini-2.5-flash",
        "pro"     : "gemini-2.5-pro",
    },
    "claude": {
        "fast"    : "claude-haiku-4-5-20251001",
        "default" : "claude-sonnet-4-6",
        "pro"     : "claude-opus-4-6",
    },
    "openai": {
        "fast"    : "gpt-4o-mini",
        "default" : "gpt-4o",
        "pro"     : "o1-preview",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# PROVIDER IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────────────────────

def _call_gemini(prompt: str, system: Optional[str], model: str, json_mode: bool) -> str:
    """
    Call Google Gemini via google-genai SDK.
    Install: pip install google-genai
    Env var:  GEMINI_API_KEY
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ImportError("Install google-genai: pip install google-genai")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set in .env")

    client = genai.Client(api_key=api_key)

    full_prompt = prompt
    if json_mode:
        full_prompt = (
            "CRITICAL: Return ONLY valid JSON. No markdown fences, no explanation.\n\n"
            + prompt
        )
    if system:
        full_prompt = f"{system}\n\n{full_prompt}"

    config = types.GenerateContentConfig()

    response = client.models.generate_content(
        model=model,
        contents=full_prompt,
        config=config,
    )

    return response.text.strip()


def _call_claude(prompt: str, system: Optional[str], model: str, json_mode: bool) -> str:
    """
    Call Anthropic Claude via anthropic SDK.
    Install: pip install anthropic
    Env var:  ANTHROPIC_API_KEY
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError("Install anthropic: pip install anthropic")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = system or "You are a helpful AI assistant."
    if json_mode:
        system_prompt += "\nCRITICAL: Return ONLY valid JSON. No markdown fences, no explanation."

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()


def _call_openai(prompt: str, system: Optional[str], model: str, json_mode: bool) -> str:
    """
    Call OpenAI ChatGPT via openai SDK.
    Install: pip install openai
    Env var:  OPENAI_API_KEY
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("Install openai: pip install openai")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set in .env")

    client = OpenAI(api_key=api_key)

    system_msg = system or "You are a helpful AI assistant."
    if json_mode:
        system_msg += "\nCRITICAL: Return ONLY valid JSON. No markdown fences, no explanation."

    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": prompt},
        ],
        max_tokens=4096,
    )
    # Native JSON mode for supported models
    if json_mode and model in ("gpt-4o", "gpt-4o-mini", "gpt-4-turbo"):
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content.strip()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PUBLIC FUNCTION  — this is what all agents call
# ─────────────────────────────────────────────────────────────────────────────

def model_connect(
    prompt          : str,
    system          : Optional[str] = None,
    json_mode       : bool = False,
    model_override  : Optional[str] = None,
    provider_override: Optional[str] = None,
    retries         : int = 2,
    retry_delay_s   : float = 3.0,
) -> str:
    """
    Universal LLM call. All agents use this function.

    Args:
        prompt:            The user message / task description.
        system:            System prompt (optional).
        json_mode:         If True, instructs the model to return pure JSON.
        model_override:    Override the default model. Can be a full model
                           name ("gemini-2.5-flash") or a shortcut
                           ("fast" | "default" | "pro").
        provider_override: Override the default provider for one call.
                           ("gemini" | "claude" | "openai")
        retries:           Number of retry attempts on failure.
        retry_delay_s:     Seconds to wait between retries.

    Returns:
        Raw string response from the LLM.

    Raises:
        RuntimeError if all retries fail.

    Example:
        from model_connect import model_connect
        text = model_connect("Summarize this data", json_mode=True)
        data = json.loads(text)
    """

    provider = (provider_override or DEFAULT_PROVIDER).lower()
    model    = model_override or DEFAULT_MODEL

    # Resolve shortcut names  ("fast" → actual model string)
    if model in MODEL_MAP.get(provider, {}):
        model = MODEL_MAP[provider][model]

    log.info(f"[model_connect] provider={provider} model={model} json={json_mode}")

    _CALLERS = {
        "gemini": _call_gemini,
        "claude": _call_claude,
        "openai": _call_openai,
    }

    caller = _CALLERS.get(provider)
    if not caller:
        raise ValueError(f"Unknown provider '{provider}'. Choose: gemini, claude, openai")

    last_error = None
    for attempt in range(1, retries + 2):  # retries + 1 total attempts
        try:
            raw = caller(prompt, system, model, json_mode)

            # Strip accidental markdown fences even when json_mode=True
            if json_mode:
                raw = _strip_fences(raw)

            return raw

        except Exception as e:
            last_error = e
            log.warning(f"[model_connect] Attempt {attempt} failed: {e}")
            if attempt <= retries:
                time.sleep(retry_delay_s)

    raise RuntimeError(
        f"[model_connect] All {retries + 1} attempts failed. "
        f"Provider: {provider}, Model: {model}. Last error: {last_error}"
    )


def model_connect_json(
    prompt          : str,
    system          : Optional[str] = None,
    model_override  : Optional[str] = None,
    provider_override: Optional[str] = None,
) -> dict:
    """
    Convenience wrapper: calls model_connect with json_mode=True and
    automatically parses + returns the dict.

    Example:
        from model_connect import model_connect_json
        data = model_connect_json("List top 3 risks as JSON array under key 'risks'")
    """
    raw = model_connect(
        prompt=prompt,
        system=system,
        json_mode=True,
        model_override=model_override,
        provider_override=provider_override,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f"[model_connect_json] JSON parse error: {e}\nRaw:\n{raw[:500]}")
        return {"error": "JSON parse failed", "raw": raw}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # drop first line (```json or ```)
        lines = lines[1:]
        # drop last ``` line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


# ─────────────────────────────────────────────────────────────────────────────
# QUICK SMOKE TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("=" * 55)
    print(f"  model_connect — active provider : {DEFAULT_PROVIDER}")
    print(f"  active model    : {DEFAULT_MODEL}")
    print("=" * 55)

    test_prompt = 'Return JSON: {"status": "ok", "message": "model_connect is working"}'
    try:
        result = model_connect(test_prompt, json_mode=True)
        print("\n✅ model_connect OK")
        print(f"   Raw response: {result}")
    except Exception as e:
        print(f"\n❌ model_connect FAILED: {e}")
        sys.exit(1)