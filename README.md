# Scaalex Intelligence

Client Project Intelligence & Documentation System for Scaalex Consulting — a
working internal application, not a mockup. Every client conversation becomes
structured, searchable institutional memory: decisions, action items, and
AI-generated leadership overviews and client emails, all built on top of a
permanent, auditable record.

## 1. What was built

A full-stack, end-to-end working application covering all four MVP phases from
the spec:

- **Phase 1** — Auth, home dashboard, client list/cards, client dashboard
  (Overview / Timeline / Conversations / Action Items / Decisions / Documents /
  AI Summary tabs), Add Update flow, timeline, decision log, action item
  management with filtering/sorting and overdue highlighting.
- **Phase 2** — Voice recording upload (MP3/WAV/M4A/MP4), Whisper-based
  transcription, editable transcript, AI structured extraction (summary,
  decisions, action items, open questions, important context), and a
  **confirmation workflow** — nothing the AI extracts becomes part of the
  permanent record until an advisor reviews and confirms it.
- **Phase 3** — "Generate Leadership Overview" (executive summary, key
  actions, key decisions, risks labeled Documented Fact vs. Advisor
  Observation, "what leadership should know," suggested next steps, and
  explicit comparison against the previous overview), plus "Draft Client
  Email" with Copy / Edit / Regenerate.
- **Phase 4** — Client-scoped search across conversations/transcripts/notes/
  decisions/action items/participants, document upload & storage linked to
  clients and conversations, advanced filters on the timeline and action item
  views, and an audit trail (`AuditLog`) recording every material change.

Two demo clients are seeded with realistic, clearly-attributable data:
**Sravana** (Google Ads / budget discussion, matching the spec's own example
almost verbatim) and **Dron Imagination** (pre-Series A fundraising).

## 2. A note on the tech stack

The spec's implementation checklist calls for inspecting the environment
before choosing a stack. **This machine has no Node.js, npm, or Homebrew
installed**, and installing them requires an admin password this session
cannot provide. Rather than block on that, the app is built as a **Python /
Flask / SQLite** application — a fully working, secure, locally-runnable
system using only what's already available (Python 3.9 + pip, no admin rights
needed). If you later install Node.js and want a JS/TS stack instead (e.g.
Next.js + Prisma + Postgres), the data model and AI workflow here translate
directly — say the word and it can be rebuilt on that stack.

## 3. Architecture overview

```
scaalex-intel/
├── app.py                 # Flask app factory, blueprint registration
├── config.py               # Config from environment (.env)
├── extensions.py           # SQLAlchemy + Flask-Login singletons
├── models.py                # All ORM models + audit logging helper
├── auth.py                  # Login/logout (Flask-Login, session cookies)
├── seed.py                  # Demo data + login user
├── ai/
│   ├── anthropic_client.py  # Shared Claude client, forces structured JSON via tool-use
│   ├── transcribe.py        # /ai/transcribe — OpenAI Whisper
│   ├── extract.py           # /ai/extract — per-conversation structured extraction
│   ├── overview.py          # /ai/overview — leadership overview generation
│   └── email_draft.py       # /ai/email — client follow-up email drafting
├── routes/
│   ├── home.py, clients.py, conversations.py, decisions.py,
│   │   action_items.py, documents.py, ai_overview.py, search.py
│   └── ai_api.py             # JSON endpoints: POST /ai/transcribe, /ai/extract
├── templates/                # Server-rendered Jinja2 (premium B/W/charcoal design)
├── static/css/style.css       # Design system: Inter type, thin borders, no gradients
├── static/js/app.js            # Voice upload, extraction review UI, email draft UI
└── uploads/{audio,documents}/  # Local file storage (gitignored)
```

**Why server-rendered Flask instead of a JS SPA:** no Node toolchain was
available (see §2). Interactivity that needs it (voice upload + transcription,
AI extraction review, email draft generation/regeneration, inline action-item
status changes) is implemented with small, targeted `fetch()` calls from
vanilla JS against JSON endpoints — the rest is plain server-rendered pages.

**Security / data isolation:**
- Session-based auth via Flask-Login; every route that touches client data is
  `@login_required`.
- Passwords hashed with PBKDF2-SHA256 (via Werkzeug) — never stored or logged
  in plaintext.
- Every client-scoped route resolves the client by slug and 404s if not
  found, so URLs can't be walked across clients by guessing IDs on documents/
  action items without going through a client-scoped query first for the
  page views.
