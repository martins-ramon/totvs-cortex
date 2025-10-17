import json
import base64
from flask import Blueprint, request, jsonify, session
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from database import SessionLocal
import services

api_bp = Blueprint('api', __name__, url_prefix='/api')

def hash_password(password):
    return generate_password_hash(password)

# --- ROTAS DE USUÁRIO (sem alteração) ---
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

@api_bp.route('/api/feedbacks/import', methods=['POST'])
def import_feedbacks_api_key():
    import os
    from datetime import datetime

    # --- 1. Validação da API Key ---
    provided_key = request.headers.get("X-API-KEY")
    valid_key = os.getenv("IMPORT_API_KEY", "12345-DEV-KEY")  # defina no ambiente
    if provided_key != valid_key:
        return jsonify({"error": "Acesso não autorizado"}), 401

    # --- 2. Lê corpo da requisição ---
    data = request.get_json()
    if not data or not isinstance(data, list):
        return jsonify({"error": "Formato inválido. Envie uma lista JSON de feedbacks."}), 400

    db = SessionLocal()
    imported = 0
    errors = []

    try:
        for item in data:
            try:
                employee_id = item.get("user_id")
                manager_id = item.get("manager_id")
                feedback_date = item.get("feedback_date")
                description = item.get("description")

                # --- 3. Validação básica ---
                if not all([employee_id, manager_id, feedback_date, description]):
                    raise ValueError("Campos obrigatórios ausentes (user_id, manager_id, feedback_date, description)")

                # --- 4. Cria o registro ---
                new_feedback = Feedback(
                    employee_id=employee_id,
                    manager_id=manager_id,
                    feedback_date=datetime.strptime(feedback_date, "%Y-%m-%d"),
                    description=description
                )

                # --- 5. Gera embedding do feedback (caso seu sistema use OpenAI) ---
                try:
                    from services import get_embedding
                    new_feedback.embedding = get_embedding(description)
                except Exception:
                    pass  # ignora falhas na IA durante a importação

                db.add(new_feedback)
                imported += 1

            except Exception as e:
                errors.append({
                    "feedback": item,
                    "error": str(e)
                })

        db.commit()
        return jsonify({
            "success": True,
            "imported": imported,
            "errors": errors
        }), 200

    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@api_bp.route('/feedbacks/<int:feedback_id>', methods=['PUT'])
