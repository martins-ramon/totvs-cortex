# AGENTS.md

Cortex: Flask + vanilla JS app para gestão de 1:1s e performance de time. Single-user: apenas o diretor faz login (Google), liderados são perfis sem login. O legado do FeedbackAI foi removido (tabelas e rotas).

## Commands

- Install deps: `uv sync` (Python >= 3.11; `uv.lock` is the lockfile)
- Dev server: `python app.py` → http://localhost:5000 (debug mode, port 5000)
- Production: `gunicorn app:app --bind 0.0.0.0:5000 --workers 4 --timeout 120`
- Drop legacy tables: `python -m cortex.drop_legacy` (idempotent; requires DATABASE_URL)
- No tests, lint, typecheck, or CI configured. Verification = booting the app and exercising endpoints manually (`/healthz` reports config/db status).

## Required environment

- `DATABASE_URL`: Supabase Postgres URI — Transaction Pooler port **6543**, not 5432.
- `OPENAI_API_KEY`: needed for all AI features (`gpt-4o-mini` extraction/cards, `gpt-4o` agenda).
- `ALLOWED_EMAILS`: exact e-mails allowed to log in (comma-separated). `ALLOWED_EMAIL_DOMAINS`: domains allowed (default `totvs.com` when neither is set). OAuth callback redirects non-allowed users to `/login?error=forbidden`.
- Optional: `SESSION_SECRET`, `GOOGLE_CLIENT_ID/SECRET`.

## Architecture

Root `app.py` is a thin entrypoint exposing `app = create_app()` for Gunicorn. All code lives in the `cortex/` package:

- `cortex/__init__.py` — `create_app()` factory; registers blueprints; `/healthz`
- `cortex/database.py` — lazy engine/session factory; DDL lists `_AUTH_DDL` (users) + `_NEW_DDL` (people, one_on_ones, commitments, checkpoints, card_jobs, member_cards) + `_EVOLUTION_DDL`; sets `INIT_DB_STATUS`; **no pgvector dependency**
- `cortex/security.py` — `login_required`, email allowlist (`email_allowed`, `director_emails`)
- `cortex/ai/openai_service.py` — lazy OpenAI client + the 4 AI features: `extract_meeting_insights`, `parse_checkpoint`, `generate_prep_agenda`, `generate_member_card`
- `cortex/views/` — blueprints (all under `/api`): `auth` (local login + Google OAuth), `people` (people CRUD under `/api/people`), `sessions` (core 1:1 model: `/api/oneonones`, AI extraction, commitments, prep/agenda), `checkpoints` (Feedz-style checkpoints), `cards` (async batch flow generate-start/person/generate-finish over `card_jobs`), `connections` (integrações: Gmail OAuth somente-leitura com tokens persistidos na tabela `connections`; demais ferramentas "em breve")
- `cortex/drop_legacy.py` — idempotent cleanup of old FeedbackAI tables

Frontend: `static/` Alpine.js SPA with no build step (CDN). `/` serves `index.html`, `/login` serves `login.html`; logic in `static/js/app.js` + `auth.js`.

## Gotchas

- Startup is resilient BY DESIGN: missing DB/OpenAI keys never crash import or boot; they surface as errors at request time and in `/healthz` (`status: degraded`). Do not reintroduce import-time raises.
- No migrations framework: schema changes go into `_NEW_DDL`/`_EVOLUTION_DDL` lists in `database.py`.
- Date parsing in PUT endpoints must happen inside try/except ValueError → return 400 (three bugs of this class were fixed already).
- Alpine's `x-show` removes the inline display property when showing an element — never rely on inline `style="display:flex"` for modals; use `.modal.open { display:flex }` CSS + `:class="{open: state}"` instead.
- Google OAuth: login usa `/api/auth/google/callback`; conexão Gmail usa `/api/connections/gmail/callback` (ambos precisam estar nas Authorized redirect URIs do Google Cloud). Credenciais lidas em runtime via `_google_creds()` (testável).
- All UI text and AI-generated content is Brazilian Portuguese (pt-BR).
- `attached_assets/` contains Replit-agent prompt dumps; ignore it.
