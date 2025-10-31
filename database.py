import os
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.dialects.postgresql import JSONB

db = SQLAlchemy()

# -----------------------------
# Tipos auxiliares dinâmicos
# -----------------------------
def json_type():
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgres://") or url.startswith("postgresql://"):
        return JSONB
    return SQLITE_JSON

def is_postgres() -> bool:
    url = os.getenv("DATABASE_URL", "")
    return url.startswith("postgres://") or url.startswith("postgresql://")

# Tipo PGVector simples (apenas Postgres). Em outros DBs, usamos JSON.
from sqlalchemy.types import UserDefinedType

class PGVector(UserDefinedType):
    def __init__(self, dims: int = 1536):
        self.dims = dims
    def get_col_spec(self, **kw):
        return f"vector({self.dims})"

def vector_type(dims: int = 1536):
    return PGVector(dims) if is_postgres() else json_type()

# -----------------------------
# Modelos ORM (mesma estrutura do SQL manual anterior)
# -----------------------------

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    company = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(50))
    slack_user_id = db.Column(db.Text)
    manager_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete="SET NULL"))
    mini_bio = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    profile_photo = db.Column(db.LargeBinary)

    manager = relationship('User', remote_side=[id], uselist=False)

class Feedback(db.Model):
    __tablename__ = "feedbacks"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete="CASCADE"))
    manager_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete="CASCADE"))
    description = db.Column(db.Text, nullable=False)
    feedback_date = db.Column(db.Date, nullable=False)
    embedding = db.Column(vector_type(1536))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Insight(db.Model):
    __tablename__ = "insights"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete="CASCADE"), nullable=False)
    manager_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete="CASCADE"), nullable=False)
    insight_data = db.Column(db.Text, nullable=False)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    source_feedback_timestamp = db.Column(db.DateTime, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('employee_id', 'manager_id', name='uq_insights_employee_manager'),
    )

class Meeting(db.Model):
    __tablename__ = "meetings"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete="CASCADE"), nullable=False)
    meeting_date = db.Column(db.Date, nullable=False)
    transcription = db.Column(db.Text)
    summary = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class MeetingChunk(db.Model):
    __tablename__ = "meeting_chunks"
    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey('meetings.id', ondelete="CASCADE"), nullable=False)
    chunk_text = db.Column(db.Text, nullable=False)
    embedding = db.Column(vector_type(1536))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class MeetingAccess(db.Model):
    __tablename__ = "meeting_access"
    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey('meetings.id', ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete="CASCADE"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('meeting_id', 'user_id', name='uq_meeting_access_meeting_user'),
    )

class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete="CASCADE"), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete="SET NULL"))
    title = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text)
    link = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -----------------------------
# (já existiam) Tabelas de agentes — usamos os modelos dos módulos de agentes
# Observação: para evitar import circular, deixamos a criação dessas tabelas
# para when importados em init_db_flask.
# -----------------------------
# from agents_models import Agent, AgentSuggestion, AgentAction

# -----------------------------
# Init para Flask + criação de TUDO
# -----------------------------
def init_db_flask(app):
    """Inicializa ORM e cria todas as tabelas do Cortex.
    - Se Postgres, ativa extensão pgvector.
    - Cria as tabelas principais e, em seguida, as dos agentes (se módulo disponível).
    """
    db_url = os.getenv("DATABASE_URL", "sqlite:///cortex.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        # Se Postgres, garantir extensão pgvector
        if is_postgres():
            try:
                db.session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                db.session.commit()
                print("[database] Extensão pgvector OK.")
            except Exception as e:
                db.session.rollback()
                print(f"[database] Aviso: não foi possível criar extensão pgvector: {e}")

        # 1) Cria tabelas principais
        db.create_all()
        print("[database] Tabelas principais criadas/verificadas com sucesso.")

        # 2) Cria tabelas dos agentes (se os modelos estiverem disponíveis)
        try:
            from agents_models import Agent, AgentSuggestion, AgentAction  # noqa: F401
            db.create_all()
            print("[database] Tabelas de agentes criadas/verificadas com sucesso.")
        except Exception as e:
            print(f"[database] Aviso: não foi possível importar/criar tabelas de agentes: {e}")
