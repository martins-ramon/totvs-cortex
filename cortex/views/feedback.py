import json

from flask import Blueprint, request, jsonify
from sqlalchemy import text

from ..database import session_factory
from .. import ai
from ..security import login_required

bp = Blueprint("feedback", __name__, url_prefix="/api")


@bp.route('/feedbacks', methods=['POST'])
@login_required
def create_feedback():
    db = session_factory()
    try:
        data = request.json
        transcription = data.get('transcription', '')
        feedback_for_employee = data.get('feedback_for_employee', '')

        description, feedback_date = data.get('description'), data.get(
            'feedback_date')

        if not description:
            return jsonify({"error": "Description is required"}), 400

        temporal_context = f"Feedback de {feedback_date}: {description}"
        embedding = ai.get_embedding(temporal_context)

        result = db.execute(
            text("""
                INSERT INTO feedbacks
                (employee_id, manager_id, description, transcription, feedback_for_employee, feedback_date, embedding)
                VALUES (:eid, :mid, :d, :tr, :fe, :fd, :e)
                RETURNING id
            """), {
                "eid": data['user_id'],
                "mid": session['user_id'],
                "d": description,
                "tr": transcription,
                "fe": feedback_for_employee,
                "fd": feedback_date,
                "e": str(embedding)
            })

        # Notifica o funcionário se houver mensagem para ele (In-App + E-mail)
        if feedback_for_employee and feedback_for_employee.strip():
            db.execute(text("""
                INSERT INTO notifications (user_id, actor_id, title, message, link, type)
                VALUES (:uid, :aid, 'Novo Feedback', 'Seu gestor registrou uma mensagem de desenvolvimento para você.', '/feedbacks', 'SYSTEM')
            """), {
                "uid": data['user_id'],
                "aid": session['user_id']
            })

            try:
                emp_data = db.execute(
                    text("SELECT name, email FROM users WHERE id = :uid"),
                    {"uid": data['user_id']}
                ).fetchone()

                manager_data = db.execute(
                    text("SELECT name FROM users WHERE id = :mid"),
                    {"mid": session['user_id']}
                ).fetchone()

                if emp_data and emp_data[1]:
                    emp_name = emp_data[0]
                    emp_email = emp_data[1]
                    manager_name = manager_data[0] if manager_data else "Seu Gestor"

                    subject = f"Cortex: Novo Feedback de {manager_name}"
                    html_body = f"""
                    <div style="font-family: sans-serif; color: #1E293B; max-width: 600px;">
                        <h2 style="color: #6366F1;">Cortex</h2>
                        <p>Olá, <strong>{emp_name}</strong>.</p>
                        <p>{manager_name} acabou de registrar um feedback de desenvolvimento para você.</p>
                        <div style="background-color: #F0FDF4; border-left: 4px solid #10B981; padding: 15px; margin: 20px 0; color: #064E3B;">
                            "Nova mensagem de desenvolvimento disponível."
                        </div>
                        <p>Acesse a plataforma para ler o conteúdo completo e interagir.</p>
                        <p style="margin-top: 25px;">
                            <a href="{request.host_url.rstrip('/')}" style="background-color: #6366F1; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px;">Ler Feedback</a>
                        </p>
                    </div>
                    """
                    ai.send_email_action(emp_email, subject, html_body)

            except Exception as e:
                print(f"Erro ao enviar e-mail de feedback: {e}")

        db.commit()
        return jsonify({"success": True, "feedback_id": result.fetchone()[0]})
    finally:
        db.close()


@bp.route('/dashboard', methods=['GET'])
@login_required
def dashboard():
    db = session_factory()
    try:
        result = db.execute(
            text(
                "SELECT u.id, u.name, u.company, u.phone FROM users u WHERE u.manager_id = :manager_id ORDER BY u.name"
            ), {"manager_id": session['user_id']})
        users = [{
            "user_id": row[0],
            "user_name": row[1],
            "company": row[2],
            "phone": row[3]
        } for row in result.fetchall()]
        return jsonify({"users": users})
    finally:
        db.close()


