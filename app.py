import os
import json
from datetime import datetime, timedelta
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
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                name VARCHAR(255) NOT NULL,
                company VARCHAR(255) NOT NULL,
                phone VARCHAR(50),
                slack_user_id VARCHAR(50),
                manager_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.commit()
        
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS feedbacks (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                author_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                feedback_to_user TEXT NOT NULL,
                feedback_to_manager TEXT,
                expectations_company TEXT,
                expectations_manager TEXT,
                feedback_date DATE NOT NULL,
                embedding vector(1536),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.commit()
        
        try:
            conn.execute(text("""
                ALTER TABLE feedbacks 
                ADD COLUMN IF NOT EXISTS feedback_date DATE DEFAULT CURRENT_DATE
            """))
            conn.commit()
            
            conn.execute(text("""
                UPDATE feedbacks 
                SET feedback_date = COALESCE(feedback_date, DATE(created_at))
                WHERE feedback_date IS NULL
            """))
            conn.commit()
        except Exception as e:
            print(f"Migration note: {e}")

def hash_password(password):
    return generate_password_hash(password)

def get_embedding(text):
    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding

def generate_insights(employee_name, latest_feedback, all_feedbacks):
    feedback_history = "\n\n".join([
        f"Feedback {i+1} ({fb['created_at']}):\n"
        f"Ao Usuário: {fb['feedback_to_user']}\n"
        f"Ao Gestor: {fb['feedback_to_manager'] or 'Não informado'}\n"
        f"Expectativas (Empresa): {fb['expectations_company'] or 'Não informado'}\n"
        f"Expectativas (Gestor): {fb['expectations_manager'] or 'Não informado'}"
        for i, fb in enumerate(all_feedbacks[-5:])
    ])
    
    prompt = f"""Analise os dados de feedback de {employee_name} e gere insights concisos em formato JSON, em PORTUGUÊS BRASILEIRO.

Último Feedback:
- Ao Usuário: {latest_feedback['feedback_to_user']}
- Ao Gestor: {latest_feedback['feedback_to_manager'] or 'Não informado'}
- Expectativas (Empresa): {latest_feedback['expectations_company'] or 'Não informado'}
- Expectativas (Gestor): {latest_feedback['expectations_manager'] or 'Não informado'}

Histórico de Feedbacks:
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
                "email": data['email'],
                "password_hash": hash_password(data['password']),
                "name": data['name'],
                "company": data.get('company', ''),
                "phone": data.get('phone', ''),
                "manager_id": data.get('manager_id')
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
            text("SELECT id, name, company, password_hash FROM users WHERE email = :email"),
            {"email": data['email']}
        )
        user = result.fetchone()
        
        if user and check_password_hash(user[3], data['password']):
            session['user_id'] = user[0]
            return jsonify({
                "success": True,
                "user": {
                    "id": user[0],
                    "name": user[1],
                    "company": user[2]
                }
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
                manager_result = db.execute(
                    text("SELECT name FROM users WHERE id = :id"),
                    {"id": user[5]}
                )
                manager_row = manager_result.fetchone()
                if manager_row:
                    manager_name = manager_row[0]
            
            return jsonify({
                "authenticated": True,
                "user": {
                    "id": user[0],
                    "name": user[1],
                    "email": user[2],
                    "company": user[3],
                    "phone": user[4],
                    "manager_id": user[5],
                    "manager_name": manager_name
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
        users = [
            {
                "id": row[0],
                "name": row[1],
                "email": row[2],
                "company": row[3]
            }
            for row in result.fetchall()
        ]
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
            {
                "id": row[0],
                "name": row[1],
                "email": row[2],
                "company": row[3],
                "phone": row[4]
            }
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
            text("""
                UPDATE users 
                SET name = :name, company = :company, phone = :phone, manager_id = :manager_id
                WHERE id = :id
            """),
            {
                "id": session['user_id'],
                "name": data['name'],
                "company": data.get('company', ''),
                "phone": data.get('phone', ''),
                "manager_id": data.get('manager_id')
            }
        )
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        db.close()

@app.route('/api/feedbacks/<int:feedback_id>', methods=['PUT'])
def update_feedback(feedback_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    db = SessionLocal()
    
    try:
        data = request.json
        
        from datetime import datetime
        feedback_date_obj = datetime.strptime(data['feedback_date'], '%Y-%m-%d')
        date_formatted = feedback_date_obj.strftime('%d/%m/%Y')
        
        temporal_context = f"No feedback realizado no dia {date_formatted} foram discutidos os seguintes pontos: "
        
        feedback_parts = [temporal_context]
        feedback_parts.append(f"Feedback ao usuário: {data['feedback_to_user']}")
        
        if data.get('feedback_to_manager'):
            feedback_parts.append(f"Feedback ao gestor: {data['feedback_to_manager']}")
        if data.get('expectations_company'):
            feedback_parts.append(f"Expectativas sobre a empresa: {data['expectations_company']}")
        if data.get('expectations_manager'):
            feedback_parts.append(f"Expectativas sobre o gestor: {data['expectations_manager']}")
        
        combined_text = " ".join(feedback_parts)
        embedding = get_embedding(combined_text)
        
        db.execute(
            text("""
                UPDATE feedbacks 
                SET feedback_to_user = :feedback_to_user,
                    feedback_to_manager = :feedback_to_manager,
                    expectations_company = :expectations_company,
                    expectations_manager = :expectations_manager,
                    feedback_date = :feedback_date,
                    embedding = :embedding
                WHERE id = :feedback_id AND author_id = :author_id
            """),
            {
                "feedback_id": feedback_id,
                "author_id": session['user_id'],
                "feedback_to_user": data['feedback_to_user'],
                "feedback_to_manager": data.get('feedback_to_manager', ''),
                "expectations_company": data.get('expectations_company', ''),
                "expectations_manager": data.get('expectations_manager', ''),
                "feedback_date": data['feedback_date'],
                "embedding": str(embedding)
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
            
            from datetime import datetime
            feedback_date_obj = datetime.strptime(data['feedback_date'], '%Y-%m-%d')
            date_formatted = feedback_date_obj.strftime('%d/%m/%Y')
            
            temporal_context = f"No feedback realizado no dia {date_formatted} foram discutidos os seguintes pontos: "
            
            feedback_parts = [temporal_context]
            feedback_parts.append(f"Feedback ao usuário: {data['feedback_to_user']}")
            
            if data.get('feedback_to_manager'):
                feedback_parts.append(f"Feedback ao gestor: {data['feedback_to_manager']}")
            if data.get('expectations_company'):
                feedback_parts.append(f"Expectativas sobre a empresa: {data['expectations_company']}")
            if data.get('expectations_manager'):
                feedback_parts.append(f"Expectativas sobre o gestor: {data['expectations_manager']}")
            
            combined_text = " ".join(feedback_parts)
            embedding = get_embedding(combined_text)
            
            result = db.execute(
                text("""
                    INSERT INTO feedbacks 
                    (user_id, author_id, feedback_to_user, feedback_to_manager, expectations_company, expectations_manager, feedback_date, embedding) 
                    VALUES (:user_id, :author_id, :feedback_to_user, :feedback_to_manager, :expectations_company, :expectations_manager, :feedback_date, :embedding) 
                    RETURNING id
                """),
                {
                    "user_id": data['user_id'],
                    "author_id": session['user_id'],
                    "feedback_to_user": data['feedback_to_user'],
                    "feedback_to_manager": data.get('feedback_to_manager', ''),
                    "expectations_company": data.get('expectations_company', ''),
                    "expectations_manager": data.get('expectations_manager', ''),
                    "feedback_date": data['feedback_date'],
                    "embedding": str(embedding)
                }
            )
            db.commit()
            feedback_id = result.fetchone()[0]
            
            return jsonify({"success": True, "feedback_id": feedback_id})
        else:
            result = db.execute(
                text("""
                    SELECT f.id, f.user_id, u.name, f.feedback_to_user, f.feedback_to_manager, 
                           f.expectations_company, f.expectations_manager, f.feedback_date, f.created_at
                    FROM feedbacks f
                    JOIN users u ON f.user_id = u.id
                    WHERE f.author_id = :author_id
                    ORDER BY f.feedback_date DESC, f.created_at DESC
                """),
                {"author_id": session['user_id']}
            )
            feedbacks = [
                {
                    "id": row[0],
                    "user_id": row[1],
                    "user_name": row[2],
                    "feedback_to_user": row[3],
                    "feedback_to_manager": row[4],
                    "expectations_company": row[5],
                    "expectations_manager": row[6],
                    "feedback_date": row[7].isoformat() if row[7] else None,
                    "created_at": row[8].isoformat() if row[8] else None
                }
                for row in result.fetchall()
            ]
            return jsonify({"feedbacks": feedbacks})
    finally:
        db.close()

@app.route('/api/dashboard', methods=['GET'])
def dashboard():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    db = SessionLocal()
    
    try:
        result = db.execute(
            text("""
                SELECT u.id, u.name, u.company, u.phone,
                       f.feedback_to_user, f.feedback_to_manager, 
                       f.expectations_company, f.expectations_manager, f.feedback_date, f.created_at
                FROM users u
                LEFT JOIN LATERAL (
                    SELECT * FROM feedbacks 
                    WHERE user_id = u.id 
                    ORDER BY feedback_date DESC, created_at DESC 
                    LIMIT 1
                ) f ON true
                WHERE u.manager_id = :manager_id
                ORDER BY f.feedback_date DESC NULLS LAST, f.created_at DESC NULLS LAST
            """),
            {"manager_id": session['user_id']}
        )
        
        dashboard_data = []
        
        for row in result.fetchall():
            user_data = {
                "user_id": row[0],
                "user_name": row[1],
                "company": row[2],
                "phone": row[3],
                "latest_feedback": None,
                "insights": None
            }
            
            if row[4]:
                latest_feedback = {
                    "feedback_to_user": row[4],
                    "feedback_to_manager": row[5],
                    "expectations_company": row[6],
                    "expectations_manager": row[7],
                    "feedback_date": row[8].isoformat() if row[8] else None,
                    "created_at": row[9].isoformat() if row[9] else None
                }
                user_data["latest_feedback"] = latest_feedback
                
                all_feedbacks_result = db.execute(
                    text("""
                        SELECT feedback_to_user, feedback_to_manager, 
                               expectations_company, expectations_manager, feedback_date, created_at
                        FROM feedbacks
                        WHERE user_id = :user_id
                        ORDER BY feedback_date DESC, created_at DESC
                    """),
                    {"user_id": row[0]}
                )
                
                all_feedbacks = [
                    {
                        "feedback_to_user": fb[0],
                        "feedback_to_manager": fb[1],
                        "expectations_company": fb[2],
                        "expectations_manager": fb[3],
                        "feedback_date": fb[4].isoformat() if fb[4] else None,
                        "created_at": fb[5].isoformat() if fb[5] else None
                    }
                    for fb in all_feedbacks_result.fetchall()
                ]
                
                insights = generate_insights(row[1], latest_feedback, all_feedbacks)
                user_data["insights"] = insights
            
            dashboard_data.append(user_data)
        
        return jsonify({"dashboard": dashboard_data})
    finally:
        db.close()

@app.route('/api/user/<int:user_id>/feedbacks', methods=['GET'])
def user_feedbacks(user_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    db = SessionLocal()
    
    try:
        result = db.execute(
            text("""
                SELECT f.id, f.feedback_to_user, f.feedback_to_manager, 
                       f.expectations_company, f.expectations_manager, f.feedback_date, f.created_at
                FROM feedbacks f
                WHERE f.user_id = :user_id AND f.author_id = :author_id
                ORDER BY f.feedback_date DESC, f.created_at DESC
            """),
            {"user_id": user_id, "author_id": session['user_id']}
        )
        
        feedbacks = [
            {
                "id": row[0],
                "feedback_to_user": row[1],
                "feedback_to_manager": row[2],
                "expectations_company": row[3],
                "expectations_manager": row[4],
                "feedback_date": row[5].isoformat() if row[5] else None,
                "created_at": row[6].isoformat() if row[6] else None
            }
            for row in result.fetchall()
        ]
        
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
            
            db.execute(
                text("UPDATE users SET profile_photo = :photo WHERE id = :user_id"),
                {"photo": photo_bytes, "user_id": session['user_id']}
            )
            db.commit()
            
            return jsonify({"success": True})
        else:
            result = db.execute(
                text("SELECT profile_photo FROM users WHERE id = :user_id"),
                {"user_id": session['user_id']}
            )
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
        result = db.execute(
            text("SELECT profile_photo FROM users WHERE id = :user_id"),
            {"user_id": user_id}
        )
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
    """Health check endpoint para deployment"""
    return jsonify({"status": "healthy", "service": "FeedbackAI"}), 200

@app.route('/api/chat', methods=['POST'])
def chat():
    # A verificação de autorização foi movida para dentro do 'try'
    # para tratar os dois casos: web e Slack.

    db = SessionLocal()

    try:
        data = request.json
        author_id = None

        # NOVO: Verifica se a requisição veio do Slack (via n8n)
        if 'slack_user_id' in data:
            slack_user_id = data.get('slack_user_id')
            # Busca o ID do usuário no banco de dados usando o slack_user_id
            user_result = db.execute(
                text("SELECT id FROM users WHERE slack_user_id = :slack_id"),
                {"slack_id": slack_user_id}
            ).fetchone()

            if user_result:
                author_id = user_result[0]
            else:
                # Retorna uma resposta amigável se o usuário do Slack não for encontrado
                return jsonify({
                    "answer": "Desculpe, não consegui encontrar seu usuário do Slack em nosso sistema. Por favor, verifique se sua conta está associada corretamente."
                }), 404

        # Lógica original: Verifica se a requisição veio da interface web
        elif 'user_id' in session:
            author_id = session['user_id']

        # Se após as duas verificações não encontramos um autor, a requisição não é autorizada.
        if not author_id:
            return jsonify({"error": "Unauthorized"}), 401

        user_question = data.get('question', '')

        if not user_question:
            return jsonify({"error": "Question is required"}), 400

        question_embedding = get_embedding(user_question)

        embedding_str = '[' + ','.join(map(str, question_embedding)) + ']'

        # MODIFICADO: A query agora usa a variável 'author_id' que foi definida
        # dinamicamente (seja pelo Slack ID ou pela sessão web).
        result = db.execute(
            text("""
                SELECT f.id, f.feedback_to_user, f.feedback_to_manager, 
                       f.expectations_company, f.expectations_manager, 
                       f.feedback_date, f.created_at, u.name,
                       (1 - (f.embedding <=> CAST(:question_embedding AS vector))) as similarity
                FROM feedbacks f
                JOIN users u ON f.user_id = u.id
                WHERE f.author_id = :author_id
                ORDER BY similarity DESC
                LIMIT 5
            """),
            {
                "question_embedding": embedding_str,
                "author_id": author_id 
            }
        )

        relevant_feedbacks = []
        for row in result.fetchall():
            relevant_feedbacks.append({
                "user_name": row[7],
                "feedback_date": row[5].strftime('%d/%m/%Y') if row[5] else 'Data não informada',
                "feedback_to_user": row[1],
                "feedback_to_manager": row[2] or '',
                "expectations_company": row[3] or '',
                "expectations_manager": row[4] or '',
                "similarity": float(row[8])
            })

        if not relevant_feedbacks:
            return jsonify({
                "answer": "Não encontrei feedbacks relacionados à sua pergunta. Tente fazer outra pergunta ou verifique se já cadastrou feedbacks no sistema."
            })

        context = "\n\n".join([
            f"Feedback de {fb['user_name']} em {fb['feedback_date']}:\n"
            f"- Ao usuário: {fb['feedback_to_user']}\n" +
            (f"- Ao gestor: {fb['feedback_to_manager']}\n" if fb['feedback_to_manager'] else "") +
            (f"- Expectativas (Empresa): {fb['expectations_company']}\n" if fb['expectations_company'] else "") +
            (f"- Expectativas (Gestor): {fb['expectations_manager']}\n" if fb['expectations_manager'] else "")
            for fb in relevant_feedbacks
        ])

        chat_response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """Você é um assistente inteligente que ajuda gestores a encontrar informações em seus feedbacks registrados. 

Responda SEMPRE em português brasileiro de forma clara, objetiva e profissional. 
Use as informações dos feedbacks fornecidos para responder a pergunta do usuário.
Se a pergunta for sobre datas, cite as datas específicas encontradas nos feedbacks.
Se não houver informação suficiente, seja honesto e sugira ao usuário cadastrar mais feedbacks."""
                },
                {
                    "role": "user",
                    "content": f"Baseado nestes feedbacks:\n\n{context}\n\nPergunta: {user_question}"
                }
            ],
            temperature=0.7,
            max_tokens=500
        )

        return jsonify({
            "answer": chat_response.choices[0].message.content,
            "sources": len(relevant_feedbacks)
        })

    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
