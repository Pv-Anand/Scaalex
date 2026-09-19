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

Be as brief as possible everywhere. A senior advisor should be able to scan the whole \
record in seconds, not read it like a transcript recap:
- Every action item's "task" is ONE short line - what to do, stated plainly (e.g. "Send \
signed NDA to legal", not "The team discussed that it would be important to eventually \
follow up on sending the signed NDA over to the legal department"). Never a sentence with \
sub-clauses; never restate context that belongs in "important_context" instead.
- Every decision's "decision" is ONE short line stating what was decided, not why (the \
reasoning goes in its own "context" field, also kept brief - one short sentence at most).
- "summary" is a short bullet-point list of what was discussed, written as plain lines \
separated by newlines, each starting with "- " (e.g. "- Reviewed Q3 pipeline\\n- Agreed to \
push renewal call to next week"). 3-6 bullets, each one short line. Never a paragraph.
"""

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "3-6 short bullet lines of what was discussed, each starting with "
                            "\"- \" and separated by newlines. No paragraphs.",
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string", "description": "One short line - what was decided, not why."},
                    "context": {"type": "string", "description": "One short sentence at most."},
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
                    "task": {"type": "string", "description": "One short line - what to do, stated plainly."},
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
