# Configuração do Supabase — Cortex

Para a migração completa, siga [DEPLOY_VERCEL.md](DEPLOY_VERCEL.md).

## Conexão

1. Abra o projeto existente no [Supabase Dashboard](https://supabase.com/dashboard/projects).
2. Em **Connect**, selecione **Transaction pooler** e copie a URI PostgreSQL, porta **6543**.
3. Substitua a senha pelo valor real, codificando caracteres especiais na URI, e use `sslmode=require`.
4. Cadastre a URI como `DATABASE_URL` em **Vercel → Project Settings → Environment Variables → Production**.

Formato ilustrativo (copie o host real do painel):

```text
postgresql://postgres.PROJECT_REF:SENHA_URL_ENCODED@aws-0-REGIAO.pooler.supabase.com:6543/postgres?sslmode=require
```

Reutilizar o mesmo projeto Supabase mantém pessoas, reuniões, combinados, checkpoints, cards, fotos e conexões Google. A Vercel hospeda a aplicação; ela não copia ou substitui o banco.

Se a origem real for um banco do Replit, será necessário exportar e restaurar esse banco no Supabase antes da troca. Trocar apenas a URI por um banco vazio não transfere dados.

## Schema

A Vercel não executa DDL automaticamente. Com `.env` preenchido e apontando para o banco correto, execute uma vez antes do primeiro deploy e quando houver mudanças de schema:

```bash
uv sync --locked
uv run --env-file .env python -m cortex.init_db
```

O comando cria tabelas/colunas ausentes e retorna código 0 em caso de sucesso; não apaga dados existentes. Não execute `cortex.drop_legacy` como parte da migração de hospedagem. Faça um backup antes de qualquer alteração no banco.

## Diagnóstico

`/healthz` consulta o banco sem escrever nele. O campo `database` pode ser:

| Estado | Ação |
|---|---|
| `ok` | Conexão e schema básico disponíveis |
| `unconfigured` | Configure `DATABASE_URL` |
| `schema_missing` | Execute a inicialização explícita no banco correto |
| `error` | Confira senha, host, porta, SSL e disponibilidade/restrições de rede no Supabase |

`db_init: skipped` é esperado na Vercel. O estado geral só será `ok` quando as demais configurações obrigatórias também estiverem presentes. Credenciais de Google/OpenAI são verificadas quanto à presença, sem chamadas pagas nem teste de autorização externa.

Referência: [Supabase — Connecting to Postgres](https://supabase.com/docs/guides/database/connecting-to-postgres).
