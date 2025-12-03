import os
import json
import base64
import time
from flask import Blueprint, request, jsonify, session, Response
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from database import SessionLocal
from services import summarize_transcription
import services
import requests
import secrets
from flask import redirect, url_for

api_bp = Blueprint('api', __name__, url_prefix='/api')


def hash_password(password):
    return generate_password_hash(password)


# --- ROTAS DE USUÁRIO E PERFIL ---
@api_bp.route('/register', methods=['POST'])
def register():
    data = request.json
    db = SessionLocal()
    try:
        # Normalize o nome
        normalized_name = services.normalize_text(data['name'])
        result = db.execute(
            text(
                "INSERT INTO users (email, password_hash, name, name_normalized, company, phone) VALUES (:email, :password_hash, :name, :normalized_name, :company, :phone) RETURNING id"
            ), {
                "email": data['email'],
                "password_hash": hash_password(data['password']),
                "name": data['name'],
                "normalized_name": normalized_name,
                "company": data.get('company', ''),
                "phone": data.get('phone', '')
            })
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
            text(
                "SELECT id, name, company, password_hash, email FROM users WHERE email = :email"
            ), {"email": data['email']})
        user = result.fetchone()
        if user and check_password_hash(user[3], data['password']):
            session['user_id'] = user[0]
            return jsonify({
                "success": True,
                "user": {
                    "id": user[0],
                    "name": user[1],
                    "company": user[2],
                    "email": user[4]
                }
            })
        else:
            return jsonify({
                "success": False,
                "error": "Invalid credentials"
            }), 401
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
            text("SELECT id, name, email, company, phone, manager_id, mini_bio, google_id FROM users WHERE id = :id"), 
            {"id": session['user_id']}
        )
        user = result.fetchone()
        if user:
            return jsonify({
                "authenticated": True,
                "user": {
                    "id": user[0],
                    "name": user[1],
                    "email": user[2],
                    "company": user[3],
                    "phone": user[4],
                    "manager_id": user[5],
                    "mini_bio": user[6],
                    "has_google_linked": bool(user[7])
                }
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
            text(
                "SELECT id, name, email, company FROM users WHERE id != :current_user_id ORDER BY name"
            ), {"current_user_id": session['user_id']})
        users = [{
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "company": row[3]
        } for row in result.fetchall()]
        return jsonify({"users": users})
    finally:
        db.close()


@api_bp.route('/managed-users', methods=['GET'])
def get_managed_users():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        result = db.execute(
            text(
                "SELECT id, name, email, company, phone FROM users WHERE manager_id = :manager_id ORDER BY name"
            ), {"manager_id": session['user_id']})
        managed_users = [{
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "company": row[3],
            "phone": row[4]
        } for row in result.fetchall()]
        return jsonify({"managed_users": managed_users})
    finally:
        db.close()


@api_bp.route('/profile', methods=['PUT'])
def update_profile():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    user_id = session['user_id']

    db = SessionLocal()
    try:
        # 1. Busca o estado ATUAL antes da atualização para saber quem é o gestor antigo
        current_user = db.execute(
            text("SELECT name, manager_id FROM users WHERE id = :id"), {
                "id": user_id
            }).fetchone()

        if not current_user:
            return jsonify({"error": "User not found"}), 404

        user_name = current_user[0]
        old_manager_id = current_user[1]

        # 2. Prepara os dados novos
        normalized_name = services.normalize_text(data['name'])

        # Tratamento do novo manager_id (pode vir como int, string numérica, string vazia ou None)
        new_manager_id = data.get('manager_id')
        if new_manager_id == "" or new_manager_id is None:
            new_manager_id = None
        else:
            new_manager_id = int(new_manager_id)

        # 3. Executa a atualização do Perfil
        db.execute(
            text(
                "UPDATE users SET name = :name, name_normalized = :normalized_name, company = :company, phone = :phone, manager_id = :manager_id, mini_bio = :mini_bio WHERE id = :id"
            ), {
                "id": user_id,
                "name": data['name'],
                "normalized_name": normalized_name,
                "company": data.get('company', ''),
                "phone": data.get('phone', ''),
                "manager_id": new_manager_id,
                "mini_bio": data.get('mini_bio')
            })

        # 4. Lógica de Notificação de Troca de Gestor
        if old_manager_id != new_manager_id:

            # A. Notifica o Gestor ANTIGO (Remoção)
            if old_manager_id:
                title_removed = "Liderança Atualizada"
                msg_removed = f"{user_name} deixou de listar você como gestor no perfil."
                db.execute(
                    text("""
                        INSERT INTO notifications (user_id, actor_id, title, message, type)
                        VALUES (:uid, :actor_id, :title, :msg, 'SYSTEM')
                    """), {
                        "uid": old_manager_id,
                        "actor_id": user_id,
                        "title": title_removed,
                        "msg": msg_removed
                    })

            # B. Notifica o NOVO Gestor (Adição)
            if new_manager_id:
                title_added = "Novo Liderado"
                msg_added = f"{user_name} selecionou você como gestor."
                link_added = "/my-team"  # Link para a tela de time
                db.execute(
                    text("""
                        INSERT INTO notifications (user_id, actor_id, title, message, link, type)
                        VALUES (:uid, :actor_id, :title, :msg, :link, 'SYSTEM')
                    """), {
                        "uid": new_manager_id,
                        "actor_id": user_id,
                        "title": title_added,
                        "msg": msg_added,
                        "link": link_added
                    })

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
    current_password, new_password = data.get('current_password'), data.get(
        'new_password')
    if not current_password or not new_password:
        return jsonify({
            "success": False,
            "error": "Todos os campos são obrigatórios"
        }), 400
    db = SessionLocal()
    try:
        user = db.execute(
            text("SELECT password_hash FROM users WHERE id = :id"), {
                "id": session['user_id']
            }).fetchone()
        if not user:
            return jsonify({
                "success": False,
                "error": "Usuário não encontrado"
            }), 404
        if not check_password_hash(user[0], current_password):
            return jsonify({
                "success": False,
                "error": "Senha atual incorreta"
            }), 401
        new_password_hash = hash_password(new_password)
        db.execute(
            text("UPDATE users SET password_hash = :new_hash WHERE id = :id"),
            {
                "new_hash": new_password_hash,
                "id": session['user_id']
            })
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
            db.execute(
                text(
                    "UPDATE users SET profile_photo = :photo WHERE id = :uid"),
                {
                    "photo": photo_bytes,
                    "uid": session['user_id']
                })
            db.commit()
            return jsonify({"success": True})
        else:
            row = db.execute(
                text("SELECT profile_photo FROM users WHERE id = :uid"), {
                    "uid": session['user_id']
                }).fetchone()
            if row and row[0]:
                photo_base64 = base64.b64encode(row[0]).decode('utf-8')
                return jsonify(
                    {"photo": f"data:image/jpeg;base64,{photo_base64}"})
            return jsonify({"photo": None})
    finally:
        db.close()


@api_bp.route('/user/<int:user_id>/photo', methods=['GET'])
def user_photo(user_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT profile_photo FROM users WHERE id = :uid"), {
                "uid": user_id
            }).fetchone()
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
        # Novos campos opcionais
        transcription = data.get('transcription', '')
        feedback_for_employee = data.get('feedback_for_employee', '')

        description, feedback_date = data.get('description'), data.get(
            'feedback_date')

        if not description:
            return jsonify({"error": "Description is required"}), 400

        # O embedding continua sendo gerado sobre a visão do gestor + data
        temporal_context = f"Feedback de {feedback_date}: {description}"
        embedding = services.get_embedding(temporal_context)

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


