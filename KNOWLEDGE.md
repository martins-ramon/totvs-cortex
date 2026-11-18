# Memória estruturada de reuniões

## Descoberta e decisões

O repositório usa Flask 3, SQLAlchemy 2 com SQL explícito, Postgres/Supabase,
Alpine.js e JavaScript sem build de aplicação. `uv.lock` mantém as versões.
O frontend orquestra os jobs de cards por requisições curtas; não havia worker
durável, embeddings, pgvector, cadastro de projetos, auditoria geral ou biblioteca
de grafos. Não foi pressuposta a existência de organização, tenant, papéis ou
permissões por reunião. Usuário autenticado (`users`) e liderado (`people`) são
cadastros distintos. O acesso legado é um espaço compartilhado pelos logins
autorizados, conforme `cortex/security.py`.

| Necessidade | Estrutura existente | Decisão e arquivos |
| --- | --- | --- |
| Identidade de pessoas | `people`, `transcript_aliases` | Reusar IDs; uma entidade por `person_id`, sem fusão de homônimos. `knowledge.py`, `transcripts.py`. |
| Fontes | `one_on_ones`, `meetings`, participantes e notas | Reusar registros; snapshot imutável por conteúdo e data. CRUD enfileira na mesma transação. `views/sessions.py`, `views/meetings.py`. |
| Tarefas | `commitments`, `meeting_actions` | Reusar IDs/estados, acrescentar versão e proteção de campos humanos. A extração 1:1 deixa de apagar/recriar tarefas quando a feature está ligada. |
| Conhecimento | Sem modelo anterior equivalente | Criar entidades de assunto/projeto, afirmações, relações, evidências por offsets, revisões e conflitos. `knowledge_schema.py`, `knowledge.py`. |
| IA | Cliente OpenAI lazy, extrações e cards | Reusar cliente/modelo; validar o contrato no servidor. `ai/knowledge_service.py`. |
| Recuperação | Busca textual do assistente | Acrescentar FTS português e consulta estruturada compartilhada; sem serviço vetorial novo. `knowledge_query.py`. |
| Processamento | Cards persistidos e orquestrados por HTTP | Jobs duráveis por versão, leases/retries e retomada por HTTP/CLI; nenhuma thread abandonada após resposta. |
| Navegação/curadoria | SPA Alpine, formulários e API Flask | Tela Memória, grafo SVG, lista e painel de evidências/revisões. `static/js/knowledge.js`, `static/css/knowledge.css`, `views/knowledge.py`. |
| Derivados e exportação | `member_cards`, pautas e assistente | Token de revisão, invalidação recuperável, fontes canônicas e ZIP Markdown. `views/cards.py`, `views/sessions.py`, `assistant_context.py`. |

Estas observações vêm do código/DDL do repositório. O schema e a configuração
de produção não foram inferidos a partir de credenciais. Aplicar DDL remoto,
fazer deploy ou processar todo o histórico exige a autorização correspondente.

## Persistência e acesso

O DDL aditivo de `knowledge_schema.py` é executado pelo comando já existente
`python -m cortex.init_db`, através de `database.py`. Não há novo framework de
migrations nem DDL durante cold start na Vercel.

Novas tabelas:

- `knowledge_entities`: pessoas vinculadas com unicidade, projetos e assuntos,
  aliases e versão para edição concorrente.
- `knowledge_sources` / `knowledge_versions`: referência ao registro canônico,
  versão atual, fingerprint, texto imutável, data e fuso. Evidências usam offsets
  dessa versão; edição de interpretação não modifica a transcrição.
- `knowledge_claims`: conteúdo, classificação, papéis de autor/sujeito/responsável,
  entidades mencionadas, revisão, estado operacional, prazo e expressão original,
  tarefa canônica opcional, proteção de campos humanos e proposta de conflito.
- `knowledge_relations`: vínculos tipados, autoria automática/manual, revisão,
  controle de versão e vínculo opcional com a afirmação que sustenta a relação.
- `knowledge_revisions`: autor autenticado, antes/depois, revisão anterior,
  origem, justificativa e data. Correção humana tem proveniência própria.
