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
- **Phase 2** — Editable transcript (pasted manually or synced from
  Fireflies), AI structured extraction (summary, decisions, action items,
  open questions, important context), and a **confirmation workflow** —
  nothing the AI extracts becomes part of the permanent record until an
  advisor reviews and confirms it.
- **Phase 3** — "Generate Leadership Overview" (executive summary, key
  actions, key decisions, risks labeled Documented Fact vs. Advisor
  Observation, "what leadership should know," suggested next steps, and
  explicit comparison against the previous overview), plus "Draft Client
  Email" with Copy / Edit / Regenerate.
- **Phase 4** — Client-scoped search across conversations/transcripts/notes/
  decisions/action items/participants, document upload & storage linked to
  clients and conversations, advanced filters on the timeline and action item
  views, and an audit trail (`AuditLog`) recording every material change.
- **Fireflies meeting sync** — pulls recordings/transcripts from Fireflies.ai,
  auto-matches them to a client by name where possible (feeding straight into
  the same AI extraction/decisions/overview pipeline as any other
  conversation), and routes anything unmatched into an **Uncategorized
  Meetings** inbox for one-click manual assignment. See §9.
- **Upcoming Meetings (Google Calendar)** — the Meetings page also shows
  what's coming up next (title, time, attendees) once a Google Calendar is
  connected, so an advisor can see the full picture - what's already
  happened (Fireflies) and what's next (Calendar) - in one place.

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
│   ├── extract.py           # /ai/extract — per-conversation structured extraction
│   ├── overview.py          # /ai/overview — leadership overview generation
│   ├── email_draft.py       # /ai/email — client follow-up email drafting
│   ├── fireflies_client.py  # Fireflies GraphQL API — meeting list/detail sync
│   └── google_calendar_client.py  # Google OAuth + Calendar API — upcoming events
├── routes/
│   ├── home.py, clients.py, conversations.py, decisions.py,
│   │   action_items.py, documents.py, ai_overview.py, search.py,
│   │   fireflies.py, calendar.py
│   └── ai_api.py             # JSON endpoint: POST /ai/extract
├── templates/                # Server-rendered Jinja2 (premium B/W/charcoal design)
├── static/css/style.css       # Design system: Inter type, thin borders, no gradients
├── static/js/app.js            # Extraction review UI, email draft UI
└── uploads/documents/          # Local file storage (gitignored)
```

**Why server-rendered Flask instead of a JS SPA:** no Node toolchain was
available (see §2). Interactivity that needs it (AI extraction review,
email draft generation/regeneration, inline action-item
status changes) is implemented with small, targeted `fetch()` calls from
vanilla JS against JSON endpoints — the rest is plain server-rendered pages.

**Security / data isolation:**
- Session-based auth via Flask-Login; every route that touches client data is
  `@login_required`.
- Passwords hashed with PBKDF2-SHA256 (via Werkzeug) — never stored or logged
  in plaintext.
- CSRF protection (Flask-WTF) on every state-changing request — form posts
  carry a hidden `csrf_token` field, and `fetch()`-based JS calls send it via
  the `X-CSRFToken` header (see `static/js/app.js`'s `csrfToken()` helper).
- Login is rate-limited (10 attempts/minute/IP via Flask-Limiter) to blunt
  brute-force attempts.
- Session cookies are `HttpOnly` + `SameSite=Lax` always, and `Secure`
  (HTTPS-only) when `FORCE_HTTPS=true` — set that once the app is actually
  served over HTTPS (see §8).
- Every client-scoped route resolves the client by slug and 404s if not
  found, so URLs can't be walked across clients by guessing IDs on documents/
  action items without going through a client-scoped query first for the
  page views.
- API keys (`ANTHROPIC_API_KEY`, `FIREFLIES_API_KEY`) live only in `.env` /
  environment variables, read server-side via `config.py`. They are never
  sent to the browser or embedded in any template or static asset.
- Uploaded files are stored outside of static/, served through an
  authenticated Flask route (`/documents/<id>/download`) rather than a
  public static path.
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
| `conversations` | client_id, interaction_type, date, participants (JSON), raw_notes, transcript, transcript_status, ai_extraction (JSON, unconfirmed), extraction_status, summary, important_context, open_questions (JSON), source (`manual` / `fireflies`) |
| `decisions` | client_id, conversation_id, decision, context, date, owner, status (`Confirmed` / `Needs Confirmation`), source_label |
| `action_items` | client_id, conversation_id, task, owner, due_date, priority, status, source_label, needs_confirmation |
| `documents` | client_id, conversation_id, file_name, stored_name, file_type, file_size, uploaded_by_id |
| `ai_overviews` | client_id, executive_summary, key_actions (JSON), key_decisions (JSON), risks (JSON), leadership_notes, suggested_next_steps (JSON), changes_since_last |
| `email_drafts` | client_id, overview_id, conversation_id, subject, body |
| `audit_log` | user_id, client_id, action, entity_type, entity_id, details, created_at |
| `fireflies_meetings` | fireflies_id (unique), title, meeting_date, duration_minutes, participants (JSON), transcript, fireflies_overview, status (`uncategorized` / `assigned` / `ignored`), matched_client_id, assigned_client_id, assigned_conversation_id |
| `calendar_connections` | email, access_token, refresh_token, token_expiry, connected_by_id |

This matches the spec's data model directly, with one addition
(`email_drafts`, `audit_log`) needed to support the email generator and audit
trail requirements, and `extraction_status` / `ai_extraction` on
`Conversation` to support the confirm/edit/discard workflow (§20 of the spec)
without a separate staging table.

## 5. AI workflow

- **Anthropic (Claude)** — extraction, leadership overviews, and email
  drafting. All three use `messages.create(..., tools=[...],
  tool_choice={"type": "tool", ...})` (`ai/anthropic_client.py`) so the model
  is forced to return a schema-validated JSON object — no prompt-based "please
  return JSON" guessing.
- **Fireflies** — supplies transcripts directly (§10), so there's no separate
  transcription step or provider needed for meeting recordings.

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

**Graceful degradation:** if `ANTHROPIC_API_KEY` isn't set, every AI endpoint
returns a clear, actionable error (no stack traces, no silent failures,
nothing fabricated) and the rest of the app — manual
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
FIREFLIES_API_KEY=             # required for meeting sync (Fireflies -> Settings -> Developer Settings)
GOOGLE_CLIENT_ID=              # required for Upcoming Meetings (see §9)
GOOGLE_CLIENT_SECRET=
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

To enable AI features, add `ANTHROPIC_API_KEY` (and `FIREFLIES_API_KEY` for
meeting sync) to `.env` and restart the app.

## 8. Deploying to Render

The app is deployment-ready: CSRF protection, login rate limiting, secure
cookies (via `ProxyFix` for Render's TLS-terminating proxy), `gunicorn`, a
`Procfile`, and a `render.yaml` blueprint are already in place. **This is a
confidential-data app** — don't skip the persistent-disk step, or client
records will be wiped on every deploy/restart.

**1. Push the code to GitHub** (this repo is already git-initialized locally
with a first commit, and `.env` / the local database / uploads are gitignored
— nothing sensitive will be pushed):

```bash
cd scaalex-intel
git remote add origin <your-new-github-repo-url>
git branch -M main
git push -u origin main
```

**2. Create the Render service.** In the [Render dashboard](https://dashboard.render.com):
   - **New → Blueprint**, connect the GitHub repo — Render will read
     `render.yaml` and configure the service, a 1GB persistent disk mounted at
     `/var/data`, and `SECRET_KEY` (auto-generated) for you.
   - Alternatively, **New → Web Service** manually: Runtime = Python, Build
     Command = `pip install -r requirements.txt`, Start Command =
     `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 180`.
     Add a persistent disk yourself (Settings → Disks) mounted at `/var/data`.

   **Pick at least the Starter plan** — Render's free tier has no persistent
   disk, so the SQLite database and uploaded files would be wiped on every
   restart or redeploy. That's not acceptable for institutional client
   records.

**3. Set the remaining environment variables** in the Render dashboard
(Settings → Environment) — these are marked `sync: false` in `render.yaml` so
they're never committed to git:
   - `ANTHROPIC_API_KEY`
   - `FIREFLIES_API_KEY` (optional, for meeting sync)
   - `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (optional, for Upcoming Meetings)

   `DATA_DIR=/var/data` and `FORCE_HTTPS=true` are already set by
   `render.yaml`.

   If you're using Google Calendar, also add your live URL's callback
   (`https://<your-app>.onrender.com/calendar/oauth/callback`) as an
   authorized redirect URI on the OAuth client in Google Cloud Console -
   see §9 for the full setup.