- API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) live only in `.env` /
  environment variables, read server-side via `config.py`. They are never
  sent to the browser or embedded in any template or static asset.
- Uploaded files are stored outside of static/, served through an
  authenticated Flask route (`/documents/<id>/download`,
  `/clients/<slug>/audio/<id>`) rather than a public static path.
- `AuditLog` records who did what and when: updates created/edited,
  transcripts edited, extractions confirmed/discarded, decisions recorded,
  action items created/completed, overviews and email drafts generated.

## 4. Database schema

SQLite via SQLAlchemy (swap `DATABASE_URL` in `.env` for Postgres in
production — the ORM layer doesn't change).

| Table | Key fields |
|---|---|
| `users` | name, email, password_hash, role |
| `clients` | name, slug, status, engagement_type, is_demo |
| `conversations` | client_id, interaction_type, date, participants (JSON), raw_notes, audio_filename, transcript, transcript_status, ai_extraction (JSON, unconfirmed), extraction_status, summary, important_context, open_questions (JSON) |
| `decisions` | client_id, conversation_id, decision, context, date, owner, status (`Confirmed` / `Needs Confirmation`), source_label |
| `action_items` | client_id, conversation_id, task, owner, due_date, priority, status, source_label, needs_confirmation |
| `documents` | client_id, conversation_id, file_name, stored_name, file_type, file_size, uploaded_by_id |
| `ai_overviews` | client_id, executive_summary, key_actions (JSON), key_decisions (JSON), risks (JSON), leadership_notes, suggested_next_steps (JSON), changes_since_last |
| `email_drafts` | client_id, overview_id, conversation_id, subject, body |
| `audit_log` | user_id, client_id, action, entity_type, entity_id, details, created_at |

This matches the spec's data model directly, with one addition
(`email_drafts`, `audit_log`) needed to support the email generator and audit
trail requirements, and `extraction_status` / `ai_extraction` on
`Conversation` to support the confirm/edit/discard workflow (§20 of the spec)
without a separate staging table.

## 5. AI workflow

Two providers, split by capability:

- **Anthropic (Claude)** — extraction, leadership overviews, and email
  drafting. All three use `messages.create(..., tools=[...],
  tool_choice={"type": "tool", ...})` (`ai/anthropic_client.py`) so the model
  is forced to return a schema-validated JSON object — no prompt-based "please
  return JSON" guessing.
- **OpenAI Whisper** — transcription only, since Claude models don't accept
  raw audio. This is the one place a second provider is unavoidable.

Every AI system prompt (`ai/extract.py`, `ai/overview.py`,
`ai/email_draft.py`) explicitly instructs the model to:
- Only state what's in the source material — never invent decisions, dates,
  owners, or commitments.
- Flag uncertain items as `needs_confirmation: true` rather than asserting
  them.
- For the leadership overview, label every risk as `Documented Fact` or
  `Advisor Observation`, and separate "suggested next steps" from anything
  already agreed.
- For the email draft, hedge anything not explicitly confirmed in the record
  ("As discussed, we will confirm...") rather than asserting agreement.

**Historical continuity:** `routes/ai_overview.py::_build_history_text()`
assembles the client's *entire* conversation history in chronological order
(summaries, transcripts, decisions, action item status) and
`_previous_overview_text()` pulls the most recent overview. Both are passed
to `ai/overview.py::generate_overview()`, which is explicitly instructed to
call out what changed since the last overview rather than only summarizing
the latest meeting — this is stored back in `changes_since_last`.

**Confirmation workflow (spec §20):** `POST /ai/extract` stores the model's
output on `Conversation.ai_extraction` with `extraction_status =
pending_review` — nothing else is written yet. The advisor reviews the
rendered cards in the browser (editable summary, decision text/owner/context
checkboxes, action item task/owner/due/priority), and only fields left
checked are persisted as real `Decision` / `ActionItem` rows on **Confirm**;
**Discard** clears the pending extraction entirely. This is the one place in
the app where AI output can become permanent record, and it's gated
end-to-end.

**Graceful degradation:** if `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` aren't
set, every AI endpoint returns a clear, actionable error (no stack traces, no
silent failures, nothing fabricated) and the rest of the app — manual
conversation/decision/action item entry, timeline, documents — works fully
without them. This was verified directly: the app runs, seeds, and is fully
usable right now with both keys blank.

## 6. Environment variables

Copy `.env.example` to `.env` and fill in as needed:

```
SECRET_KEY=                    # already generated for you in .env
DATABASE_URL=                  # blank = local SQLite at instance/scaalex.db
ANTHROPIC_API_KEY=             # required for extraction/overview/email
ANTHROPIC_MODEL=claude-sonnet-5
OPENAI_API_KEY=                # required for voice transcription
OPENAI_TRANSCRIBE_MODEL=whisper-1
MAX_UPLOAD_MB=50
```

## 7. How to run locally

```bash
cd scaalex-intel
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # already done in this session; edit to add API keys
python3 seed.py                  # creates login user + demo data (safe to re-run)
python3 app.py                    # serves on http://localhost:5050
```

**Login:** `anand@scaalex.com` / `Scaalex@2026` (created by `seed.py` — change
or rotate this before using with real client data).

To enable AI features, add `ANTHROPIC_API_KEY` (and `OPENAI_API_KEY` for
transcription) to `.env` and restart the app.

## 8. How voice transcription works

1. On a conversation's detail page, **Upload Voice Recording** sends the file
   via `fetch()` to `POST /ai/transcribe` (multipart form).
2. The file is saved to `uploads/audio/<uuid>.<ext>` (never inside `static/`,
   so it isn't publicly servable without auth) and linked to the conversation.
3. `ai/transcribe.py` posts it to OpenAI's `/v1/audio/transcriptions`
   endpoint (Whisper) and returns the transcript text.
4. The transcript is shown in an editable textarea — the advisor can correct
   it before anything downstream happens.
5. **Generate Structured Information** sends the (possibly edited) transcript
   to `POST /ai/extract`, which runs the Claude extraction and renders the
   confirm/edit/discard review UI described above.

If `OPENAI_API_KEY` isn't set, the upload still stores the audio file (for
playback) and the UI clearly states that transcription isn't configured,
prompting manual transcript entry instead.

## 9. Known limitations

- **No Node/JS toolchain in this environment** — see §2. This is a deliberate
  adaptation, not an oversight; flagged explicitly rather than silently
  built.
- **Single shared login** — there's one seeded advisor account, not a
  full team-management/roles system. The `role` field on `User` exists for
  future use but isn't yet enforced anywhere (no admin-vs-advisor permission
  differences).