@api_bp.route('/user/<int:user_id>/insights', methods=['GET'])
def user_insights(user_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    manager_id = session['user_id']
    db = SessionLocal()
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

        # 1. Busca o Feedback "Mais Recente" (Lógica de Negócio: Data do Feedback)
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

        # 2. Busca o Timestamp da última alteração real no banco (para invalidar cache)
        last_update_res = db.execute(
            text(
                "SELECT MAX(created_at) FROM feedbacks WHERE employee_id = :eid"
            ), {
                "eid": user_id
            }).fetchone()
        last_update_ts = last_update_res[
            0] if last_update_res else last_feedback_res[2]

        # 3. Verifica Cache
        cached_insight = db.execute(
            text(
                "SELECT insight_data, source_feedback_timestamp FROM insights WHERE employee_id = :eid AND manager_id = :mid"
            ), {
                "eid": user_id,
                "mid": manager_id
            }).fetchone()

        # Objeto do último feedback para retorno
        latest_feedback_obj = {
            "description": last_feedback_res[0],
            "feedback_date": last_feedback_res[1].isoformat()
        }

        # Se o cache existe e é mais recente que a última atualização na tabela de feedbacks
        if cached_insight and cached_insight[1] >= last_update_ts:
            insights = json.loads(cached_insight[0])
            # Força o nome da Sarah se o cache for antigo e não tiver
            if "agent_name" not in insights: insights["agent_name"] = "Sarah"

            return jsonify({
                "user_id": user_id,
                "user_name": user_check[1],
                "company": user_check[2],
                "latest_feedback": latest_feedback_obj,
                "insights": insights
            })

        # 4. Gera Novos Insights (Se cache expirado ou inexistente)
        # Busca histórico ordenado cronologicamente
        all_feedbacks_res = db.execute(
            text(
                "SELECT description, feedback_date FROM feedbacks WHERE employee_id = :eid ORDER BY feedback_date DESC"
            ), {"eid": user_id})
        all_feedbacks = [{
            "description": fb[0],
            "feedback_date": fb[1].isoformat()
        } for fb in all_feedbacks_res.fetchall()]

        new_insights = services.generate_insights_from_feedback(
            user_check[1], latest_feedback_obj, all_feedbacks)
        new_insights_json = json.dumps(new_insights)

        # Atualiza Cache
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


@api_bp.route('/user/<int:user_id>/feedbacks', methods=['GET'])
def user_feedbacks(user_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        # ✅ NOVO: Incluí transcription e feedback_for_employee na query
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
                "transcription": row[3],  # ✅ Retorna para edição
                "feedback_for_employee": row[4]  # ✅ Retorna para edição
            } for row in result.fetchall()
        ]
        return jsonify({"feedbacks": feedbacks})
    finally:
        db.close()


