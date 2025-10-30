from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text
from database import SessionLocal
import services
import json

def get_user_context_for_agent(db, user_id):
    """Busca dados de feedbacks e reuniões para o agente."""
    
    feedbacks_res = db.execute(
        text("SELECT description, feedback_date FROM feedbacks WHERE employee_id = :uid ORDER BY feedback_date DESC LIMIT 5"),
        {"uid": user_id}
    ).fetchall()
    feedbacks = [f"Data: {f[1]}, Feedback: {f[0]}" for f in feedbacks_res]

    meetings_res = db.execute(
        text("SELECT summary, meeting_date FROM meetings WHERE user_id = :uid ORDER BY meeting_date DESC LIMIT 5"),
        {"uid": user_id}
    ).fetchall()
    meetings = [f"Data: {m[1]}, Resumo: {m[0]}" for m in meetings_res]

    feedbacks_text = '\n'.join(feedbacks)
    meetings_text = '\n'.join(meetings)
    return f"Contexto de Feedbacks Recebidos:\n{feedbacks_text}\n\nContexto de Reuniões:\n{meetings_text}"

def run_career_mentor_agent(user_id, agent):
    """Lógica proativa do agente Momentum."""
    db = SessionLocal()
    try:
        context = get_user_context_for_agent(db, user_id)
        if not context.strip():
            print(f"Sem contexto para o usuário {user_id}, pulando.")
            return

        last_insight = db.execute(
            text("""
                SELECT id FROM agent_conversations 
                WHERE user_id = :uid AND agent_id = :aid AND is_proactive_insight = TRUE AND is_read = FALSE
            """),
            {"uid": user_id, "aid": agent['id']}
        ).fetchone()
        
        if last_insight:
            print(f"Usuário {user_id} já possui um insight pendente.")
            return

        insight = services.generate_agent_insight(agent['prompt'], context)
        
        if insight and insight.get("proactive_message"):
            db.execute(
                text("""
                    INSERT INTO agent_conversations 
                    (user_id, agent_id, message_text, is_from_agent, is_proactive_insight, is_read, related_action_data)
                    VALUES (:uid, :aid, :msg, TRUE, TRUE, FALSE, :action_data)
                """),
                {
                    "uid": user_id,
                    "aid": agent['id'],
                    "msg": insight["proactive_message"],
                    "action_data": json.dumps(insight.get("action_data")) if insight.get("action_data") else None
                }
            )
            db.commit()
            print(f"Novo insight gerado para o usuário {user_id}")

    except Exception as e:
        db.rollback()
        print(f"Erro ao rodar agente para usuário {user_id}: {e}")
    finally:
        db.close()

def agent_job():
    """Trabalho que roda periodicamente para todos os usuários."""
    print("Iniciando verificação proativa dos agentes...")
    db = SessionLocal()
    try:
        users = db.execute(text("SELECT id FROM users")).fetchall()
        mentor_agent_res = db.execute(text("SELECT id, personality_prompt FROM agents WHERE type = 'career_mentor'")).fetchone()
        
        if not mentor_agent_res:
            print("Agente 'career_mentor' não encontrado.")
            return
            
        mentor_agent = {"id": mentor_agent_res[0], "prompt": mentor_agent_res[1]}

        for user in users:
            run_career_mentor_agent(user[0], mentor_agent)

    finally:
        db.close()

def init_scheduler(app):
    """Inicializa o scheduler."""
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(agent_job, 'interval', hours=4)
    scheduler.start()
