import json
import os
from unidecode import unidecode

_client = None


def get_client():
    """Cliente OpenAI criado sob demanda (não derruba a aplicação na importação)."""
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY não configurada. Defina o Secret no Replit."
            )
        from openai import OpenAI

        _client = OpenAI(api_key=api_key)
    return _client


def normalize_text(text: str) -> str:
    """Converte texto para minúsculas e remove acentos."""
    if not text:
        return ""
    return unidecode(text).lower()


def extract_meeting_insights(person_name: str, meeting_date: str, transcript: str):
    """Extrai insights estruturados de uma transcrição de 1:1 (gpt-4o-mini, JSON).

    Retorna dict com: resumo, sentimento, topicos, combinados
    (descricao/responsavel/prazo), pontos_atencao, pontos_desenvolvimento,
    conquistas.
    """
    prompt = f"""Você é um assistente de gestão sênior. Analise a transcrição abaixo de uma reunião 1:1
entre um diretor (gestor) e sua liderada/liderado **{person_name}**, realizada em {meeting_date}.

Extraia as informações em JSON válido, em português do Brasil. Regras:
- "resumo": síntese objetiva da conversa (3-5 frases).
- "sentimento": um de ["positivo", "neutro", "preocupante"] — tom geral da conversa.
- "topicos": lista curta dos assuntos discutidos (máx. 6).
- "combinados": compromissos/acordos com ações concretas que ficaram definidos.
  Para cada um: {{"descricao": "...", "responsavel": "gestor" ou "liderado", "prazo": "YYYY-MM-DD" ou null}}.
  Se não houver prazo explícito, use null (não invente datas). Se não houver combinados, lista vazia.
- "pontos_atencao": sinais de alerta (risco de saída, sobrecarga, conflitos, desmotivação).
- "pontos_desenvolvimento": gaps técnicos/comportamentais e oportunidades de crescimento.
- "conquistas": entregas, aprendizados e vitórias recentes a reconhecer.

Não invente informação ausente; prefira listas vazias a conjecturas.

Transcrição:
{transcript}"""

    response = get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return json.loads(response.choices[0].message.content)


def parse_checkpoint(raw_text: str, person_name: str):
    """Estrutura um texto colado do Feedz no formato de checkpoint.

    Retorna dict: {acoes: [{acao, responsavel}], notas_privadas, notas_publicas}.
    """
    prompt = f"""Você é um assistente de gestão. Abaixo está um conteúdo copiado do sistema Feedz
referente a uma reunião de checkpoint/avaliação de performance do colaborador **{person_name}**.

Estruture o conteúdo em JSON válido, em português do Brasil:

{{
  "acoes": [{{"acao": "descrição da ação combinada", "responsavel": "quem ficou responsável"}}],
  "notas_privadas": "anotações privadas do gestor (somente para ele)",
  "notas_publicas": "anotações públicas/compartilhadas com o colaborador"
}}

Regras:
- "acoes": liste TODAS as ações/tarefas combinadas encontradas. Se não houver nenhuma, lista vazia.
  Em "responsavel", normalize para "gestor" quando se referir ao gestor/diretor/eu,
  para "liderado" quando se referir a {person_name}/colaborador, ou mantenha o nome citado.
- "notas_privadas": texto destinado apenas ao gestor. Se não houver seção privada identificável,
  string vazia.
- "notas_publicas": feedback/anotações visíveis ao colaborador. Se não houver, string vazia.
- Não invente informações ausentes.

CONTEÚDO DO FEEDZ:
{raw_text}"""

    response = get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return json.loads(response.choices[0].message.content)


def extract_email_insights(person_name: str, email_context: str):
    """Extrai pendências, to-dos e assuntos em andamento das threads de e-mail."""
    prompt = f"""Você é um assistente de gestão. Abaixo estão threads de e-mail em que
**{person_name}** participa. Extraia o que ainda está em andamento, em JSON válido,
em português do Brasil:

{{
  "pendencias": ["itens aguardando resposta, decisão ou desbloqueio"],
  "todos": ["ações concretas mencionadas, ainda em aberto"],
  "assuntos": ["temas/projetos importantes em curso nas threads"]
}}

Regras:
- Só use o que estiver nas threads. Não invente.
- Prefira listas vazias a conjecturas.
- Seja específico (cite o assunto da thread quando ajudar).
- Máximo 8 itens por lista.

THREADS:
{email_context}"""

    response = get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return json.loads(response.choices[0].message.content)