@api_bp.route('/feedbacks/<int:feedback_id>', methods=['PUT'])
def update_feedback(feedback_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    description = data.get('description')
    feedback_date = data.get('feedback_date')

    # Novos campos opcionais
    transcription = data.get('transcription', '')
    feedback_for_employee = data.get('feedback_for_employee', '')

    if not description or not feedback_date:
        return jsonify({"error": "Dados incompletos"}), 400

    db = SessionLocal()
    try:
        # Verifica se o feedback pertence ao gestor logado antes de editar
        check = db.execute(
            text(
                "SELECT id FROM feedbacks WHERE id = :fid AND manager_id = :mid"
            ), {
                "fid": feedback_id,
                "mid": session['user_id']
            }).fetchone()

        if not check:
            return jsonify(
                {"error": "Feedback não encontrado ou acesso negado"}), 404

        # Recalcula o embedding pois o conteúdo mudou
        temporal_context = f"Feedback de {feedback_date}: {description}"
        embedding = services.get_embedding(temporal_context)

        # Atualiza o registro
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
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
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
    payload = {"userId": user_id, "question": user_question}

    try:
        # 3. Chame o webhook do n8n
        response = requests.post(n8n_webhook_url, json=payload,
                                 timeout=120)  # Timeout de 2 minutos
        response.raise_for_status()  # Lança um erro para respostas 4xx/5xx

        # 4. Retorne a resposta do n8n diretamente para o frontend
        return jsonify(response.json())

    except requests.exceptions.RequestException as e:
        print(f"Error calling n8n webhook: {e}")
        return jsonify({"error":
                        "Could not connect to the chat service."}), 503
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
                text(
                    "INSERT INTO meetings (user_id, meeting_date, transcription, summary) VALUES (:uid, :date, :trans, :sum) RETURNING id"
                ), {
                    "uid": user_id,
                    "date": data['meeting_date'],
                    "trans": data.get('transcription', ''),
                    "sum": data['summary']
                })
            meeting_id = result.fetchone()[0]

            full_text = f"Reunião de {data['meeting_date']}. Resumo: {data['summary']}. Transcrição: {data.get('transcription', '')}"
            text_chunks = services.chunk_text(full_text)

            for chunk in text_chunks:
                embedding = services.get_embedding(chunk)
                db.execute(
                    text(
                        "INSERT INTO meeting_chunks (meeting_id, chunk_text, embedding) VALUES (:mid, :text, :emb)"
                    ), {
                        "mid": meeting_id,
                        "text": chunk,
                        "emb": str(embedding)
                    })
            db.commit()
            return jsonify({"success": True, "meeting_id": meeting_id})
        else:  # GET
            result = db.execute(
                text("""
                    SELECT m.id, m.meeting_date, m.summary, u.name as owner_name, m.user_id
                    FROM meetings m
                    JOIN users u ON m.user_id = u.id
                    WHERE m.user_id = :uid OR m.id IN (
                        SELECT meeting_id FROM meeting_access WHERE user_id = :uid
                    )
                    ORDER BY m.meeting_date DESC
                """), {"uid": user_id})
            meetings = [{
                "id": r[0],
                "meeting_date": r[1].isoformat(),
                "summary": r[2],
                "owner_name": r[3],
                "is_owner": r[4] == user_id
            } for r in result.fetchall()]
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
            result = db.execute(query, {
                "mid": meeting_id,
                "uid": user_id
            }).fetchone()
            if result:
                return jsonify({
                    "id": result[0],
                    "meeting_date": result[1].isoformat(),
                    "summary": result[2],
                    "transcription": result[3]
                })
            return jsonify({"error":
                            "Meeting not found or access denied"}), 404
        elif request.method == 'PUT':
            data = request.json
            db.execute(
                text(
                    "UPDATE meetings SET meeting_date = :date, transcription = :trans, summary = :sum WHERE id = :mid AND user_id = :uid"
                ), {
                    "date": data['meeting_date'],
                    "trans": data.get('transcription', ''),
                    "sum": data['summary'],
                    "mid": meeting_id,
                    "uid": session['user_id']
                })
            db.execute(
                text("DELETE FROM meeting_chunks WHERE meeting_id = :mid"),
                {"mid": meeting_id})

            full_text = f"Reunião de {data['meeting_date']}. Resumo: {data['summary']}. Transcrição: {data.get('transcription', '')}"
            text_chunks = services.chunk_text(full_text)
            for chunk in text_chunks:
                embedding = services.get_embedding(chunk)
                db.execute(
                    text(
                        "INSERT INTO meeting_chunks (meeting_id, chunk_text, embedding) VALUES (:mid, :text, :emb)"
                    ), {
                        "mid": meeting_id,
                        "text": chunk,
                        "emb": str(embedding)
                    })
            db.commit()
            return jsonify({"success": True})
        elif request.method == 'DELETE':
            db.execute(
                text(
                    "DELETE FROM meetings WHERE id = :mid AND user_id = :uid"),
                {
                    "mid": meeting_id,
                    "uid": session['user_id']
                })
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
        # Verifica se é o dono da reunião
        owner_check = db.execute(
            text(
                "SELECT u.name, m.summary FROM meetings m JOIN users u ON m.user_id = u.id WHERE m.id = :mid AND m.user_id = :uid"
            ), {
                "mid": meeting_id,
                "uid": user_id
            }).fetchone()

        # Se for POST, processa a atualização
        if request.method == 'POST':
            if not owner_check:
                return jsonify(
                    {"error":
                     "Somente o criador pode compartilhar a reunião"}), 403

            data = request.json
            # IDs recebidos do frontend (convertidos para set de inteiros para comparação)
            new_participant_ids = set(
                int(pid) for pid in data.get('user_ids', [])
                if int(pid) != user_id)

            # 1. Busca participantes atuais ANTES de deletar
            current_access_res = db.execute(
                text(
                    "SELECT user_id FROM meeting_access WHERE meeting_id = :mid"
                ), {"mid": meeting_id})
            current_participant_ids = set(
                row[0] for row in current_access_res.fetchall())

            # 2. Calcula Delta (Quem entrou e quem saiu)
            added_ids = new_participant_ids - current_participant_ids
            removed_ids = current_participant_ids - new_participant_ids

            # 3. Atualiza a tabela de acesso (Estratégia: Remove tudo e reinsere os atuais para garantir sincronia)
            db.execute(
                text("DELETE FROM meeting_access WHERE meeting_id = :mid"),
                {"mid": meeting_id})

            for p_id in new_participant_ids:
                db.execute(
                    text(
                        "INSERT INTO meeting_access (meeting_id, user_id) VALUES (:mid, :uid) ON CONFLICT DO NOTHING"
                    ), {
                        "mid": meeting_id,
                        "uid": p_id
                    })

            # 4. Prepara dados para notificações
            owner_name = owner_check[0]
            meeting_summary = owner_check[1]
            # Link abre a modal da reunião
            link = f"/meetings/{meeting_id}"

            # A. Notifica NOVOS usuários (Adicionados)
            if added_ids:
                title_share = "Nova reunião compartilhada"
                msg_share = f"{owner_name} compartilhou a reunião '{meeting_summary[:50]}...' com você."
                for p_id in added_ids:
                    db.execute(
                        text("""
                            INSERT INTO notifications (user_id, actor_id, title, message, link)
                            VALUES (:uid, :actor_id, :title, :message, :link)
                        """), {
                            "uid": p_id,
                            "actor_id": user_id,
                            "title": title_share,
                            "message": msg_share,
                            "link": link
                        })

            # B. Notifica USUÁRIOS REMOVIDOS (Feature Solicitada)
            if removed_ids:
                title_remove = "Acesso revogado"
                msg_remove = f"{owner_name} removeu seu acesso à reunião '{meeting_summary[:50]}...'."
                # Link é None pois o usuário não tem mais acesso
                for p_id in removed_ids:
                    db.execute(
                        text("""
                            INSERT INTO notifications (user_id, actor_id, title, message, link)
                            VALUES (:uid, :actor_id, :title, :message, :link)
                        """), {
                            "uid": p_id,
                            "actor_id": user_id,
                            "title": title_remove,
                            "message": msg_remove,
                            "link": None
                        })

            db.commit()
            return jsonify({"success": True})

        else:  # GET
            # Se não for dono, verifica se tem acesso para ver quem mais está na reunião (opcional, mas seguro manter restrito ou aberto conforme regra de negócio)
            # Aqui mantemos a lógica original: retorna lista de IDs
            result = db.execute(
                text(
                    "SELECT user_id FROM meeting_access WHERE meeting_id = :mid"
                ), {"mid": meeting_id})
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
    # Captura a data enviada pelo frontend
    meeting_date = data.get("meeting_date", "Data não informada")

    if not transcription.strip():
        return jsonify({"error": "Nenhuma transcrição fornecida."}), 400

    try:
        # Passa a data para o serviço
        summary = summarize_transcription(transcription, meeting_date)
        return jsonify({"summary": summary}), 200
    except Exception as e:
        print(f"Erro ao gerar resumo: {e}")
        return jsonify({"error": str(e)}), 500


