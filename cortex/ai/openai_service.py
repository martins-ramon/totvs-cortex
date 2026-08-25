import os
import json
import requests
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


def chunk_text(text: str, chunk_size: int = 2000, overlap: int = 200):
    """Divide um texto em chunks menores com sobreposição."""
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


def get_embedding(text_to_embed: str):
    """Gera embedding para um texto usando o modelo da OpenAI."""
    response = get_client().embeddings.create(
        model="text-embedding-3-small",
        input=text_to_embed
    )
    return response.data[0].embedding


def generate_insights_from_feedback(employee_name: str, latest_feedback: dict, all_feedbacks: list):
    """Gera insights de IA usando a persona 'Sarah' sob a ótica da Cultura TOTVS."""

    feedback_history = "\n\n---\n\n".join([
        f"Feedback de {fb['feedback_date']}:\n{fb['description']}"
        for fb in all_feedbacks[:15]
    ])

    prompt = f"""Você é **Sarah**, Consultora de Liderança e Especialista em Cultura Organizacional (TOTVS).
    Analise os dados de feedback de {employee_name} sob a lente dos 5 valores da empresa.

    **LENTE CULTURAL TOTVS:**
    1. **Gente é tudo:** O colaborador demonstra 'atitude e engajamento' ou está passivo? É inclusivo?
    2. **Cliente é pra vida:** Ele age como um 'Trusted Advisor' ou é apenas transacional?
    3. **Inovar juntos:** Ele colabora e simplifica processos ou trabalha em silos?
    4. **IH + IA:** Ele busca aprendizado contínuo e usa dados/IA?
    5. **Resultado Responsável:** Ele entrega com integridade e sustentabilidade?

    Último Feedback ({latest_feedback['feedback_date']}):
    {latest_feedback['description']}

    Histórico Recente:
    {feedback_history}

    Gere um objeto JSON em Português do Brasil.

    Formato JSON esperado:
    {{
        "agent_name": "Sarah",
        "resumo": "Síntese executiva focada na aderência cultural e entrega.",
        "pontos_desenvolvimento": ["Identifique gaps culturais (ex: Falta de 'Inovar Juntos') ou técnicos"],
        "fortalezas": ["Destaque comportamentos que exemplificam os valores (ex: 'Role model de IH+IA')"],
        "risco_saida": {{"nivel": "baixo|medio|alto", "motivo": "Analise sinais de perda de 'inquietação' ou engajamento"}},
        "acoes_pendencias": ["Sugira ações que reforcem o papel de Trusted Advisor ou aprendizado"]
    }}"""

    response = get_client().chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


def generate_bio_from_text(raw_text: str):
    """Gera uma mini bio profissional a partir de pontos-chave."""
    prompt = f"""Gere uma mini biografia profissional e concisa em primeira pessoa, em português do Brasil, com base nos seguintes pontos:

{raw_text}

A biografia deve ser fluida, profissional e adequada para um perfil como o LinkedIn. Destaque as principais competências e experiências. Não ultrapasse 3 ou 4 frases."""
    response = get_client().chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content


def summarize_transcription(transcription: str, meeting_date: str):
    """
    Gera um resumo estratégico de reunião usando a persona 'Angelo'.
    """
    try:
        if '-' in meeting_date:
            from datetime import datetime
            dt = datetime.strptime(meeting_date, '%Y-%m-%d')
            meeting_date = dt.strftime('%d/%m/%Y')
    except Exception:
        pass

    prompt = f"""Você é **Angelo**, Chief Operating Officer (COO) focado em Eficiência Radical (10x Productivity).
    A data desta reunião é {meeting_date}.

    Não faça uma ata passiva. Eu quero a **Destilação Executiva** desta conversa.

    **Sua Saída deve conter:**
    1. **O "Bottom Line":** Em uma frase, qual foi o resultado dessa reunião? (Ex: "Decidimos cancelar o projeto X para focar em Y").
    2. **Decisões Blindadas:** O que foi martelado e não se discute mais.
    3. **Pontos de Atrito:** Onde o time está patinando? Identifique gargalos citados.
    4. **Plano de Ataque (Ações):** Quem faz o quê e para quando. Use bullet points diretos.

    Se a reunião foi improdutiva, seja honesto e aponte isso no resumo.

Transcrição:
{transcription}"""

    response = get_client().chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response.choices[0].message.content


def create_chat_response(question: str, context: str):
    """Gera uma resposta de chat com base em um contexto."""
    chat_response = get_client().chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Você é um assistente que ajuda gestores a encontrar informações em feedbacks e resumos de reuniões. Responda em português brasileiro de forma clara e objetiva, usando os dados fornecidos. Se não houver informação, seja honesto."},
            {"role": "user", "content": f"Baseado nestes dados:\n\n{context}\n\nPergunta: {question}"}
        ],
        temperature=0.7, max_tokens=500
    )
    return chat_response.choices[0].message.content


def normalize_text(text: str) -> str:
    """Converte texto para minúsculas e remove acentos."""
    if not text:
        return ""
    return unidecode(text).lower()


