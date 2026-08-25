import base64

from flask import Blueprint, request, jsonify
from sqlalchemy import text

from ..database import session_factory
from ..security import login_required

bp = Blueprint("people", __name__, url_prefix="/api")


# NOVO MODELO — Pessoas do time (liderados, sem login)
# ============================================================

_PERSON_SELECT = """
    SELECT id, full_name, preferred_name, email, role_title, active,
           hired_at, notes, photo IS NOT NULL, created_at
    FROM people
"""


def _serialize_person(row):
    return {
        "id": row[0],
        "full_name": row[1],
        "preferred_name": row[2],
        "email": row[3],
        "role_title": row[4],
        "active": row[5],
        "hired_at": row[6].isoformat() if row[6] else None,
        "notes": row[7],
        "has_photo": bool(row[8]),
        "created_at": row[9].isoformat() if row[9] else None,
    }


def _parse_optional_date(value):
    if not value:
        return None
    from datetime import date as _date
    try:
        return _date.fromisoformat(value)
    except ValueError:
        raise ValueError("Data inválida (use YYYY-MM-DD).")


@bp.route('/people', methods=['GET'])
@login_required
def api_list_people():
    db = session_factory()
    try:
        rows = db.execute(
            text(_PERSON_SELECT + " ORDER BY active DESC, full_name")
        ).fetchall()
        return jsonify({"people": [_serialize_person(r) for r in rows]})
    finally:
        db.close()


@bp.route('/people', methods=['POST'])
@login_required
def api_create_person():
    data = request.json or {}
    full_name = (data.get('full_name') or '').strip()
    if not full_name:
        return jsonify({"error": "Nome é obrigatório"}), 400
    try:
        hired_at = _parse_optional_date(data.get('hired_at'))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    db = session_factory()
    try:
        result = db.execute(text("""
            INSERT INTO people (full_name, preferred_name, email, role_title, hired_at, notes)
            VALUES (:name, :pref, :email, :role, :hired, :notes)
            RETURNING id
        """), {
            "name": full_name,
            "pref": (data.get('preferred_name') or '').strip() or None,
            "email": (data.get('email') or '').strip() or None,
            "role": (data.get('role_title') or '').strip() or None,
            "hired": hired_at,
            "notes": data.get('notes') or None,
        })
        db.commit()
        person_id = result.fetchone()[0]
        return jsonify({"success": True, "person_id": person_id}), 201
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@bp.route('/people/<int:person_id>', methods=['GET'])
@login_required
def api_get_person(person_id):
    db = session_factory()
    try:
        row = db.execute(
            text(_PERSON_SELECT + " WHERE id = :pid"), {"pid": person_id}
        ).fetchone()
        if not row:
            return jsonify({"error": "Pessoa não encontrada"}), 404
        return jsonify({"person": _serialize_person(row)})
    finally:
        db.close()


@bp.route('/people/<int:person_id>', methods=['PUT'])
@login_required
def api_update_person(person_id):
    data = request.json or {}
    updates, params = [], {"pid": person_id}

    field_map = {
        'full_name': 'full_name', 'preferred_name': 'preferred_name',
        'email': 'email', 'role_title': 'role_title', 'notes': 'notes',
    }
    for key, col in field_map.items():
        if key in data:
            updates.append(f"{col} = :{key}")
            params[key] = (data[key] or '').strip() or None
    if 'active' in data:
        updates.append("active = :active")
        params["active"] = bool(data['active'])
    if 'hired_at' in data:
        try:
            updates.append("hired_at = :hired")
            params["hired"] = _parse_optional_date(data['hired_at'])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    if not updates:
        return jsonify({"error": "Nenhum campo para atualizar"}), 400
    updates.append("updated_at = CURRENT_TIMESTAMP")

    db = session_factory()
    try:
        result = db.execute(
            text(f"UPDATE people SET {', '.join(updates)} WHERE id = :pid"),
            params)
        db.commit()
        if result.rowcount == 0:
            return jsonify({"error": "Pessoa não encontrada"}), 404
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@bp.route('/people/<int:person_id>', methods=['DELETE'])
@login_required
def api_deactivate_person(person_id):
    """Soft delete: desativa a pessoa preservando histórico."""
    db = session_factory()
    try:
        result = db.execute(
            text("UPDATE people SET active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE id = :pid"),
            {"pid": person_id})
        db.commit()
        if result.rowcount == 0:
            return jsonify({"error": "Pessoa não encontrada"}), 404
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@bp.route('/people/<int:person_id>/photo', methods=['POST', 'GET'])
@login_required
def api_person_photo(person_id):
    db = session_factory()
    try:
        if request.method == 'POST':
            photo_data = request.json.get('photo').split(',')[1]
            photo_bytes = base64.b64decode(photo_data)
            db.execute(
                text("UPDATE people SET photo = :photo, updated_at = CURRENT_TIMESTAMP WHERE id = :pid"),
                {"photo": photo_bytes, "pid": person_id})
            db.commit()
            return jsonify({"success": True})
        else:
            row = db.execute(
                text("SELECT photo FROM people WHERE id = :pid"),
                {"pid": person_id}).fetchone()
            if row and row[0]:
                photo_base64 = base64.b64encode(row[0]).decode('utf-8')
                return jsonify({"photo": f"data:image/jpeg;base64,{photo_base64}"})
            return jsonify({"photo": None})
    finally:
        db.close()