- `knowledge_jobs`: uma execução por versão, lease, tentativas, cursor,
  candidatos parciais e métricas. `knowledge_outbox`: invalidações de derivados
  recuperáveis, separadas da publicação canônica.

`commitments` e `meeting_actions` recebem `knowledge_version`,
`knowledge_human_fields`, `knowledge_event_on` e `knowledge_created`; `member_cards` recebe
`knowledge_revision`. Índices cobrem fontes/versões únicas, tarefas,
entidades JSONB, FTS português, relações, revisões, jobs e outbox.

RLS é habilitada nas novas tabelas **sem políticas de acesso direto para os
papéis anon/authenticated do Supabase**. O frontend usa exclusivamente as rotas
Flask autenticadas; `DATABASE_URL` permanece no servidor. A conexão administrativa
do backend e o CLI são privilegiados. As tabelas legadas conservam seu modelo de
acesso; não foi inventada uma política por organização inexistente.
As funções internas de trigger têm execução revogada de `PUBLIC` e dos grants
diretos de `anon`, `authenticated` e `service_role`, quando esses papéis existem; não são
endpoints RPC para clientes Supabase. A conexão do backend precisa ser o
proprietário das tabelas/funções, conforme o acesso administrativo já usado.

Para o uso de diretor único, configure `KNOWLEDGE_OWNER_USER_ID` com o ID real do
diretor em `users`. A aplicação restringe os caminhos do domínio e seus
consumidores a esse usuário, inclusive com `KNOWLEDGE_ENABLED=0`. Sem a configuração, os usuários
autorizados compartilham o espaço, como no produto legado. Ser mencionado ou ter
um perfil `people` não concede login ou permissão.

## Ativação e processamento

Antes de ativar, faça backup e aplique o DDL em um banco de teste identificado.
Prepare o schema no ambiente desejado pelo processo de deploy já existente:

```sh
uv sync
uv run --env-file .env python -m cortex.init_db
```

Como alternativa ao comando, execute **todo** o arquivo
[`scripts/migrate_knowledge.sql`](scripts/migrate_knowledge.sql) no SQL Editor
do Supabase com o proprietário administrativo `postgres`. O script reúne o DDL
canônico em `BEGIN`/`COMMIT`, verifica as tabelas legadas necessárias e pode ser
reexecutado. A consulta final deve listar oito tabelas com `rls_ativa = true`.
Ele não ativa a feature nem executa backfill. Se as tabelas de fóruns ainda não
existirem, aplique `scripts/migrate_forum_meetings.sql` antes deste script.

Configure `KNOWLEDGE_ENABLED=1` e, para diretor único,
`KNOWLEDGE_OWNER_USER_ID`. A flag é lida em runtime; o padrão é desligado.
`OPENAI_API_KEY` habilita a extração. Ao salvar uma transcrição ou notas no
Cortex, o navegador inicia a atualização dessa conversa. Ao abrir **Memória**,
o Cortex retoma o que ficou pendente e inclui transcrições e notas do histórico.
A atualização avança enquanto o navegador estiver aberto e pode continuar na
próxima visita à Memória. Cada requisição faz no máximo uma chamada de IA e
registra o progresso no banco antes de responder.

A lista mostra conexões e registros com suas fontes. Abra um deles para
conferir o trecho original. Use a busca para acompanhar uma pessoa, um projeto
ou um assunto em várias conversas. O mapa oferece outra visualização dos
mesmos registros. Se alguma conversa continuar pendente após uma falha,
**Atualizar memória** retoma a atualização.

O CLI segue disponível para manutenção com limites explícitos por execução.

O backfill é somente simulação por padrão. Não registra textos nos logs:

```sh
uv run --env-file .env python -m cortex.knowledge_backfill --kind one_on_one --limit 10
uv run --env-file .env python -m cortex.knowledge_backfill --kind meeting --date-from 2026-11-01 --limit 5
```

Para aplicar **um lote autorizado** e limitar o custo a passos explícitos:

```sh
uv run --env-file .env python -m cortex.knowledge_backfill --kind one_on_one --limit 10 --max-calls 5 --apply
```