def generate_prep_agenda(person_name: str, prep_context: str):
    """Gera a agenda sugerida para o próximo 1:1 com base no contexto consolidado."""
    prompt = f"""Você é um coach executivo de elite ajudando um diretor a preparar o próximo 1:1
com **{person_name}**. Com base no contexto consolidado abaixo (histórico de conversas,
combinados em aberto, e-mails em andamento, pontos de atenção e desenvolvimento), gere uma
AGENDA PRÁTICA para a reunião, em português do Brasil, usando markdown simples
(## seções, listas com -, **negrito**).

Estrutura obrigatória:
## Check-in (5 min)
- 1-2 perguntas abertas personalizadas

## Follow-up de Combinados (10 min)
- Liste os pendentes mais críticos (marque vencidos com ⚠️)

## Inbox e pendências de e-mail (5-10 min)
- Pendências, to-dos e assuntos em andamento nas threads recentes
- Se não houver e-mails no contexto, omita esta seção

## Temas para a Conversa (20 min)
- Tópicos priorizados a partir do histórico recente (atenção + desenvolvimento + inbox), com o porquê de cada um

## Reconhecimento (5 min)
- Conquistas recentes específicas a celebrar

## Alinhamento e Próximos Passos (5 min)
- Fechamento sugerido e possíveis novos combinados

Seja específico e cite fatos do contexto. NÃO invente informações.

CONTEXTO CONSOLIDADO:
{prep_context}"""

    response = get_client().chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return response.choices[0].message.content


def generate_member_card(person_name: str, prep_context: str):
    """Gera o card de acompanhamento de um membro do time (JSON estruturado).

    Fundamentação: Project Aristotle (Google), accountability radical e
    práticas de gestão de times de alta performance.
    """
    prompt = f"""Você é um conselheiro de gestão de times de alta performance. Analise o contexto
consolidado abaixo da liderada/liderado **{person_name}** e gere o CARD DE ACOMPANHAMENTO em JSON válido,
em português do Brasil.

{{
  "saude": "verde|amarelo|vermelho",
  "tendencia": "subindo|estavel|caindo",
  "resumo": "2-3 frases objetivas sobre o momento da pessoa",
  "pontos_atencao": ["sinais de alerta: sobrecarga, desengajamento, conflitos, risco de saída"],
  "desenvolvimento": ["focos de crescimento ativos"],
  "conquistas": ["vitórias recentes a reconhecer"],
  "riscos": [{{"nivel": "baixo|medio|alto", "descricao": "risco identificado"}}],
  "foco_sugerido": "o 1 tema mais importante para a próxima conversa 1:1",
  "emails": {{
    "pendencias": ["itens aguardando resposta, decisão ou desbloqueio nas threads"],
    "todos": ["ações concretas mencionadas nos e-mails, ainda em andamento"],
    "assuntos": ["temas importantes em curso nas threads (projetos, entregas, alinhamentos)"]
  }}
}}

Diretrizes (Project Aristotle + alta performance):
- "saude": verde = engajado e entregando; amarelo = sinais de atenção; vermelho = risco concreto.
- Considere cadência de 1:1s (distância demais = risco de conexão), combinados vencidos
  (accountability — promessas não cumpridas corroem confiança) e sentimento das conversas.
- Se houver seção E-MAILS no contexto, extraia pendências, to-dos e assuntos em "emails".
  Use esses sinais também em pontos_atencao, riscos e foco_sugerido quando forem materiais.
- Se não houver e-mails no contexto, retorne "emails" com listas vazias — nunca invente threads.
- Seja específico citando fatos do contexto; nunca invente informações.
- "riscos" pode ser lista vazia se não houver riscos reais.

CONTEXTO CONSOLIDADO:
{prep_context}"""

    response = get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    return json.loads(response.choices[0].message.content)
