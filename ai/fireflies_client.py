"""Direct integration with Fireflies' own GraphQL API.

This is separate from any Fireflies access Claude might have in a chat
session - the deployed app runs standalone and authenticates to Fireflies
itself with its own API key (FIREFLIES_API_KEY), the same way it talks to
Anthropic and OpenAI directly rather than through a session-bound tool.

API reference: https://docs.fireflies.ai/graphql-api
"""
import re
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
    summary { short_overview short_summary overview gist bullet_gist action_items keywords }
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


def pick_overview(summary):
    """The call summary as text. Fireflies has moved the summary between
    fields over time (short_overview is now usually empty), so take the first
    one that has content, fullest first."""
    summary = summary or {}
    for field in ("short_summary", "overview", "short_overview", "bullet_gist", "gist"):
        value = summary.get(field)
        if isinstance(value, list):
            value = "\n".join(str(v) for v in value if v)
        if value and str(value).strip():
            return str(value).strip()
    return ""


def pick_action_items(summary):
    value = (summary or {}).get("action_items")
    if isinstance(value, list):
        value = "\n".join(str(v) for v in value if v)
    return (value or "").strip()


def _strip_marks(line):
    """Drop leading emoji and markdown from a line: '💰 **Status:** x' -> 'Status: x'."""
    line = re.sub(r"^[^\w]+", "", line.strip())
    return line.replace("**", "").strip()


def pick_highlights(summary):
    """Short key points, one per line. Fireflies' bullet_gist is exactly this
    (label: point). If a call has none, fall back to the first sentences of the
    summary so the card is never empty."""
    summary = summary or {}
    raw = summary.get("bullet_gist")
    if isinstance(raw, list):
        raw = "\n".join(str(v) for v in raw if v)
    lines = [_strip_marks(l) for l in (raw or "").splitlines() if l.strip()]
    lines = [l for l in lines if l]
    if lines:
        return "\n".join(lines[:8])
    text = pick_overview(summary)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    return "\n".join(sentences[:4])


def parse_highlights(text):
    """[(label, point)] from stored highlight lines ('Label: point')."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        label, sep, rest = line.partition(": ")
        if sep and len(label) <= 40:
            out.append((label, rest.strip()))
        else:
            out.append(("", line))
    return out


def parse_action_items(text):
    """[(person, [tasks])] from Fireflies' '**Name**\\ntask (12:30)' format."""
    groups, current = [], None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\*\*(.+?)\*\*:?$", line)
        if m:
            current = (m.group(1).strip(), [])
            groups.append(current)
            continue
        task = re.sub(r"\s*\(\d{1,2}:\d{2}(?::\d{2})?\)\s*$", "", line).lstrip("-* ").strip()
        if not task:
            continue
        if current is None:
            current = ("", [])
            groups.append(current)
        current[1].append(task)
    return groups