# --- ROTAS DE NOTIFICAÇÕES ---
# Arquivo: routes.py

@api_bp.route('/notifications', methods=['GET'])
def get_notifications():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    user_id = session['user_id']
    try:
        # 1. Traz TUDO que não foi lido (is_read = FALSE)
        # 2. OU Traz o que foi lido APENAS se for das últimas 24h
        notifs_res = db.execute(
            text("""
                SELECT id, title, message, link, is_read, created_at, actor_id
                FROM notifications 
                WHERE user_id = :uid 
                  AND (
                      is_read = FALSE 
                      OR 
                      created_at >= (CURRENT_TIMESTAMP - INTERVAL '24 HOURS')
                  )
                ORDER BY created_at DESC
            """),
            {"uid": user_id}
        ).fetchall()

        unread_count_res = db.execute(text("SELECT COUNT(id) FROM notifications WHERE user_id = :uid AND is_read = FALSE"), {"uid": user_id}).fetchone()

        notifications = [
            {
                "id": n[0], "title": n[1], "message": n[2], "link": n[3], 
                "is_read": n[4], "created_at": n[5].isoformat(), 
                "actor_id": n[6]
            } for n in notifs_res
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
            text(
                "UPDATE notifications SET is_read = TRUE WHERE id = :nid AND user_id = :uid"
            ), {
                "nid": notification_id,
                "uid": session['user_id']
            })
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
            text("UPDATE notifications SET is_read = TRUE WHERE user_id = :uid"
                 ), {"uid": session['user_id']})
        db.commit()
        return jsonify({"success": True})
    finally:
        db.close()


