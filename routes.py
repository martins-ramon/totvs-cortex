import os
import json
import base64
import time
from flask import Blueprint, request, jsonify, session, Response
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from database import SessionLocal
from services import generate_meeting_summary
import services
import requests

api_bp = Blueprint('api', __name__, url_prefix='/api')

def hash_password(password):
    return generate_password_hash(password)

# --- ROTAS DE USUÁRIO E PERFIL ---
@api_bp.route('/register', methods=['POST'])
def register():
    data = request.json
    db = SessionLocal()
    try:
        result = db.execute(
            text("INSERT INTO users (email, password_hash, name, company, phone) VALUES (:email, :password_hash, :name, :company, :phone) RETURNING id"),
            {"email": data['email'], "password_hash": hash_password(data['password']), "name": data['name'], "company": data.get('company', ''), "phone": data.get('phone', '')}
        )
        db.commit()
        user_id = result.fetchone()[0]
        session['user_id'] = user_id
        return jsonify({"success": True, "user_id": user_id})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        db.close()

@api_bp.route('/login', methods=['POST'])
def login():
    data = request.json
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, name, company, password_hash, email FROM users WHERE email = :email"),
            {"email": data['email']}
        )
        user = result.fetchone()
        if user and check_password_hash(user[3], data['password']):
            session['user_id'] = user[0]
            return jsonify({"success": True, "user": {"id": user[0], "name": user[1], "company": user[2], "email": user[4]}})
        else:
            return jsonify({"success": False, "error": "Invalid credentials"}), 401
    finally:
        db.close()

@api_bp.route('/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    return jsonify({"success": True})

@api_bp.route('/current-user', methods=['GET'])
def current_user():
    if 'user_id' not in session:
        return jsonify({"authenticated": False}), 401
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, name, email, company, phone, manager_id, mini_bio FROM users WHERE id = :id"),
            {"id": session['user_id']}
        )
        user = result.fetchone()
        if user:
            return jsonify({
                "authenticated": True,
                "user": {"id": user[0], "name": user[1], "email": user[2], "company": user[3], "phone": user[4], "manager_id": user[5], "mini_bio": user[6]}
            })
        else:
            session.pop('user_id', None)
            return jsonify({"authenticated": False}), 401
    finally:
        db.close()

@api_bp.route('/users', methods=['GET'])
def get_users():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, name, email, company FROM users WHERE id != :current_user_id ORDER BY name"),
            {"current_user_id": session['user_id']}
        )
        users = [{"id": row[0], "name": row[1], "email": row[2], "company": row[3]} for row in result.fetchall()]
        return jsonify({"users": users})
    finally:
        db.close()

@api_bp.route('/managed-users', methods=['GET'])
def get_managed_users():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, name, email, company, phone FROM users WHERE manager_id = :manager_id ORDER BY name"),
            {"manager_id": session['user_id']}
        )
        managed_users = [{"id": row[0], "name": row[1], "email": row[2], "company": row[3], "phone": row[4]} for row in result.fetchall()]
        return jsonify({"managed_users": managed_users})
    finally:
        db.close()

@api_bp.route('/profile', methods=['PUT'])
def update_profile():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE users SET name = :name, company = :company, phone = :phone, manager_id = :manager_id, mini_bio = :mini_bio WHERE id = :id"),
            {"id": session['user_id'], "name": data['name'], "company": data.get('company', ''), "phone": data.get('phone', ''), "manager_id": data.get('manager_id'), "mini_bio": data.get('mini_bio')}
        )
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        db.close()

@api_bp.route('/profile/password', methods=['PUT'])
def change_password():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    current_password, new_password = data.get('current_password'), data.get('new_password')
    if not current_password or not new_password: return jsonify({"success": False, "error": "Todos os campos são obrigatórios"}), 400
    db = SessionLocal()
    try:
        user = db.execute(text("SELECT password_hash FROM users WHERE id = :id"), {"id": session['user_id']}).fetchone()
        if not user: return jsonify({"success": False, "error": "Usuário não encontrado"}), 404
        if not check_password_hash(user[0], current_password): return jsonify({"success": False, "error": "Senha atual incorreta"}), 401
        new_password_hash = hash_password(new_password)
        db.execute(text("UPDATE users SET password_hash = :new_hash WHERE id = :id"), {"new_hash": new_password_hash, "id": session['user_id']})
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/profile/generate-bio', methods=['POST'])
def generate_bio():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    raw_text = data.get('raw_text')
    if not raw_text: return jsonify({"error": "Raw text is required"}), 400
    try:
        bio = services.generate_bio_from_text(raw_text)
        return jsonify({"bio": bio})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route('/profile/photo', methods=['POST', 'GET'])
