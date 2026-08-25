# Cortex — Copiloto de Gestão 1:1 e Performance

Sistema web para apoio à gestão de um time de Inteligência Artificial: registro de reuniões 1:1 com transcrição, preparação inteligente das próximas conversas, checkpoints no formato Feedz e cards de acompanhamento do time gerados por IA.

> Projeto em evolução (Fase 0 concluída — fundações da nova arquitetura). O sistema anterior (FeedbackAI) permanece operacional durante a transição; os dados legados são preservados e migrados para o novo modelo.

## Stack

- **Backend**: Python 3.11+ / Flask (pacote `cortex/`)
- **IA**: OpenAI (`gpt-4o` para análise, `text-embedding-3-small` para embeddings legados)
- **Banco**: PostgreSQL no Supabase (extensão pgvector habilitada)
- **Frontend**: HTML/CSS/JS vanilla, sem build step
- **Hospedagem**: Replit (Autoscale)

## Como rodar

```bash
uv sync              # instala dependências
python app.py        # servidor de desenvolvimento em http://localhost:5000
```

Produção (Replit): `gunicorn app:app --bind 0.0.0.0:5000 --workers 4 --timeout 120`

### Variáveis de ambiente (Secrets no Replit)

| Secret | Uso |
|---|---|
| `DATABASE_URL` | Postgres Supabase — **Transaction Pooler, porta 6543** (não usar 5432) |
| `OPENAI_API_KEY` | Geração de resumos/insights e embeddings |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Login com Google Workspace (TOTVS) |
| `ALLOWED_EMAILS` | E-mails autorizados a logar (ex.: o seu) |
| `ALLOWED_EMAIL_DOMAINS` | Domínios autorizados (padrão: `totvs.com`) |
| `SESSION_SECRET` | Segredo das sessões Flask (defina em produção!) |

Redirect URI do Google OAuth: `https://<seu-repl>.replit.app/api/auth/google/callback`

### Migração de dados legados

```bash
python -m cortex.migrate_data   # idempotente; preserva tabelas antigas intactas
```

Define `DIRECTOR_EMAILS` (ou `ALLOWED_EMAILS`) antes de rodar — esses usuários não são convertidos em "pessoas".

## Arquitetura (resumo)

```
app.py                  # entrypoint enxuto (Gunicorn: gunicorn app:app)
cortex/
├── __init__.py         # create_app() + /healthz
├── database.py         # engine lazy + DDL legado/novo (sem Alembic)
├── security.py         # allowlist de e-mails + login_required
├── ai/                 # camada OpenAI + e-mail (MailerSend)
├── views/              # blueprints: auth, people, feedback,
│                       #   oneonones, notifications, staff, webhooks
└── migrate_data.py     # migração legado -> novo schema
static/                 # SPA vanilla JS (index.html + login.html)
```

- **Startup resiliente**: falta de banco/chave OpenAI não derruba a aplicação; `/healthz` reporta `status: ok|degraded` com detalhes.
- **Schema novo sem pgvector**: `people`, `one_on_ones`, `commitments`, `checkpoints`, `card_jobs`, `member_cards`.
- **Schema legado preservado**: `users`, `feedbacks`, `meetings`, `meeting_chunks`, `insights`, `agents`, `agent_insights`, `notifications`.

## Roadmap

- [x] **F0 — Fundações**: arquitetura em pacotes, allowlist TOTVS no login Google, schema novo + migração dos dados legados
- [x] **F1 — Núcleo 1:1**: cadastro de pessoas, registro com transcrição do Meet e extração IA automática (combinados, atenção, desenvolvimento, sentimento), tela "Preparar 1:1" com agenda sugerida
- [x] **F2 — Checkpoints Feedz**: colar texto do Feedz → IA estrutura ações/responsáveis/notas privadas/públicas
- [x] **F3 — Cards do Time**: geração assíncrona por botão com barra de progresso; saúde 🟢🟡🔴, tendência, cadência de 1:1s, accountability e riscos
- [ ] **F4 — Automação**: agendamento semanal (Scheduled Deployments ou n8n externo)

Ver `SETUP_INSTRUCTIONS.md` para detalhes de conexão com o Supabase.
