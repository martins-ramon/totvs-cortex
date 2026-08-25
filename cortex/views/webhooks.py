"""Endpoints de ingestão para automações externas (ex.: n8n).

ATENÇÃO: estes endpoints não usam login. Proteja-os definindo o Secret
`INGEST_SECRET` — quando presente, a requisição deve trazer o header
`X-Ingest-Secret` com o mesmo valor.
"""
import json

from flask import Blueprint, request, jsonify
from sqlalchemy import text

from ..database import session_factory
from .. import ai

bp = Blueprint("webhooks", __name__, url_prefix="/api")


def _ingest_authorized():
    secret = os_environ_get("INGEST_SECRET")
    if not secret:
        return True  # compatibilidade: sem secret configurado, porta aberta
    return request.headers.get("X-Ingest-Secret") == secret


def os_environ_get(name):
    import os
    return os.environ.get(name)


@bp.route('/import/feedback', methods=['POST'])
def import_feedback():
    """
    Endpoint dedicado para importação de feedbacks em lote (ex: via n8n).
    """
    if not _ingest_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    employee_id = data.get('employee_id')
    manager_id = data.get('manager_id')
    description = data.get('description')
    feedback_date = data.get('feedback_date')

    if not all([employee_id, manager_id, description, feedback_date]):
        return jsonify({
            "error":
            "Campos obrigatórios ausentes: employee_id, manager_id, description, feedback_date"
        }), 400

    db = session_factory()
    try:
        temporal_context = f"Feedback de {feedback_date}: {description}"
        embedding = ai.get_embedding(temporal_context)

        result = db.execute(
            text("""
                INSERT INTO feedbacks (employee_id, manager_id, description, feedback_date, embedding)
                VALUES (:eid, :mid, :d, :fd, :e)
                RETURNING id
            """), {
                "eid": employee_id,
                "mid": manager_id,
                "d": description,
                "fd": feedback_date,
                "e": str(embedding)
            })
        db.commit()

        feedback_id = result.fetchone()[0]
        return jsonify({"success": True, "feedback_id": feedback_id}), 201

    except Exception as e:
        db.rollback()
        if "foreign key constraint" in str(e).lower():
            return jsonify({
                "success":
                False,
                "error":
                "ID de funcionário (employee_id) ou gestor (manager_id) inválido. O usuário não existe."
            }), 400
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db.close()


@bp.route('/insights/ingest', methods=['POST'])
def ingest_ai_insights():
    """
    Endpoint exclusivo para o N8N.
    Salva na tabela 'agent_insights' e atualiza o 'agents'.
    """
    if not _ingest_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.json
        user_id = data.get('user_id')
        agent_output = data.get('agent_output', {})

        if isinstance(agent_output, str):
            try:
                agent_output = json.loads(agent_output)
            except json.JSONDecodeError:
                return jsonify({"error": "Invalid JSON in agent_output"}), 400

        if not user_id:
            return jsonify({"error": "user_id required"}), 400
        if not agent_output.get('has_insight'):
            return jsonify({"success": True}), 200

        agent_name = agent_output.get('agent_name', 'Cortex AI')

        db = session_factory()
        try:
            role = "Assistente Inteligente"
            if "Sarah" in agent_name:
                role = "Consultora de Liderança"
            if "Angelo" in agent_name:
                role = "Estrategista Corporativo"

            from .staff import upsert_agent
            upsert_agent(db, agent_name, role)

            insights_list = agent_output.get('insights', [])
            for item in insights_list:
                payload_json = json.dumps(item.get('action_payload')) if item.get('action_payload') else None

                db.execute(
                    text("""
                        INSERT INTO agent_insights
                        (user_id, agent_name, title, observation, solution_proposal, severity, category, action_payload)
                        VALUES (:uid, :agent, :title, :obs, :sol, :sev, :cat, :payload)
                    """), {
                        "uid": user_id,
                        "agent": agent_name,
                        "title": item.get('title', 'Insight'),
                        "obs": item.get('observation', ''),
                        "sol": item.get('solution_proposal', ''),
                        "sev": item.get('severity', 'MEDIA'),
                        "cat": item.get('type', 'GERAL'),
                        "payload": payload_json
                    })

            db.execute(
                text("INSERT INTO notifications (user_id, type, title, message) VALUES (:uid, 'SYSTEM', :title, :msg)"),
                {"uid": user_id, "title": f"Novos insights de {agent_name}", "msg": "Acesse o Meu Conselho Estratégico para ver os detalhes."}
            )

            db.commit()
            return jsonify({"success": True, "count": len(insights_list)})
        except Exception as e:
            db.rollback()
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            db.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
