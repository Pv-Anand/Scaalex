"""Thin wrapper around the Anthropic API used by every /ai/* service.

Centralizing this here means every AI call:
- uses the same model/key configuration from Config
- fails with one consistent, catchable error (AIConfigError / AIRequestError)
- can force structured JSON output via tool-use, so extraction/overview/email
  generation never has to hope the model emitted well-formed JSON in prose.
"""
import json

from flask import current_app


class AIConfigError(Exception):
    """Raised when a required API key is missing."""


class AIRequestError(Exception):
    """Raised when the underlying API call fails."""


def _get_client():
    api_key = current_app.config.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AIConfigError(
            "ANTHROPIC_API_KEY is not configured. Add it to your .env file to enable "
            "AI extraction, leadership overviews, and email drafting."
        )
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def call_structured(system_prompt: str, user_prompt: str, json_schema: dict, tool_name: str = "emit_result"):
    """Call Claude and force a structured JSON response matching json_schema via tool-use."""
    client = _get_client()
    model = current_app.config.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    tool = {
        "name": tool_name,
        "description": "Emit the structured result for this task.",
        "input_schema": json_schema,
    }

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4000,
            system=system_prompt,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:  # noqa: BLE001 - surface any SDK/network error uniformly
        raise AIRequestError(f"Anthropic API request failed: {exc}") from exc

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input

    raise AIRequestError("Anthropic response did not include the expected structured tool output.")


def call_text(system_prompt: str, user_prompt: str) -> str:
    client = _get_client()
    model = current_app.config.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    try:
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        raise AIRequestError(f"Anthropic API request failed: {exc}") from exc

    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip()
