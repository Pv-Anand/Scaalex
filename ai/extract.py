"""Structured extraction from a single conversation's notes/transcript (/ai/extract).

Turns raw notes or a transcript into: summary, decisions, action items,
important context, and open questions. Never invents facts - anything the
model is not confident about is flagged "Needs Confirmation" rather than
asserted, and the advisor reviews everything before it becomes part of the
permanent record (see routes/conversations.py confirm_extraction).
"""
from ai.anthropic_client import call_structured

SYSTEM_PROMPT = """You are a meticulous documentation assistant for Scaalex Consulting, \
a premium M&A and capital advisory firm. You convert raw notes or transcripts of client \
conversations into structured institutional records.

Strict rules:
- Only extract what is explicitly stated or unambiguously implied in the source text.
- Never invent decisions, dates, owners, deadlines, or commitments.
- If a decision or action item is implied but not clearly confirmed, still include it but \
set "needs_confirmation": true.
- Owners should be a person or side named in the text (e.g. "Anand", "Client CEO", \
"Scaalex", "Client"). If unclear, use "Unassigned" and set needs_confirmation true.
- Due dates must be explicit in the text (e.g. "by Friday", "next week") - resolve relative \
dates only if a reference date is given; otherwise leave due_date null and flag needs_confirmation.
- Keep the summary factual and concise, written for a senior advisor, not a transcript recap.
"""

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Concise factual summary of what was discussed (2-5 sentences).",
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                    "context": {"type": "string"},
                    "owner": {"type": "string"},
                    "needs_confirmation": {"type": "boolean"},
                },
                "required": ["decision", "needs_confirmation"],
            },
        },
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "owner": {"type": "string"},
                    "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD or null"},
                    "priority": {"type": "string", "enum": ["High", "Medium", "Low"]},
                    "needs_confirmation": {"type": "boolean"},
                },
                "required": ["task", "owner", "priority", "needs_confirmation"],
            },
        },
        "important_context": {
            "type": "string",
            "description": "Information that may matter in future conversations but isn't a decision or task.",
        },
        "open_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "decisions", "action_items", "important_context", "open_questions"],
}


def extract_from_text(client_name: str, interaction_type: str, participants, date_str: str, source_text: str):
    user_prompt = f"""Client: {client_name}
Interaction type: {interaction_type}
Date: {date_str}
Participants: {', '.join(participants) if participants else 'Not specified'}

Source material (raw notes and/or transcript):
---
{source_text}
---

Extract the structured record using the emit_result tool."""

    return call_structured(SYSTEM_PROMPT, user_prompt, EXTRACTION_SCHEMA)
