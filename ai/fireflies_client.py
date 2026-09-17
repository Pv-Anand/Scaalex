"""Direct integration with Fireflies' own GraphQL API.

This is separate from any Fireflies access Claude might have in a chat
session - the deployed app runs standalone and authenticates to Fireflies
itself with its own API key (FIREFLIES_API_KEY), the same way it talks to
Anthropic and OpenAI directly rather than through a session-bound tool.

API reference: https://docs.fireflies.ai/graphql-api
"""
from datetime import datetime, timezone

import requests
from flask import current_app

FIREFLIES_API_URL = "https://api.fireflies.ai/graphql"


class FirefliesConfigError(Exception):
    """Raised when FIREFLIES_API_KEY is not configured or malformed."""


class FirefliesRequestError(Exception):
    """Raised when a Fireflies API call fails."""


def _get_api_key():
    api_key = current_app.config.get("FIREFLIES_API_KEY")
    if not api_key:
        raise FirefliesConfigError(
            "FIREFLIES_API_KEY is not configured. Add it to your .env file to enable "
            "meeting sync from Fireflies."
        )
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError:
        # API keys go into an HTTP header, which requires pure ASCII. A key with
        # invisible non-ASCII characters (e.g. copied from a masked dashboard
        # display instead of the real value) fails deep in the HTTP layer with
        # a cryptic error - catch it here with something actionable instead.
        raise FirefliesConfigError(
            "FIREFLIES_API_KEY contains invalid (non-ASCII) characters - it was likely "
            "corrupted during copy/paste. Delete and re-enter it from the original source."
        )
    return api_key


def _graphql_request(query: str, variables: dict):
    api_key = _get_api_key()
    try:
        response = requests.post(
            FIREFLIES_API_URL,
            json={"query": query, "variables": variables},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise FirefliesRequestError(f"Fireflies API request failed: {exc}") from exc

    if response.status_code != 200:
        raise FirefliesRequestError(f"Fireflies API returned {response.status_code}: {response.text[:300]}")

    data = response.json()
    if data.get("errors"):
        raise FirefliesRequestError(f"Fireflies API error: {data['errors']}")
    return data.get("data") or {}


LIST_QUERY = """
query Transcripts($limit: Int, $skip: Int) {
  transcripts(limit: $limit, skip: $skip) {
    id
    title
    date
    duration
    meeting_attendees { name email displayName }
  }
}
"""

DETAIL_QUERY = """
query Transcript($transcriptId: String!) {
  transcript(id: $transcriptId) {
    id
    title
    date
    duration
    sentences { text speaker_name }
    summary { short_overview action_items keywords }
    meeting_attendees { name email displayName }
  }
}
"""


def fetch_recent_transcripts(limit: int = 25, skip: int = 0):
    """List recent meetings (metadata only, no transcript text)."""
    data = _graphql_request(LIST_QUERY, {"limit": limit, "skip": skip})
    return data.get("transcripts") or []


def fetch_transcript_detail(transcript_id: str):
    """Full detail for one meeting, including the transcript text."""
    data = _graphql_request(DETAIL_QUERY, {"transcriptId": transcript_id})
    transcript = data.get("transcript")
    if not transcript:
        raise FirefliesRequestError(f"Transcript {transcript_id} was not found.")
    return transcript


def parse_fireflies_date(value):
    """Fireflies has returned both epoch-millisecond and ISO timestamps in
    different API versions - handle either rather than assume one."""
    if value is None:
        return datetime.utcnow()
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).replace(tzinfo=None)
        text = str(value)
        if text.isdigit():
            return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc).replace(tzinfo=None)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, OverflowError, OSError):
        return datetime.utcnow()


def extract_participant_names(meeting_attendees):
    names = []
    for attendee in meeting_attendees or []:
        name = attendee.get("displayName") or attendee.get("name") or attendee.get("email")
        if name and name not in names:
            names.append(name)
    return names


def build_transcript_text(sentences):
    lines = []
    for s in sentences or []:
        speaker = s.get("speaker_name") or "Speaker"
        text = (s.get("text") or "").strip()
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)