def profile_photo():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        if request.method == 'POST':
            photo_data = request.json.get('photo').split(',')[1]
            photo_bytes = base64.b64decode(photo_data)
            db.execute(text("UPDATE users SET profile_photo = :photo WHERE id = :uid"), {"photo": photo_bytes, "uid": session['user_id']})
            db.commit()
            return jsonify({"success": True})
        else:
            row = db.execute(text("SELECT profile_photo FROM users WHERE id = :uid"), {"uid": session['user_id']}).fetchone()
            if row and row[0]:
                photo_base64 = base64.b64encode(row[0]).decode('utf-8')
                return jsonify({"photo": f"data:image/jpeg;base64,{photo_base64}"})
            return jsonify({"photo": None})
    finally:
        db.close()

@api_bp.route('/user/<int:user_id>/photo', methods=['GET'])
def user_photo(user_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT profile_photo FROM users WHERE id = :uid"), {"uid": user_id}).fetchone()
        if row and row[0]:
            photo_base64 = base64.b64encode(row[0]).decode('utf-8')
            return jsonify({"photo": f"data:image/jpeg;base64,{photo_base64}"})
        return jsonify({"photo": None})
    finally:
        db.close()

# --- ROTAS DE FEEDBACKS E DASHBOARD ---
@api_bp.route('/feedbacks', methods=['POST'])
def create_feedback():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        data = request.json
        description, feedback_date = data.get('description'), data.get('feedback_date')
        if not description: return jsonify({"error": "Description is required"}), 400
        temporal_context = f"Feedback de {feedback_date}: {description}"
        embedding = services.get_embedding(temporal_context)
        result = db.execute(
            text("INSERT INTO feedbacks (employee_id, manager_id, description, feedback_date, embedding) VALUES (:eid, :mid, :d, :fd, :e) RETURNING id"),
            {"eid": data['user_id'], "mid": session['user_id'], "d": description, "fd": feedback_date, "e": str(embedding)}
        )
        db.commit()
        return jsonify({"success": True, "feedback_id": result.fetchone()[0]})
    finally:
        db.close()

# ✅ ROTA CORRIGIDA (ADICIONADA NOVAMENTE)
@api_bp.route('/dashboard', methods=['GET'])
def dashboard():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT u.id, u.name, u.company, u.phone FROM users u WHERE u.manager_id = :manager_id ORDER BY u.name"),
            {"manager_id": session['user_id']}
        )
        users = [{"user_id": row[0], "user_name": row[1], "company": row[2], "phone": row[3]} for row in result.fetchall()]
        return jsonify({"users": users})
    finally:
        db.close()

