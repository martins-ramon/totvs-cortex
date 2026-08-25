# Estado atual do projeto (atualizado na Fase 0)

## Cortex — evolução do FeedbackAI

Sistema single-user para gestão de 1:1s/performance de um time de IA na TOTVS.
Apenas o diretor autentica (Google, allowlist); liderados são perfis sem login.

## Stack real (verifique o código, não documentos antigos)

- Backend: pacote `cortex/` sobre Flask. Entrypoint raiz: `app.py` (`gunicorn app:app`).
- IA: OpenAI `gpt-4o` (+ `text-embedding-3-small` nos fluxos legados).
- Banco: Supabase Postgres — usar **Transaction Pooler porta 6543**. Schema definido em `cortex/database.py` (`_LEGACY_DDL` + `_NEW_DDL`), executado no startup de forma resiliente.
- Frontend: `static/` vanilla JS sem build step.
- Automações externas (futuro): Scheduled Deployments do Replit ou n8n externo chamando endpoints em `/api/webhooks`... (ver `cortex/views/webhooks.py`).

## Comandos

- Dev: `python app.py` (porta 5000, debug)
- Produção: `gunicorn app:app --bind 0.0.0.0:5000 --workers 4 --timeout 120`
- Migração de dados: `python -m cortex.migrate_data` (idempotente)

## Secrets

`DATABASE_URL` · `OPENAI_API_KEY` · `GOOGLE_CLIENT_ID` · `GOOGLE_CLIENT_SECRET` · `SESSION_SECRET` · `ALLOWED_EMAILS` (ou `ALLOWED_EMAIL_DOMAINS`, padrão `totvs.com`) · opcionais: `MAILERSEND_API_KEY`, `MAILERSEND_FROM`, `N8N_CHAT_WEBHOOK_URL`, `INGEST_SECRET`

## Roadmap

- F1: núcleo 1:1 (pessoas + transcrição Meet + preparação IA)
- F2: checkpoints formato Feedz
- F3: cards do time assíncronos com barra de progresso
- F4: automação de agendamentos
