# Migração do Cortex: Replit → Vercel

O código está preparado para a Vercel. As etapas abaixo configuram sua conta, os segredos e o domínio de produção; nenhum deploy é executado apenas por alterar estes arquivos.

## 1. Preserve o banco e as integrações

- Faça um backup do banco atual e mantenha o Replit disponível até validar a nova hospedagem.
- Confirme que `DATABASE_URL` aponta para o Supabase existente, pelo **Transaction Pooler na porta 6543**, com SSL. Reutilize esse banco na Vercel para manter todos os dados e tokens Google.
- Se estiver usando um banco fornecido pelo Replit, migre os dados para o Supabase antes de desativá-lo. Consulte [SETUP_INSTRUCTIONS.md](SETUP_INSTRUCTIONS.md).
- Reutilize o mesmo cliente OAuth Google e a chave OpenAI. Não adicione segredos aos arquivos versionados.

## 2. Importe o repositório

Envie as alterações para seu repositório Git. Na Vercel, abra **Add New → Project**, selecione o repositório e use:

| Campo | Valor |
|---|---|
| Framework Preset | `Flask` |
| Root Directory | Raiz do repositório (`./`) |
| Python | `3.13`, definido em `.python-version` |
| Build Command | `python scripts/build_static.py`, definido em `vercel.json` |
| Install Command | Padrão da Vercel; usa `pyproject.toml` e `uv.lock` |
| Output Directory | Padrão do framework; não sobrescrever |
| Entrypoint | `app.py`, exportando `app` |
| Fluid Compute | Habilitado |

Não configure `python app.py` nem Gunicorn como Build Command. Escolha a região da função próxima à região do seu Supabase em **Settings → Functions**.

O build gera `public/static/` a partir de `static/css`, `static/js` e `static/images`. Esse diretório é servido pela CDN, preservando as URLs `/static/...`. As páginas `/`, `/login` e `/connections`, assim como a API, são servidas pelo Flask. Não é necessário criar rewrites nem um diretório `api/` adicional.

## 3. Cadastre as variáveis de ambiente

Em **Settings → Environment Variables**, configure para **Production**:

| Nome | Valor |
|---|---|
| `DATABASE_URL` | URI do Supabase atual, porta 6543 e `sslmode=require` |
| `OPENAI_API_KEY` | Sua chave OpenAI |
| `GOOGLE_CLIENT_ID` | ID do cliente OAuth existente |
| `GOOGLE_CLIENT_SECRET` | Segredo do mesmo cliente |
| `SESSION_SECRET` | Segredo longo, estável, compartilhado por todas as instâncias |
| `ALLOWED_EMAILS` | Seu e-mail exato; se houver mais de um, separe por vírgulas |
| `ALLOWED_EMAIL_DOMAINS` | Deixe vazio para restringir aos e-mails exatos acima |
| `APP_BASE_URL` | Origem HTTPS final, ex.: `https://seu-projeto.vercel.app`, sem caminho |
| `AUTO_INIT_DB` | `false` (já é o padrão na Vercel) |

Para gerar um novo segredo de sessão, execute localmente e copie o resultado somente para a configuração privada:

```bash
python -c 'import secrets; print(secrets.token_hex(32))'
```

`SESSION_SECRET` não pode usar o valor de desenvolvimento. Um novo domínio exige novo login mesmo que o segredo seja preservado. Na Vercel os cookies são `Secure`, `HttpOnly` e `SameSite=Lax`.

Se `ALLOWED_EMAILS` e `ALLOWED_EMAIL_DOMAINS` estiverem ambos vazios, o comportamento existente permite os domínios `totvs.com.br` e `totvs.com`. Preencha o e-mail exato para restringir ao diretor.

Se a URL definitiva ainda não estiver disponível, faça o primeiro deploy para obtê-la, configure `APP_BASE_URL`, cadastre os callbacks abaixo e faça **Redeploy** antes de testar login. Mudanças de variáveis exigem novo deployment.

Para Preview, use banco de teste e URL estável própria, com os callbacks correspondentes. Não faça login em uma URL aleatória de preview configurada com o `APP_BASE_URL` de produção: o cookie de OAuth pertence ao domínio onde o fluxo começou. A proteção de deployments da Vercel também pode exigir autenticação antes de abrir o preview.

## 4. Atualize o Google OAuth