@api_bp.route('/user/<int:user_id>/insights', methods=['GET'])
def user_insights(user_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    manager_id = session['user_id']
    db = SessionLocal()
    try:
        user_check = db.execute(
            text("SELECT id, name, company FROM users WHERE id = :uid AND manager_id = :mid"),
            {"uid": user_id, "mid": manager_id}
        ).fetchone()
        if not user_check:
            return jsonify({"error": "User not managed by you"}), 404

        last_feedback_res = db.execute(
            text("SELECT description, feedback_date, created_at FROM feedbacks WHERE employee_id = :eid ORDER BY created_at DESC LIMIT 1"),
            {"eid": user_id}
        ).fetchone()

        if not last_feedback_res:
            return jsonify({"user_id": user_id, "user_name": user_check[1], "latest_feedback": None})

        latest_feedback_ts = last_feedback_res[2]
        cached_insight = db.execute(
            text("SELECT insight_data, source_feedback_timestamp FROM insights WHERE employee_id = :eid AND manager_id = :mid"),
            {"eid": user_id, "mid": manager_id}
        ).fetchone()

        if cached_insight and cached_insight[1] >= latest_feedback_ts:
            insights = json.loads(cached_insight[0])
            latest_feedback = {"description": last_feedback_res[0], "feedback_date": last_feedback_res[1].isoformat()}
            return jsonify({
                "user_id": user_id, "user_name": user_check[1], "company": user_check[2], 
                "latest_feedback": latest_feedback, "insights": insights
            })

        all_feedbacks_res = db.execute(
            text("SELECT description, feedback_date FROM feedbacks WHERE employee_id = :eid ORDER BY feedback_date DESC"),
            {"eid": user_id}
        )
        all_feedbacks = [{"description": fb[0], "feedback_date": fb[1].isoformat()} for fb in all_feedbacks_res.fetchall()]
        latest_feedback_obj = {"description": last_feedback_res[0], "feedback_date": last_feedback_res[1].isoformat()}

        new_insights = services.generate_insights_from_feedback(user_check[1], latest_feedback_obj, all_feedbacks)
        new_insights_json = json.dumps(new_insights)

        db.execute(
            text("""
                INSERT INTO insights (employee_id, manager_id, insight_data, source_feedback_timestamp, generated_at)
                VALUES (:eid, :mid, :data, :ts, CURRENT_TIMESTAMP)
                ON CONFLICT (employee_id, manager_id) 
                DO UPDATE SET insight_data = EXCLUDED.insight_data, source_feedback_timestamp = EXCLUDED.source_feedback_timestamp, generated_at = CURRENT_TIMESTAMP
            """),
            {"eid": user_id, "mid": manager_id, "data": new_insights_json, "ts": latest_feedback_ts}
        )
        db.commit()

        return jsonify({
            "user_id": user_id, "user_name": user_check[1], "company": user_check[2], 
            "latest_feedback": latest_feedback_obj, "insights": new_insights
        })
    finally:
        db.close()

@api_bp.route('/user/<int:user_id>/feedbacks', methods=['GET'])
def user_feedbacks(user_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, description, feedback_date FROM feedbacks WHERE employee_id = :eid AND manager_id = :mid ORDER BY feedback_date DESC"),
            {"eid": user_id, "mid": session['user_id']}
        )
        feedbacks = [{"id": row[0], "description": row[1], "feedback_date": row[2].isoformat()} for row in result.fetchall()]
        return jsonify({"feedbacks": feedbacks})
    finally:
        db.close()

# --- ROTAS DE CHAT E REUNIÕES ---
@api_bp.route('/chat', methods=['POST'])
def chat():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    user_question = data.get("question", "")
    user_id = session["user_id"]

    # 1. Obtenha a URL do webhook do ambiente
    n8n_webhook_url = os.environ.get('N8N_CHAT_WEBHOOK_URL')
    if not n8n_webhook_url:
        return jsonify({"error": "Chat service is not configured."}), 500

    # 2. Prepare os dados para enviar ao n8n
    payload = {
        "userId": user_id,
        "question": user_question
    }

    try:
        # 3. Chame o webhook do n8n
        response = requests.post(n8n_webhook_url, json=payload, timeout=120) # Timeout de 2 minutos
        response.raise_for_status() # Lança um erro para respostas 4xx/5xx

        # 4. Retorne a resposta do n8n diretamente para o frontend
        return jsonify(response.json())

    except requests.exceptions.RequestException as e:
        print(f"Error calling n8n webhook: {e}")
        return jsonify({"error": "Could not connect to the chat service."}), 503
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({"error": str(e)}), 500

@api_bp.route('/meetings', methods=['GET', 'POST'])
def handle_meetings():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    user_id = session['user_id']
    try:
        if request.method == 'POST':
            data = request.json
            result = db.execute(
                text("INSERT INTO meetings (user_id, meeting_date, transcription, summary) VALUES (:uid, :date, :trans, :sum) RETURNING id"),
                {"uid": user_id, "date": data['meeting_date'], "trans": data.get('transcription', ''), "sum": data['summary']}
            )
            meeting_id = result.fetchone()[0]

            full_text = f"Reunião de {data['meeting_date']}. Resumo: {data['summary']}. Transcrição: {data.get('transcription', '')}"
            text_chunks = services.chunk_text(full_text)

            for chunk in text_chunks:
                embedding = services.get_embedding(chunk)
                db.execute(
                    text("INSERT INTO meeting_chunks (meeting_id, chunk_text, embedding) VALUES (:mid, :text, :emb)"),
                    {"mid": meeting_id, "text": chunk, "emb": str(embedding)}
                )
            db.commit()
            return jsonify({"success": True, "meeting_id": meeting_id})
        else: # GET
            result = db.execute(
                text("""
                    SELECT m.id, m.meeting_date, m.summary, u.name as owner_name, m.user_id
                    FROM meetings m
                    JOIN users u ON m.user_id = u.id
                    WHERE m.user_id = :uid OR m.id IN (
                        SELECT meeting_id FROM meeting_access WHERE user_id = :uid
                    )
                    ORDER BY m.meeting_date DESC
                """),
                {"uid": user_id}
            )
            meetings = [{"id": r[0], "meeting_date": r[1].isoformat(), "summary": r[2], "owner_name": r[3], "is_owner": r[4] == user_id} for r in result.fetchall()]
            return jsonify({"meetings": meetings})
    finally:
        db.close()

@api_bp.route('/meetings/<int:meeting_id>', methods=['GET', 'PUT', 'DELETE'])
def handle_single_meeting(meeting_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    user_id = session['user_id']
    try:
        if request.method == 'GET':
            query = text("""
                SELECT id, meeting_date, summary, transcription 
                FROM meetings 
                WHERE id = :mid AND (user_id = :uid OR id IN (
                    SELECT meeting_id FROM meeting_access WHERE user_id = :uid
                ))
            """)
            result = db.execute(query, {"mid": meeting_id, "uid": user_id}).fetchone()
            if result: 
                return jsonify({"id": result[0], "meeting_date": result[1].isoformat(), "summary": result[2], "transcription": result[3]})
            return jsonify({"error": "Meeting not found or access denied"}), 404
        elif request.method == 'PUT':
            data = request.json
            db.execute(
                text("UPDATE meetings SET meeting_date = :date, transcription = :trans, summary = :sum WHERE id = :mid AND user_id = :uid"),
                {"date": data['meeting_date'], "trans": data.get('transcription', ''), "sum": data['summary'], "mid": meeting_id, "uid": session['user_id']}
            )
            db.execute(text("DELETE FROM meeting_chunks WHERE meeting_id = :mid"), {"mid": meeting_id})

            full_text = f"Reunião de {data['meeting_date']}. Resumo: {data['summary']}. Transcrição: {data.get('transcription', '')}"
            text_chunks = services.chunk_text(full_text)
            for chunk in text_chunks:
                embedding = services.get_embedding(chunk)
                db.execute(
                    text("INSERT INTO meeting_chunks (meeting_id, chunk_text, embedding) VALUES (:mid, :text, :emb)"),
                    {"mid": meeting_id, "text": chunk, "emb": str(embedding)}
                )
            db.commit()
            return jsonify({"success": True})
        elif request.method == 'DELETE':
            db.execute(text("DELETE FROM meetings WHERE id = :mid AND user_id = :uid"), {"mid": meeting_id, "uid": session['user_id']})
            db.commit()
            return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/meetings/<int:meeting_id>/share', methods=['POST', 'GET'])
def share_meeting(meeting_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    user_id = session['user_id']
    try:
        owner_check = db.execute(text("SELECT u.name, m.summary FROM meetings m JOIN users u ON m.user_id = u.id WHERE m.id = :mid AND m.user_id = :uid"), {"mid": meeting_id, "uid": user_id}).fetchone()
        if not owner_check and request.method == 'POST':
            return jsonify({"error": "Somente o criador pode compartilhar a reunião"}), 403

        if request.method == 'POST':
            data = request.json
            participant_ids = data.get('user_ids', [])

            db.execute(text("DELETE FROM meeting_access WHERE meeting_id = :mid"), {"mid": meeting_id})

            owner_name = owner_check[0]
            meeting_summary = owner_check[1]
            title = "Nova reunião compartilhada"
            message = f"{owner_name} compartilhou a reunião '{meeting_summary[:30]}...' com você."
            link = f"/meetings/{meeting_id}"

            for p_id in participant_ids:
                if int(p_id) != user_id:
                    db.execute(
                        text("INSERT INTO meeting_access (meeting_id, user_id) VALUES (:mid, :uid) ON CONFLICT DO NOTHING"),
                        {"mid": meeting_id, "uid": p_id}
                    )
                    db.execute(
                        text("""
                            INSERT INTO notifications (user_id, actor_id, title, message, link)
                            VALUES (:uid, :actor_id, :title, :message, :link)
                        """),
                        {"uid": p_id, "actor_id": user_id, "title": title, "message": message, "link": link}
                    )
            db.commit()
            return jsonify({"success": True})

        else: # GET
            result = db.execute(text("SELECT user_id FROM meeting_access WHERE meeting_id = :mid"), {"mid": meeting_id})
            shared_with_ids = [row[0] for row in result.fetchall()]
            return jsonify({"shared_with": shared_with_ids})

    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route("/meetings/summarize", methods=["POST"])
def summarize_meeting():
    data = request.get_json()
    transcription = data.get("transcription", "")
    if not transcription.strip():
        return jsonify({"error": "Nenhuma transcrição fornecida."}), 400

    try:
        summary = generate_meeting_summary(transcription)
        return jsonify({"summary": summary}), 200
    except Exception as e:
        print(f"Erro ao gerar resumo: {e}")
        return jsonify({"error": str(e)}), 500

# --- ROTAS DE NOTIFICAÇÕES ---
@api_bp.route('/notifications', methods=['GET'])
def get_notifications():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    user_id = session['user_id']
    try:
        notifs_res = db.execute(
            text("""
                SELECT id, title, message, link, is_read, created_at, actor_id 
                FROM notifications 
                WHERE user_id = :uid 
                ORDER BY created_at DESC
            """),
            {"uid": user_id}
        ).fetchall()

        unread_count_res = db.execute(
            text("SELECT COUNT(id) FROM notifications WHERE user_id = :uid AND is_read = FALSE"),
            {"uid": user_id}
        ).fetchone()

        notifications = [
            {"id": n[0], "title": n[1], "message": n[2], "link": n[3], "is_read": n[4], 
             "created_at": n[5].isoformat(), "actor_id": n[6]} for n in notifs_res
        ]

        return jsonify({
            "notifications": notifications,
            "unread_count": unread_count_res[0] if unread_count_res else 0
        })
    finally:
        db.close()

@api_bp.route('/notifications/<int:notification_id>/read', methods=['POST'])
def mark_notification_as_read(notification_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE notifications SET is_read = TRUE WHERE id = :nid AND user_id = :uid"),
            {"nid": notification_id, "uid": session['user_id']}
        )
        db.commit()
        return jsonify({"success": True})
    finally:
        db.close()

@api_bp.route('/notifications/read-all', methods=['POST'])
def mark_all_notifications_as_read():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE notifications SET is_read = TRUE WHERE user_id = :uid"),
            {"uid": session['user_id']}
        )
        db.commit()
        return jsonify({"success": True})
    finally:
        db.close()

@api_bp.route('/notifications/poll')
def poll_notifications():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    # ✅ ALTERADO: Agora esperamos um ID em vez de um timestamp
    since_id_str = request.args.get('since_id')
    if not since_id_str:
        return jsonify([])

    try:
        since_id = int(since_id_str)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid ID format"}), 400

    db = SessionLocal()
    try:
        # ✅ ALTERADO: A query agora compara o ID, que é exato e sem perdas
        new_notifs_res = db.execute(
            text("""
                SELECT id, title, message, link, is_read, created_at, actor_id
                FROM notifications
                WHERE user_id = :uid AND id > :since_id
                ORDER BY id ASC
            """),
            {"uid": session['user_id'], "since_id": since_id}
        ).fetchall()

        notifications = [
            {
                "id": n[0], "title": n[1], "message": n[2], "link": n[3],
                "is_read": n[4], "created_at": n[5].isoformat(), "actor_id": n[6]
            } for n in new_notifs_res
        ]

        return jsonify(notifications)
    finally:
        db.close()

bp_ops = Blueprint("bp_ops", __name__, url_prefix="/ops")

@bp_ops.get("/version")
def version():
    return jsonify({"service": "cortex", "version": "0.1.0"})

def init_routes(app):
    app.register_blueprint(bp_ops)
    # Aqui você pode registrar outros blueprints/rotas existentes:
    # from meetings_routes import bp_meetings
    # app.register_blueprint(bp_meetings)
    # from feedbacks_routes import bp_feedbacks
    # app.register_blueprint(bp_feedbacks)
    return app