Se o proprietário não estiver configurado, informe `--user-id ID_DO_DIRETOR`.
O ID é validado no banco. Não use IDs de `people`. O comando retorna fontes,
cursores por tipo e resultados dos passos; não imprime transcrições. Retome
com `--kind meeting --after-id ULTIMO_ID`. Repetir a mesma fonte/versão reaproveita
o job e não duplica tarefas. Jobs ainda pendentes podem ser retomados em outro
lote, mesmo sem novas fontes. Mantenha o mesmo cursor até concluir os segmentos
pendentes antes de avançar `--after-id`; o filtro do cursor também restringe
quais jobs podem ser retomados. Datas e tipo se aplicam tanto ao enfileiramento
quanto à retomada. Prefira um tipo explícito para avançar os cursores. `--limit`
e `--max-calls` aceitam 1 a 100; comece com
valores pequenos e acompanhe as métricas antes de ampliar. A contagem de passos
é limite de chamadas, não promessa de custo monetário fixo.

Falhas de IA ficam registradas para retry limitado. Um lease impede publicação
concorrente; execução expirada pode ser retomada. Uma versão ultrapassada ou
uma fonte removida não pode voltar a publicar. Alterações humanas são protegidas
por campo; novas evidências incompatíveis ficam como conflito para revisão.
Cada segmento permite três tentativas. **Atualizar memória** reabre tentativas
esgotadas e preserva os segmentos já confirmados. Versões da extração anterior
que falharam ou terminaram sem registros são retomadas uma vez pelo extrator
atual. O servidor encontra a posição de citações literais únicas e mantém os
registros válidos quando outra interpretação do mesmo segmento falha.
A extração destina até 40 mil caracteres a entidades e tarefas, até 20 mil
a afirmações anteriores e divide a transcrição em segmentos de até 10 mil
caracteres. Identidades ambíguas continuam pendentes. Conflitos ficam
disponíveis para revisão.

## Consumo e propagação

`knowledge_query.recover` centraliza filtros e carrega as tarefas abertas por
consulta estruturada paginada, sem top-k vetorial. A API de tarefas devolve total
e próximo offset. A formatação do contexto tem orçamento explícito e informa
cobertura parcial; uma síntese limitada não pode afirmar que examinou tudo.

Preparações e cards recebem compromissos antigos ainda abertos e afirmações em
que a pessoa é sujeito, autor, responsável ou mencionada, inclusive em reuniões
de terceiros. Percepções mantêm autoria, condições e classificação. Data de
processamento não substitui data do evento, e reunião antiga não restaura prazo
mais recente ou editado por uma pessoa.

Fontes já processadas usam o conhecimento canônico nos consumidores. Resumos
legados e transcrições não recolocam uma interpretação rejeitada no contexto.
Fontes ainda não processadas conservam o fallback anterior, com cobertura
identificada. Se a consulta canônica estiver indisponível, o assistente omite
interpretações antigas e os dados operacionais continuam disponíveis. Cards
novos não são salvos como atuais se a revisão mudar durante a geração; pautas
e respostas também sinalizam a concorrência. Cards já existentes recebem
indicação de desatualização até serem regenerados.

O grafo e a lista consultam os mesmos registros da API, com limites e expansão
progressiva. O painel separa evidência original da interpretação editável e
exibe revisão/histórico/conflitos. Salvar exige a versão lida: outra edição gera
HTTP 409. Rejeitar retira conhecimento ativo preservando auditoria; apenas
ocultar/posicionar um nó não cria, remove ou altera relações semânticas. Editar
prazo/estado de uma afirmação vinculada a tarefa atualiza o registro canônico.
Corrigir seu texto também atualiza a descrição canônica. Alterar a classificação
ou rejeitar a única interpretação aceita cancela a tarefa criada automaticamente
sem estado humano independente. Uma tarefa previamente editada por uma pessoa
é preservada e o painel avisa que seu estado precisa ser revisto explicitamente.
Condições têm campo próprio; classificação “compromisso” e aceite confirmado
são conceitos separados. O painel permite vincular uma tarefa existente com
controle da versão da tarefa, sem inferir aceite a partir desse vínculo.

