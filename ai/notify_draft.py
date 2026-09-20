"""Client follow-up notification drafting (/notify/<entity_type>/<id>).

Generates two short, ready-to-send templates for a single decision, action
item, or milestone - an email (subject + body) and a WhatsApp message - so a
follow-up takes seconds instead of writing a fresh note. Tone shifts by
state: a polite ask when something is pending on the client, a courtesy
update once it's done.
"""
from typing import Optional

from ai.anthropic_client import call_structured

SYSTEM_PROMPT = """You are a senior advisor at Scaalex Consulting writing a short, human \
follow-up to a client contact, prompting them to log into their client portal.

Voice: warm, brief, professional - the way a trusted advisor messages a client they know \
well. Never robotic, never corporate. No "I hope this email finds you well", no filler, no \
marketing language, no generic AI phrasing.

You will be given: the item type (decision / action item / milestone), whether it's PENDING \
(something is needed from the client) or COMPLETED (a courtesy update, no action required \
beyond taking a look), the item's title or description, the client's name, the contact's \
first name, the advisor's first name, and the client portal URL.

Write THREE things:
1. A subject line - short and specific, no "Re:", no generic phrasing.
2. An email body - a few short sentences: greeting, one sentence of context, the ask or \
update, the portal link on its own line, a brief sign-off with just the advisor's first name \
then "Scaalex". No bullet points, no headings, no restating the subject.
3. A WhatsApp message - a single short paragraph, casual but professional, no subject line, \
no formal sign-off, includes the portal link, safe to paste directly into a chat.

If PENDING: the tone is a polite, low-pressure ask - "could you take a minute to...".
If COMPLETED: the tone is a courtesy update - "wanted to let you know...", nothing beyond an \
optional look is implied.
"""

NOTIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "Email subject line only, no body text."},
        "email_body": {
            "type": "string",
            "description": "Full email body as plain text with line breaks, ready to send. Do not repeat the subject line inside it.",
        },
        "whatsapp_body": {
            "type": "string",
            "description": "A single short WhatsApp message as plain text, ready to paste into a chat.",
        },
    },
    "required": ["subject", "email_body", "whatsapp_body"],
}


def draft_notify(
    item_type: str,
    state: str,
    item_title: str,
    client_name: str,
    contact_first_name: str,
    advisor_first_name: str,
    portal_url: str,
    extra_context: Optional[str] = None,
):
    user_prompt = f"""Item type: {item_type}
State: {state.upper()}
Item: {item_title}
Client: {client_name}
Contact first name: {contact_first_name}
Advisor first name: {advisor_first_name}
Portal URL: {portal_url}
{f"Additional context: {extra_context}" if extra_context else ""}

Draft the subject, email body, and WhatsApp message using the emit_result tool."""

    return call_structured(SYSTEM_PROMPT, user_prompt, NOTIFY_SCHEMA)
