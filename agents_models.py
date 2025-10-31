# agents_models.py
from datetime import datetime
import os
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.dialects.postgresql import JSONB
from database import db

def json_type():
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgres://") or url.startswith("postgresql://"):
        return JSONB
    return SQLITE_JSON

class Agent(db.Model):
    __tablename__ = "agents"
    id = db.Column(db.String, primary_key=True)
    name = db.Column(db.String, nullable=False)
    slug = db.Column(db.String, unique=True, nullable=False)
    mode = db.Column(db.String, nullable=False, default="hitl")
    preferences = db.Column(json_type())
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AgentSuggestion(db.Model):
    __tablename__ = "agent_suggestions"
    id = db.Column(db.String, primary_key=True)
    agent_slug = db.Column(db.String, nullable=False, index=True)
    user_id = db.Column(db.String, nullable=False, index=True)
    title = db.Column(db.String)
    summary = db.Column(db.String)
    details = db.Column(json_type())
    status = db.Column(db.String, nullable=False, default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

class AgentAction(db.Model):
    __tablename__ = "agent_actions"
    id = db.Column(db.String, primary_key=True)
    agent_slug = db.Column(db.String, nullable=False, index=True)
    action = db.Column(db.String, nullable=False)
    payload = db.Column(json_type())
    approved_by = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
