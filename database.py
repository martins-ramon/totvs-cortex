import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine)

def init_db():
    """Cria as tabelas no banco de dados se elas não existirem."""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        
        conn.commit()
        
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, email VARCHAR(255) UNIQUE NOT NULL, password_hash VARCHAR(255) NOT NULL,
            name VARCHAR(255) NOT NULL, company VARCHAR(255) NOT NULL, phone VARCHAR(50),
            slack_user_id TEXT, manager_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            mini_bio TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, profile_photo BYTEA,
            name_normalized TEXT
        )"""))
        conn.commit()

        conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_users_name_normalized
        ON users (name_normalized);
        """))
        conn.commit()
        
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS feedbacks (
                id SERIAL PRIMARY KEY, employee_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                manager_id INTEGER REFERENCES users(id) ON DELETE CASCADE, description TEXT NOT NULL,
                feedback_date DATE NOT NULL, embedding vector(1536), created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )"""))
        conn.commit()
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS insights (
                id SERIAL PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                manager_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, insight_data TEXT NOT NULL,
                generated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, 
                source_feedback_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                UNIQUE(employee_id, manager_id)
            )"""))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS meetings (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                meeting_date DATE NOT NULL,
                transcription TEXT,
                summary TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS meeting_chunks (
                id SERIAL PRIMARY KEY,
                meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                chunk_text TEXT NOT NULL,
                embedding vector(1536),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS meeting_access (
                id SERIAL PRIMARY KEY,
                meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(meeting_id, user_id)
            )"""))
        conn.commit()

        try:
            conn.execute(text("ALTER TABLE meetings DROP COLUMN embedding"))
            conn.commit()
            print("Coluna 'embedding' removida da tabela 'meetings'.")
        except Exception as e:
            conn.rollback()
            print("Coluna 'embedding' não encontrada em 'meetings' ou já removida.")

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                title VARCHAR(255) NOT NULL,
                message TEXT,
                link VARCHAR(255),
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )"""))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agents (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                personality_prompt TEXT NOT NULL,
                type VARCHAR(50) UNIQUE NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""))
        conn.commit()

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agent_conversations (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                message_text TEXT NOT NULL,
                is_from_agent BOOLEAN DEFAULT FALSE,
                is_proactive_insight BOOLEAN DEFAULT FALSE,
                is_read BOOLEAN DEFAULT FALSE,
                related_action_data JSONB,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )"""))
        conn.commit()

        conn.execute(text("""
            INSERT INTO agents (name, personality_prompt, type)
            VALUES (
                'Momentum',
                'Você é Momentum, um mentor de carreira focado em alta performance. Você analisa feedbacks e reuniões passadas para encontrar padrões e oportunidades de crescimento. Você é proativo, encorajador e suas sugestões são sempre acionáveis. Responda em português brasileiro.',
                'career_mentor'
            )
            ON CONFLICT (type) DO NOTHING
        """))
        conn.commit()