Pautas e cards recebem também alterações desde a última conversa, separando
`event_on` de `recorded_at`. O histórico mostra valores anteriores como histórico,
sem tratá-los como estado vigente. A importação tardia de uma reunião não
representa uma nova renegociação na data do processamento.

Excluir uma fonte purga suas versões/evidências e cards reconstruíveis, inclusive
com a flag desligada. Relações de conflito armazenam referências e carregam o
conteúdo no momento da inspeção, evitando cópias ocultas de uma fonte excluída.
Tarefas automáticas sem suporte nem correção humana viram registros cancelados
sem descrição/prazo derivados; seu histórico automático é removido. Ações ainda
sustentadas por outra reunião mudam a origem; quando só resta um 1:1, o vínculo
é migrado para `commitments` e suas referências/histórico acompanham a migração.
Tarefas preexistentes ou humanas permanecem registros operacionais independentes.
Se a fonte removida sustentava o prazo/estado mais recente e ainda existe suporte
antigo para a tarefa, o valor operacional não é revertido silenciosamente: surge
`task_evidence_removed`, com aviso no grafo, lista de combinados e preparação.
Os consumidores informam que esse estado precisa de confirmação. Resolver esse
conflito com justificativa registra confirmação humana e protege os campos, mesmo
quando o valor confirmado é igual ao que estava exibido.

O exportador gera ZIP Markdown a partir do estado canônico vigente, com nomes
estáveis por ID, YAML escapado, links internos, fontes e metadados de revisão.
Anotações manuais mantêm sua autoria. A projeção não é armazenamento canônico e
o produto não depende do Obsidian. Não há sincronização ou edição bidirecional.
Uma cópia já baixada não pode ser revogada remotamente.

## Desativação e verificação

Defina `KNOWLEDGE_ENABLED=0` para suspender o novo caminho sem apagar suas
tabelas. Os registros canônicos de pessoas/tarefas continuam no banco e a
aplicação usa os consumidores anteriores. A reanálise 1:1 também preserva os
combinados existentes com a memória desligada: procura a mesma pessoa, sessão,
descrição e responsável, reaproveita o ID e não modifica prazo ou estado.
Somente combinados sem correspondência são inseridos. Correções operacionais
humanas sobrevivem ao desligamento; a consulta ao grafo e suas interpretações
curadas volta a ser usada quando a feature é reativada. A restrição de
`KNOWLEDGE_OWNER_USER_ID` permanece válida durante o desligamento. Não descarte
tabelas ou revisões como procedimento de desligamento.

Validação do projeto:

```sh
uv run python -m unittest discover -s tests -v
node --check static/js/knowledge.js
node --check static/js/app.js
uv run python scripts/build_static.py
```

O projeto não configura lint, typecheck ou CI. Testes com IA simulada validam
contrato, rastreabilidade e persistência, sem comprovar qualidade de extração
do provedor real. Validação com banco local não comprova o schema remoto.
Não se deve executar backfill massivo de produção para testar. Os jobs expõem
métricas disponíveis de latência, tokens, tentativas e revisão; não há promessa
de ganho sem medição.

A suíte de Postgres é opt-in e recusa bancos que não sejam locais e chamados
`cortex_knowledge_test`. Ela redefine as fixtures desse banco; use um banco
descartável dedicado. Exemplo após criar esse banco no seu Postgres de teste:

```sh
KNOWLEDGE_TEST_DATABASE_URL=postgresql://localhost:5432/cortex_knowledge_test uv run python -m unittest discover -s tests -p test_knowledge_postgres.py -v
```

O cenário fictício usa 17/11/2026 como referência, fontes de 10, 12 e 16/11,
Jean já cadastrado, prazo 20→24→26/11 e percepção atribuída à Marina. Os testes
exercitam persistência real, replay, edições concorrentes, curadoria, cards,
pautas, autorização/RLS, exclusão, retomada e ZIP com links verificáveis. As
respostas de IA são simuladas e passam pelo validador real. A verificação HTTP
local confirma páginas e APIs; ela não substitui a inspeção da renderização.

A busca usa texto e vínculos registrados no Postgres. A aplicação mantém
separadas as identidades ambíguas e usa a versão vigente de cada fonte. A
atualização depende das requisições do navegador ou do CLI.