@api_bp.route('/notifications/<int:notification_id>/status', methods=['PUT'])
def update_notification_status(notification_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    is_read = data.get('is_read')

    if is_read is None:
        return jsonify({"error": "Campo 'is_read' é obrigatório"}), 400

    db = SessionLocal()
    try:
        # Atualiza o status
        db.execute(
            text(
                "UPDATE notifications SET is_read = :status WHERE id = :nid AND user_id = :uid"
            ), {
                "status": is_read,
                "nid": notification_id,
                "uid": session['user_id']
            })
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/notifications/poll')
def poll_notifications():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    since_id_str = request.args.get('since_id')
    if not since_id_str: return jsonify([])

    try: since_id = int(since_id_str)
    except: return jsonify({"error": "Invalid ID"}), 400

    db = SessionLocal()
    try:
        # ✅ REMOVIDO: agent_name da query
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
                "is_read": n[4], "created_at": n[5].isoformat(), 
                "actor_id": n[6]
            } for n in new_notifs_res
        ]
        return jsonify(notifications)
    finally:
        db.close()

@api_bp.route('/import/feedback', methods=['POST'])
def import_feedback():
    """
    Endpoint dedicado para importação de feedbacks em lote (ex: via n8n).
    [ATENÇÃO] Esta versão não possui autenticação e está aberta.
    Remova ou proteja este endpoint após o uso.
    """

    # 1. Obter Dados do JSON
    data = request.json
    employee_id = data.get('employee_id')
    manager_id = data.get('manager_id')
    description = data.get('description')
    feedback_date = data.get(
        'feedback_date')  # Espera uma string de data (ex: "2023-10-25")

    # 2. Validação
    if not all([employee_id, manager_id, description, feedback_date]):
        return jsonify({
            "error":
            "Campos obrigatórios ausentes: employee_id, manager_id, description, feedback_date"
        }), 400

    db = SessionLocal()
    try:
        # 3. Gerar Embedding (reutilizando a lógica de 'services')
        temporal_context = f"Feedback de {feedback_date}: {description}"
        embedding = services.get_embedding(temporal_context)

        # 4. Inserir no Banco de Dados
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
        # Trata erros comuns, como IDs de usuário que não existem
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


