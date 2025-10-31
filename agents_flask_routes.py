# agents_flask_routes.py
from flask import Blueprint, request, jsonify
from agents_service import (
    list_suggestions, update_suggestion_status,
    add_suggestion, list_agents, register_action, ensure_default_agent
)

bp_agents = Blueprint("bp_agents", __name__, url_prefix="/api/agents")

@bp_agents.get("/suggestions")
def get_suggestions():
    agent = request.args.get("agent", "aurelio")
    status = request.args.get("status", "pending")
    sugs = list_suggestions(agent, status)
    data = [{
        "id": s.id,
        "agent_slug": s.agent_slug,
        "user_id": s.user_id,
        "title": s.title,
        "summary": s.summary,
        "details": s.details,
        "status": s.status,
        "created_at": s.created_at.isoformat()
    } for s in sugs]
    return jsonify(data), 200

@bp_agents.patch("/suggestions/<suggestion_id>")
def patch_suggestion(suggestion_id):
    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if status not in {"pending", "approved", "rejected", "applied"}:
        return jsonify({"error": "invalid status"}), 400
    s = update_suggestion_status(suggestion_id, status)
    if not s:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True, "id": s.id, "status": s.status}), 200

@bp_agents.post("/actions")
def post_action():
    body = request.get_json(silent=True) or {}
    agent = body.get("agent", "aurelio")
    action = body.get("action")
    payload = body.get("payload", {})
    approved_by = request.headers.get("X-User-Id", "manager-demo")
    if not action:
        return jsonify({"error": "action is required"}), 400
    act = register_action(agent, action, payload, approved_by)
    # (aqui futuramente você aciona a lógica específica do Cortex)
    return jsonify({"ok": True, "action_id": act.id})

@bp_agents.post("/seed-suggestion")
def seed_suggestion():
    ensure_default_agent()
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id", "manager-demo")
    s = add_suggestion(
        "aurelio", user_id,
        body.get("title", "Follow-up recomendado"),
        body.get("summary", "Agendar 1:1 com Maria e revisar prioridades do Projeto X"),
        body.get("details", {"actions":[{"label":"Agendar 1:1","actionId":"schedule_11","payload":{"target_user_id":"u_maria"}}]})
    )
    return jsonify({"ok": True, "id": s.id}), 201
