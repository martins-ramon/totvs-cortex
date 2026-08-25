import os
import logging
import threading
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

log = logging.getLogger("cortex.db")

# Status da última tentativa de inicialização do banco (para /healthz)
# Valores: None (não executado), 'ok', 'unconfigured', 'error:<msg>'
INIT_DB_STATUS = None

_engine = None
_SessionLocal = None
_lock = threading.Lock()


def database_url():
    return os.environ.get("DATABASE_URL")


def get_engine():
    """Cria o engine sob demanda (lazy). Não derruba a aplicação na importação."""
    global _engine, _SessionLocal
    if _engine is None:
        with _lock:
            if _engine is None:
                url = database_url()
                if not url:
                    raise RuntimeError(
                        "DATABASE_URL não configurada. "
                        "Defina o Secret no Replit (Transaction Pooler do Supabase, porta 6543)."
                    )
                _engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
                _SessionLocal = sessionmaker(bind=_engine)
    return _engine


def session_factory():
    """Retorna uma nova Session. Uso padrão nos views:

        db = session_factory()
        try:
            ...
            db.commit()
        finally:
            db.close()
    """
    get_engine()
    return _SessionLocal()


@contextmanager
def db_session():
    """Context manager que faz commit/rollback/close automático."""
    db = session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# SCHEMA LEGADO (FeedbackAI original) — preservado para migração/histórico.
# Depende de pgvector em duas tabelas; falhas individuais são registradas e
# não interrompem a criação das demais tabelas.
# ---------------------------------------------------------------------------

