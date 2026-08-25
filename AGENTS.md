# AGENTS.md

Cortex: Flask + vanilla JS app para gestão de 1:1s e performance de time (evolução do FeedbackAI; originalmente construído no Replit). Single-user: apenas o diretor faz login (Google), liderados são perfis sem login.

## Commands

- Install deps: `uv sync` (Python >= 3.11; `uv.lock` is the lockfile)
- Dev server: `python app.py` → http://localhost:5000 (debug mode, port 5000)
- Production: `gunicorn app:app --bind 0.0.0.0:5000 --workers 4 --timeout 120`
- Data migration (idempotent): `python -m cortex.migrate_data` (requires DATABASE_URL)
- No tests, lint, typecheck, or CI configured. Verification = booting the app and exercising endpoints manually (`/healthz` reports config/db status).

## Required environment

- `DATABASE_URL` (required for DB access): Supabase Transaction Pooler URI (port **6543**), not direct connection (5432).
- `OPENAI_API_KEY`: needed for embeddings and AI features.
- `ALLOWED_EMAILS`: exact e-mails allowed to log in (comma-separated). `ALLOWED_EMAIL_DOMAINS`: domains allowed (default `totvs.com` when neither is set). OAuth callback redirects non-allowed users to `/login?error=forbidden`.
- `DIRECTOR_EMAILS`: e-mails treated as director by the migration script (falls back to ALLOWED_EMAILS).
- Optional: `SESSION_SECRET`, `GOOGLE_CLIENT_ID/SECRET`, `MAILERSEND_API_KEY/FROM`, `N8N_CHAT_WEBHOOK_URL`, `INGEST_SECRET` (protects /api/import/* and /api/insights/ingest when set).

## Architecture

Root `app.py` is a thin entrypoint exposing `app = create_app()` for Gunicorn. All code lives in the `cortex/` package:

- `cortex/__init__.py` — `create_app()` factory; registers blueprints; `/healthz`
- `cortex/database.py` — lazy engine/session factory; DDL split into `_LEGACY_DDL` (old FeedbackAI tables, kept intact) and `_NEW_DDL` (people, one_on_ones, commitments, checkpoints, card_jobs, member_cards); sets `INIT_DB_STATUS`
- `cortex/security.py` — `login_required`, email allowlist (`email_allowed`)
- `cortex/ai/openai_service.py` — all OpenAI calls (`gpt-4o`; `text-embedding-3-small`) + MailerSend sender; client created lazily via `get_client()`
- `cortex/views/` — blueprints (all under `/api`): `auth` (local login + Google OAuth), `people` (legacy profiles + new-model people CRUD under `/api/people`), `sessions` (NEW core 1:1 model: `/api/oneonones`, AI extraction, commitments, prep/agenda), `checkpoints` (Feedz-style checkpoints: `/api/checkpoints`, IA parse preview, CRUD), `cards` (team member cards: async batch flow generate-start/person/generate-finish over `card_jobs`, latest cards grid), `feedback` (legacy feedbacks+insights), `oneonones` (legacy meetings CRUD/share/summarize, n8n chat proxy), `notifications`, `staff` (agents/insights approve-archive), `webhooks` (unauthenticated ingest endpoints)
- `cortex/migrate_data.py` — idempotent legacy→new data migration (`python -m cortex.migrate_data`)

Frontend: `static/` vanilla JS SPA with no build step. `/` serves `index.html`, `/login` serves `login.html`; logic in `static/js/app.js` + `auth.js`.

## Gotchas

- Startup is resilient BY DESIGN: missing DB/OpenAI keys never crash import or boot; they surface as errors at request time and in `/healthz` (`status: degraded`). Do not reintroduce import-time raises.
- No migrations framework: schema changes go into `_LEGACY_DDL`/`_NEW_DDL` lists in `database.py`. Existing tables are never altered automatically.
- New-schema tables avoid pgvector on purpose; only legacy tables need it (Supabase has it enabled).
- Legacy tables (`users`, `feedbacks`, `meetings`, etc.) are preserved untouched; new features must use the new tables and link via `legacy_table`/`legacy_id` or `people.legacy_user_id`.
- Importing `cortex.database` no longer requires env vars; calling `session_factory()`/`init_db()` does.
- All UI text and AI-generated content is Brazilian Portuguese (pt-BR).
- `attached_assets/` contains Replit-agent prompt dumps; ignore it.