def generate_feedback_summary(transcription: str, feedback_date: str):
    """Gera um resumo estruturado de gestão usando a persona Sarah, incluindo a data."""

    try:
        if '-' in feedback_date:
            from datetime import datetime
            dt = datetime.strptime(feedback_date, '%Y-%m-%d')
            feedback_date = dt.strftime('%d/%m/%Y')
    except Exception:
        pass

    prompt = f"""Você é **Sarah**, uma Consultora de Liderança (QI 150).
Abaixo está a transcrição bruta de uma conversa de feedback realizada em **{feedback_date}**.
Sua tarefa é transformar isso em um **Registro de Feedback Gerencial** claro, imparcial e estruturado.

Transcrição:
{transcription}

Saída (Texto corrido, profissional):
Inicie citando a data da conversa. Resuma os fatos principais, comportamentos observados e combinados feitos. Remova ruídos de fala."""

    response = get_client().chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content


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
  "foco_sugerido": "o 1 tema mais importante para a próxima conversa 1:1"
}}

Diretrizes (Project Aristotle + alta performance):
- "saude": verde = engajado e entregando; amarelo = sinais de atenção; vermelho = risco concreto.
- Considere cadência de 1:1s (distância demais = risco de conexão), combinados vencidos
  (accountability — promessas não cumpridas corroem confiança) e sentimento das conversas.
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


def generate_prep_agenda(person_name: str, prep_context: str):
    """Gera a agenda sugerida para o próximo 1:1 com base no contexto consolidado."""
    prompt = f"""Você é um coach executivo de elite ajudando um diretor a preparar o próximo 1:1
com **{person_name}**. Com base no contexto consolidado abaixo (histórico de conversas,
combinados em aberto, pontos de atenção e desenvolvimento), gere uma AGENDA PRÁTICA para a reunião,
em português do Brasil, usando markdown simples (## seções, listas com -, **negrito**).

Estrutura obrigatória:
## Check-in (5 min)
- 1-2 perguntas abertas personalizadas

## Follow-up de Combinados (10 min)
- Liste os pendentes mais críticos (marque vencidos com ⚠️)

## Temas para a Conversa (20 min)
- Tópicos priorizados a partir do histórico recente (atenção + desenvolvimento), com o porquê de cada um

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


def generate_employee_message(manager_notes: str, transcription: str, employee_name: str):
    """
    Gera mensagem de desenvolvimento (Sarah) personalizada com o nome do funcionário,
    alinhada à Cultura TOTVS.
    """
    context = f"Notas do Gestor: {manager_notes}\n"
    if transcription:
        context += f"Contexto da Conversa: {transcription}"

    prompt = f"""Você é **Sarah**, Chief People Officer (CPO) pessoal deste gestor e Guardiã da Cultura TOTVS.
    Sua missão: Transformar anotações brutas em uma comunicação de liderança inspiradora, conectando a performance do colaborador aos Valores da TOTVS.

    Colaborador: **{employee_name}**
    Dados Brutos:
    {context}

    **GUIA DE CULTURA TOTVS (Use como base para o vocabulário):**
    1. **Gente é tudo:** Valorizamos inquietação, atitude e engajamento. Fazemos acontecer (accountability) em um ambiente inclusivo.
    2. **Cliente é pra vida:** Somos "Trusted Advisors". Vamos além da tecnologia para ajudar o cliente.
    3. **Inovar juntos:** Inovação colaborativa. Simplificamos coisas complexas. Construímos oportunidades na incerteza.
    4. **IH + IA:** Somamos Inteligência Humana + Artificial. Aprendemos sempre e usamos dados para eficiência.
    5. **Resultado Responsável:** Excelência com integridade. Bom para todos, não a qualquer preço.

    **Sua Estrutura de Resposta (Tom: Coach de Elite & Parceiro de Evolução):**
    1. **Abertura Empática:** Conecte-se com o colaborador ("Gente é tudo").
    2. **O "Unlock" (Fortalezas):** Conecte o talento dele a um Valor TOTVS.
    3. **O Desafio (Pontos de Melhoria):** Apresente o próximo nível.
    4. **Call to Action:** Uma ação prática para a próxima semana focada em evolução.

    Use formatação rica, parágrafos curtos e tom inspirador."""

    response = get_client().chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content


def send_email_action(to_email: str, subject: str, html_content: str):
    """Dispara e-mail via MailerSend. Falha silenciosa (log) se não configurado."""

    api_key = os.environ.get("MAILERSEND_API_KEY")
    from_email = os.environ.get("MAILERSEND_FROM")
    if not api_key or not from_email:
        print("MailerSend não configurado (MAILERSEND_API_KEY/MAILERSEND_FROM). E-mail ignorado.")
        return False

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    if not to_email or "@" not in to_email:
        print(f"E-mail ignorado (formato inválido): {to_email}")
        return False

    payload = {
        "from": {"email": from_email, "name": "Cortex AI"},
        "to": [{"email": to_email}],
        "subject": subject,
        "html": html_content
    }

    try:
        response = requests.post(url="https://api.mailersend.com/v1/email",
                                 headers=headers, json=payload)
        if not response.ok:
            print(f"⚠️ ERRO MAILERSEND (Status {response.status_code}):")
            print(response.text)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Falha crítica no envio de e-mail: {e}")
        return False
