import os

import requests
from flask import Blueprint, request, jsonify, session
from sqlalchemy import text

from ..database import session_factory
from .. import ai
from ..ai.openai_service import summarize_transcription
from ..security import login_required

bp = Blueprint("oneonones", __name__, url_prefix="/api")


# --- CHAT VIA WEBHOOK EXTERNO (n8n) ---

@bp.route('/chat', methods=['POST'])
@login_required
def chat():
    data = request.json
    user_question = data.get("question", "")
    user_id = session["user_id"]

    n8n_webhook_url = os.environ.get('N8N_CHAT_WEBHOOK_URL')
    if not n8n_webhook_url:
        return jsonify({"error": "Chat service is not configured."}), 500

    payload = {"userId": user_id, "question": user_question}

    try:
        response = requests.post(n8n_webhook_url, json=payload, timeout=120)
        response.raise_for_status()
        return jsonify(response.json())

    except requests.exceptions.RequestException as e:
        print(f"Error calling n8n webhook: {e}")
        return jsonify({"error":
                        "Could not connect to the chat service."}), 503
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({"error": str(e)}), 500


# --- REUNIÕES / 1:1 ---

@bp.route('/meetings', methods=['GET', 'POST'])
@login_required
def handle_meetings():
    db = session_factory()
    user_id = session['user_id']
    try:
        if request.method == 'POST':
            data = request.json
            result = db.execute(
                text(
                    "INSERT INTO meetings (user_id, meeting_date, transcription, summary) VALUES (:uid, :date, :trans, :sum) RETURNING id"
                ), {
                    "uid": user_id,
                    "date": data['meeting_date'],
                    "trans": data.get('transcription', ''),
                    "sum": data['summary']
                })
            meeting_id = result.fetchone()[0]

            full_text = f"Reunião de {data['meeting_date']}. Resumo: {data['summary']}. Transcrição: {data.get('transcription', '')}"
            text_chunks = ai.chunk_text(full_text)

            for chunk in text_chunks:
                embedding = ai.get_embedding(chunk)
                db.execute(
                    text(
                        "INSERT INTO meeting_chunks (meeting_id, chunk_text, embedding) VALUES (:mid, :text, :emb)"
                    ), {
                        "mid": meeting_id,
                        "text": chunk,
                        "emb": str(embedding)
                    })
            db.commit()
            return jsonify({"success": True, "meeting_id": meeting_id})
        else:  # GET
            result = db.execute(
                text("""
                    SELECT m.id, m.meeting_date, m.summary, u.name as owner_name, m.user_id
                    FROM meetings m
                    JOIN users u ON m.user_id = u.id
                    WHERE m.user_id = :uid OR m.id IN (
                        SELECT meeting_id FROM meeting_access WHERE user_id = :uid
                    )
                    ORDER BY m.meeting_date DESC
                """), {"uid": user_id})
            meetings = [{
                "id": r[0],
                "meeting_date": r[1].isoformat(),
                "summary": r[2],
                "owner_name": r[3],
                "is_owner": r[4] == user_id
            } for r in result.fetchall()]
            return jsonify({"meetings": meetings})
    finally:
        db.close()


@bp.route('/meetings/<int:meeting_id>', methods=['GET', 'PUT', 'DELETE'])
@login_required
def handle_single_meeting(meeting_id):
    db = session_factory()
    user_id = session['user_id']
    try:
        if request.method == 'GET':
            query = text("""
                SELECT id, meeting_date, summary, transcription
                FROM meetings
                WHERE id = :mid AND (user_id = :uid OR id IN (
                    SELECT meeting_id FROM meeting_access WHERE user_id = :uid
                ))
            """)
            result = db.execute(query, {
                "mid": meeting_id,
                "uid": user_id
            }).fetchone()
            if result:
                return jsonify({
                    "id": result[0],
                    "meeting_date": result[1].isoformat(),
                    "summary": result[2],
                    "transcription": result[3]
                })
            return jsonify({"error":
                            "Meeting not found or access denied"}), 404

        elif request.method == 'PUT':
            data = request.json
            new_summary = data.get('summary', '')
            new_transcription = data.get('transcription', '')
            new_date = data.get('meeting_date')

            current_meeting = db.execute(
                text("SELECT summary, transcription FROM meetings WHERE id = :mid AND user_id = :uid"),
                {"mid": meeting_id, "uid": user_id}
            ).fetchone()

            if not current_meeting:
                return jsonify({"error": "Meeting not found"}), 404

            old_summary = current_meeting[0] or ''
            old_transcription = current_meeting[1] or ''

            content_changed = (old_summary != new_summary) or (old_transcription != new_transcription)

            db.execute(
                text("UPDATE meetings SET meeting_date = :date, transcription = :trans, summary = :sum WHERE id = :mid AND user_id = :uid"),
                {
                    "date": new_date,
                    "trans": new_transcription,
                    "sum": new_summary,
                    "mid": meeting_id,
                    "uid": user_id
                }
            )

            if content_changed:
                print(f"Conteúdo da reunião {meeting_id} alterado. Recalculando embeddings...")

                db.execute(text("DELETE FROM meeting_chunks WHERE meeting_id = :mid"), {"mid": meeting_id})

                full_text = f"Reunião de {new_date}. Resumo: {new_summary}. Transcrição: {new_transcription}"
                text_chunks = ai.chunk_text(full_text)

                for chunk in text_chunks:
                    embedding = ai.get_embedding(chunk)
                    db.execute(
                        text("INSERT INTO meeting_chunks (meeting_id, chunk_text, embedding) VALUES (:mid, :text, :emb)"),
                        {
                            "mid": meeting_id,
                            "text": chunk,
                            "emb": str(embedding)
                        }
                    )
            else:
                print(f"Conteúdo da reunião {meeting_id} inalterado. Pulando geração de embeddings.")

            db.commit()
            return jsonify({"success": True})

        elif request.method == 'DELETE':
            db.execute(
                text(
                    "DELETE FROM meetings WHERE id = :mid AND user_id = :uid"),
                {
                    "mid": meeting_id,
                    "uid": session['user_id']
                })
            db.commit()
            return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@bp.route('/meetings/<int:meeting_id>/share', methods=['POST', 'GET'])
