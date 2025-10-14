import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from openai import OpenAI
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder='static', static_url_path='')
app.secret_key = os.environ.get('SESSION_SECRET', 'dev-secret-key-change-in-production')
CORS(app, supports_credentials=True)

DATABASE_URL = os.environ.get('DATABASE_URL')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is not set")

if '5432' in DATABASE_URL and 'supabase' in DATABASE_URL:
    print("WARNING: You're using Supabase direct connection (port 5432).")
    print("This may not work from Replit. Use Transaction Pooler (port 6543) instead.")
    print("Instructions in README.md")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine)

openai_client = OpenAI(api_key=OPENAI_API_KEY)

def init_db():
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY, email VARCHAR(255) UNIQUE NOT NULL, password_hash VARCHAR(255) NOT NULL,
                name VARCHAR(255) NOT NULL, company VARCHAR(255) NOT NULL, phone VARCHAR(50),
                slack_user_id TEXT, manager_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, profile_photo BYTEA
            )"""))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS feedbacks (
                id SERIAL PRIMARY KEY, employee_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                manager_id INTEGER REFERENCES users(id) ON DELETE CASCADE, description TEXT NOT NULL,
                feedback_date DATE NOT NULL, embedding vector(1536), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS insights (
                id SERIAL PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                manager_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, insight_data TEXT NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source_feedback_timestamp TIMESTAMP NOT NULL,
                UNIQUE(employee_id, manager_id)
            )"""))
        conn.commit()

def hash_password(password):
    return generate_password_hash(password)

def get_embedding(text):
    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding

def generate_insights(employee_name, latest_feedback, all_feedbacks):
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

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    db = SessionLocal()
    try:
        result = db.execute(
            text("INSERT INTO users (email, password_hash, name, company, phone, manager_id) VALUES (:email, :password_hash, :name, :company, :phone, :manager_id) RETURNING id"),
            {
                "email": data['email'], "password_hash": hash_password(data['password']), "name": data['name'],
                "company": data.get('company', ''), "phone": data.get('phone', ''), "manager_id": data.get('manager_id')
            }
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

@app.route('/api/login', methods=['POST'])
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
            return jsonify({
                "success": True,
                "user": {"id": user[0], "name": user[1], "company": user[2], "email": user[4]}
            })
        else:
            return jsonify({"success": False, "error": "Invalid credentials"}), 401
    finally:
        db.close()

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    return jsonify({"success": True})

@app.route('/api/current-user', methods=['GET'])
def current_user():
    if 'user_id' not in session:
        return jsonify({"authenticated": False}), 401
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, name, email, company, phone, manager_id FROM users WHERE id = :id"),
            {"id": session['user_id']}
        )
        user = result.fetchone()
        if user:
            manager_name = None
            if user[5]:
                manager_result = db.execute(text("SELECT name FROM users WHERE id = :id"), {"id": user[5]})
                manager_row = manager_result.fetchone()
                if manager_row:
                    manager_name = manager_row[0]
            return jsonify({
                "authenticated": True,
                "user": {
                    "id": user[0], "name": user[1], "email": user[2], "company": user[3],
                    "phone": user[4], "manager_id": user[5], "manager_name": manager_name
                }
            })
        else:
            return jsonify({"authenticated": False}), 401
    finally:
        db.close()

@app.route('/api/users', methods=['GET'])
def get_users():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
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

@app.route('/api/managed-users', methods=['GET'])
def get_managed_users():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, name, email, company, phone FROM users WHERE manager_id = :manager_id ORDER BY name"),
            {"manager_id": session['user_id']}
        )
        managed_users = [
            {"id": row[0], "name": row[1], "email": row[2], "company": row[3], "phone": row[4]}
            for row in result.fetchall()
        ]
        return jsonify({"managed_users": managed_users})
    finally:
        db.close()

@app.route('/api/profile', methods=['PUT'])
def update_profile():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE users SET name = :name, company = :company, phone = :phone, manager_id = :manager_id WHERE id = :id"),
            {
                "id": session['user_id'], "name": data['name'], "company": data.get('company', ''),
                "phone": data.get('phone', ''), "manager_id": data.get('manager_id')
            }
        )
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        db.close()