_LEGACY_DDL = [
    ("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        name VARCHAR(255) NOT NULL,
        company VARCHAR(255) NOT NULL,
        phone VARCHAR(50),
        slack_user_id TEXT,
        manager_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        mini_bio TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        profile_photo BYTEA,
        name_normalized TEXT,
        google_id VARCHAR(255) UNIQUE
    )
    """, "users"),
    ("CREATE INDEX IF NOT EXISTS idx_users_name_normalized ON users (name_normalized)", "idx_users_name_normalized"),
    ("""
    CREATE TABLE IF NOT EXISTS feedbacks (
        id SERIAL PRIMARY KEY,
        employee_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        manager_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        description TEXT NOT NULL,
        transcription TEXT,
        feedback_for_employee TEXT,
        feedback_date DATE NOT NULL,
        embedding vector(1536),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    )
    """, "feedbacks"),
    ("""
    CREATE TABLE IF NOT EXISTS insights (
        id SERIAL PRIMARY KEY,
        employee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        manager_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        insight_data TEXT NOT NULL,
        generated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        source_feedback_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
        UNIQUE(employee_id, manager_id)
    )
    """, "insights"),
    ("""
    CREATE TABLE IF NOT EXISTS meetings (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        meeting_date DATE NOT NULL,
        transcription TEXT,
        summary TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """, "meetings"),
    ("""
    CREATE TABLE IF NOT EXISTS meeting_chunks (
        id SERIAL PRIMARY KEY,
        meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        chunk_text TEXT NOT NULL,
        embedding vector(1536),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """, "meeting_chunks"),
    ("""
    CREATE TABLE IF NOT EXISTS meeting_access (
        id SERIAL PRIMARY KEY,
        meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(meeting_id, user_id)
    )
    """, "meeting_access"),
    ("""
    CREATE TABLE IF NOT EXISTS notifications (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        type VARCHAR(50) DEFAULT 'SYSTEM',
        title VARCHAR(255) NOT NULL,
        message TEXT,
        link VARCHAR(255),
        is_read BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    )
    """, "notifications"),
    ("""
    CREATE TABLE IF NOT EXISTS agents (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) UNIQUE NOT NULL,
        role VARCHAR(100),
        description TEXT,
        avatar_style VARCHAR(50),
        last_active_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """, "agents"),
    ("""
    CREATE TABLE IF NOT EXISTS agent_insights (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        agent_name VARCHAR(100),
        title VARCHAR(255) NOT NULL,
        observation TEXT,
        solution_proposal TEXT,
        severity VARCHAR(20),
        category VARCHAR(50),
        action_payload TEXT,
        is_archived BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    )
    """, "agent_insights"),
]

# ---------------------------------------------------------------------------
# NOVO SCHEMA (Cortex — gestão 1:1 e performance). Sem dependência de pgvector.
# ---------------------------------------------------------------------------

_NEW_DDL = [
    ("""
    CREATE TABLE IF NOT EXISTS people (
        id SERIAL PRIMARY KEY,
        full_name VARCHAR(255) NOT NULL,
        preferred_name VARCHAR(255),
        email VARCHAR(255),
        role_title VARCHAR(255),
        photo BYTEA,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        hired_at DATE,
        notes TEXT,
        legacy_user_id INTEGER UNIQUE REFERENCES users(id),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    )
    """, "people"),
    ("""
    CREATE TABLE IF NOT EXISTS one_on_ones (
        id SERIAL PRIMARY KEY,
        person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        occurred_on DATE NOT NULL,
        title VARCHAR(255),
        source VARCHAR(30) NOT NULL DEFAULT 'manual',
        transcript_raw TEXT,
        summary_ai TEXT,
        private_notes TEXT,
        public_notes TEXT,
        sentiment VARCHAR(20),
        topics JSONB DEFAULT '[]'::jsonb,
        extraction_json JSONB,
        legacy_table VARCHAR(30),
        legacy_id INTEGER,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(legacy_table, legacy_id)
    )
    """, "one_on_ones"),
    ("CREATE INDEX IF NOT EXISTS idx_one_on_ones_person ON one_on_ones (person_id, occurred_on DESC)", "idx_one_on_ones_person"),
    ("""
    CREATE TABLE IF NOT EXISTS commitments (
        id SERIAL PRIMARY KEY,
        one_on_one_id INTEGER REFERENCES one_on_ones(id) ON DELETE SET NULL,
        person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        description TEXT NOT NULL,
        owner VARCHAR(10) NOT NULL DEFAULT 'person'
            CONSTRAINT chk_commitments_owner CHECK (owner IN ('manager', 'person')),
        due_date DATE,
        status VARCHAR(15) NOT NULL DEFAULT 'open'
            CONSTRAINT chk_commitments_status CHECK (status IN ('open', 'done', 'cancelled')),
        closed_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    )
    """, "commitments"),
    ("CREATE INDEX IF NOT EXISTS idx_commitments_person ON commitments (person_id, status)", "idx_commitments_person"),
    ("""
    CREATE TABLE IF NOT EXISTS checkpoints (
        id SERIAL PRIMARY KEY,
        person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        checkpoint_date DATE NOT NULL,
        period_start DATE,
        period_end DATE,
        actions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        private_notes TEXT,
        public_notes TEXT,
        source VARCHAR(30) NOT NULL DEFAULT 'feedz_paste',
        raw_input TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    )
    """, "checkpoints"),
    ("CREATE INDEX IF NOT EXISTS idx_checkpoints_person ON checkpoints (person_id, checkpoint_date DESC)", "idx_checkpoints_person"),
    ("""
    CREATE TABLE IF NOT EXISTS card_jobs (
        id SERIAL PRIMARY KEY,
        status VARCHAR(15) NOT NULL DEFAULT 'running'
            CONSTRAINT chk_card_jobs_status CHECK (status IN ('running', 'done', 'error')),
        total INTEGER NOT NULL DEFAULT 0,
        done INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP WITH TIME ZONE
    )
    """, "card_jobs"),
    ("""
    CREATE TABLE IF NOT EXISTS member_cards (
        id SERIAL PRIMARY KEY,
        person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        job_id INTEGER REFERENCES card_jobs(id) ON DELETE SET NULL,
        generated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        valid_until DATE,
        card_json JSONB NOT NULL
    )
    """, "member_cards"),
    ("CREATE INDEX IF NOT EXISTS idx_member_cards_person ON member_cards (person_id, generated_at DESC)", "idx_member_cards_person"),
]


def init_db():
    """Executa o DDL evolutivo. Nunca levanta exceção para cima: registra o
    status em INIT_DB_STATUS e permite que a aplicação suba mesmo se o banco
    estiver temporariamente indisponível (resiliência no Autoscale do Replit).
    """
    global INIT_DB_STATUS
    try:
        engine = get_engine()
    except Exception as e:
        INIT_DB_STATUS = f"unconfigured:{e}"
        log.warning(f"Banco indisponível no startup: {e}")
        return

    with engine.connect() as conn:
        # Extensão pgvector (necessária apenas para as tabelas legadas)
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.warning(f"Extensão pgvector indisponível (tabelas legadas com vetor serão ignoradas): {e}")

        for ddl, name in _LEGACY_DDL:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception as e:
                conn.rollback()
                log.warning(f"DDL legado '{name}' pulado: {e}")

        # Migração pontual legada: remover coluna embedding de meetings
        try:
            conn.execute(text("ALTER TABLE meetings DROP COLUMN embedding"))
            conn.commit()
            log.info("Coluna 'embedding' removida da tabela 'meetings'.")
        except Exception:
            conn.rollback()

        for ddl, name in _NEW_DDL:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception as e:
                conn.rollback()
                log.error(f"DDL novo '{name}' falhou: {e}")
                INIT_DB_STATUS = f"error:{name}:{e}"
                return

        # Evolução incremental do novo schema (tabelas já criadas por versões antigas)
        _EVOLUTION_DDL = [
            ("ALTER TABLE one_on_ones ADD COLUMN IF NOT EXISTS extraction_json JSONB", "one_on_ones.extraction_json"),
        ]
        for ddl, name in _EVOLUTION_DDL:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception as e:
                conn.rollback()
                log.warning(f"Evolução '{name}' pulada: {e}")

    INIT_DB_STATUS = "ok"
    log.info("Schema verificado/atualizado com sucesso.")