@bp.route('/user/<int:user_id>/insights', methods=['GET'])
@login_required
def user_insights(user_id):
    manager_id = session['user_id']
    db = session_factory()
    try:
        user_check = db.execute(
            text(
                "SELECT id, name, company FROM users WHERE id = :uid AND manager_id = :mid"
            ), {
                "uid": user_id,
                "mid": manager_id
            }).fetchone()
        if not user_check:
            return jsonify({"error": "User not managed by you"}), 404

        last_feedback_res = db.execute(
            text(
                "SELECT description, feedback_date, created_at FROM feedbacks WHERE employee_id = :eid ORDER BY feedback_date DESC, id DESC LIMIT 1"
            ), {
                "eid": user_id
            }).fetchone()

        if not last_feedback_res:
            return jsonify({
                "user_id": user_id,
                "user_name": user_check[1],
                "latest_feedback": None
            })

        last_update_res = db.execute(
            text(
                "SELECT MAX(created_at) FROM feedbacks WHERE employee_id = :eid"
            ), {
                "eid": user_id
            }).fetchone()
        last_update_ts = last_update_res[
            0] if last_update_res else last_feedback_res[2]

        cached_insight = db.execute(
            text(
                "SELECT insight_data, source_feedback_timestamp FROM insights WHERE employee_id = :eid AND manager_id = :mid"
            ), {
                "eid": user_id,
                "mid": manager_id
            }).fetchone()

        latest_feedback_obj = {
            "description": last_feedback_res[0],
            "feedback_date": last_feedback_res[1].isoformat()
        }

        if cached_insight and cached_insight[1] >= last_update_ts:
            insights = json.loads(cached_insight[0])
            if "agent_name" not in insights:
                insights["agent_name"] = "Sarah"

            return jsonify({
                "user_id": user_id,
                "user_name": user_check[1],
                "company": user_check[2],
                "latest_feedback": latest_feedback_obj,
                "insights": insights
            })

        all_feedbacks_res = db.execute(
            text(
                "SELECT description, feedback_date FROM feedbacks WHERE employee_id = :eid ORDER BY feedback_date DESC"
            ), {"eid": user_id})
        all_feedbacks = [{
            "description": fb[0],
            "feedback_date": fb[1].isoformat()
        } for fb in all_feedbacks_res.fetchall()]

        new_insights = ai.generate_insights_from_feedback(
            user_check[1], latest_feedback_obj, all_feedbacks)
        new_insights_json = json.dumps(new_insights)

        db.execute(
            text("""
                INSERT INTO insights (employee_id, manager_id, insight_data, source_feedback_timestamp, generated_at)
                VALUES (:eid, :mid, :data, :ts, CURRENT_TIMESTAMP)
                ON CONFLICT (employee_id, manager_id)
                DO UPDATE SET insight_data = EXCLUDED.insight_data, source_feedback_timestamp = EXCLUDED.source_feedback_timestamp, generated_at = CURRENT_TIMESTAMP
            """), {
                "eid": user_id,
                "mid": manager_id,
                "data": new_insights_json,
                "ts": last_update_ts
            })
        db.commit()

        return jsonify({
            "user_id": user_id,
            "user_name": user_check[1],
            "company": user_check[2],
            "latest_feedback": latest_feedback_obj,
            "insights": new_insights
        })
    finally:
        db.close()


@bp.route('/user/<int:user_id>/feedbacks', methods=['GET'])
@login_required
def user_feedbacks(user_id):
    db = session_factory()
    try:
        result = db.execute(
            text(
                "SELECT id, description, feedback_date, transcription, feedback_for_employee FROM feedbacks WHERE employee_id = :eid AND manager_id = :mid ORDER BY feedback_date DESC"
            ), {
                "eid": user_id,
                "mid": session['user_id']
            })
        feedbacks = [
            {
                "id": row[0],
                "description": row[1],
                "feedback_date": row[2].isoformat(),
                "transcription": row[3],
                "feedback_for_employee": row[4]
            } for row in result.fetchall()
        ]
        return jsonify({"feedbacks": feedbacks})
    finally:
        db.close()


