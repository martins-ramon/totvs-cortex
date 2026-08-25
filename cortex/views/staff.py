import json

from flask import Blueprint, request, jsonify
from sqlalchemy import text

from ..database import session_factory
from .. import ai
from ..security import login_required

bp = Blueprint("staff", __name__, url_prefix="/api")


def upsert_agent(db, agent_name, role_hint="Assistente Inteligente"):
    """Cria ou atualiza o agente baseado na atividade."""
    result = db.execute(
        text(
            "UPDATE agents SET last_active_at = CURRENT_TIMESTAMP WHERE name = :name RETURNING id"
        ), {"name": agent_name})
    if result.rowcount == 0:
        styles = ['blue', 'purple', 'green', 'orange', 'pink']
        style = styles[len(agent_name) % len(styles)]

        db.execute(
            text("""
                INSERT INTO agents (name, role, description, avatar_style, last_active_at)
                VALUES (:name, :role, 'Agente autônomo do Cortex.', :style, CURRENT_TIMESTAMP)
            """), {
                "name": agent_name,
                "role": role_hint,
                "style": style
            })


@bp.route('/insights/<int:insight_id>/approve', methods=['POST'])
@login_required
def approve_insight_action(insight_id):
    db = session_factory()
    try:
        data = db.execute(
            text("""
                SELECT i.action_payload, u.email
                FROM agent_insights i
                JOIN users u ON i.user_id = u.id
                WHERE i.id = :id AND i.user_id = :uid
            """),
            {"id": insight_id, "uid": session['user_id']}
        ).fetchone()

        if not data or not data[0]:
            return jsonify({"error": "Insight não encontrado ou sem ação pendente"}), 404

        payload = json.loads(data[0])
        user_email = data[1]
        action_type = payload.get('type')

        if action_type == 'UPDATE_FEEDBACK':
            target_feedback_id = payload.get('feedback_id')
            draft_message = payload.get('draft_message')

            if target_feedback_id and draft_message:
                db.execute(text("UPDATE feedbacks SET feedback_for_employee = :msg WHERE id = :fid"),
                           {"msg": draft_message, "fid": target_feedback_id})

                emp_res = db.execute(
                    text("SELECT employee_id FROM feedbacks WHERE id = :fid"),
                    {"fid": target_feedback_id}
                ).fetchone()

                if emp_res:
                    db.execute(text("""
                        INSERT INTO notifications (user_id, actor_id, title, message, link, type)
                        VALUES (:uid, :aid, 'Novo Feedback', 'Seu gestor compartilhou uma mensagem de desenvolvimento com você.', '/feedbacks', 'SYSTEM')
                    """), {
                        "uid": emp_res[0],
                        "aid": session['user_id']
                    })

        elif action_type == 'SEND_EMAIL':
            subject = payload.get('subject')
            html_body = payload.get('html_body')

            if subject and html_body:
                ai.send_email_action(user_email, subject, html_body)

        db.execute(
            text("UPDATE agent_insights SET is_archived = TRUE WHERE id = :id"),
            {"id": insight_id}
        )

        db.commit()
        return jsonify({"success": True})

    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@bp.route('/agents', methods=['GET'])
@login_required
def get_agents():
    user_id = session['user_id']
    db = session_factory()
    try:
        query = text("""
            SELECT a.id, a.name, a.role, a.description, a.avatar_style, a.last_active_at,
            (
                SELECT COUNT(*)
                FROM agent_insights i
                WHERE i.agent_name = a.name
                  AND i.user_id = :uid
                  AND i.is_archived = FALSE
                  AND i.created_at >= CURRENT_DATE
            ) as insights_today,
            (
                SELECT COUNT(*)
                FROM agent_insights i
                WHERE i.agent_name = a.name
                  AND i.user_id = :uid
                  AND i.is_archived = FALSE
            ) as total_insights
            FROM agents a
            WHERE EXISTS (
                SELECT 1 FROM agent_insights i
                WHERE i.agent_name = a.name AND i.user_id = :uid
            )
            ORDER BY a.last_active_at DESC
        """)

        result = db.execute(query, {"uid": user_id})
        agents = [{
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "description": r[3],
            "style": r[4],
            "last_active": r[5].isoformat() if r[5] else None,
            "insights_today": r[6],
            "total_insights": r[7]
        } for r in result.fetchall()]
        return jsonify({"agents": agents})
    finally:
        db.close()


@bp.route('/agents/<string:agent_name>/insights', methods=['GET'])
@login_required
def get_agent_insights(agent_name):
    user_id = session['user_id']
    db = session_factory()
    try:
        query = text("""
            SELECT id, title, observation, solution_proposal, severity, category, created_at, action_payload
            FROM agent_insights
            WHERE user_id = :uid
              AND agent_name = :agent
              AND is_archived = FALSE
            ORDER BY created_at DESC
            LIMIT 50
        """)

        result = db.execute(query, {"uid": user_id, "agent": agent_name})

        insights = []
        for r in result.fetchall():
            insights.append({
                "id": r[0],
                "title": r[1],
                "observation": r[2],
                "solution": r[3],
                "severity": r[4],
                "type": r[5],
                "created_at": r[6].isoformat(),
                "action_payload": r[7]
            })

        return jsonify({"insights": insights})
    finally:
        db.close()


@bp.route('/insights/<int:insight_id>/archive', methods=['PUT'])
@login_required
def archive_insight(insight_id):
    db = session_factory()
    try:
        result = db.execute(
            text("UPDATE agent_insights SET is_archived = TRUE WHERE id = :id AND user_id = :uid"),
            {"id": insight_id, "uid": session['user_id']}
        )
        db.commit()
        if result.rowcount > 0:
            return jsonify({"success": True})
        return jsonify({"error": "Insight not found"}), 404
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()
