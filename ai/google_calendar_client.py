"""Direct integration with the Google Calendar API for upcoming meetings.

Like ai/fireflies_client.py, this is separate from any calendar access
Claude might have in a chat session - the deployed app authenticates to
Google itself via its own OAuth app credentials (GOOGLE_CLIENT_ID/SECRET),
with one shared connection for the firm rather than per-advisor tokens.
"""
from datetime import datetime, timedelta, timezone

import requests
from flask import current_app

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/userinfo.email"


class CalendarConfigError(Exception):
    """Raised when GOOGLE_CLIENT_ID/SECRET are not configured or malformed."""


class CalendarRequestError(Exception):
    """Raised when a Google Calendar API call fails."""


def _get_credentials():
    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
    client_secret = current_app.config.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise CalendarConfigError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not configured. Add them to your "
            ".env file to enable upcoming meetings from Google Calendar."
        )
    try:
        client_id.encode("ascii")
        client_secret.encode("ascii")
    except UnicodeEncodeError:
        # These go into an OAuth request; invisible non-ASCII characters (e.g.
        # copied from a masked dashboard display instead of the real value)
        # fail deep in the HTTP layer with a cryptic error - catch it here.
        raise CalendarConfigError(
            "GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET contains invalid (non-ASCII) "
            "characters - likely corrupted during copy/paste. Delete and re-enter "
            "from the original source."
        )
    return client_id, client_secret


def build_auth_url(redirect_uri: str, state: str) -> str:
    client_id, _ = _get_credentials()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",  # forces a refresh_token even on repeat connects
        "state": state,
    }
    query = "&".join(f"{k}={requests.utils.quote(v)}" for k, v in params.items())
    return f"{AUTH_URL}?{query}"


def exchange_code_for_tokens(code: str, redirect_uri: str) -> dict:
    client_id, client_secret = _get_credentials()
    try:
        response = requests.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise CalendarRequestError(f"Google token exchange failed: {exc}") from exc

    if response.status_code != 200:
        raise CalendarRequestError(f"Google token exchange returned {response.status_code}: {response.text[:300]}")
    return response.json()


def refresh_access_token(refresh_token: str) -> dict:
    client_id, client_secret = _get_credentials()
    try:
        response = requests.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise CalendarRequestError(f"Google token refresh failed: {exc}") from exc

    if response.status_code != 200:
        raise CalendarRequestError(f"Google token refresh returned {response.status_code}: {response.text[:300]}")
    return response.json()


def fetch_user_email(access_token: str) -> str:
    try:
        response = requests.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15,
        )
    except requests.RequestException as exc:
        raise CalendarRequestError(f"Failed to fetch account info: {exc}") from exc
    if response.status_code != 200:
        raise CalendarRequestError(f"Failed to fetch account info ({response.status_code}).")
    return response.json().get("email", "")


def fetch_upcoming_events(access_token: str, max_results: int = 10):
    now = datetime.now(timezone.utc).isoformat()
    try:
        response = requests.get(
            EVENTS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "timeMin": now,
                "maxResults": max_results,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise CalendarRequestError(f"Google Calendar request failed: {exc}") from exc

    if response.status_code == 401:
        raise CalendarRequestError("EXPIRED_TOKEN")
    if response.status_code != 200:
        raise CalendarRequestError(f"Google Calendar returned {response.status_code}: {response.text[:300]}")

    return response.json().get("items") or []


def parse_event(event: dict) -> dict:
    start = event.get("start") or {}
    start_str = start.get("dateTime") or start.get("date")
    is_all_day = "date" in start and "dateTime" not in start

    start_dt = None
    if start_str:
        try:
            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        except ValueError:
            start_dt = None

    attendees = []
    for a in event.get("attendees") or []:
        name = a.get("displayName") or a.get("email")
        if name and name not in attendees:
            attendees.append(name)

    return {
        "id": event.get("id"),
        "title": event.get("summary") or "(No title)",
        "start": start_dt,
        "is_all_day": is_all_day,
        "attendees": attendees,
        "html_link": event.get("htmlLink"),
    }
