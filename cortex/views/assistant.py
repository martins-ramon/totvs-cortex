"""Copiloto autenticado com ações explícitas, assinadas e idempotentes."""
import logging
import uuid
from datetime import date
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request, session
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text

from ..ai.assistant_service import generate_assistant_reply, validate_reply
from ..assistant_context import gather_context
from ..database import session_factory
from ..security import login_required

bp = Blueprint("assistant", __name__, url_prefix="/api/assistant")
log = logging.getLogger("cortex.assistant")
_ACTION_TTL = 15 * 60


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="cortex-assistant-action-v1")


def _body():
    if request.content_length and request.content_length > 80000:
        raise ValueError("Mensagem muito longa.")
    origin = request.headers.get("Origin")
    if origin:
        parsed = urlsplit(origin)
        if (parsed.scheme, parsed.netloc) != (request.scheme, request.host):
            raise ValueError("Origem da requisição inválida.")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Envie um objeto JSON válido.")
    return data


def _chat_input(data):
    message = data.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 6000:
        raise ValueError("Escreva uma mensagem de até 6.000 caracteres.")
    history = data.get("history", [])
    if not isinstance(history, list) or len(history) > 20:
        raise ValueError("Histórico inválido. Inicie uma nova conversa.")
    clean = []
    for turn in history:
        if (not isinstance(turn, dict) or not isinstance(turn.get("role"), str)
                or turn["role"] not in {"user", "assistant"}
                or not isinstance(turn.get("content"), str) or len(turn["content"]) > 16000):
            raise ValueError("Histórico de conversa inválido.")
        clean.append({"role": turn["role"], "content": turn["content"]})
    if sum(len(t["content"]) for t in clean) > 48000:
        raise ValueError("A conversa ficou longa. Inicie uma nova conversa.")
    return message.strip(), clean


def _positive_id(value):
    return type(value) is int and value > 0


def _sign_action(proposal, user_id, context):
    """Só assina operações pequenas sobre alvos presentes no contexto atual."""
    kind = proposal.get("type")
    payload = {"type": kind, "user_id": user_id, "nonce": uuid.uuid4().hex}
    if kind == "create_commitment":
        pid = proposal.get("person_id")
        person = next((p for p in context.get("people", []) if p["id"] == pid), None)
        description = proposal.get("description")
        owner, due = proposal.get("owner"), proposal.get("due_date")
        if (not _positive_id(pid) or not person or not isinstance(description, str)
                or not 1 <= len(description.strip()) <= 2000 or not isinstance(owner, str)
                or owner not in {"manager", "person"}):
            raise ValueError("Dados do combinado inválidos.")
        if pid in context.get("coverage", {}).get("ambiguous_person_ids", []):
            raise ValueError("Informe o nome completo da pessoa antes de criar o combinado.")
        if due is not None:
            if not isinstance(due, str) or date.fromisoformat(due).isoformat() != due:
                raise ValueError("Prazo inválido.")
        payload.update(person_id=pid, description=description.strip(), owner=owner, due_date=due)
        label = "Criar combinado"
        details = (f"{person['full_name']}: {description.strip()}\n"
                   f"Responsável: {'diretor' if owner == 'manager' else person['full_name']}. "
                   f"Prazo: {due or 'sem prazo definido'}.")
    elif kind == "complete_commitment":
        cid = proposal.get("commitment_id")
        if not _positive_id(cid) or not any(s["id"] == f"commitment:{cid}" for s in context.get("sources", [])):
            raise ValueError("Combinado ausente das fontes consultadas.")
        db = session_factory()
        try:
            row = db.execute(text("SELECT person_id, description, status FROM commitments WHERE id = :cid"),
                             {"cid": cid}).fetchone()
        finally:
            db.close()
        if not row or row[2] != "open":
            raise ValueError("O combinado já não está em aberto.")
        payload.update(commitment_id=cid, person_id=row[0], expected_description=row[1])
        label, details = "Concluir combinado", row[1]
    else:
        raise ValueError("Ação não disponível.")
    return {"token": _serializer().dumps(payload), "label": label, "description": details}


