"""Conexões com ferramentas externas.

Gmail é a primeira integração disponível (leitura somente-leitura via
OAuth do Google). Slack, Drive e Agenda aparecem como "em breve".
"""
import os
import secrets
from datetime import datetime, timedelta, timezone

import requests
from flask import Blueprint, request, jsonify, session, redirect
from sqlalchemy import text

from ..database import session_factory
from ..security import login_required
from .. import gmail as gmail_svc

bp = Blueprint("connections", __name__, url_prefix="/api")

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def _google_creds():
    """Credenciais OAuth lidas em tempo de chamada (permite configurar
    os Secrets sem reiniciar o processo e facilita testes)."""
    return (os.environ.get("GOOGLE_CLIENT_ID"),
            os.environ.get("GOOGLE_CLIENT_SECRET"))

# Só leitura da caixa. Não misturar com openid do login — senão o Google
# devolve um token incremental sem gmail.readonly (403 em threads.list).
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SCOPES = [GMAIL_READONLY]

TOOLS = [
    {
        "id": "gmail",
        "name": "Gmail",
        "icon": "✉️",
        "description": "Lê threads em que seus liderados participam e levanta pendências, to-dos e assuntos em andamento nos cards.",
        "available": True,
    },
    {
        "id": "google_drive",
        "name": "Google Drive",
        "icon": "📂",
        "description": "Transcrições e documentos das reuniões do Meet, automaticamente.",
        "available": False,
    },
    {
        "id": "slack",
        "name": "Slack",
        "icon": "💬",
        "description": "Sinais do time onde o trabalho realmente acontece.",
        "available": False,
    },
    {
        "id": "calendar",
        "name": "Google Agenda",
        "icon": "📅",
        "description": "Detecta os 1:1s agendados e lembra você de registrá-los.",
        "available": False,
    },
]


def _redirect_uri():
    return request.host_url.replace('http://', 'https://').rstrip('/') + "/api/connections/gmail/callback"


@bp.route('/connections', methods=['GET'])
@login_required
def list_connections():
    db = session_factory()
    try:
        rows = db.execute(text("""
            SELECT tool, status, account_email, connected_at
            FROM connections WHERE user_id = :uid
        """), {"uid": session['user_id']}).fetchall()
        by_tool = {r[0]: r for r in rows}

        items = []
        for t in TOOLS:
            item = dict(t)
            r = by_tool.get(t["id"])
            if r:
                item.update({
                    "status": r[1],
                    "account": r[2],
                    "connected_at": r[3].isoformat() if r[3] else None,
                })
            else:
                item["status"] = "not_connected"
            items.append(item)
        return jsonify({"connections": items})
    finally:
        db.close()


@bp.route('/connections/<tool_id>/start', methods=['GET'])
@login_required
def connect_start(tool_id):
    tool = next((t for t in TOOLS if t["id"] == tool_id), None)
    if not tool or not tool["available"]:
        return jsonify({"error": "Integração ainda não disponível."}), 400
    client_id, client_secret = _google_creds()
    if not client_id or not client_secret:
        return jsonify({"error": "Google OAuth não configurado (Secrets ausentes)."}), 500

    if tool_id == "gmail":
        db = session_factory()
        try:
            user_email = db.execute(text("SELECT email FROM users WHERE id = :uid"),
                                    {"uid": session['user_id']}).fetchone()
        finally:
            db.close()

        session['conn_state'] = secrets.token_urlsafe(16)
        cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
        url = requests.Request('GET', cfg["authorization_endpoint"], params={
            "client_id": client_id,
            "scope": " ".join(GMAIL_SCOPES),
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "redirect_uri": _redirect_uri(),
            "state": session['conn_state'],
            "login_hint": user_email[0] if user_email else None,
        }).prepare().url
        return jsonify({"redirect_url": url})

    return jsonify({"error": "Integração ainda não disponível."}), 400


@bp.route('/connections/gmail/callback')
def gmail_callback():
    """Retorno do Google. Sem @login_required (o Google redireciona direto),
    mas exige sessão ativa para saber em quem conectar."""
    if 'user_id' not in session:
        return redirect('/login')

    error = request.args.get('error')
    code = request.args.get('code')
    state = request.args.get('state')

    if error == 'access_denied':
        return redirect('/connections?error=gmail_denied')
    if not code or not state or state != session.get('conn_state'):
        return redirect('/connections?error=invalid_state')
    session.pop('conn_state', None)

    cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
    client_id, client_secret = _google_creds()
    token_response = requests.post(cfg["token_endpoint"], data={
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    tokens = token_response.json()

    access_token = tokens.get('access_token')
    if not access_token:
        return redirect('/connections?error=token_exchange_failed')

    granted = (tokens.get('scope') or '')
    if granted and GMAIL_READONLY not in granted.split():
        return redirect('/connections?error=gmail_scope')

    profile = {}
    try:
        prof = requests.get(GMAIL_PROFILE_URL, headers={
            "Authorization": f"Bearer {access_token}"
        }, timeout=15)
        if prof.status_code != 200:
            print(f"Gmail profile HTTP {prof.status_code}: {prof.text[:300]}")
            return redirect('/connections?error=gmail_forbidden')
        profile = prof.json() or {}
    except Exception as e:
        print(f"Gmail profile falhou: {e}")
        return redirect('/connections?error=gmail_forbidden')
    account_email = profile.get('emailAddress') or ''
    if not account_email:
        return redirect('/connections?error=gmail_forbidden')

    expires_in = tokens.get('expires_in', 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    refresh_token = tokens.get('refresh_token')

    db = session_factory()
    try:
        db.execute(text("""
            INSERT INTO connections
                (user_id, tool, status, account_email, scopes,
                 access_token, refresh_token, expires_at)
            VALUES (:uid, 'gmail', 'connected', :acct, :scopes,
                    :at, :rt, :exp)
            ON CONFLICT (user_id, tool) DO UPDATE SET
                status = 'connected',
                account_email = EXCLUDED.account_email,
                scopes = EXCLUDED.scopes,
                access_token = EXCLUDED.access_token,
                refresh_token = COALESCE(EXCLUDED.refresh_token, connections.refresh_token),
                expires_at = EXCLUDED.expires_at,
                updated_at = CURRENT_TIMESTAMP
        """), {
            "uid": session['user_id'],
            "acct": account_email or None,
            "scopes": " ".join(GMAIL_SCOPES),
            "at": access_token,
            "rt": refresh_token,
            "exp": expires_at,
        })
        db.commit()
        gmail_svc.clear_auth_skip(session['user_id'])
    finally:
        db.close()

    return redirect('/connections?connected=gmail')


@bp.route('/connections/<tool_id>/disconnect', methods=['POST'])
@login_required
def disconnect(tool_id):
    if tool_id != "gmail":
        return jsonify({"error": "Integração ainda não disponível."}), 400

    db = session_factory()
    try:
        row = db.execute(text("""
            SELECT refresh_token FROM connections
            WHERE user_id = :uid AND tool = 'gmail'
        """), {"uid": session['user_id']}).fetchone()

        if row and row[0]:
            try:
                requests.post("https://oauth2.googleapis.com/revoke",
                              data={"token": row[0]},
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
            except Exception as e:
                print(f"Aviso: falha ao revogar token no Google: {e}")

        db.execute(text("""
            DELETE FROM connections WHERE user_id = :uid AND tool = 'gmail'
        """), {"uid": session['user_id']})
        db.commit()
        gmail_svc.clear_auth_skip(session['user_id'])
        return jsonify({"success": True})
    finally:
        db.close()
