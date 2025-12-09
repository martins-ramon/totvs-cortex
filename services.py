import os
import json
from openai import OpenAI
from unidecode import unidecode
import requests

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is not set")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ✅ NOVO: Função para quebrar o texto em pedaços (chunks)
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
    response = openai_client.embeddings.create(
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
    1. **Gente é tudo:** O colaborador demonstra 'atitude e engajamento' ou está passivo? É inclusivo? [cite: 62, 66]
    2. **Cliente é pra vida:** Ele age como um 'Trusted Advisor' ou é apenas transacional? [cite: 72]
    3. **Inovar juntos:** Ele colabora e simplifica processos ou trabalha em silos? [cite: 77]
    4. **IH + IA:** Ele busca aprendizado contínuo e usa dados/IA? [cite: 83, 84]
    5. **Resultado Responsável:** Ele entrega com integridade e sustentabilidade? [cite: 90]

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

    response = openai_client.chat.completions.create(
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
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

def summarize_transcription(transcription: str, meeting_date: str):
    """
    Gera um resumo estratégico de reunião usando a persona 'Angelo'.
    """
    # Garante formatação da data se possível
    try:
        # Tenta converter YYYY-MM-DD para DD/MM/YYYY se necessário
        if '-' in meeting_date:
            from datetime import datetime
            dt = datetime.strptime(meeting_date, '%Y-%m-%d')
            meeting_date = dt.strftime('%d/%m/%Y')
    except:
        pass # Mantém como veio se der erro

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

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response.choices[0].message.content

def create_chat_response(question: str, context: str):
    """Gera uma resposta de chat com base em um contexto."""
    chat_response = openai_client.chat.completions.create(
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
    print("executando generate_feedback_summary")

    # Formata a data se vier no padrão YYYY-MM-DD para ficar mais natural no texto
    try:
        if '-' in feedback_date:
            from datetime import datetime
            dt = datetime.strptime(feedback_date, '%Y-%m-%d')
            feedback_date = dt.strftime('%d/%m/%Y')
            print("converteu")
    except:
        pass

    prompt = f"""Você é **Sarah**, uma Consultora de Liderança (QI 150).
Abaixo está a transcrição bruta de uma conversa de feedback realizada em **{feedback_date}**.
Sua tarefa é transformar isso em um **Registro de Feedback Gerencial** claro, imparcial e estruturado.

Transcrição:
{transcription}

Saída (Texto corrido, profissional):
Inicie citando a data da conversa. Resuma os fatos principais, comportamentos observados e combinados feitos. Remova ruídos de fala."""

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

def generate_employee_message(manager_notes: str, transcription: str, employee_name: str):
    """
    Gera mensagem de desenvolvimento (Sarah) personalizada com o nome do funcionário,
    alinhada à Cultura TOTVS (Gente é tudo, Cliente é pra vida, Inovar Juntos, IH+IA, Resultado Responsável).
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
    1. **Gente é tudo:** Valorizamos inquietação, atitude e engajamento. Fazemos acontecer (accountability) em um ambiente inclusivo[cite: 61, 62].
    2. **Cliente é pra vida:** Somos "Trusted Advisors". Vamos além da tecnologia para ajudar o cliente[cite: 67, 72].
    3. **Inovar juntos:** Inovação colaborativa. Simplificamos coisas complexas. Construímos oportunidades na incerteza[cite: 73, 77].
    4. **IH + IA:** Somamos Inteligência Humana + Artificial. Aprendemos sempre e usamos dados para eficiência[cite: 80, 83].
    5. **Resultado Responsável:** Excelência com integridade. Bom para todos, não a qualquer preço[cite: 86, 90].

    **Sua Estrutura de Resposta (Tom: Coach de Elite & Parceiro de Evolução):**
    1. **Abertura Empática:** Conecte-se com o colaborador ("Gente é tudo").
    2. **O "Unlock" (Fortalezas):** Conecte o talento dele a um Valor TOTVS. (Ex: "Sua capacidade de usar dados mostra o valor IH+IA na prática").
    3. **O Desafio (Pontos de Melhoria):** Apresente o próximo nível. (Ex: "Para ser um verdadeiro Trusted Advisor, precisamos evoluir em...").
    4. **Call to Action:** Uma ação prática para a próxima semana focada em evolução.

    Use formatação rica, parágrafos curtos e tom inspirador."""

    response = openai_client.chat.completions.create(
        model="gpt-4o", # Mantido gpt-4o para maior qualidade na redação cultural
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

MAILERSEND_API_KEY = os.environ.get('MAILERSEND_API_KEY')
if not MAILERSEND_API_KEY:
    raise ValueError("MAILERSEND_API_KEY environment variable is not set")

MAILERSEND_FROM = os.environ.get('MAILERSEND_FROM')
if not MAILERSEND_FROM:
    raise ValueError("MAILERSEND_FROM environment variable is not set")

def send_email_action(to_email: str, subject: str, html_content: str):
    """Dispara e-mail via MailerSend (Ação do Angelo)."""

    url = "https://api.mailersend.com/v1/email"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MAILERSEND_API_KEY}"
    }

    payload = {
        "from": {"email": MAILERSEND_FROM, "name": "Cortex AI"},
        "to": [{"email": to_email}], # Envia para o próprio usuário (ou lista, se expandirmos)
        "subject": subject,
        "html": html_content
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Erro ao enviar email: {e}")
        raise e