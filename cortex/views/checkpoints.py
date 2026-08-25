"""Checkpoints no formato Feedz: ações + responsável, notas privadas e públicas."""
import json
from datetime import date

from flask import Blueprint, request, jsonify
from sqlalchemy import text

from ..database import session_factory
from .. import ai
from ..security import login_required

bp = Blueprint("checkpoints", __name__, url_prefix="/api")

_CP_SELECT = """
    SELECT id, person_id, checkpoint_date, period_start, period_end,
           actions_json, private_notes, public_notes, source, raw_input, created_at
    FROM checkpoints
"""


def _serialize_checkpoint(row):
    return {
        "id": row[0],
        "person_id": row[1],
        "checkpoint_date": row[2].isoformat(),
        "period_start": row[3].isoformat() if row[3] else None,
        "period_end": row[4].isoformat() if row[4] else None,
        "actions": row[5] if isinstance(row[5], list) else [],
        "private_notes": row[6],
        "public_notes": row[7],
        "source": row[8],
        "has_raw_input": bool(row[9]),
        "created_at": row[10].isoformat() if row[10] else None,
    }


def _parse_date(value, field):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"Data inválida em '{field}' (use YYYY-MM-DD).")


def _normalize_actions(raw):
    """Normaliza a lista de ações: [{acao, responsavel}]."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        acao = str(item.get('acao') or item.get('action') or '').strip()
        if not acao:
            continue
        resp = str(item.get('responsavel') or item.get('owner') or '').strip()
        low = resp.lower()
        if 'gest' in low or low in ('eu', 'você', 'voce'):
            resp = 'gestor'
        elif 'lider' in low or 'colaborador' in low or 'liderado' in low:
            resp = 'liderado'
        out.append({"acao": acao, "responsavel": resp or 'gestor'})
    return out


@bp.route('/people/<int:person_id>/checkpoints', methods=['GET'])
@login_required
def list_checkpoints(person_id):
    db = session_factory()
    try:
        rows = db.execute(
            text(_CP_SELECT + " WHERE person_id = :pid ORDER BY checkpoint_date DESC, id DESC"),
            {"pid": person_id}).fetchall()
        return jsonify({"checkpoints": [_serialize_checkpoint(r) for r in rows]})
    finally:
        db.close()


@bp.route('/checkpoints/parse', methods=['POST'])
@login_required
def parse_checkpoint_preview():
    """Estrutura um texto do Feedz com IA SEM salvar — retorna preview editável."""
    data = request.json or {}
    raw_text = (data.get('raw_text') or '').strip()
    if not raw_text:
        return jsonify({"error": "Cole o texto do Feedz para estruturar."}), 400

    person_name = data.get('person_name')
    if not person_name:
        pid = data.get('person_id')
        if pid:
            db = session_factory()
            try:
                row = db.execute(text("SELECT full_name FROM people WHERE id = :pid"),
                                 {"pid": pid}).fetchone()
                person_name = row[0] if row else None
            finally:
                db.close()
    if not person_name:
        person_name = "o colaborador"

    parsed = ai.parse_checkpoint(raw_text, person_name)
    result = {
        "actions": _normalize_actions(parsed.get('acoes')),
        "private_notes": str(parsed.get('notas_privadas') or '').strip(),
        "public_notes": str(parsed.get('notas_publicas') or '').strip(),
    }
    return jsonify({"parsed": result})


@bp.route('/checkpoints', methods=['POST'])
@login_required
def create_checkpoint():
    data = request.json or {}
    person_id = data.get('person_id')
    if not person_id:
        return jsonify({"error": "person_id é obrigatório"}), 400

    try:
        checkpoint_date = _parse_date(data.get('checkpoint_date'), 'checkpoint_date')
        period_start = _parse_date(data.get('period_start'), 'period_start') if data.get('period_start') else None
        period_end = _parse_date(data.get('period_end'), 'period_end') if data.get('period_end') else None
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    actions = _normalize_actions(data.get('actions'))
    private_notes = (data.get('private_notes') or '').strip() or None
    public_notes = (data.get('public_notes') or '').strip() or None
    if not actions and not private_notes and not public_notes:
        return jsonify({"error": "Preencha ao menos uma ação ou anotação."}), 400

    db = session_factory()
    try:
        exists = db.execute(text("SELECT 1 FROM people WHERE id = :pid"),
                            {"pid": person_id}).fetchone()
        if not exists:
            return jsonify({"error": "Pessoa não encontrada"}), 404

        result = db.execute(text("""
            INSERT INTO checkpoints
                (person_id, checkpoint_date, period_start, period_end,
                 actions_json, private_notes, public_notes, source, raw_input)
            VALUES (:pid, :cdate, :pstart, :pend,
                    CAST(:actions AS jsonb), :priv, :pub, :source, :raw)
            RETURNING id
        """), {
            "pid": person_id,
            "cdate": checkpoint_date,
            "pstart": period_start,
            "pend": period_end,
            "actions": json.dumps(actions),
            "priv": private_notes,
            "pub": public_notes,
            "source": data.get('source') or 'feedz_paste',
            "raw": data.get('raw_input') or None,
        })
        db.commit()
        cp_id = result.fetchone()[0]
        return jsonify({"success": True, "checkpoint_id": cp_id}), 201
    finally:
        db.close()


@bp.route('/checkpoints/<int:cp_id>', methods=['PUT'])
@login_required
def update_checkpoint(cp_id):
    data = request.json or {}

    try:
        updates, params = [], {"cid": cp_id}

        if 'checkpoint_date' in data:
            updates.append("checkpoint_date = :cdate")
            params["cdate"] = _parse_date(data['checkpoint_date'], 'checkpoint_date')
        for key in ('private_notes', 'public_notes'):
            if key in data:
                updates.append(f"{key} = :{key}")
                params[key] = (data[key] or '').strip() or None
        if 'actions' in data:
            updates.append("actions_json = CAST(:actions AS jsonb)")
            params["actions"] = json.dumps(_normalize_actions(data['actions']))
        if 'period_start' in data:
            updates.append("period_start = :ps")
            params["ps"] = _parse_date(data['period_start'], 'period_start') if data['period_start'] else None
        if 'period_end' in data:
            updates.append("period_end = :pe")
            params["pe"] = _parse_date(data['period_end'], 'period_end') if data['period_end'] else None

        if not updates:
            return jsonify({"error": "Nenhum campo para atualizar"}), 400
        updates.append("updated_at = CURRENT_TIMESTAMP")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    db = session_factory()
    try:
        result = db.execute(
            text(f"UPDATE checkpoints SET {', '.join(updates)} WHERE id = :cid"), params)
        db.commit()
        if result.rowcount == 0:
            return jsonify({"error": "Checkpoint não encontrado"}), 404
        return jsonify({"success": True})
    except ValueError as e:
        db.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@bp.route('/checkpoints/<int:cp_id>', methods=['DELETE'])
@login_required
def delete_checkpoint(cp_id):
    db = session_factory()
    try:
        result = db.execute(text("DELETE FROM checkpoints WHERE id = :cid"), {"cid": cp_id})
        db.commit()
        if result.rowcount == 0:
            return jsonify({"error": "Checkpoint não encontrado"}), 404
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()