def update_feedback(feedback_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        data = request.json
        description, feedback_date = data.get('description'), data.get('feedback_date')
        if not description: return jsonify({"error": "Description is required"}), 400
        temporal_context = f"Feedback de {feedback_date}: {description}"
        embedding = services.get_embedding(temporal_context)
        db.execute(
            text("UPDATE feedbacks SET description = :d, feedback_date = :fd, embedding = :e, created_at = CURRENT_TIMESTAMP WHERE id = :fid AND manager_id = :mid"),
            {"d": description, "fd": feedback_date, "e": str(embedding), "fid": feedback_id, "mid": session['user_id']}
        )
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

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

@api_bp.route('/chat', methods=['POST'])
def chat():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    db = SessionLocal()
    try:
        data = request.json
        user_question = data.get("question", "")
        user_id = session["user_id"]

        # 1. Gera embedding da pergunta
        question_embedding = services.get_embedding(user_question)
        embedding_str = "[" + ",".join(map(str, question_embedding)) + "]"

        # 2. Busca feedbacks relacionados
        feedback_results = db.execute(
            text("""
                SELECT u.name AS employee_name, f.feedback_date AS date, f.description AS content, 
                       'Feedback' AS type, (1 - (f.embedding <=> CAST(:embedding AS vector))) AS similarity
                FROM feedbacks f
                JOIN users u ON f.employee_id = u.id
                WHERE f.manager_id = :user_id
                ORDER BY similarity DESC
                LIMIT 5
            """),
            {"embedding": embedding_str, "user_id": user_id}
        ).fetchall()

        # 3. Busca trechos de reuniões relacionados
        meeting_results = db.execute(
            text("""
                SELECT m.meeting_date AS date, mc.chunk_text AS content, 
                       'Trecho de Reunião' AS type, (1 - (mc.embedding <=> CAST(:embedding AS vector))) AS similarity
                FROM meeting_chunks mc
                JOIN meetings m ON mc.meeting_id = m.id
                WHERE m.user_id = :user_id
                ORDER BY similarity DESC
                LIMIT 5
            """),
            {"embedding": embedding_str, "user_id": user_id}
        ).fetchall()

        # 4. Concatena resultados válidos (similaridade > 0.5)
        relevant_docs = []
        for row in feedback_results:
            if row.similarity and row.similarity > 0.3:
                relevant_docs.append({
                    "name": row.employee_name,
                    "date": row.date,
                    "content": row.content,
                    "type": row.type,
                    "similarity": float(row.similarity)
                })

        for row in meeting_results:
            if row.similarity and row.similarity > 0.3:
                relevant_docs.append({
                    "name": "Você",
                    "date": row.date,
                    "content": row.content,
                    "type": row.type,
                    "similarity": float(row.similarity)
                })

        relevant_docs.sort(key=lambda x: x["similarity"], reverse=True)

        # 5. Gera resposta
        if not relevant_docs:
            return jsonify({"answer": "Não encontrei feedbacks ou reuniões relacionados à sua pergunta."})

        context = "\n\n---\n\n".join(
            f"{doc['type']} de {doc['name']} em {doc['date'].strftime('%d/%m/%Y')}:\n{doc['content']}"
            for doc in relevant_docs
        )

        answer = services.create_chat_response(user_question, context)
        return jsonify({"answer": answer, "sources": len(relevant_docs)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

# ✅ ALTERADO: Salvar reunião agora cria a reunião e depois os chunks
@api_bp.route('/meetings', methods=['GET', 'POST'])
def handle_meetings():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        if request.method == 'POST':
            data = request.json
            # 1. Insere a reunião e obtém o ID
            result = db.execute(
                text("INSERT INTO meetings (user_id, meeting_date, transcription, summary) VALUES (:uid, :date, :trans, :sum) RETURNING id"),
                {"uid": session['user_id'], "date": data['meeting_date'], "trans": data.get('transcription', ''), "sum": data['summary']}
            )
            meeting_id = result.fetchone()[0]

            # 2. Constrói o texto completo e o divide em chunks
            full_text = f"Reunião de {data['meeting_date']}. Resumo: {data['summary']}. Transcrição: {data.get('transcription', '')}"
            text_chunks = services.chunk_text(full_text)

            # 3. Gera embedding para cada chunk e salva
            for chunk in text_chunks:
                embedding = services.get_embedding(chunk)
                db.execute(
                    text("INSERT INTO meeting_chunks (meeting_id, chunk_text, embedding) VALUES (:mid, :text, :emb)"),
                    {"mid": meeting_id, "text": chunk, "emb": str(embedding)}
                )

            db.commit()
            return jsonify({"success": True})
        else: # GET
            result = db.execute(
                text("SELECT id, meeting_date, summary FROM meetings WHERE user_id = :uid ORDER BY meeting_date DESC"),
                {"uid": session['user_id']}
            )
            meetings = [{"id": r[0], "meeting_date": r[1].isoformat(), "summary": r[2]} for r in result.fetchall()]
            return jsonify({"meetings": meetings})
    finally:
        db.close()

# ✅ ALTERADO: Atualizar reunião agora apaga chunks antigos e cria novos
@api_bp.route('/meetings/<int:meeting_id>', methods=['GET', 'PUT', 'DELETE'])
def handle_single_meeting(meeting_id):
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        if request.method == 'GET':
            result = db.execute(text("SELECT id, meeting_date, summary, transcription FROM meetings WHERE id = :mid AND user_id = :uid"),{"mid": meeting_id, "uid": session['user_id']}).fetchone()
            if result: return jsonify({"id": result[0], "meeting_date": result[1].isoformat(), "summary": result[2], "transcription": result[3]})
            return jsonify({"error": "Meeting not found"}), 404
        elif request.method == 'PUT':
            data = request.json
            # 1. Atualiza a reunião principal
            db.execute(
                text("UPDATE meetings SET meeting_date = :date, transcription = :trans, summary = :sum WHERE id = :mid AND user_id = :uid"),
                {"date": data['meeting_date'], "trans": data.get('transcription', ''), "sum": data['summary'], "mid": meeting_id, "uid": session['user_id']}
            )
            # 2. Apaga os chunks antigos
            db.execute(text("DELETE FROM meeting_chunks WHERE meeting_id = :mid"), {"mid": meeting_id})

            # 3. Cria e salva os novos chunks (mesma lógica do POST)
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
            # O 'ON DELETE CASCADE' na tabela de chunks já garante que eles serão apagados.
            db.execute(text("DELETE FROM meetings WHERE id = :mid AND user_id = :uid"), {"mid": meeting_id, "uid": session['user_id']})
            db.commit()
            return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/meetings/summarize', methods=['POST'])
def summarize_meeting():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    transcription = data.get('transcription')
    if not transcription: return jsonify({"error": "Transcription is required"}), 400
    try:
        summary = services.summarize_transcription(transcription)
        return jsonify({"summary": summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500