def upsert_agent(db, agent_name, role_hint="Assistente Inteligente"):
    """Cria ou atualiza o agente baseado na atividade."""
    # Tenta atualizar o timestamp
    result = db.execute(
        text(
            "UPDATE agents SET last_active_at = CURRENT_TIMESTAMP WHERE name = :name RETURNING id"
        ), {"name": agent_name})
    if result.rowcount == 0:
        # Se não existe, cria (Auto-Discovery)
        # Define um estilo/cor baseado num hash simples do nome ou aleatório
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
        db.commit()


@api_bp.route('/insights/ingest', methods=['POST'])
def ingest_ai_insights():
    """
    Novo endpoint exclusivo para o N8N.
    Salva na tabela 'agent_insights' e atualiza o 'agents'.
    """
    try:
        data = request.json
        user_id = data.get('user_id')
        agent_output = data.get('agent_output', {})

        # Robustez: Parse se vier como string (como corrigimos antes)
        if isinstance(agent_output, str):
            try:
                agent_output = json.loads(agent_output)
            except json.JSONDecodeError:
                return jsonify({"error": "Invalid JSON in agent_output"}), 400

        if not user_id: return jsonify({"error": "user_id required"}), 400
        if not agent_output.get('has_insight'):
            return jsonify({"success": True}), 200

        agent_name = agent_output.get('agent_name', 'Cortex AI')

        # Auto-registro do Agente (Reutilizando a função que criamos)
        db = SessionLocal()
        try:
            # Atualiza/Cria o agente na tabela 'agents'
            role = "Assistente Inteligente"
            if "Sarah" in agent_name: role = "Consultora de Liderança"
            if "Angelo" in agent_name: role = "Estrategista Corporativo"

            # (Copie a lógica de upsert_agent aqui ou importe se extraiu para services)
            # Para simplificar, vou repetir a lógica inline do UPDATE/INSERT agents:
            res = db.execute(
                text(
                    "UPDATE agents SET last_active_at = CURRENT_TIMESTAMP WHERE name = :name RETURNING id"
                ), {"name": agent_name})
            if res.rowcount == 0:
                styles = ['blue', 'purple', 'green', 'orange', 'pink']
                style = styles[len(agent_name) % len(styles)]
                db.execute(
                    text(
                        "INSERT INTO agents (name, role, description, avatar_style, last_active_at) VALUES (:name, :role, 'Agente autônomo.', :style, CURRENT_TIMESTAMP)"
                    ), {
                        "name": agent_name,
                        "role": role,
                        "style": style
                    })

            # INSERE NA NOVA TABELA DE INSIGHTS
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
                        "payload": payload_json # <--- NOVO CAMPO
                    })

            # Opcional: Criar uma notificação de sistema APENAS avisando "Novos insights disponíveis",
            # sem poluir com o conteúdo, ou não criar nada e deixar o usuário ver na Staff.
            # Decisão: Vamos criar um aviso discreto no sino.
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

