# agents_service.py
import uuid
from typing import List, Optional, Dict, Any
from flask import current_app
from agents_models import db, Agent, AgentSuggestion, AgentAction
from agent_loader import load_agent_config

def _uuid() -> str:
    return str(uuid.uuid4())

# --- Agents CRUD ---
def ensure_default_agent() -> Agent:
    """Cria o agente 'Aurélio' caso ainda não exista."""
    agent = Agent.query.filter_by(slug="aurelio").first()
    if agent:
        return agent
    agent = Agent(
        id=_uuid(),
        name="Aurélio",
        slug="aurelio",
        mode="hitl",
        preferences={"digest_hour": "08:30", "risk_sensitivity": "medium"}
    )
    db.session.add(agent)
    db.session.commit()
    return agent

def list_agents() -> List[Agent]:
    ensure_default_agent()
    return Agent.query.order_by(Agent.created_at.asc()).all()

# --- Suggestions ---
def list_suggestions(agent_slug: str, status: Optional[str] = "pending") -> List[AgentSuggestion]:
    q = AgentSuggestion.query.filter_by(agent_slug=agent_slug)
    if status:
        q = q.filter_by(status=status)
    return q.order_by(AgentSuggestion.created_at.desc()).all()

def add_suggestion(agent_slug: str, user_id: str, title: str, summary: str, details: Dict[str, Any]) -> AgentSuggestion:
    sug = AgentSuggestion(
        id=_uuid(),
        agent_slug=agent_slug,
        user_id=user_id,
        title=title,
        summary=summary,
        details=details,
        status="pending"
    )
    db.session.add(sug)
    db.session.commit()
    return sug

def update_suggestion_status(suggestion_id: str, status: str) -> Optional[AgentSuggestion]:
    sug = AgentSuggestion.query.get(suggestion_id)
    if not sug:
        return None
    sug.status = status
    db.session.commit()
    return sug

# --- Actions ---
def register_action(agent_slug: str, action: str, payload: Dict[str, Any], approved_by: str) -> AgentAction:
    act = AgentAction(
        id=_uuid(),
        agent_slug=agent_slug,
        action=action,
        payload=payload,
        approved_by=approved_by
    )
    db.session.add(act)
    db.session.commit()
    return act

agent_cfg = load_agent_config("aurelio_agent.yaml")
print("Agente carregado:", agent_cfg["agent"]["name"])