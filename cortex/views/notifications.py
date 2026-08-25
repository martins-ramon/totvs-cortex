from flask import Blueprint, request, jsonify
from sqlalchemy import text

from ..database import session_factory
from ..security import login_required

bp = Blueprint("notifications", __name__, url_prefix="/api")


@bp.route('/notifications', methods=['GET'])
@login_required
def get_notifications():
    db = session_factory()
    user_id = session['user_id']
    try:
        notifs_res = db.execute(
            text("""
                SELECT id, title, message, link, is_read, created_at, actor_id
                FROM notifications
                WHERE user_id = :uid
                  AND (
                      is_read = FALSE
                      OR
                      created_at >= (CURRENT_TIMESTAMP - INTERVAL '24 HOURS')
                  )
                ORDER BY created_at DESC
            """),
            {"uid": user_id}
        ).fetchall()

        unread_count_res = db.execute(
            text("SELECT COUNT(id) FROM notifications WHERE user_id = :uid AND is_read = FALSE"),
            {"uid": user_id}).fetchone()

        notifications = [
            {
                "id": n[0], "title": n[1], "message": n[2], "link": n[3],
                "is_read": n[4], "created_at": n[5].isoformat(),
                "actor_id": n[6]
            } for n in notifs_res
        ]

        return jsonify({
            "notifications": notifications,
            "unread_count": unread_count_res[0] if unread_count_res else 0
        })
    finally:
        db.close()


@bp.route('/notifications/<int:notification_id>/read', methods=['POST'])
@login_required
def mark_notification_as_read(notification_id):
    db = session_factory()
    try:
        db.execute(
            text(
                "UPDATE notifications SET is_read = TRUE WHERE id = :nid AND user_id = :uid"
            ), {
                "nid": notification_id,
                "uid": session['user_id']
            })
        db.commit()
        return jsonify({"success": True})
    finally:
        db.close()


@bp.route('/notifications/read-all', methods=['POST'])
@login_required
def mark_all_notifications_as_read():
    db = session_factory()
    try:
        db.execute(text("UPDATE notifications SET is_read = TRUE WHERE user_id = :uid"),
                   {"uid": session['user_id']})
        db.commit()
        return jsonify({"success": True})
    finally:
        db.close()


@bp.route('/notifications/<int:notification_id>/status', methods=['PUT'])
@login_required
def update_notification_status(notification_id):
    data = request.json
    is_read = data.get('is_read')

    if is_read is None:
        return jsonify({"error": "Campo 'is_read' é obrigatório"}), 400

    db = session_factory()
    try:
        db.execute(
            text(
                "UPDATE notifications SET is_read = :status WHERE id = :nid AND user_id = :uid"
            ), {
                "status": is_read,
                "nid": notification_id,
                "uid": session['user_id']
            })
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db.close()


@bp.route('/notifications/poll')
@login_required
def poll_notifications():
    since_id_str = request.args.get('since_id')
    if not since_id_str:
        return jsonify([])

    try:
        since_id = int(since_id_str)
    except Exception:
        return jsonify({"error": "Invalid ID"}), 400

    db = session_factory()
    try:
        new_notifs_res = db.execute(
            text("""
                SELECT id, title, message, link, is_read, created_at, actor_id
                FROM notifications
                WHERE user_id = :uid AND id > :since_id
                ORDER BY id ASC
            """),
            {"uid": session['user_id'], "since_id": since_id}
        ).fetchall()

        notifications = [
            {
                "id": n[0], "title": n[1], "message": n[2], "link": n[3],
                "is_read": n[4], "created_at": n[5].isoformat(),
                "actor_id": n[6]
            } for n in new_notifs_res
        ]
        return jsonify(notifications)
    finally:
        db.close()