No [Google Cloud Console](https://console.cloud.google.com/apis/credentials), edite o cliente OAuth do tipo aplicação Web e adicione estas **Authorized redirect URIs**, substituindo `seu-dominio` pelo host usado em `APP_BASE_URL`:

```text
https://seu-dominio/api/auth/google/callback
https://seu-dominio/api/connections/gmail/callback
https://seu-dominio/api/connections/calendar/callback
```

Mantenha temporariamente os callbacks antigos do Replit para permitir retorno à hospedagem anterior. Se o cliente tiver uma lista de origens JavaScript autorizadas, atualize também a origem `https://seu-dominio`. Confira a configuração de domínio/consentimento aplicável ao seu cliente OAuth.

Mantenha as APIs Gmail e Google Calendar habilitadas. O Gmail usa `gmail.readonly`; a Agenda usa `calendar.events.readonly`, `openid` e `email`.

Usando o mesmo banco e cliente OAuth, os refresh tokens continuam armazenados. Reconecte uma integração apenas se ela pedir nova autorização. Para desenvolvimento local, use `APP_BASE_URL=http://localhost:5000` e cadastre os mesmos três caminhos sob essa origem no cliente de desenvolvimento.

## 5. Prepare o schema

Não há criação de tabelas no build nem no cold start da Vercel. Faça a preparação explícita com o banco correto antes de usar a aplicação:

```bash
uv sync --locked
cp .env.example .env
# Edite .env com seus valores reais antes do próximo comando.
uv run --env-file .env python -m cortex.init_db
```

O comando é idempotente, preserva dados e termina com código 0 quando todas as etapas concluem. Não execute o comando de remoção de tabelas legadas durante esta migração.

Para instalações já em uso que precisam somente do módulo de reuniões de fóruns, [scripts/migrate_forum_meetings.sql](scripts/migrate_forum_meetings.sql) contém o SQL idempotente para executar diretamente no PostgreSQL/Supabase. Aplique antes de publicar a nova versão; `/healthz` verifica também as tabelas de reuniões, participantes e ações.

## 6. Faça deploy e valide

Após cadastrar as variáveis e callbacks, use **Deploy** ou **Redeploy** na Vercel. Abra a URL de produção definida em `APP_BASE_URL`.

1. Consulte `/healthz`: deve retornar HTTP **200**, `status: ok` e `database: ok`. `db_init: skipped` é normal. HTTP 503 indica configuração incompleta, indisponibilidade do banco ou schema pendente; confira os campos e os logs da função.
2. Abra `/login`, entre com sua conta autorizada e confirme que os dados do time aparecem.
3. Verifique estilos, imagens e navegação; abra `/connections` diretamente para confirmar a página de retorno OAuth.
4. Confira Gmail e Google Agenda em **Conexões** e o próximo 1:1 em **Meu Time**.
5. Gere um card e uma pauta com IA. Esses testes consomem as APIs conforme o uso normal.
6. Crie e edite um registro de teste, recarregue a página e confirme sua persistência; remova o registro de teste ao terminar.
7. Registre uma reunião de fórum, associe participantes e analise o conteúdo. Revise e adicione uma ação sugerida, altere seu status e confira o contexto no dashboard, perfil do participante, pauta e assistente.

O diagnóstico verifica presença das chaves e acesso ao banco; não comprova a validade das credenciais OpenAI/Google. Os testes funcionais acima completam essa validação.

## Limites operacionais

- A função tem `maxDuration: 300` segundos. Chamadas OpenAI usam timeout de 60 segundos, sem retries automáticos; Gmail e Agenda têm orçamento de 60 segundos para iniciar suas consultas e limitam o timeout de cada chamada ao tempo restante. Esses timeouts de rede medem inatividade e não são um cancelamento rígido por tempo total. Falhas externas retornam pelo tratamento normal da aplicação.
- Os cards já são gerados por requisições individuais, com progresso persistido no banco e coordenação pelo navegador. Mantenha a aba aberta até concluir o lote; não há worker ou cron configurado.
- O filesystem da função não guarda dados da aplicação. Fotos, transcrições, tokens e resultados permanecem no PostgreSQL.
- APIs e diagnóstico enviam `Cache-Control: no-store`. Assets sem hash são revalidados a cada acesso, evitando prender o navegador a versões antigas.

## Troca definitiva e retorno

Após validar, passe a usar a URL Vercel ou configure seu domínio em **Settings → Domains** e aplique os registros DNS indicados. Se mudar o domínio, atualize `APP_BASE_URL` e os três callbacks Google e faça redeploy.

Desative a hospedagem Replit somente após essa validação e remova depois os callbacks antigos. Se precisar retornar, reabra a hospedagem anterior usando o mesmo Supabase. Os arquivos `.replit` e `replit.md` foram preservados como referência e excluídos do envio via CLI para a Vercel.

## Verificação do código

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/build_static.py
```

Os testes não usam credenciais reais nem alteram o banco. A validação do build remoto e das integrações depende do primeiro deploy na sua conta.

Referências oficiais: [Flask na Vercel](https://vercel.com/docs/frameworks/backend/flask), [runtime Python](https://vercel.com/docs/functions/runtimes/python), [duração das funções](https://vercel.com/docs/functions/configuring-functions/duration), [variáveis de ambiente](https://vercel.com/docs/environment-variables).