@bp.route("/chat", methods=["POST"])
@login_required
def chat():
    try:
        message, history = _chat_input(_body())
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    try:
        context = gather_context(session["user_id"], message, history)
    except Exception as exc:
        log.warning("Contexto do assistente indisponível (%s).", type(exc).__name__)
        return jsonify(error="Não foi possível consultar os dados do Cortex. Tente novamente."), 503
    try:
        raw = generate_assistant_reply(message, history, context)
        reply = validate_reply(raw, context)
    except RuntimeError as exc:
        log.warning("Assistente não configurado (%s).", type(exc).__name__)
        return jsonify(error="Assistente indisponível. Verifique a configuração da IA."), 503
    except Exception as exc:
        log.warning("Falha na resposta do assistente (%s).", type(exc).__name__)
        return jsonify(error="Não foi possível gerar uma resposta agora. Tente novamente."), 502
    actions, warnings = [], list(context.get("warnings", []))
    for proposal in reply.pop("action_proposals"):
        try:
            actions.append(_sign_action(proposal, session["user_id"], context))
        except ValueError:
            warnings.append("Uma ação não pôde ser preparada. Especifique a pessoa e o combinado no pedido.")
        except Exception as exc:
            log.warning("Proposta de ação indisponível (%s).", type(exc).__name__)
            warnings.append("Não foi possível preparar a alteração. Os dados continuam sem alterações.")
    reply.update(actions=actions, coverage=context.get("coverage", {}), warnings=warnings)
    return jsonify(reply)


@bp.route("/actions", methods=["POST"])
@login_required
def execute_action():
    try:
        data = _body()
        token = data.get("token")
        if not isinstance(token, str) or not 1 <= len(token) <= 16000:
            raise ValueError("Ação inválida.")
        payload = _serializer().loads(token, max_age=_ACTION_TTL)
        if (not isinstance(payload, dict) or payload.get("user_id") != session["user_id"]
                or payload.get("type") not in {"create_commitment", "complete_commitment"}):
            raise ValueError("Ação inválida para esta sessão.")
    except SignatureExpired:
        return jsonify(error="Esta ação expirou. Faça o pedido novamente."), 400
    except (BadSignature, ValueError):
        return jsonify(error="Ação inválida ou expirada. Faça o pedido novamente."), 400

    db = None
    try:
        db = session_factory()
        if payload["type"] == "create_commitment":
            person = db.execute(text("SELECT id FROM people WHERE id = :pid"),
                                {"pid": payload["person_id"]}).fetchone()
            if not person:
                return jsonify(error="A pessoa já não está cadastrada."), 404
            params = {"pid": payload["person_id"], "description": payload["description"],
                      "owner": payload["owner"], "due": date.fromisoformat(payload["due_date"]) if payload["due_date"] else None,
                      "request_id": payload["nonce"]}
            row = db.execute(text("""
                INSERT INTO commitments (person_id, description, owner, due_date, assistant_request_id)
                VALUES (:pid, :description, :owner, :due, :request_id)
                ON CONFLICT (assistant_request_id) DO NOTHING RETURNING id
            """), params).fetchone()
            if not row:
                row = db.execute(text("SELECT id FROM commitments WHERE assistant_request_id = :request_id"),
                                 {"request_id": payload["nonce"]}).fetchone()
            result = {"message": "Combinado criado.", "commitment_id": row[0], "person_id": payload["person_id"]}
        else:
            params = {"cid": payload["commitment_id"]}
            row = db.execute(text("""
                SELECT person_id, description, status FROM commitments WHERE id = :cid FOR UPDATE
            """), params).fetchone()
            if not row:
                return jsonify(error="O combinado já não existe."), 404
            if row[0] != payload["person_id"] or row[1] != payload["expected_description"] or row[2] == "cancelled":
                return jsonify(error="O combinado mudou desde a proposta. Faça o pedido novamente."), 409
            if row[2] != "done":
                db.execute(text("""
                    UPDATE commitments SET status = 'done', closed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP WHERE id = :cid
                """), params)
            result = {"message": "Combinado concluído.", "commitment_id": payload["commitment_id"], "person_id": row[0]}
        db.commit()
        return jsonify(result)
    except Exception as exc:
        if db is not None:
            db.rollback()
        log.warning("Falha ao executar ação do assistente (%s).", type(exc).__name__)
        return jsonify(error="Não foi possível salvar o combinado. Tente novamente."), 503
    finally:
        if db is not None:
            db.close()
