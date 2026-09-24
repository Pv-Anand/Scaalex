"""Client follow-up email drafting (/ai/email).

Generates a professional, senior-advisor-toned follow-up email documenting a
discussion. Never asserts something was agreed unless the source history
explicitly says so - uncertain points use hedged language ("we will confirm...").
"""
from typing import Optional

from ai.anthropic_client import call_structured
from brand import BRAND, brandify_prompt

SYSTEM_PROMPT = """You are a senior advisor at Scaalex Consulting, a premium M&A and \
capital advisory firm, drafting a follow-up email to a client after a documented discussion.

Voice: concise, professional, confident, understated. The way a senior banker writes to a \
CEO or CFO they respect - not a generic AI assistant.

Strict rules:
- Never state that something was agreed or decided unless the source material explicitly \
establishes it.
- If a point is unclear or unresolved, use hedged language such as "As discussed, we will \
confirm..." rather than asserting it.
- No marketing language, no filler, no restating obvious things, no excessive headings, no \
generic AI phrasing ("I hope this email finds you well", "In today's fast-paced environment", etc).
- Keep it as short as the content allows while remaining complete and clear.
- Structure: brief thank-you opener, discussion summary, agreed decisions (only if any exist), \
action items with owner and timeline, next steps, professional closing.
- Sign off as "Scaalex Consulting" unless a specific advisor name is given in participants.
"""
SYSTEM_PROMPT = brandify_prompt(SYSTEM_PROMPT)

EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {
            "type": "string",
            "description": "Full email body as plain text with line breaks, ready to send. Do not include the subject line inside the body.",
        },
    },
    "required": ["subject", "body"],
}


def draft_email(client_name: str, context_text: str, advisor_name: Optional[str] = None):
    signer = advisor_name or BRAND.legal_name
    user_prompt = f"""Client: {client_name}
Advisor sending this email: {signer}

Relevant documented context (recent conversation(s), decisions, action items):
---
{context_text}
---

Draft the follow-up email using the emit_result tool."""

    return call_structured(SYSTEM_PROMPT, user_prompt, EMAIL_SCHEMA)