**4. Seed the database once**, using Render's Shell tab (Dashboard → your
service → Shell):

```bash
python3 seed.py
```

**5. Change the default password** for every account before real client data
goes in — there's no self-service password change screen yet (see §10), so
do it via the Shell:

```bash
python3 -c "
from app import create_app
from extensions import db
from models import User
app = create_app()
with app.app_context():
    u = User.query.filter_by(email='anand@scaalex.com').first()
    u.set_password('a-real-password-here')
    db.session.commit()
"
```

Your team can then sign in at the `.onrender.com` URL Render gives you (or a
custom domain you attach in Settings → Custom Domains).

## 9. Meetings page (Fireflies sync + Google Calendar)

The **Meetings** sidebar link (`/fireflies` - the URL and blueprint name are
unchanged from before the page was relabeled) pulls meeting transcripts
from [Fireflies.ai](https://fireflies.ai) directly into the app. There's
no audio upload feature in the app itself - Fireflies records the meeting
and does the transcription; the app's job is turning that transcript into
institutional record (a transcript can also be pasted manually onto a
conversation's Transcript field if it came from somewhere other than
Fireflies).

**How it works:**

1. **Sync Now** (`POST /fireflies/sync`) calls Fireflies' own GraphQL API
   (`ai/fireflies_client.py`) with your `FIREFLIES_API_KEY`, listing recent
   meetings and fetching the full transcript for any not already synced
   (deduplicated by Fireflies' meeting ID).
2. For each new meeting, `routes/fireflies.py::guess_client_match()` checks
   whether the meeting title or any participant's name contains a client's
   name. This is a deliberately simple, explainable substring match - not
   an AI guess - so a mismatch is obvious and easy to correct, never a
   confident-sounding wrong answer.
3. **Confidently matched** meetings become a real `Conversation` on that
   client immediately (`interaction_type="Video Call"`, transcript attached,
   `source="fireflies"`), exactly as if someone had pasted that transcript
   in by hand. From there it's already wired into everything else: the advisor
   can click **Generate Structured Information** to run it through the same
   AI extraction / confirm-edit-discard workflow as any other conversation,
   and it will be included in future leadership overviews and decision logs
   for that client.
4. **Unmatched** meetings land in the **Uncategorized Meetings** section on
   the Fireflies page, showing the title, date, participants, and Fireflies'
   own short overview (labeled as such - it's a hint, never treated as our
   own record). An advisor picks a client from a dropdown and clicks **Move**
   to assign it (creating the Conversation the same way step 3 does), or
   **Ignore** to dismiss a meeting that isn't client-related (e.g. an
   internal standup).
5. A badge on the sidebar "Meetings" link shows the current uncategorized
   count, so nothing sits unreviewed silently.

If `FIREFLIES_API_KEY` isn't set, **Sync Now** shows a clear configuration
error and nothing else on the page is affected - meetings can still be
documented manually as always.

**Upcoming Meetings (Google Calendar):**

The same page also shows what's coming up next - title, time, and
attendees - once a calendar is connected. This is a *separate* integration
from Fireflies (it reads a calendar, not a transcript source), added
because Fireflies' API only exposes recorded/in-progress meetings, not
future scheduled ones.

Setup requires a one-time OAuth client in Google Cloud Console:

1. Go to **[console.cloud.google.com](https://console.cloud.google.com)**,
   create or select a project.
2. **APIs & Services → Library** → search **Google Calendar API** → Enable.
3. **APIs & Services → OAuth consent screen** → set up an "External" (or
   "Internal" if using Google Workspace) app with at least your email as a
   test user, and add the `.../auth/calendar.readonly` scope.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   → Application type **Web application**.
5. Under **Authorized redirect URIs**, add both:
   - `http://localhost:5050/calendar/oauth/callback` (local dev)
   - `https://<your-app>.onrender.com/calendar/oauth/callback` (production)
6. Copy the generated **Client ID** and **Client Secret** into
   `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (locally in `.env`, in
   production via Render's Environment tab).
7. In the app, click **Connect Google Calendar** on the Meetings page and
   approve access.

This is **one shared connection for the firm** (whoever connects it),
matching how Fireflies sync also runs against one shared workspace rather
than per-advisor credentials - see §10 for the security trade-off that
comes with that. Disconnecting (button next to the connected email) removes
the stored tokens entirely.

## 10. Known limitations

- **No Node/JS toolchain in this environment** — see §2. This is a deliberate
  adaptation, not an oversight; flagged explicitly rather than silently
  built.
- **No self-service roles or password change** — accounts are created via
  `seed.py` or a one-off script (see §8 step 5); the `role` field on `User`
  exists for future use but isn't enforced anywhere yet (no admin-vs-advisor
  permission differences), and there's no in-app "change my password" flow.
- **Rate limiting is in-memory** (Flask-Limiter's default backend) — fine for
  a single small deployment, but with multiple gunicorn workers each worker
  tracks its own counts, so the login rate limit is approximate rather than
  a hard global cap. A shared backend (Redis) would fix this if it matters.
- **Local file storage only** — uploads live on disk under `uploads/`
  (`DATA_DIR` on a deployed host), not S3/object storage. Fine for a
  single-instance deployment with a persistent disk (see §8); would need a
  storage backend swap for a multi-instance deployment.
- **Search is per-client, substring-based** (SQL `ILIKE` + a Python-side pass
  for JSON participant fields), not fuzzy/semantic. It satisfies the spec's
  example searches but won't handle typos or paraphrases.
- **No pagination** on conversation/action-item/decision lists — fine at
  demo/early scale, will need it once a client has hundreds of entries.
- **AI overview "priority" reasoning is only as good as the documented
  history** — if conversations are recorded sparsely or without dates, the
  model has less evidence to prioritize from, and its output will say so
  rather than guessing.
- **Fireflies auto-matching is substring-based, not AI-based** — a meeting
  titled or attended by someone whose name happens to contain a client's
  name will match that client even if unrelated (rare in practice, but
  possible with short client names). It's intentionally simple rather than
  AI-guessed so a wrong match is obvious and easy to fix, not a confident-
  sounding error - but it also means it requires no manual sync scheduling
  yet: sync only runs when someone clicks "Sync Now," not automatically.
- **Google Calendar OAuth tokens are stored in plaintext in the database**
  (`calendar_connections.access_token` / `refresh_token`), not encrypted at
  rest. Acceptable for a single-instance internal deployment with disk-level
  access control, but worth encrypting (e.g. Fernet with a key from env)
  before wider or multi-tenant use. There's also only **one shared calendar
  connection for the whole firm**, not per-advisor - whoever connects it is
  the calendar everyone sees on the Meetings page.

## 11. Recommended next development steps

1. **Self-service password change / reset** — currently only a Shell script
   (§8 step 5); a real "Change Password" page should ship before wider use.
2. **Role-based access** — differentiate advisor vs. partner/admin, e.g. who
   can generate leadership overviews or edit engagement status.
3. **Pagination + infinite scroll** on timeline/action items once client
   history grows.
4. **Postgres + S3-compatible storage** for a shared/production deployment
   (the code already reads `DATABASE_URL` from env and normalizes Render's
   `postgres://` scheme; only the file-storage layer in
   `routes/documents.py` would need an abstraction to move off local disk).
5. **Email sending integration** (not just drafting) — e.g. wire "Copy Email"
   up to an actual send-via-Outlook/Gmail-API action once the firm decides
   that's wanted, with an explicit send confirmation step.
6. **Team-wide visibility controls** if Scaalex wants some clients restricted
   to specific advisors.
7. **Automated tests** — the app currently relies on manual + curl-based
   verification (documented above); a pytest suite covering the extraction
   confirm/discard workflow and AI graceful-degradation paths would catch
   regressions early, especially given how central the confirmation workflow
   is to data integrity.
8. **Scheduled Fireflies sync** — currently manual (click "Sync Now"); a
   background job (Render Cron Job, or a simple APScheduler loop) hitting
   `POST /fireflies/sync` every 15-30 minutes would mean meetings show up
   without anyone remembering to trigger it.
9. **Encrypt Google Calendar OAuth tokens at rest**, and consider per-advisor
   calendar connections instead of one shared connection, once more than one
   person's schedule needs to show up independently.
