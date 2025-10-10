import os
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from openai import OpenAI
import hashlib

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
            CREATE TABLE IF NOT EXISTS managers (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                name VARCHAR(255) NOT NULL,
                company_name VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.commit()
        
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS employees (
                id SERIAL PRIMARY KEY,
                manager_id INTEGER REFERENCES managers(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL,
                position VARCHAR(255),
                department VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.commit()
        
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS feedbacks (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                manager_id INTEGER REFERENCES managers(id) ON DELETE CASCADE,
                feedback_to_employee TEXT,
                feedback_to_manager TEXT,
                expectations_company TEXT,
                expectations_manager TEXT,
                embedding vector(1536),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.commit()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_embedding(text):
    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding

def generate_insights(employee_name, latest_feedback, all_feedbacks):
    feedback_history = "\n\n".join([
        f"Feedback {i+1} ({fb['created_at']}):\n"
        f"To Employee: {fb['feedback_to_employee']}\n"
        f"To Manager: {fb['feedback_to_manager']}\n"
        f"Expectations (Company): {fb['expectations_company']}\n"
        f"Expectations (Manager): {fb['expectations_manager']}"
        for i, fb in enumerate(all_feedbacks[-5:])
    ])
    
    prompt = f"""Analyze the following feedback data for {employee_name} and generate concise insights in JSON format.

Latest Feedback:
- To Employee: {latest_feedback['feedback_to_employee']}
- To Manager: {latest_feedback['feedback_to_manager']}
- Expectations (Company): {latest_feedback['expectations_company']}
- Expectations (Manager): {latest_feedback['expectations_manager']}

Historical Feedback:
{feedback_history}

Generate insights in the following JSON format:
{{
    "development_points": ["point 1", "point 2", "point 3"],
    "strengths": ["strength 1", "strength 2", "strength 3"],
    "turnover_risk": {{"level": "low|medium|high", "reason": "explanation"}},
    "requires_attention": ["action item 1", "action item 2"]
}}

Be specific, actionable, and focus on patterns across feedbacks."""

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
            text("INSERT INTO managers (email, password_hash, name, company_name) VALUES (:email, :password_hash, :name, :company_name) RETURNING id"),
            {
                "email": data['email'],
                "password_hash": hash_password(data['password']),
                "name": data['name'],
                "company_name": data['company_name']
            }
        )
        db.commit()
        manager_id = result.fetchone()[0]
        
        session['manager_id'] = manager_id
        return jsonify({"success": True, "manager_id": manager_id})
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
            text("SELECT id, name, company_name FROM managers WHERE email = :email AND password_hash = :password_hash"),
            {
                "email": data['email'],
                "password_hash": hash_password(data['password'])
            }
        )
        manager = result.fetchone()
        
        if manager:
            session['manager_id'] = manager[0]
            return jsonify({
                "success": True,
                "manager": {
                    "id": manager[0],
                    "name": manager[1],
                    "company_name": manager[2]
                }
            })
        else:
            return jsonify({"success": False, "error": "Invalid credentials"}), 401
    finally:
        db.close()

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('manager_id', None)
    return jsonify({"success": True})

@app.route('/api/current-user', methods=['GET'])
def current_user():
    if 'manager_id' not in session:
        return jsonify({"authenticated": False}), 401
    
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, name, email, company_name FROM managers WHERE id = :id"),
            {"id": session['manager_id']}
        )
        manager = result.fetchone()
        
        if manager:
            return jsonify({
                "authenticated": True,
                "manager": {
                    "id": manager[0],
                    "name": manager[1],
                    "email": manager[2],
                    "company_name": manager[3]
                }
            })
        else:
            return jsonify({"authenticated": False}), 401
    finally:
        db.close()

@app.route('/api/employees', methods=['GET', 'POST'])
def employees():
    if 'manager_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    db = SessionLocal()
    
    try:
        if request.method == 'POST':
            data = request.json
            result = db.execute(
                text("INSERT INTO employees (manager_id, name, email, position, department) VALUES (:manager_id, :name, :email, :position, :department) RETURNING id"),
                {
                    "manager_id": session['manager_id'],
                    "name": data['name'],
                    "email": data['email'],
                    "position": data.get('position', ''),
                    "department": data.get('department', '')
                }
            )
            db.commit()
            employee_id = result.fetchone()[0]
            return jsonify({"success": True, "employee_id": employee_id})
        else:
            result = db.execute(
                text("SELECT id, name, email, position, department, created_at FROM employees WHERE manager_id = :manager_id ORDER BY created_at DESC"),
                {"manager_id": session['manager_id']}
            )
            employees = [
                {
                    "id": row[0],
                    "name": row[1],
                    "email": row[2],
                    "position": row[3],
                    "department": row[4],
                    "created_at": row[5].isoformat() if row[5] else None
                }
                for row in result.fetchall()
            ]
            return jsonify({"employees": employees})
    finally:
        db.close()

@app.route('/api/employees/<int:employee_id>', methods=['GET', 'PUT', 'DELETE'])
def employee_detail(employee_id):
    if 'manager_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    db = SessionLocal()
    
    try:
        if request.method == 'GET':
            result = db.execute(
                text("SELECT id, name, email, position, department FROM employees WHERE id = :id AND manager_id = :manager_id"),
                {"id": employee_id, "manager_id": session['manager_id']}
            )
            employee = result.fetchone()
            
            if employee:
                return jsonify({
                    "id": employee[0],
                    "name": employee[1],
                    "email": employee[2],
                    "position": employee[3],
                    "department": employee[4]
                })
            else:
                return jsonify({"error": "Employee not found"}), 404
        
        elif request.method == 'PUT':
            data = request.json
            db.execute(
                text("UPDATE employees SET name = :name, email = :email, position = :position, department = :department WHERE id = :id AND manager_id = :manager_id"),
                {
                    "id": employee_id,
                    "manager_id": session['manager_id'],
                    "name": data['name'],
                    "email": data['email'],
                    "position": data.get('position', ''),
                    "department": data.get('department', '')
                }
            )
            db.commit()
            return jsonify({"success": True})
        
        elif request.method == 'DELETE':
            db.execute(
                text("DELETE FROM employees WHERE id = :id AND manager_id = :manager_id"),
                {"id": employee_id, "manager_id": session['manager_id']}
            )
            db.commit()
            return jsonify({"success": True})
    finally:
        db.close()

@app.route('/api/feedbacks', methods=['GET', 'POST'])
def feedbacks():
    if 'manager_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    db = SessionLocal()
    
    try:
        if request.method == 'POST':
            data = request.json
            
            combined_text = f"{data['feedback_to_employee']} {data['feedback_to_manager']} {data['expectations_company']} {data['expectations_manager']}"
            embedding = get_embedding(combined_text)
            
            result = db.execute(
                text("""
                    INSERT INTO feedbacks 
                    (employee_id, manager_id, feedback_to_employee, feedback_to_manager, expectations_company, expectations_manager, embedding) 
                    VALUES (:employee_id, :manager_id, :feedback_to_employee, :feedback_to_manager, :expectations_company, :expectations_manager, :embedding) 
                    RETURNING id
                """),
                {
                    "employee_id": data['employee_id'],
                    "manager_id": session['manager_id'],
                    "feedback_to_employee": data['feedback_to_employee'],
                    "feedback_to_manager": data['feedback_to_manager'],
                    "expectations_company": data['expectations_company'],
                    "expectations_manager": data['expectations_manager'],
                    "embedding": str(embedding)
                }
            )
            db.commit()
            feedback_id = result.fetchone()[0]
            
            return jsonify({"success": True, "feedback_id": feedback_id})
        else:
            result = db.execute(
                text("""
                    SELECT f.id, f.employee_id, e.name, f.feedback_to_employee, f.feedback_to_manager, 
                           f.expectations_company, f.expectations_manager, f.created_at
                    FROM feedbacks f
                    JOIN employees e ON f.employee_id = e.id
                    WHERE f.manager_id = :manager_id
                    ORDER BY f.created_at DESC
                """),
                {"manager_id": session['manager_id']}
            )
            feedbacks = [
                {
                    "id": row[0],
                    "employee_id": row[1],
                    "employee_name": row[2],
                    "feedback_to_employee": row[3],
                    "feedback_to_manager": row[4],
                    "expectations_company": row[5],
                    "expectations_manager": row[6],
                    "created_at": row[7].isoformat() if row[7] else None
                }
                for row in result.fetchall()
            ]
            return jsonify({"feedbacks": feedbacks})
    finally:
        db.close()

@app.route('/api/dashboard', methods=['GET'])
def dashboard():
    if 'manager_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    db = SessionLocal()
    
    try:
        result = db.execute(
            text("""
                SELECT e.id, e.name, e.position, e.department,
                       f.feedback_to_employee, f.feedback_to_manager, 
                       f.expectations_company, f.expectations_manager, f.created_at
                FROM employees e
                LEFT JOIN LATERAL (
                    SELECT * FROM feedbacks 
                    WHERE employee_id = e.id 
                    ORDER BY created_at DESC 
                    LIMIT 1
                ) f ON true
                WHERE e.manager_id = :manager_id
                ORDER BY f.created_at DESC NULLS LAST
            """),
            {"manager_id": session['manager_id']}
        )
        
        dashboard_data = []
        
        for row in result.fetchall():
            employee_data = {
                "employee_id": row[0],
                "employee_name": row[1],
                "position": row[2],
                "department": row[3],
                "latest_feedback": None,
                "insights": None
            }
            
            if row[4]:
                latest_feedback = {
                    "feedback_to_employee": row[4],
                    "feedback_to_manager": row[5],
                    "expectations_company": row[6],
                    "expectations_manager": row[7],
                    "created_at": row[8].isoformat() if row[8] else None
                }
                employee_data["latest_feedback"] = latest_feedback
                
                all_feedbacks_result = db.execute(
                    text("""
                        SELECT feedback_to_employee, feedback_to_manager, 
                               expectations_company, expectations_manager, created_at
                        FROM feedbacks
                        WHERE employee_id = :employee_id
                        ORDER BY created_at DESC
                    """),
                    {"employee_id": row[0]}
                )
                
                all_feedbacks = [
                    {
                        "feedback_to_employee": fb[0],
                        "feedback_to_manager": fb[1],
                        "expectations_company": fb[2],
                        "expectations_manager": fb[3],
                        "created_at": fb[4].isoformat() if fb[4] else None
                    }
                    for fb in all_feedbacks_result.fetchall()
                ]
                
                insights = generate_insights(row[1], latest_feedback, all_feedbacks)
                employee_data["insights"] = insights
            
            dashboard_data.append(employee_data)
        
        return jsonify({"dashboard": dashboard_data})
    finally:
        db.close()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
