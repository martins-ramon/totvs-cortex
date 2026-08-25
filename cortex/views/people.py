import base64

from flask import Blueprint, request, jsonify
from sqlalchemy import text

from werkzeug.security import check_password_hash

from ..database import session_factory
from .. import ai
from ..security import login_required

bp = Blueprint("people", __name__, url_prefix="/api")


# --- LISTAGEM DE USUÁRIOS (legado) ---

@bp.route('/users', methods=['GET'])
@login_required
def get_users():
    db = session_factory()
    try:
        result = db.execute(
            text(
                "SELECT id, name, email, company FROM users WHERE id != :current_user_id ORDER BY name"
            ), {"current_user_id": session['user_id']})
        users = [{
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "company": row[3]
        } for row in result.fetchall()]
        return jsonify({"users": users})
    finally:
        db.close()


@bp.route('/managed-users', methods=['GET'])
@login_required
def get_managed_users():
    db = session_factory()
    try:
        result = db.execute(
            text(
                "SELECT id, name, email, company, phone FROM users WHERE manager_id = :manager_id ORDER BY name"
            ), {"manager_id": session['user_id']})
        managed_users = [{
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "company": row[3],
            "phone": row[4]
        } for row in result.fetchall()]
        return jsonify({"managed_users": managed_users})
    finally:
        db.close()


# --- PERFIL ---

@bp.route('/profile', methods=['PUT'])
@login_required
def update_profile():
    data = request.json
    user_id = session['user_id']

    db = session_factory()
    try:
        current_user = db.execute(
            text("SELECT name, manager_id FROM users WHERE id = :id"), {
                "id": user_id
            }).fetchone()

        if not current_user:
            return jsonify({"error": "User not found"}), 404

        user_name = current_user[0]
        old_manager_id = current_user[1]

        normalized_name = ai.normalize_text(data['name'])

        new_manager_id = data.get('manager_id')
        if new_manager_id == "" or new_manager_id is None:
            new_manager_id = None
        else:
            new_manager_id = int(new_manager_id)

        db.execute(
            text(
                "UPDATE users SET name = :name, name_normalized = :normalized_name, company = :company, phone = :phone, manager_id = :manager_id, mini_bio = :mini_bio WHERE id = :id"
            ), {
                "id": user_id,
                "name": data['name'],
                "normalized_name": normalized_name,
                "company": data.get('company', ''),
                "phone": data.get('phone', ''),
                "manager_id": new_manager_id,
                "mini_bio": data.get('mini_bio')
            })

        # Notificação de troca de gestor
        if old_manager_id != new_manager_id:
            if old_manager_id:
                db.execute(
                    text("""
                        INSERT INTO notifications (user_id, actor_id, title, message, type)
                        VALUES (:uid, :actor_id, :title, :msg, 'SYSTEM')
                    """), {
                        "uid": old_manager_id,
                        "actor_id": user_id,
                        "title": "Liderança Atualizada",
                        "msg": f"{user_name} deixou de listar você como gestor no perfil."
                    })

            if new_manager_id:
                db.execute(
                    text("""
                        INSERT INTO notifications (user_id, actor_id, title, message, link, type)
                        VALUES (:uid, :actor_id, :title, :msg, :link, 'SYSTEM')
                    """), {
                        "uid": new_manager_id,
                        "actor_id": user_id,
                        "title": "Novo Liderado",
                        "msg": f"{user_name} selecionou você como gestor.",
                        "link": "/my-team"
                    })

        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        db.close()


@bp.route('/profile/password', methods=['PUT'])
@login_required
def change_password():
    data = request.json
    current_password, new_password = data.get('current_password'), data.get(
        'new_password')
    if not current_password or not new_password:
        return jsonify({
            "success": False,
            "error": "Todos os campos são obrigatórios"
        }), 400
    db = session_factory()
    try:
        from .auth import hash_password
        user = db.execute(
            text("SELECT password_hash FROM users WHERE id = :id"), {
                "id": session['user_id']
            }).fetchone()
        if not user:
            return jsonify({
                "success": False,
                "error": "Usuário não encontrado"
            }), 404
        if not check_password_hash(user[0], current_password):
            return jsonify({
                "success": False,
                "error": "Senha atual incorreta"
            }), 401
        new_password_hash = hash_password(new_password)
        db.execute(
            text("UPDATE users SET password_hash = :new_hash WHERE id = :id"),
            {
                "new_hash": new_password_hash,
                "id": session['user_id']
            })
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db.close()


@bp.route('/profile/generate-bio', methods=['POST'])
@login_required
def generate_bio():
    data = request.json
    raw_text = data.get('raw_text')
    if not raw_text:
        return jsonify({"error": "Raw text is required"}), 400
    try:
        bio = ai.generate_bio_from_text(raw_text)
        return jsonify({"bio": bio})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/profile/photo', methods=['POST', 'GET'])
@login_required
def profile_photo():
    db = session_factory()
    try:
        if request.method == 'POST':
            photo_data = request.json.get('photo').split(',')[1]
            photo_bytes = base64.b64decode(photo_data)
            db.execute(
                text(
                    "UPDATE users SET profile_photo = :photo WHERE id = :uid"),
                {
                    "photo": photo_bytes,
                    "uid": session['user_id']
                })
            db.commit()
            return jsonify({"success": True})
        else:
            row = db.execute(
                text("SELECT profile_photo FROM users WHERE id = :uid"), {
                    "uid": session['user_id']
                }).fetchone()
            if row and row[0]:
                photo_base64 = base64.b64encode(row[0]).decode('utf-8')
                return jsonify(
                    {"photo": f"data:image/jpeg;base64,{photo_base64}"})
            return jsonify({"photo": None})
    finally:
        db.close()


@bp.route('/user/<int:user_id>/photo', methods=['GET'])
@login_required
def user_photo(user_id):
    db = session_factory()
    try:
        row = db.execute(
            text("SELECT profile_photo FROM users WHERE id = :uid"), {
                "uid": user_id
            }).fetchone()
        if row and row[0]:
            photo_base64 = base64.b64encode(row[0]).decode('utf-8')
            return jsonify({"photo": f"data:image/jpeg;base64,{photo_base64}"})
        return jsonify({"photo": None})
    finally:
        db.close()


# ============================================================
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
