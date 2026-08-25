"""Núcleo 1:1 (novo modelo): sessões, extração IA e preparação de reuniões."""
import json
from datetime import date

from flask import Blueprint, request, jsonify
from sqlalchemy import text

from ..database import session_factory
from .. import ai
from ..security import login_required

bp = Blueprint("sessions", __name__, url_prefix="/api")

_VALID_SENTIMENTS = {"positivo", "neutro", "preocupante"}
_VALID_OWNERS = {"manager", "person"}


def _parse_date(value, field):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"Data inválida em '{field}' (use YYYY-MM-DD).")


def _as_str_list(value, limit=20):
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def _serialize_session(row, include_body=False):
    """row: id, person_id, occurred_on, title, source, transcript_raw,
    summary_ai, private_notes, public_notes, sentiment, topics, extraction_json"""
    data = {
        "id": row[0],
        "person_id": row[1],
        "occurred_on": row[2].isoformat(),
        "title": row[3],
        "source": row[4],
        "has_transcript": bool(row[5]),
        "sentiment": row[9],
        "topics": row[10] if isinstance(row[10], list) else [],
        "summary_ai": row[6],
        "extraction": row[11] if isinstance(row[11], dict) else None,
    }
    if include_body:
        data.update({
            "transcript_raw": row[5],
            "private_notes": row[7],
            "public_notes": row[8],
        })
    return data


_SESSION_SELECT = """
    SELECT id, person_id, occurred_on, title, source, transcript_raw,
           summary_ai, private_notes, public_notes, sentiment, topics, extraction_json
    FROM one_on_ones
"""


# --- CRUD de sessões 1:1 ---

@bp.route('/oneonones', methods=['GET'])
@login_required
def list_sessions():
    person_id = request.args.get('person_id', type=int)
    limit = request.args.get('limit', type=int) or 50

    sql = _SESSION_SELECT
    params = {"limit": min(limit, 200)}
    if person_id:
        sql += " WHERE person_id = :pid"
        params["pid"] = person_id
    sql += " ORDER BY occurred_on DESC, id DESC LIMIT :limit"

    db = session_factory()
    try:
        rows = db.execute(text(sql), params).fetchall()
        return jsonify({"sessions": [_serialize_session(r) for r in rows]})
    finally:
        db.close()


