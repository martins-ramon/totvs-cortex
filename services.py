import os
import json
from openai import OpenAI

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
    """Gera insights de IA a partir do histórico de feedbacks."""
    feedback_history = "\n\n---\n\n".join([
        f"Feedback de {fb['feedback_date']}:\n{fb['description']}"
        for fb in all_feedbacks[-5:]
    ])
    prompt = f"""Analise os dados de feedback de {employee_name} e gere insights concisos em formato JSON, em PORTUGUÊS BRASILEIRO.

Último Feedback ({latest_feedback['feedback_date']}):
{latest_feedback['description']}

Histórico de Feedbacks Anteriores:
{feedback_history}

Gere insights no seguinte formato JSON (TODO EM PORTUGUÊS):
{{
    "resumo": "Um parágrafo resumindo o último feedback de forma clara e objetiva",
    "pontos_desenvolvimento": ["ponto 1", "ponto 2", "ponto 3"],
    "fortalezas": ["força 1", "força 2", "força 3"],
    "risco_saida": {{"nivel": "baixo|medio|alto", "motivo": "explicação clara"}},
    "acoes_pendencias": ["ação 1", "ação 2"] ou [] se não houver
}}

Seja específico, acionável e foque em padrões identificados nos feedbacks. Use SEMPRE português brasileiro."""
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

def summarize_transcription(transcription: str):
    """Resume a transcrição de uma reunião."""
    prompt = f"""Resuma a seguinte transcrição de reunião em um parágrafo conciso em português do Brasil. Foque nos principais pontos discutidos, decisões tomadas e ações a serem seguidas.

Transcrição:
{transcription}"""
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
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

def generate_meeting_summary(transcription: str) -> str:
    """Gera um resumo com IA para uma transcrição de reunião."""
    client = OpenAI()
    prompt = f"Resuma de forma objetiva e estruturada a reunião a seguir:\n\n{transcription}"
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return response.choices[0].message.content.strip()