import os
import secrets

import requests
from flask import Blueprint, request, jsonify, session, redirect
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash

from ..database import session_factory
from .. import ai
from ..security import email_allowed, login_required

bp = Blueprint("auth", __name__, url_prefix="/api")

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"


def hash_password(password):
    return generate_password_hash(password)


def get_google_provider_cfg():
    return requests.get(GOOGLE_DISCOVERY_URL).json()


# --- AUTENTICAÇÃO LOCAL (legado, agora restrito pela allowlist) ---

@bp.route('/register', methods=['POST'])
def register():
    data = request.json
    if not email_allowed(data.get('email')):
        return jsonify({"success": False, "error": "Acesso restrito a contas TOTVS autorizadas."}), 403
    db = session_factory()
    try:
        normalized_name = ai.normalize_text(data['name'])
        result = db.execute(
            text(
                "INSERT INTO users (email, password_hash, name, name_normalized, company, phone) VALUES (:email, :password_hash, :name, :normalized_name, :company, :phone) RETURNING id"
            ), {
                "email": data['email'],
                "password_hash": hash_password(data['password']),
                "name": data['name'],
                "normalized_name": normalized_name,
                "company": data.get('company', ''),
                "phone": data.get('phone', '')
            })
        db.commit()
        user_id = result.fetchone()[0]
        session['user_id'] = user_id
        return jsonify({"success": True, "user_id": user_id})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        db.close()


@bp.route('/login', methods=['POST'])
def login():
    data = request.json
    if not email_allowed(data.get('email')):
        return jsonify({"success": False, "error": "Acesso restrito a contas TOTVS autorizadas."}), 403
    db = session_factory()
    try:
        result = db.execute(
            text(
                "SELECT id, name, company, password_hash, email FROM users WHERE email = :email"
            ), {"email": data['email']})
        user = result.fetchone()
        if user and check_password_hash(user[3], data['password']):
            session['user_id'] = user[0]
            return jsonify({
                "success": True,
                "user": {
                    "id": user[0],
                    "name": user[1],
                    "company": user[2],
                    "email": user[4]
                }
            })
        else:
            return jsonify({
                "success": False,
                "error": "Invalid credentials"
            }), 401
    finally:
        db.close()


@bp.route('/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    return jsonify({"success": True})


@bp.route('/current-user', methods=['GET'])
def current_user():
    if 'user_id' not in session:
        return jsonify({"authenticated": False}), 401
    db = session_factory()
    try:
        result = db.execute(
            text("SELECT id, name, email, company, phone, manager_id, mini_bio, google_id FROM users WHERE id = :id"),
            {"id": session['user_id']}
        )
        user = result.fetchone()
        if user:
            return jsonify({
                "authenticated": True,
                "user": {
                    "id": user[0],
                    "name": user[1],
                    "email": user[2],
                    "company": user[3],
                    "phone": user[4],
                    "manager_id": user[5],
                    "mini_bio": user[6],
                    "has_google_linked": bool(user[7])
                }
            })
        else:
            session.pop('user_id', None)
            return jsonify({"authenticated": False}), 401
    finally:
        db.close()


# --- OAUTH GOOGLE (restrito pela allowlist) ---

@bp.route('/auth/google/login')
def google_login():
    """Inicia o fluxo de login/registro via Google."""
    session['oauth_mode'] = 'login'
    return _start_google_flow()


@bp.route('/auth/google/link')
def google_link():
    """Inicia o fluxo de vinculação de conta para usuário logado."""
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    session['oauth_mode'] = 'link'
    return _start_google_flow()


def _start_google_flow():
    if not GOOGLE_CLIENT_ID:
        return jsonify({"error": "Google Client ID not configured"}), 500

    state = secrets.token_urlsafe(16)
    session['oauth_state'] = state

    google_provider_cfg = get_google_provider_cfg()
    authorization_endpoint = google_provider_cfg["authorization_endpoint"]

    redirect_uri = request.host_url.replace('http://', 'https://').rstrip('/') + "/api/auth/google/callback"

    request_uri = requests.Request('GET', authorization_endpoint, params={
        "client_id": GOOGLE_CLIENT_ID,
        "access_type": "offline",
        "scope": "openid email profile",
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state
    }).prepare().url

    return jsonify({"redirect_url": request_uri})


@bp.route('/auth/google/callback')
def google_callback():
    """Recebe o retorno do Google, troca o code por token e loga/registra o usuário."""
    code = request.args.get("code")
    state = request.args.get("state")

    if state != session.get('oauth_state'):
        return jsonify({"error": "Invalid state parameter"}), 400

    google_provider_cfg = get_google_provider_cfg()
    token_endpoint = google_provider_cfg["token_endpoint"]

    token_response = requests.post(
        token_endpoint,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": request.host_url.replace('http://', 'https://').rstrip('/') + "/api/auth/google/callback",
            "grant_type": "authorization_code",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )

    tokens = token_response.json()

    userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
    userinfo_response = requests.get(userinfo_endpoint, headers={"Authorization": f"Bearer {tokens['access_token']}"})

    if userinfo_response.status_code != 200:
        return jsonify({"error": "Failed to get user info"}), 400

    user_info = userinfo_response.json()
    google_id = user_info["sub"]
    email = user_info["email"]
    name = user_info.get("name", email.split('@')[0])

    mode = session.get('oauth_mode', 'login')

    # Allowlist: somente e-mails autorizados podem entrar (modo login).
    if mode == 'login' and not email_allowed(email):
        return redirect('/login?error=forbidden')

    db = session_factory()

    try:
        if mode == 'link':
            # --- CENÁRIO A: VINCULAR CONTA EXISTENTE ---
            current_user_id = session.get('user_id')
            if not current_user_id:
                return redirect('/?error=session_expired')

            conflict = db.execute(text("SELECT id FROM users WHERE google_id = :gid AND id != :uid"),
                                  {"gid": google_id, "uid": current_user_id}).fetchone()
            if conflict:
                return redirect('/?error=google_account_already_linked')

            db.execute(text("UPDATE users SET google_id = :gid WHERE id = :uid"),
                       {"gid": google_id, "uid": current_user_id})
            db.commit()
            return redirect('/?success=google_linked')

        else:
            # --- CENÁRIO B: LOGIN / REGISTRO ---

            # B1. Tenta achar pelo Google ID
            user = db.execute(text("SELECT id, name, company, email FROM users WHERE google_id = :gid"), {"gid": google_id}).fetchone()

            # B2. Vinculação automática por e-mail já cadastrado
            if not user:
                user_by_email = db.execute(text("SELECT id, name, company, email FROM users WHERE email = :email"), {"email": email}).fetchone()
                if user_by_email:
                    db.execute(text("UPDATE users SET google_id = :gid WHERE id = :uid"), {"gid": google_id, "uid": user_by_email[0]})
                    db.commit()
                    user = user_by_email

            # B3. Cria novo usuário (só chega aqui se passou na allowlist)
            if not user:
                normalized_name = ai.normalize_text(name)
                random_pass = secrets.token_urlsafe(16)

                result = db.execute(text("""
                    INSERT INTO users (email, password_hash, name, name_normalized, company, google_id)
                    VALUES (:email, :pwd, :name, :norm, 'TOTVS', :gid)
                    RETURNING id, name, company, email
                """), {
                    "email": email,
                    "pwd": hash_password(random_pass),
                    "name": name,
                    "norm": normalized_name,
                    "gid": google_id
                })
                db.commit()
                user = result.fetchone()

            session['user_id'] = user[0]
            return redirect('/')

    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()