@bp.route('/oneonones', methods=['POST'])
@login_required
def create_session():
    data = request.json or {}
    person_id = data.get('person_id')
    if not person_id:
        return jsonify({"error": "person_id é obrigatório"}), 400

    try:
        occurred_on = _parse_date(data.get('occurred_on'), 'occurred_on')
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    db = session_factory()
    try:
        person = db.execute(
            text("SELECT id FROM people WHERE id = :pid"), {"pid": person_id}
        ).fetchone()
        if not person:
            return jsonify({"error": "Pessoa não encontrada"}), 404

        result = db.execute(text("""
            INSERT INTO one_on_ones
                (person_id, occurred_on, title, source, transcript_raw,
                 private_notes, public_notes)
            VALUES (:pid, :when, :title, 'meet_paste', :transcript,
                    :pnotes, :pubnotes)
            RETURNING id
        """), {
            "pid": person_id,
            "when": occurred_on,
            "title": (data.get('title') or '').strip() or None,
            "transcript": data.get('transcript_raw') or None,
            "pnotes": data.get('private_notes') or None,
            "pubnotes": data.get('public_notes') or None,
        })
        db.commit()
        session_id = result.fetchone()[0]
        return jsonify({"success": True, "session_id": session_id}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        db.close()


@bp.route('/oneonones/<int:session_id>', methods=['GET'])
@login_required
def get_session(session_id):
    db = session_factory()
    try:
        row = db.execute(
            text(_SESSION_SELECT + " WHERE id = :sid"), {"sid": session_id}
        ).fetchone()
        if not row:
            return jsonify({"error": "Sessão não encontrada"}), 404
        return jsonify({"session": _serialize_session(row, include_body=True)})
    finally:
        db.close()


@bp.route('/oneonones/<int:session_id>', methods=['PUT'])
@login_required
def update_session(session_id):
    data = request.json or {}

    try:
        updates, params = [], {"sid": session_id}

        if 'title' in data:
            updates.append("title = :title")
            params["title"] = (data['title'] or '').strip() or None
        if 'occurred_on' in data:
            updates.append("occurred_on = :when")
            params["when"] = _parse_date(data['occurred_on'], 'occurred_on')
        if 'transcript_raw' in data:
            updates.append("transcript_raw = :trans")
            params["trans"] = data['transcript_raw'] or None
        if 'private_notes' in data:
            updates.append("private_notes = :pn")
            params["pn"] = data['private_notes'] or None
        if 'public_notes' in data:
            updates.append("public_notes = :pubn")
            params["pubn"] = data['public_notes'] or None
        if 'summary_ai' in data:
            updates.append("summary_ai = :sum")
            params["sum"] = data['summary_ai'] or None

        if not updates:
            return jsonify({"error": "Nenhum campo para atualizar"}), 400
        updates.append("updated_at = CURRENT_TIMESTAMP")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    db = session_factory()
    try:
        result = db.execute(
            text(f"UPDATE one_on_ones SET {', '.join(updates)} WHERE id = :sid"),
            params)
        db.commit()
        if result.rowcount == 0:
            return jsonify({"error": "Sessão não encontrada"}), 404
        return jsonify({"success": True})
    except ValueError as e:
        db.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@bp.route('/oneonones/<int:session_id>', methods=['DELETE'])
@login_required
def delete_session(session_id):
    db = session_factory()
    try:
        # Combinados vinculados ficam órfãos com one_on_one_id NULL (FK SET NULL)
        result = db.execute(
            text("DELETE FROM one_on_ones WHERE id = :sid"), {"sid": session_id})
        db.commit()
        if result.rowcount == 0:
            return jsonify({"error": "Sessão não encontrada"}), 404
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


# --- Extração IA ---

def _normalize_extraction(raw):
    """Defende o payload da IA contra formatos inesperados."""
    if not isinstance(raw, dict):
        raise ValueError("Resposta da IA não é um objeto válido.")
    resumo = str(raw.get('resumo') or '').strip()
    sentimento = str(raw.get('sentimento') or 'neutro').strip().lower()
    if sentimento not in _VALID_SENTIMENTS:
        sentimento = 'neutro'
    combinados = []
    for c in raw.get('combinados') or []:
        if not isinstance(c, dict):
            continue
        descricao = str(c.get('descricao') or '').strip()
        if not descricao:
            continue
        owner = str(c.get('responsavel') or 'liderado').strip().lower()
        owner = 'manager' if owner.startswith('gest') else 'person'
        prazo = c.get('prazo')
        if prazo:
            try:
                prazo = date.fromisoformat(str(prazo)[:10]).isoformat()
            except ValueError:
                prazo = None
        combinados.append({
            "descricao": descricao,
            "responsavel": owner,
            "prazo": prazo,
        })
    return {
        "resumo": resumo,
        "sentimento": sentimento,
        "topicos": _as_str_list(raw.get('topicos'), 8),
        "combinados": combinados,
        "pontos_atencao": _as_str_list(raw.get('pontos_atencao')),
        "pontos_desenvolvimento": _as_str_list(raw.get('pontos_desenvolvimento')),
        "conquistas": _as_str_list(raw.get('conquistas')),
    }


@bp.route('/oneonones/<int:session_id>/extract', methods=['POST'])
@login_required
def extract_session(session_id):
    db = session_factory()
    try:
        row = db.execute(text(_SESSION_SELECT + " WHERE id = :sid"),
                         {"sid": session_id}).fetchone()
        if not row:
            return jsonify({"error": "Sessão não encontrada"}), 404
        if not row[5]:
            return jsonify({"error": "Sessão sem transcrição para extrair."}), 400

        person = db.execute(
            text("SELECT full_name FROM people WHERE id = :pid"), {"pid": row[1]}
        ).fetchone()
        person_name = person[0] if person else "Liderado"

        raw = ai.extract_meeting_insights(person_name, row[2].isoformat(), row[5])
        ext = _normalize_extraction(raw)

        db.execute(text("""
            UPDATE one_on_ones
            SET summary_ai = :resumo, sentiment = :sent, topics = CAST(:topics AS jsonb),
                extraction_json = CAST(:ext AS jsonb), updated_at = CURRENT_TIMESTAMP
            WHERE id = :sid
        """), {
            "resumo": ext["resumo"] or None,
            "sent": ext["sentimento"],
            "topics": json.dumps(ext["topicos"]),
            "ext": json.dumps(ext),
            "sid": session_id,
        })

        # Sincroniza combinados: substitui os ainda abertos desta sessão;
        # preserva os já concluídos/cancelados pelo usuário.
        db.execute(text("""
            DELETE FROM commitments
            WHERE one_on_one_id = :sid AND status = 'open'
        """), {"sid": session_id})
        for c in ext["combinados"]:
            db.execute(text("""
                INSERT INTO commitments (one_on_one_id, person_id, description, owner, due_date)
                VALUES (:sid, :pid, :descr, :owner, :due)
            """), {
                "sid": session_id,
                "pid": row[1],
                "descr": c["descricao"],
                "owner": c["responsavel"],
                "due": c["prazo"],
            })
        db.commit()

        updated = db.execute(text(_SESSION_SELECT + " WHERE id = :sid"),
                             {"sid": session_id}).fetchone()
        return jsonify({"success": True,
                        "session": _serialize_session(updated, include_body=True)})
    except Exception as e:
        db.rollback()
        print(f"Erro na extração IA da sessão {session_id}: {e}")
        return jsonify({"error": f"Falha ao extrair insights: {e}"}), 500
    finally:
        db.close()


# --- Combinados ---

@bp.route('/people/<int:person_id>/commitments', methods=['GET'])
@login_required
def list_person_commitments(person_id):
    db = session_factory()
    try:
        rows = db.execute(text("""
            SELECT c.id, c.one_on_one_id, c.description, c.owner, c.due_date,
                   c.status, c.closed_at
            FROM commitments c
            WHERE c.person_id = :pid
            ORDER BY CASE c.status WHEN 'open' THEN 0 ELSE 1 END,
                     c.due_date NULLS LAST, c.id DESC
        """), {"pid": person_id}).fetchall()
        return jsonify({"commitments": [{
            "id": r[0], "one_on_one_id": r[1], "description": r[2],
            "owner": r[3],
            "due_date": r[4].isoformat() if r[4] else None,
            "status": r[5], "is_overdue": bool(r[4] and r[4] < date.today()),
            "closed_at": r[6].isoformat() if r[6] else None,
        } for r in rows]})
    finally:
        db.close()


@bp.route('/commitments/<int:commitment_id>', methods=['PUT'])
@login_required
def update_commitment(commitment_id):
    data = request.json or {}

    try:
        updates, params = [], {"cid": commitment_id}

        if 'status' in data:
            if data['status'] not in ('open', 'done', 'cancelled'):
                return jsonify({"error": "Status inválido"}), 400
            updates.append("status = :status")
            params["status"] = data['status']
            updates.append(
                "closed_at = CASE WHEN :status = 'open' THEN NULL ELSE CURRENT_TIMESTAMP END")
        if 'description' in data:
            descr = (data['description'] or '').strip()
            if not descr:
                return jsonify({"error": "Descrição não pode ser vazia"}), 400
            updates.append("description = :descr")
            params["descr"] = descr
        if 'owner' in data:
            if data['owner'] not in _VALID_OWNERS:
                return jsonify({"error": "Responsável inválido"}), 400
            updates.append("owner = :owner")
            params["owner"] = data['owner']
        if 'due_date' in data:
            updates.append("due_date = :due")
            params["due"] = _parse_date(data['due_date'], 'due_date') if data['due_date'] else None

        if not updates:
            return jsonify({"error": "Nenhum campo para atualizar"}), 400
        updates.append("updated_at = CURRENT_TIMESTAMP")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    db = session_factory()
    try:
        result = db.execute(
            text(f"UPDATE commitments SET {', '.join(updates)} WHERE id = :cid"),
            params)
        db.commit()
        if result.rowcount == 0:
            return jsonify({"error": "Combinado não encontrado"}), 404
        return jsonify({"success": True})
    except ValueError as e:
        db.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


# --- Preparação do próximo 1:1 ---

_SENTIMENT_SCORE = {"positivo": 1, "neutro": 0, "preocupante": -1}


def _collect_prep_data(db, person_row):
    person_id = person_row[0]

    commitments = []
    for r in db.execute(text("""
            SELECT id, description, owner, due_date, status, one_on_one_id
            FROM commitments
            WHERE person_id = :pid AND status = 'open'
            ORDER BY due_date NULLS LAST, id
        """), {"pid": person_id}).fetchall():
        commitments.append({
            "id": r[0], "description": r[1], "owner": r[2],
            "due_date": r[3].isoformat() if r[3] else None,
            "is_overdue": bool(r[3] and r[3] < date.today()),
            "one_on_one_id": r[5],
        })

    sessions = []
    for r in db.execute(text(_SESSION_SELECT +
                             " WHERE person_id = :pid ORDER BY occurred_on DESC, id DESC LIMIT 8"),
                        {"pid": person_id}).fetchall():
        ext = r[11] if isinstance(r[11], dict) else {}
        sessions.append({
            **_serialize_session(r),
            "pontos_atencao": ext.get('pontos_atencao', []),
            "pontos_desenvolvimento": ext.get('pontos_desenvolvimento', []),
            "conquistas": ext.get('conquistas', []),
        })

    checkpoint = None
    cp = db.execute(text("""
        SELECT id, checkpoint_date, actions_json, private_notes, public_notes, source
        FROM checkpoints WHERE person_id = :pid
        ORDER BY checkpoint_date DESC, id DESC LIMIT 1
    """), {"pid": person_id}).fetchone()
    if cp:
        checkpoint = {
            "id": cp[0], "checkpoint_date": cp[1].isoformat(),
            "actions": cp[2] if isinstance(cp[2], list) else [],
            "private_notes": cp[3], "public_notes": cp[4], "source": cp[5],
        }

    stats = {}
    if sessions:
        days = (date.today() - date.fromisoformat(sessions[0]["occurred_on"])).days
        stats["days_since_last"] = days
    else:
        stats["days_since_last"] = None
    stats["open_count"] = len(commitments)
    stats["overdue_count"] = sum(1 for c in commitments if c["is_overdue"])

    trend = None
    scores = [_SENTIMENT_SCORE.get(s["sentiment"]) for s in sessions[:2]]
    if len(scores) == 2 and None not in scores:
        if scores[0] != scores[1]:
            trend = "melhorando" if scores[0] > scores[1] else "piorando"
        else:
            trend = "estável"
    stats["sentiment_trend"] = trend

    return {
        "person": {
            "id": person_row[0],
            "full_name": person_row[1],
            "preferred_name": person_row[2],
            "email": person_row[3],
            "role_title": person_row[4],
            "active": person_row[5],
            "hired_at": person_row[6].isoformat() if person_row[6] else None,
            "notes": person_row[7],
            "has_photo": bool(person_row[8]),
        },
        "stats": stats,
        "commitments": commitments,
        "recent_sessions": sessions,
        "last_checkpoint": checkpoint,
    }


_PERSON_FOR_PREP = """
    SELECT id, full_name, preferred_name, email, role_title, active,
           hired_at, notes, photo IS NOT NULL
    FROM people WHERE id = :pid
"""


@bp.route('/people/<int:person_id>/prep', methods=['GET'])
@login_required
def person_prep(person_id):
    db = session_factory()
    try:
        person = db.execute(text(_PERSON_FOR_PREP), {"pid": person_id}).fetchone()
        if not person:
            return jsonify({"error": "Pessoa não encontrada"}), 404
        return jsonify(_collect_prep_data(db, person))
    finally:
        db.close()


@bp.route('/people/<int:person_id>/agenda', methods=['POST'])
@login_required
def person_agenda(person_id):
    db = session_factory()
    try:
        person = db.execute(text(_PERSON_FOR_PREP), {"pid": person_id}).fetchone()
        if not person:
            return jsonify({"error": "Pessoa não encontrada"}), 404

        prep = _collect_prep_data(db, person)
    finally:
        db.close()

    lines = [f"Pessoa: {prep['person']['full_name']}"]
    role = prep['person'].get('role_title')
    if role:
        lines.append(f"Cargo: {role}")
    st = prep['stats']
    lines.append(
        f"Dias desde o último 1:1: {st['days_since_last'] if st['days_since_last'] is not None else 'sem registro'}")

    if prep['commitments']:
        lines.append("\nCOMBINADOS EM ABERTO:")
        for c in prep['commitments']:
            flag = " ⚠️ VENCIDO" if c['is_overdue'] else ""
            due = f" (prazo {c['due_date']})" if c['due_date'] else ""
            who = "Gestor" if c['owner'] == 'manager' else "Liderado"
            lines.append(f"- [{who}] {c['description']}{due}{flag}")

    if prep['recent_sessions']:
        lines.append("\nHISTÓRICO RECENTE DE 1:1s (mais recente primeiro):")
        for s in prep['recent_sessions'][:6]:
            lines.append(f"\n### {s['occurred_on']} — {s['title'] or '1:1'} "
                         f"(sentimento: {s['sentiment'] or 'n/a'})")
            if s['summary_ai']:
                lines.append(s['summary_ai'])
            if s['pontos_atencao']:
                lines.append("Atenção: " + "; ".join(s['pontos_atencao']))
            if s['pontos_desenvolvimento']:
                lines.append("Desenvolvimento: " + "; ".join(s['pontos_desenvolvimento']))
            if s['conquistas']:
                lines.append("Conquistas: " + "; ".join(s['conquistas']))

    if prep['last_checkpoint']:
        cp = prep['last_checkpoint']
        lines.append(f"\nÚLTIMO CHECKPOINT ({cp['checkpoint_date']}, fonte {cp['source']}):")
        for a in cp['actions']:
            who = a.get('responsavel') or a.get('owner') or '?'
            lines.append(f"- Ação: {a.get('acao') or a.get('action')} — Responsável: {who}")
        if cp['public_notes']:
            lines.append(f"Notas públicas: {cp['public_notes']}")

    agenda_md = ai.generate_prep_agenda(prep['person']['full_name'], "\n".join(lines))
    return jsonify({"agenda_md": agenda_md})
