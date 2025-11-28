import os
import json
from openai import OpenAI
from unidecode import unidecode

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
    """Gera insights de IA usando a persona 'Sarah'."""

    # Prepara o histórico (excluindo o último para não duplicar no contexto, se desejar, ou mantendo para contexto total)
    # Aqui mantemos os 5 últimos cronológicos
    feedback_history = "\n\n---\n\n".join([
        f"Feedback de {fb['feedback_date']}:\n{fb['description']}"
        for fb in all_feedbacks[:15] # Pega os 15 mais recentes da lista já ordenada
    ])

    prompt = f"""Você é **Sarah**, uma Consultora de Liderança e Alta Performance (QI 150).
Analise os dados de feedback de {employee_name}.

Último Feedback ({latest_feedback['feedback_date']}):
{latest_feedback['description']}

Histórico Recente:
{feedback_history}

Gere um objeto JSON em Português do Brasil com insights estratégicos sobre este colaborador.
O "resumo" deve ser uma síntese executiva do ÚLTIMO feedback informado acima.

Formato JSON esperado:
{{
    "agent_name": "Sarah",
    "resumo": "Síntese clara e direta do feedback de {latest_feedback['feedback_date']}.",
    "pontos_desenvolvimento": ["ponto 1", "ponto 2", "ponto 3"],
    "fortalezas": ["força 1", "força 2", "força 3"],
    "risco_saida": {{"nivel": "baixo|medio|alto", "motivo": "explicação baseada em fatos"}},
    "acoes_pendencias": ["ação sugerida 1", "ação sugerida 2"]
}}"""

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
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
        model="gpt-4o-mini",
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
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response.choices[0].message.content

def create_chat_response(question: str, context: str):
    """Gera uma resposta de chat com base em um contexto."""
    chat_response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
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

def generate_feedback_summary(transcription: str):
    """Gera um resumo estruturado de gestão usando a persona Sarah."""
    prompt = f"""Você é **Sarah**, uma Consultora de Liderança (QI 150).
Abaixo está a transcrição bruta de uma conversa de feedback ou anotações soltas de um gestor.
Sua tarefa é transformar isso em um **Registro de Feedback Gerencial** claro, imparcial e estruturado.

Transcrição:
{transcription}

Saída (Texto corrido, profissional):
Resuma os fatos principais, comportamentos observados e combinados feitos. Remova ruídos de fala."""

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

def generate_employee_message(manager_notes: str, transcription: str, employee_name: str):
    """
    Gera mensagem de desenvolvimento (Sarah) personalizada com o nome do funcionário.
    """
    context = f"Notas do Gestor: {manager_notes}\n"
    if transcription:
        context += f"Contexto da Conversa: {transcription}"

    prompt = f"""Você é **Sarah**, Chief People Officer (CPO) pessoal deste gestor.
    Sua missão: Transformar anotações brutas em uma comunicação de liderança inspiradora e de alto impacto (Nível Executivo).

    Colaborador: **{employee_name}**
    Dados Brutos:
    {context}

    **Sua Estrutura de Resposta (Growth Mindset):**
    1. **Abertura Empática:** Conecte-se com o colaborador.
    2. **O "Unlock" (Fortalezas):** Não apenas elogie, mostre como o talento dele impacta o negócio (Gatilho de Propósito).
    3. **O Desafio (Pontos de Melhoria):** Não aponte erros. Apresente o próximo nível de performance que ele pode atingir. Seja direta, mas encorajadora.
    4. **Call to Action (Próximos Passos):** Sugira 1 ação prática para a próxima semana.

    Use formatação rica, parágrafos curtos e tom de "Coach de Elite"."""

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content