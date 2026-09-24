"""Leadership Overview generation (/ai/overview).

Synthesizes the entire documented history of a client - conversations,
transcripts, decisions, action items, and the most recent prior overview -
into a partner-ready briefing. This is where historical continuity lives:
the model is explicitly given the previous overview (if any) and asked to
call out what changed, not just restate the latest meeting.
"""
from typing import Optional

from ai.anthropic_client import call_structured
from brand import BRAND, brandify_prompt

SYSTEM_PROMPT = """You are a senior partner at Scaalex Consulting, a premium M&A and \
capital advisory firm, preparing a leadership briefing on a client relationship for other \
partners who have limited time.

You will be given the full documented history for one client: conversation summaries, \
transcripts, confirmed decisions, and action items, in chronological order, plus the most \
recent leadership overview if one exists.

Strict rules:
- Base everything ONLY on the documented history provided. Do not invent facts, dates, \
figures, or commitments.
- Distinguish clearly between a "Documented Fact" (explicitly stated in the source material) \
and an "Advisor Observation" (your own inference or judgment about risk/priority). Every risk \
item must be labeled as one or the other.
- Recommended next steps are YOUR suggestions, not things already agreed with the client - \
label them as suggestions, never as decisions.
- If there is a previous overview, explicitly note what has changed since then (new decisions, \
resolved or new action items, shifts in priority). If nothing material changed, say so briefly.
- Prioritize action items using evidence from the history (explicit urgency, deadlines, \
repeated mentions, client-facing dependencies) - do not assign priority arbitrarily.
- Be concise. Leadership reads this in under two minutes.
"""
SYSTEM_PROMPT = brandify_prompt(SYSTEM_PROMPT)

OVERVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "executive_summary": {
            "type": "string",
            "description": "Leadership-level overview: what is happening, what changed, current priorities, major developments, important context. 3-6 sentences.",
        },
        "key_actions": {
            "type": "array",
            "description": "Most important outstanding action items, most urgent first.",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "owner": {"type": "string"},
                    "due_date": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                },
                "required": ["action", "owner", "status"],
            },
        },
        "key_decisions": {
            "type": "array",
            "description": "Major decisions taken recently.",
            "items": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                    "date": {"type": ["string", "null"]},
                    "context": {"type": "string"},
                    "impact": {"type": "string"},
                },
                "required": ["decision", "context", "impact"],
            },
        },
        "risks": {
            "type": "array",
            "description": "Unresolved issues, dependencies, delays, or risks.",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "label": {"type": "string", "enum": ["Documented Fact", "Advisor Observation"]},
                },
                "required": ["issue", "label"],
            },
        },
        "leadership_notes": {
            "type": "string",
            "description": "Answer: if a partner spends only two minutes on this client, what must they know? Extremely concise - 2-4 short sentences or bullet-like fragments.",
        },
        "suggested_next_steps": {
            "type": "array",
            "description": "Practical suggested next steps. These are proposals, not agreed actions.",
            "items": {"type": "string"},
        },
        "changes_since_last": {
            "type": "string",
            "description": "What changed since the previous overview. If no previous overview exists, briefly state this is the first overview.",
        },
    },
    "required": [
        "executive_summary", "key_actions", "key_decisions", "risks",
        "leadership_notes", "suggested_next_steps", "changes_since_last",
    ],
}


def generate_overview(client_name: str, history_text: str, previous_overview_text: Optional[str]):
    prev_block = previous_overview_text or "None - this is the first leadership overview for this client."
    user_prompt = f"""Client: {client_name}

=== PREVIOUS LEADERSHIP OVERVIEW ===
{prev_block}

=== FULL DOCUMENTED HISTORY (chronological) ===
{history_text}

Generate the leadership overview using the emit_result tool."""

    return call_structured(SYSTEM_PROMPT, user_prompt, OVERVIEW_SCHEMA)