- **No CSRF token on forms** — acceptable for a local/internal single-tenant
  tool behind normal network access controls, but should be added
  (Flask-WTF or a manual token) before any multi-user or internet-facing
  deployment.
- **Local file storage only** — uploads live on disk under `uploads/`, not
  S3/object storage. Fine for a single-server internal deployment; would need
  a storage backend swap for multi-instance or cloud deployment.
- **Whisper transcription has no chunking** — very long recordings (beyond
  OpenAI's per-request limits, ~25MB) will fail; there's no automatic
  splitting yet.
- **Search is per-client, substring-based** (SQL `ILIKE` + a Python-side pass
  for JSON participant fields), not fuzzy/semantic. It satisfies the spec's
  example searches but won't handle typos or paraphrases.
- **No pagination** on conversation/action-item/decision lists — fine at
  demo/early scale, will need it once a client has hundreds of entries.
- **AI overview "priority" reasoning is only as good as the documented
  history** — if conversations are recorded sparsely or without dates, the
  model has less evidence to prioritize from, and its output will say so
  rather than guessing.

## 10. Recommended next development steps

1. **Add CSRF protection** (Flask-WTF) before any wider rollout.
2. **Role-based access** — differentiate advisor vs. partner/admin, e.g. who
   can generate leadership overviews or edit engagement status.
3. **Pagination + infinite scroll** on timeline/action items once client
   history grows.
4. **Chunked/long-form transcription** for multi-hour recordings.
5. **Postgres + S3-compatible storage** for a shared/production deployment
   (the code already reads `DATABASE_URL` from env; only the file-storage
   layer in `routes/documents.py` / `ai_api.py` would need an abstraction).
6. **Email sending integration** (not just drafting) — e.g. wire "Copy Email"
   up to an actual send-via-Outlook/Gmail-API action once the firm decides
   that's wanted, with an explicit send confirmation step.
7. **Team-wide visibility controls** if Scaalex wants some clients restricted
   to specific advisors.
8. **Automated tests** — the app currently relies on manual + curl-based
   verification (documented above); a pytest suite covering the extraction
   confirm/discard workflow and AI graceful-degradation paths would catch
   regressions early, especially given how central the confirmation workflow
   is to data integrity.
