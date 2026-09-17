"""Voice recording transcription (/ai/transcribe).

Uses OpenAI's Whisper API. Kept separate from the Anthropic client because
Claude models do not accept raw audio. If OPENAI_API_KEY is not configured,
callers should catch AIConfigError and fall back to manual transcript entry.
"""
import requests
from flask import current_app

from ai.anthropic_client import AIConfigError, AIRequestError


def transcribe_audio_file(file_path: str) -> str:
    api_key = current_app.config.get("OPENAI_API_KEY")
    if not api_key:
        raise AIConfigError(
            "OPENAI_API_KEY is not configured. Add it to your .env file to enable "
            "automatic transcription, or type the transcript manually below."
        )

    model = current_app.config.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1")

    try:
        with open(file_path, "rb") as f:
            response = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": model},
                files={"file": f},
                timeout=180,
            )
    except requests.RequestException as exc:
        raise AIRequestError(f"Transcription request failed: {exc}") from exc

    if response.status_code != 200:
        raise AIRequestError(f"Transcription failed ({response.status_code}): {response.text[:300]}")

    data = response.json()
    return data.get("text", "").strip()