@bp.route('/feedbacks/<int:feedback_id>', methods=['PUT'])
@login_required
def update_feedback(feedback_id):
    data = request.json
    description = data.get('description')
    feedback_date = data.get('feedback_date')

    transcription = data.get('transcription', '')
    feedback_for_employee = data.get('feedback_for_employee', '')

    if not description or not feedback_date:
        return jsonify({"error": "Dados incompletos"}), 400

    db = session_factory()
    try:
        check = db.execute(
            text(
                "SELECT id, employee_id FROM feedbacks WHERE id = :fid AND manager_id = :mid"
            ), {
                "fid": feedback_id,
                "mid": session['user_id']
            }).fetchone()

        if not check:
            return jsonify(
                {"error": "Feedback não encontrado ou acesso negado"}), 404

        temporal_context = f"Feedback de {feedback_date}: {description}"
        embedding = ai.get_embedding(temporal_context)

        db.execute(
            text("""
                UPDATE feedbacks
                SET description = :desc,
                    feedback_date = :date,
                    transcription = :trans,
                    feedback_for_employee = :emp_msg,
                    embedding = :emb
                WHERE id = :fid
            """), {
                "desc": description,
                "date": feedback_date,
                "trans": transcription,
                "emp_msg": feedback_for_employee,
                "emb": str(embedding),
                "fid": feedback_id
            })

        if feedback_for_employee and feedback_for_employee.strip():
            emp_res = db.execute(
                text("""
                    SELECT f.employee_id, u.name, u.email
                    FROM feedbacks f
                    JOIN users u ON f.employee_id = u.id
                    WHERE f.id = :fid
                """),
                {"fid": feedback_id}
            ).fetchone()

            if emp_res:
                emp_id = emp_res[0]
                emp_name = emp_res[1]
                emp_email = emp_res[2]

                db.execute(text("""
                    INSERT INTO notifications (user_id, actor_id, title, message, link, type)
                    VALUES (:uid, :aid, 'Feedback Atualizado', 'Seu gestor atualizou seu feedback de desenvolvimento.', '/feedbacks', 'SYSTEM')
                """), {
                    "uid": emp_id,
                    "aid": session['user_id']
                })

                try:
                    if emp_email:
                        manager_name_res = db.execute(
                            text("SELECT name FROM users WHERE id = :uid"),
                            {"uid": session['user_id']}
                        ).fetchone()
                        manager_name = manager_name_res[0] if manager_name_res else "Seu Gestor"

                        subject = f"Cortex: Feedback Atualizado por {manager_name}"
                        html_body = f"""
                        <div style="font-family: sans-serif; color: #1E293B; max-width: 600px;">
                            <h2 style="color: #6366F1;">Cortex</h2>
                            <p>Olá, <strong>{emp_name}</strong>.</p>
                            <p>{manager_name} atualizou as orientações no seu feedback de desenvolvimento.</p>
                            <p style="margin-top: 25px;">
                                <a href="{request.host_url.rstrip('/')}" style="background-color: #6366F1; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px;">Ver Atualizações</a>
                            </p>
                        </div>
                        """
                        ai.send_email_action(emp_email, subject, html_body)
                except Exception as e:
                    print(f"Erro ao enviar e-mail de atualização de feedback: {e}")

        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db.close()


@bp.route('/feedbacks/generate-summary', methods=['POST'])
def generate_feedback_summary_route():
    data = request.json
    text_input = data.get('transcription', '')
    feedback_date = data.get('feedback_date', 'Data não informada')
    if not text_input:
        return jsonify({"error": "No text provided"}), 400
    try:
        summary = ai.generate_feedback_summary(text_input, feedback_date)
        return jsonify({"result": summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/feedbacks/generate-employee-msg', methods=['POST'])
def generate_employee_msg_route():
    data = request.json
    description = data.get('description', '')
    transcription = data.get('transcription', '')
    employee_name = data.get('employee_name', 'Colaborador')

    if not description and not transcription:
        return jsonify({"error": "No context provided"}), 400

    try:
        msg = ai.generate_employee_message(description, transcription,
                                           employee_name)
        return jsonify({"result": msg})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/my-received-feedbacks', methods=['GET'])
@login_required
def get_my_received_feedbacks():
    user_id = session['user_id']
    db = session_factory()
    try:
        result = db.execute(
            text("""
                SELECT f.feedback_date, f.feedback_for_employee, u.name as manager_name
                FROM feedbacks f
                JOIN users u ON f.manager_id = u.id
                WHERE f.employee_id = :uid
                  AND f.feedback_for_employee IS NOT NULL
                  AND f.feedback_for_employee != ''
                ORDER BY f.feedback_date DESC
            """), {"uid": user_id})
        feedbacks = [{
            "date": r[0].isoformat(),
            "message": r[1],
            "manager": r[2]
        } for r in result.fetchall()]
        return jsonify({"feedbacks": feedbacks})
    finally:
        db.close()