@app.route('/api/profile/password', methods=['PUT'])
def change_password():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not current_password or not new_password:
        return jsonify({"success": False, "error": "Todos os campos são obrigatórios"}), 400

    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT password_hash FROM users WHERE id = :id"),
            {"id": session['user_id']}
        )
        user = result.fetchone()

        if not user:
            return jsonify({"success": False, "error": "Usuário não encontrado"}), 404

        if not check_password_hash(user[0], current_password):
            return jsonify({"success": False, "error": "Senha atual incorreta"}), 401

        new_password_hash = hash_password(new_password)

        db.execute(
            text("UPDATE users SET password_hash = :new_hash WHERE id = :id"),
            {"new_hash": new_password_hash, "id": session['user_id']}
        )
        db.commit()

        return jsonify({"success": True})

    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/feedbacks/<int:feedback_id>', methods=['PUT'])
def update_feedback(feedback_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        data = request.json
        description = data.get('description')
        if not description:
            return jsonify({"error": "Description is required"}), 400
        feedback_date_obj = datetime.strptime(data['feedback_date'], '%Y-%m-%d')
        date_formatted = feedback_date_obj.strftime('%d/%m/%Y')
        temporal_context = f"No feedback realizado no dia {date_formatted}, foi discutido o seguinte: {description}"
        embedding = get_embedding(temporal_context)
        db.execute(
            text("UPDATE feedbacks SET description = :description, feedback_date = :feedback_date, embedding = :embedding WHERE id = :feedback_id AND manager_id = :manager_id"),
            {
                "feedback_id": feedback_id, "manager_id": session['user_id'], "description": description,
                "feedback_date": data['feedback_date'], "embedding": str(embedding)
            }
        )
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/feedbacks', methods=['GET', 'POST'])
def feedbacks():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        if request.method == 'POST':
            data = request.json
            description = data.get('description')
            if not description:
                return jsonify({"error": "Description is required"}), 400
            feedback_date_obj = datetime.strptime(data['feedback_date'], '%Y-%m-%d')
            date_formatted = feedback_date_obj.strftime('%d/%m/%Y')
            temporal_context = f"No feedback realizado no dia {date_formatted}, foi discutido o seguinte: {description}"
            embedding = get_embedding(temporal_context)
            result = db.execute(
                text("INSERT INTO feedbacks (employee_id, manager_id, description, feedback_date, embedding) VALUES (:employee_id, :manager_id, :description, :feedback_date, :embedding) RETURNING id"),
                {
                    "employee_id": data['user_id'], "manager_id": session['user_id'], "description": description,
                    "feedback_date": data['feedback_date'], "embedding": str(embedding)
                }
            )
            db.commit()
            feedback_id = result.fetchone()[0]
            return jsonify({"success": True, "feedback_id": feedback_id})
        else: # GET
            result = db.execute(
                text("SELECT f.id, f.employee_id, u.name, f.description, f.feedback_date, f.created_at FROM feedbacks f JOIN users u ON f.employee_id = u.id WHERE f.manager_id = :manager_id ORDER BY f.feedback_date DESC, f.created_at DESC"),
                {"manager_id": session['user_id']}
            )
            feedbacks_list = [
                {"id": row[0], "user_id": row[1], "user_name": row[2], "description": row[3], "feedback_date": row[4].isoformat() if row[4] else None, "created_at": row[5].isoformat() if row[5] else None}
                for row in result.fetchall()
            ]
            return jsonify({"feedbacks": feedbacks_list})
    finally:
        db.close()

@app.route('/api/dashboard', methods=['GET'])
def dashboard():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
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

@app.route('/api/user/<int:user_id>/insights', methods=['GET'])
def user_insights(user_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    manager_id = session['user_id']
    db = SessionLocal()
    try:
        user_check = db.execute(
            text("SELECT id, name, company, phone FROM users WHERE id = :user_id AND manager_id = :manager_id"),
            {"user_id": user_id, "manager_id": manager_id}
        ).fetchone()
        if not user_check:
            return jsonify({"error": "User not found or not managed by you"}), 404
        last_feedback_result = db.execute(
            text("SELECT description, feedback_date, created_at FROM feedbacks WHERE employee_id = :employee_id AND manager_id = :manager_id ORDER BY feedback_date DESC, created_at DESC LIMIT 1"),
            {"employee_id": user_id, "manager_id": manager_id}
        ).fetchone()
        if not last_feedback_result:
            return jsonify({"user_id": user_id, "user_name": user_check[1], "company": user_check[2], "phone": user_check[3], "latest_feedback": None, "insights": None})
        last_feedback_timestamp = last_feedback_result[2]
        cache_result = db.execute(
            text("SELECT insight_data FROM insights WHERE employee_id = :employee_id AND manager_id = :manager_id AND generated_at >= :last_feedback_timestamp ORDER BY generated_at DESC LIMIT 1"),
            {"employee_id": user_id, "manager_id": manager_id, "last_feedback_timestamp": last_feedback_timestamp}
        ).fetchone()
        latest_feedback = {"description": last_feedback_result[0], "feedback_date": last_feedback_result[1].isoformat() if last_feedback_result[1] else None, "created_at": last_feedback_result[2].isoformat() if last_feedback_result[2] else None}
        if cache_result:
            cached_insights = json.loads(cache_result[0])
            return jsonify({"user_id": user_id, "user_name": user_check[1], "company": user_check[2], "phone": user_check[3], "latest_feedback": latest_feedback, "insights": cached_insights, "cached": True})
        all_feedbacks_result = db.execute(
            text("SELECT description, feedback_date, created_at FROM feedbacks WHERE employee_id = :employee_id AND manager_id = :manager_id ORDER BY feedback_date DESC, created_at DESC"),
            {"employee_id": user_id, "manager_id": manager_id}
        )
        all_feedbacks = [{"description": fb[0], "feedback_date": fb[1].isoformat() if fb[1] else None, "created_at": fb[2].isoformat() if fb[2] else None} for fb in all_feedbacks_result.fetchall()]
        insights = generate_insights(user_check[1], latest_feedback, all_feedbacks)
        db.execute(
            text("INSERT INTO insights (employee_id, manager_id, insight_data, source_feedback_timestamp) VALUES (:employee_id, :manager_id, :insight_data, :source_feedback_timestamp) ON CONFLICT (employee_id, manager_id) DO UPDATE SET insight_data = :insight_data, generated_at = CURRENT_TIMESTAMP, source_feedback_timestamp = :source_feedback_timestamp"),
            {"employee_id": user_id, "manager_id": manager_id, "insight_data": json.dumps(insights, ensure_ascii=False), "source_feedback_timestamp": last_feedback_timestamp}
        )
        db.commit()
        return jsonify({"user_id": user_id, "user_name": user_check[1], "company": user_check[2], "phone": user_check[3], "latest_feedback": latest_feedback, "insights": insights, "cached": False})
    except Exception as e:
        print(f"Error generating insights: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/user/<int:user_id>/feedbacks', methods=['GET'])
def user_feedbacks(user_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT f.id, f.description, f.feedback_date, f.created_at FROM feedbacks f WHERE f.employee_id = :employee_id AND f.manager_id = :manager_id ORDER BY f.feedback_date DESC, f.created_at DESC"),
            {"employee_id": user_id, "manager_id": session['user_id']}
        )
        feedbacks = [{"id": row[0], "description": row[1], "feedback_date": row[2].isoformat() if row[2] else None, "created_at": row[3].isoformat() if row[3] else None} for row in result.fetchall()]
        return jsonify({"feedbacks": feedbacks})
    finally:
        db.close()

@app.route('/api/profile/photo', methods=['POST', 'GET'])
def profile_photo():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        if request.method == 'POST':
            import base64
            data = request.json
            photo_data = data.get('photo')
            if not photo_data:
                return jsonify({"error": "No photo provided"}), 400
            if photo_data.startswith('data:image'):
                photo_data = photo_data.split(',')[1]
            photo_bytes = base64.b64decode(photo_data)
            db.execute(text("UPDATE users SET profile_photo = :photo WHERE id = :user_id"), {"photo": photo_bytes, "user_id": session['user_id']})
            db.commit()
            return jsonify({"success": True})
        else:
            result = db.execute(text("SELECT profile_photo FROM users WHERE id = :user_id"), {"user_id": session['user_id']})
            row = result.fetchone()
            if row and row[0]:
                import base64
                photo_base64 = base64.b64encode(row[0]).decode('utf-8')
                return jsonify({"photo": f"data:image/jpeg;base64,{photo_base64}"})
            else:
                return jsonify({"photo": None})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/user/<int:user_id>/photo', methods=['GET'])
def user_photo(user_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    db = SessionLocal()
    try:
        result = db.execute(text("SELECT profile_photo FROM users WHERE id = :user_id"), {"user_id": user_id})
        row = result.fetchone()
        if row and row[0]:
            import base64
            photo_base64 = base64.b64encode(row[0]).decode('utf-8')
            return jsonify({"photo": f"data:image/jpeg;base64,{photo_base64}"})
        else:
            return jsonify({"photo": None})
    finally:
        db.close()

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "service": "FeedbackAI"}), 200

@app.route('/api/chat', methods=['POST'])
def chat():
    db = SessionLocal()
    try:
        data = request.json
        author_id = None
        if 'slack_user_id' in data:
            slack_user_id = data.get('slack_user_id')
            user_result = db.execute(text("SELECT id FROM users WHERE slack_user_id = :slack_id"), {"slack_id": slack_user_id}).fetchone()
            if user_result:
                author_id = user_result[0]
            else:
                return jsonify({"answer": "Desculpe, não consegui encontrar seu usuário do Slack."}), 404
        elif 'user_id' in session:
            author_id = session['user_id']
        if not author_id:
            return jsonify({"error": "Unauthorized"}), 401
        user_question = data.get('question', '')
        if not user_question:
            return jsonify({"error": "Question is required"}), 400
        question_embedding = get_embedding(user_question)
        embedding_str = '[' + ','.join(map(str, question_embedding)) + ']'
        result = db.execute(
            text("SELECT f.id, f.description, f.feedback_date, u.name, (1 - (f.embedding <=> CAST(:question_embedding AS vector))) as similarity FROM feedbacks f JOIN users u ON f.employee_id = u.id WHERE f.manager_id = :author_id ORDER BY similarity DESC LIMIT 5"),
            {"question_embedding": embedding_str, "author_id": author_id}
        )
        relevant_feedbacks = [{"user_name": row[3], "feedback_date": row[2].strftime('%d/%m/%Y') if row[2] else 'Data não informada', "description": row[1], "similarity": float(row[4])} for row in result.fetchall()]
        if not relevant_feedbacks:
            return jsonify({"answer": "Não encontrei feedbacks relacionados à sua pergunta."})
        context = "\n\n---\n\n".join([f"Feedback de {fb['user_name']} em {fb['feedback_date']}:\n{fb['description']}" for fb in relevant_feedbacks])
        chat_response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Você é um assistente que ajuda gestores a encontrar informações em feedbacks. Responda em português brasileiro de forma clara e objetiva, usando os feedbacks fornecidos. Se não houver informação, seja honesto."},
                {"role": "user", "content": f"Baseado nestes feedbacks:\n\n{context}\n\nPergunta: {user_question}"}
            ],
            temperature=0.7, max_tokens=500
        )
        return jsonify({"answer": chat_response.choices[0].message.content, "sources": len(relevant_feedbacks)})
    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/batch/feedback', methods=['POST'])
def batch_feedback():

    data = request.json

    required_fields = ['employee_id', 'manager_id', 'feedback_date', 'description']
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    db = SessionLocal()
    try:
        # Lógica de inserção similar à original, mas com manager_id vindo do corpo da requisição
        description = data['description']
        feedback_date_obj = datetime.strptime(data['feedback_date'], '%Y-%m-%d')
        date_formatted = feedback_date_obj.strftime('%d/%m/%Y')
        temporal_context = f"No feedback realizado no dia {date_formatted}, foi discutido o seguinte: {description}"
        embedding = get_embedding(temporal_context)

        db.execute(
            text("INSERT INTO feedbacks (employee_id, manager_id, description, feedback_date, embedding) VALUES (:employee_id, :manager_id, :description, :feedback_date, :embedding)"),
            {
                "employee_id": data['employee_id'],
                "manager_id": data['manager_id'],
                "description": description,
                "feedback_date": data['feedback_date'],
                "embedding": str(embedding)
            }
        )
        db.commit()
        return jsonify({"success": True, "message": f"Feedback for employee {data['employee_id']} created."})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)