@api_bp.route('/insights/<int:insight_id>/approve', methods=['POST'])
def approve_insight_action(insight_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401

    db = SessionLocal()
    try:
        # 1. Busca o Insight, Payload e o E-mail do Usuário Atual
        # Precisamos do e-mail do usuário para enviar a ata para ele (ou para os participantes, se evoluirmos)
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

        # 2. Executa a Ação baseada no Tipo
        if action_type == 'UPDATE_FEEDBACK':
            target_feedback_id = payload.get('feedback_id')
            draft_message = payload.get('draft_message')

            if target_feedback_id and draft_message:
                # Atualiza o feedback original com a mensagem aprovada
                db.execute(text("UPDATE feedbacks SET feedback_for_employee = :msg WHERE id = :fid"),
                    {"msg": draft_message, "fid": target_feedback_id})

        elif action_type == 'SEND_EMAIL':
            subject = payload.get('subject')
            html_body = payload.get('html_body')

            if subject and html_body:
                # Por segurança/MVP, enviamos para o próprio usuário revisar e encaminhar
                # Ou enviamos direto se a confiança for total. Aqui mandamos para o usuário.
                services.send_email_action(user_email, subject, html_body)

        # 3. Arquiva o Insight (Ação Concluída)
        db.execute(
            text("UPDATE agent_insights SET is_archived = TRUE WHERE id = :id"),
            {"id": insight_id}
        )

        db.commit()
        return jsonify({"success": True})

    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/agents', methods=['GET'])
def get_agents():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    user_id = session['user_id']
    db = SessionLocal()
    try:
        # Query ajustada para contar da tabela agent_insights
        # E filtrar apenas insights NÃO ARQUIVADOS (is_archived = FALSE)
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
        # ... (resto do código de formatação igual) ...
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


@api_bp.route('/agents/<string:agent_name>/insights', methods=['GET'])
def get_agent_insights(agent_name):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    user_id = session['user_id']
    db = SessionLocal()
    try:
        # Busca na nova tabela, ignorando os arquivados
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

@api_bp.route('/insights/<int:insight_id>/archive', methods=['PUT'])
def archive_insight(insight_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        # Soft delete: marca como arquivado
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
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/feedbacks/generate-summary', methods=['POST'])
def generate_feedback_summary_route():
    data = request.json
    text_input = data.get('transcription', '')
    # ✅ NOVO: Recebe a data
    feedback_date = data.get('feedback_date', 'Data não informada')

    if not text_input: return jsonify({"error": "No text provided"}), 400
    try:
        # ✅ NOVO: Passa a data para o serviço
        summary = services.generate_feedback_summary(text_input, feedback_date)
        return jsonify({"result": summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route('/feedbacks/generate-employee-msg', methods=['POST'])
def generate_employee_msg_route():
    data = request.json
    description = data.get('description', '')
    transcription = data.get('transcription', '')
    # ✅ NOVO: Recebe o nome do funcionário
    employee_name = data.get('employee_name', 'Colaborador')

    if not description and not transcription:
        return jsonify({"error": "No context provided"}), 400

    try:
        # Passa o nome para o serviço da Sarah
        msg = services.generate_employee_message(description, transcription,
                                                 employee_name)
        return jsonify({"result": msg})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route('/my-received-feedbacks', methods=['GET'])
def get_my_received_feedbacks():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    user_id = session['user_id']
    db = SessionLocal()
    try:
        # Traz apenas feedbacks que tenham mensagem para o funcionário preenchida
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

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

def get_google_provider_cfg():
    return requests.get(GOOGLE_DISCOVERY_URL).json()

@api_bp.route('/auth/google/login')
def google_login():
    """Inicia o fluxo de login/registro via Google."""
    # Define modo 'login' na sessão para o callback saber o que fazer
    session['oauth_mode'] = 'login'
    return _start_google_flow()

@api_bp.route('/auth/google/link')
def google_link():
    """Inicia o fluxo de vinculação de conta para usuário logado."""
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    # Define modo 'link' na sessão
    session['oauth_mode'] = 'link'
    return _start_google_flow()

def _start_google_flow():
    if not GOOGLE_CLIENT_ID:
        return jsonify({"error": "Google Client ID not configured"}), 500

    # Gera estado aleatório para segurança CSRF
    state = secrets.token_urlsafe(16)
    session['oauth_state'] = state

    # Descobre endpoint de autorização
    google_provider_cfg = get_google_provider_cfg()
    authorization_endpoint = google_provider_cfg["authorization_endpoint"]

    # Monta a URL de redirecionamento
    redirect_uri = request.host_url.replace('http://', 'https://').rstrip('/') + "/api/auth/google/callback"

    request_uri = requests.Request('GET', authorization_endpoint, params={
        "client_id": GOOGLE_CLIENT_ID,
        "access_type": "offline",
        "scope": "openid email profile",
        "response_type": "code",
        "redirect_uri": redirect_uri, # Use a variável corrigida
        "state": state
    }).prepare().url

    return jsonify({"redirect_url": request_uri})

@api_bp.route('/auth/google/callback')
def google_callback():
    """Recebe o retorno do Google, troca o code por token e loga/registra o usuário."""
    code = request.args.get("code")
    state = request.args.get("state")

    # Validação CSRF
    if state != session.get('oauth_state'):
        return jsonify({"error": "Invalid state parameter"}), 400

    # 1. Troca o Code pelo Token
    google_provider_cfg = get_google_provider_cfg()
    token_endpoint = google_provider_cfg["token_endpoint"]

    token_response = requests.post(
        token_endpoint,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            # Mantendo a correção de HTTPS que fizemos anteriormente
            "redirect_uri": request.host_url.replace('http://', 'https://').rstrip('/') + "/api/auth/google/callback",
            "grant_type": "authorization_code",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )

    tokens = token_response.json()

    # 2. Obtém dados do Usuário
    userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
    userinfo_response = requests.get(userinfo_endpoint, headers={"Authorization": f"Bearer {tokens['access_token']}"})

    if userinfo_response.status_code != 200:
        return jsonify({"error": "Failed to get user info"}), 400

    user_info = userinfo_response.json()
    google_id = user_info["sub"]
    email = user_info["email"]
    name = user_info.get("name", email.split('@')[0])

    mode = session.get('oauth_mode', 'login')
    db = SessionLocal()

    try:
        if mode == 'link':
            # --- CENÁRIO A: VINCULAR CONTA EXISTENTE ---
            current_user_id = session.get('user_id')
            if not current_user_id:
                return redirect('/?error=session_expired')

            # Verifica se este google_id já está em uso por OUTRA conta
            conflict = db.execute(text("SELECT id FROM users WHERE google_id = :gid AND id != :uid"), 
                                {"gid": google_id, "uid": current_user_id}).fetchone()
            if conflict:
                return redirect('/?error=google_account_already_linked')

            db.execute(text("UPDATE users SET google_id = :gid WHERE id = :uid"), 
                     {"gid": google_id, "uid": current_user_id})
            db.commit()
            return redirect('/?success=google_linked')

        else:
            # --- CENÁRIO B: LOGIN / REGISTRO ---

            # B1. Tenta achar pelo Google ID
            user = db.execute(text("SELECT id, name, company, email FROM users WHERE google_id = :gid"), {"gid": google_id}).fetchone()

            # B2. Se não achar, tenta achar pelo Email (Vinculação Automática por Confiança)
            if not user:
                user_by_email = db.execute(text("SELECT id, name, company, email FROM users WHERE email = :email"), {"email": email}).fetchone()
                if user_by_email:
                    # Vincula a conta existente ao Google ID
                    db.execute(text("UPDATE users SET google_id = :gid WHERE id = :uid"), {"gid": google_id, "uid": user_by_email[0]})
                    db.commit()
                    user = user_by_email

            # B3. Se não achar nada, Cria Novo Usuário
            if not user:
                normalized_name = services.normalize_text(name)
                # Senha aleatória forte (já que ele loga via Google)
                random_pass = secrets.token_urlsafe(16)

                result = db.execute(text("""
                    INSERT INTO users (email, password_hash, name, name_normalized, company, google_id) 
                    VALUES (:email, :pwd, :name, :norm, 'Minha Empresa', :gid) 
                    RETURNING id, name, company, email
                """), {
                    "email": email,
                    "pwd": hash_password(random_pass),
                    "name": name,
                    "norm": normalized_name,
                    "gid": google_id
                })
                db.commit()
                user = result.fetchone()

            # Efetua Login na Sessão
            session['user_id'] = user[0]

            # Redireciona para o Frontend
            return redirect('/')

    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()