@login_required
def share_meeting(meeting_id):
    db = session_factory()
    user_id = session['user_id']
    try:
        owner_check = db.execute(
            text(
                "SELECT u.name, m.summary FROM meetings m JOIN users u ON m.user_id = u.id WHERE m.id = :mid AND m.user_id = :uid"
            ), {
                "mid": meeting_id,
                "uid": user_id
            }).fetchone()

        if request.method == 'POST':
            if not owner_check:
                return jsonify(
                    {"error":
                     "Somente o criador pode compartilhar a reunião"}), 403

            data = request.json
            new_participant_ids = set(
                int(pid) for pid in data.get('user_ids', [])
                if int(pid) != user_id)

            current_access_res = db.execute(
                text(
                    "SELECT user_id FROM meeting_access WHERE meeting_id = :mid"
                ), {"mid": meeting_id})
            current_participant_ids = set(
                row[0] for row in current_access_res.fetchall())

            added_ids = new_participant_ids - current_participant_ids
            removed_ids = current_participant_ids - new_participant_ids

            db.execute(
                text("DELETE FROM meeting_access WHERE meeting_id = :mid"),
                {"mid": meeting_id})

            for p_id in new_participant_ids:
                db.execute(
                    text(
                        "INSERT INTO meeting_access (meeting_id, user_id) VALUES (:mid, :uid) ON CONFLICT DO NOTHING"
                    ), {
                        "mid": meeting_id,
                        "uid": p_id
                    })

            owner_name = owner_check[0]
            meeting_summary = owner_check[1]
            link = f"/meetings/{meeting_id}"

            if added_ids:
                title_share = "Nova reunião compartilhada"
                msg_share = f"{owner_name} compartilhou a reunião '{meeting_summary[:50]}...' com você."

                if len(added_ids) > 0:
                    added_ids_tuple = tuple(added_ids)
                    users_to_notify = db.execute(
                        text("SELECT id, name, email FROM users WHERE id IN :ids"),
                        {"ids": added_ids_tuple}
                    ).fetchall()

                    for user_row in users_to_notify:
                        target_uid = user_row[0]
                        target_name = user_row[1]
                        target_email = user_row[2]

                        db.execute(
                            text("""
                                INSERT INTO notifications (user_id, actor_id, title, message, link)
                                VALUES (:uid, :actor_id, :title, :message, :link)
                            """), {
                                "uid": target_uid,
                                "actor_id": user_id,
                                "title": title_share,
                                "message": msg_share,
                                "link": link
                            })

                        if target_email:
                            try:
                                email_subject = f"Cortex: {owner_name} compartilhou uma reunião com você"
                                email_body = f"""
                                <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1E293B; max-width: 600px;">
                                    <h2 style="color: #6366F1;">Cortex</h2>
                                    <p>Olá, <strong>{target_name}</strong>.</p>
                                    <p>{owner_name} concedeu a você acesso à seguinte reunião:</p>
                                    <div style="background-color: #F8FAFC; border-left: 4px solid #6366F1; padding: 15px; margin: 20px 0; color: #475569;">
                                        "{meeting_summary}"
                                    </div>
                                    <p>Você pode acessar a transcrição e o resumo executivo clicando abaixo:</p>
                                    <p style="margin-top: 25px;">
                                        <a href="{request.host_url.rstrip('/')}" style="background-color: #6366F1; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; font-weight: 500;">Acessar Cortex</a>
                                    </p>
                                </div>
                                """
                                ai.send_email_action(target_email, email_subject, email_body)
                            except Exception as e:
                                print(f"Erro ao enviar e-mail de compartilhamento para {target_email}: {e}")

            if removed_ids:
                title_remove = "Acesso revogado"
                msg_remove = f"{owner_name} removeu seu acesso à reunião '{meeting_summary[:50]}...'."
                for p_id in removed_ids:
                    db.execute(
                        text("""
                            INSERT INTO notifications (user_id, actor_id, title, message, link)
                            VALUES (:uid, :actor_id, :title, :message, :link)
                        """), {
                            "uid": p_id,
                            "actor_id": user_id,
                            "title": title_remove,
                            "message": msg_remove,
                            "link": None
                        })

            db.commit()
            return jsonify({"success": True})

        else:  # GET
            result = db.execute(
                text(
                    "SELECT user_id FROM meeting_access WHERE meeting_id = :mid"
                ), {"mid": meeting_id})
            shared_with_ids = [row[0] for row in result.fetchall()]
            return jsonify({"shared_with": shared_with_ids})

    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@bp.route("/meetings/summarize", methods=["POST"])
@login_required
def summarize_meeting():
    data = request.get_json()
    transcription = data.get("transcription", "")
    meeting_date = data.get("meeting_date", "Data não informada")

    if not transcription.strip():
        return jsonify({"error": "Nenhuma transcrição fornecida."}), 400

    try:
        summary = summarize_transcription(transcription, meeting_date)
        return jsonify({"summary": summary}), 200
    except Exception as e:
        print(f"Erro ao gerar resumo: {e}")
        return jsonify({"error": str(e)}), 500
