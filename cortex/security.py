"""Segurança: allowlist de e-mails e decorador de autenticação.

Allowlist (variáveis de ambiente):
- ALLOWED_EMAILS: e-mails exatos permitidos, separados por vírgula (ex.: o seu).
- ALLOWED_EMAIL_DOMAINS: domínios permitidos, separados por vírgula.
- Se nenhum dos dois estiver definido, o padrão é aceitar somente @totvs.com.
"""
import os
from functools import wraps

from flask import session, jsonify


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


def _parse_list(value):
    return [item.strip().lower() for item in (value or "").split(",") if item.strip()]


def allowed_emails():
    return _parse_list(os.environ.get("ALLOWED_EMAILS"))


def allowed_domains():
    domains = _parse_list(os.environ.get("ALLOWED_EMAIL_DOMAINS"))
    if not domains and not allowed_emails():
        domains = ["totvs.com.br", "totvs.com"]
    return domains


def email_allowed(email):
    if not email:
        return False
    normalized = email.strip().lower()
    exact = allowed_emails()
    if exact and normalized in exact:
        return True
    domain = normalized.rsplit("@", 1)[-1] if "@" in normalized else ""
    return domain in allowed_domains()


def director_emails():
    """E-mails considerados 'diretor' (dono do sistema), para a migração."""
    directors = _parse_list(os.environ.get("DIRECTOR_EMAILS"))
    if directors:
        return directors
    exact = allowed_emails()
    return exact  # se o allowlist exato está definido, ele É o diretor
