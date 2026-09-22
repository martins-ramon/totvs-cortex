# Cortex — Copiloto de Gestão 1:1 e Performance

Sistema web para apoio à gestão de um time de Inteligência Artificial: registro de reuniões 1:1 com transcrição, preparação inteligente das próximas conversas, checkpoints no formato Feedz e cards de acompanhamento do time gerados por IA.

Sistema single-user: apenas o diretor autentica via Google (allowlist TOTVS); liderados são perfis sem login.

## Stack

- **Backend**: Python 3.11+ / Flask (pacote `cortex/`)
- **IA**: OpenAI (`gpt-4o-mini` para extrações/cards, `gpt-4o` para agenda sugerida)
- **Banco**: PostgreSQL no Supabase (sem extensões adicionais)
- **Frontend**: HTML/CSS/JS vanilla + Alpine.js via CDN, sem build step
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
| `ALLOWED_EMAIL_DOMAINS` | Domínios autorizados (padrão: `totvs.com.br, totvs.com`) |
| `SESSION_SECRET` | Segredo das sessões Flask (defina em produção!) |

Redirect URIs do Google OAuth no Google Cloud Console:
- Login: `https://<seu-repl>.replit.app/api/auth/google/callback`
- Conexão Gmail: `https://<seu-repl>.replit.app/api/connections/gmail/callback`
- Conexão Google Agenda: `https://<seu-repl>.replit.app/api/connections/calendar/callback`

### Próximos 1:1s em Meu Time

Habilite a **Google Calendar API** no mesmo projeto Google Cloud do login e adicione o redirect URI da Agenda acima. Em **Conexões → Google Agenda**, autorize a leitura dos eventos (`calendar.events.readonly`) e a identificação da conta (`openid email`). A conexão Gmail não concede acesso à Agenda.

Em **Meu Time**, cada pessoa passa a mostrar o próximo 1:1 abaixo do tempo desde a última conversa. A consulta é feita ao abrir a seção, para os **próximos 90 dias da agenda principal**, incluindo ocorrências de eventos recorrentes. O horário aparece no fuso do navegador; eventos de dia inteiro preservam a data da agenda.

O encontro precisa ter um título como `1:1`, `1x1`, `1-1`, `1/1`, `1on1`, `one-on-one`, `one-to-one` ou `um a um` e o e-mail cadastrado do liderado entre os participantes/organizador. Eventos de grupo, cancelados ou recusados pelo diretor ou liderado são ignorados. Salas de reunião não contam como participantes. Sem e-mail ou acesso à Agenda, a tela informa a pendência; erros de consulta nunca aparecem como “sem agendamento”. Desconectar a Agenda remove os tokens locais sem revogar o acesso de outras integrações Google.

Referência da API: [Events: list — Google Calendar](https://developers.google.com/workspace/calendar/api/v3/reference/events/list).

### Limpeza de tabelas legadas

```bash
python -m cortex.drop_legacy   # idempotente; remove tabelas do FeedbackAI antigo
```

## Arquitetura (resumo)

```
app.py                  # entrypoint enxuto (Gunicorn: gunicorn app:app)
cortex/
├── __init__.py         # create_app() + /healthz
├── database.py         # engine lazy + DDL evolutivo (sem Alembic)
├── security.py         # allowlist de e-mails + login_required
├── ai/                 # camada OpenAI (4 features de IA)
├── views/              # blueprints: auth, people, sessions,
│                       #   checkpoints, cards, connections
└── drop_legacy.py      # limpeza idempotente das tabelas antigas
static/                 # SPA Alpine.js (index.html + login.html)
```

- **Startup resiliente**: falta de banco/chave OpenAI não derruba a aplicação; `/healthz` reporta `status: ok|degraded` com detalhes.
- **Schema**: `users` (diretor) + `people`, `one_on_ones`, `commitments`, `checkpoints`, `card_jobs`, `member_cards`, `connections`.

## Roadmap

- [x] **F0 — Fundações**: arquitetura em pacotes, allowlist TOTVS no login Google, schema novo + migração dos dados legados
- [x] **F1 — Núcleo 1:1**: cadastro de pessoas, registro com transcrição do Meet e extração IA automática (combinados, atenção, desenvolvimento, sentimento), tela "Preparar 1:1" com agenda sugerida
- [x] **F2 — Checkpoints Feedz**: colar texto do Feedz → IA estrutura ações/responsáveis/notas privadas/públicas
- [x] **F3 — Cards do Time**: geração assíncrona por botão com barra de progresso; saúde 🟢🟡🔴, tendência, cadência de 1:1s, accountability e riscos
- [ ] **F4 — Automação**: agendamento semanal (Scheduled Deployments ou n8n externo)

Ver `SETUP_INSTRUCTIONS.md` para detalhes de conexão com o Supabase.
