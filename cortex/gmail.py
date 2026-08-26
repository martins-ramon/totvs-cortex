"""Leitura somente-leitura da caixa Gmail do diretor.

Usa o token persistido em `connections` (tool=gmail). Falhas nunca derrubam
a geração do card: o chamador trata o resultado `skipped`.
"""
import base64
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape

import requests
from sqlalchemy import text

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

GMAIL_THREADS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/threads"
_MAX_THREADS = 10
_BODY_LIMIT = 900
_NEWER_THAN = "90d"


def _aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _header(headers, name):
    target = name.lower()
    for h in headers or []:
        if (h.get("name") or "").lower() == target:
            return (h.get("value") or "").strip()
    return ""


def _b64url_decode(data):
    if not data:
        return ""
    pad = "=" * ((4 - len(data) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _strip_html(raw):
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(re.sub(r"[ \t]+", " ", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _walk_body(payload):
    if not payload:
        return ""
    mime = payload.get("mimeType") or ""
    body = payload.get("body") or {}
    data = body.get("data")
    if mime.startswith("text/plain") and data:
        return _b64url_decode(data)
    for part in payload.get("parts") or []:
        found = _walk_body(part)
        if found:
            return found
    if mime.startswith("text/html") and data:
        return _strip_html(_b64url_decode(data))
    return ""


def _refresh_access_token(refresh_token):
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret or not refresh_token:
        return None
    try:
        cfg = requests.get(GOOGLE_DISCOVERY_URL, timeout=15).json()
        resp = requests.post(cfg["token_endpoint"], data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
        data = resp.json()
    except Exception as e:
        print(f"Gmail: falha ao renovar token: {e}")
        return None
    token = data.get("access_token")
    if not token:
        print(f"Gmail: refresh sem access_token: {data.get('error')}")
        return None
    expires_in = int(data.get("expires_in") or 3600)
    return token, datetime.now(timezone.utc) + timedelta(seconds=expires_in)


def get_access_token(db, user_id):
    """Devolve access_token válido ou None se Gmail não estiver conectado."""
    row = db.execute(text("""
        SELECT access_token, refresh_token, expires_at
        FROM connections
        WHERE user_id = :uid AND tool = 'gmail' AND status = 'connected'
    """), {"uid": user_id}).fetchone()
    if not row:
        return None

    access, refresh, expires_at = row[0], row[1], _aware(row[2])
    soon = datetime.now(timezone.utc) + timedelta(seconds=90)
    if access and expires_at and expires_at > soon:
        return access

    refreshed = _refresh_access_token(refresh)
    if not refreshed:
        return access or None
    new_access, new_exp = refreshed
    db.execute(text("""
        UPDATE connections
        SET access_token = :at, expires_at = :exp, updated_at = CURRENT_TIMESTAMP
        WHERE user_id = :uid AND tool = 'gmail'
    """), {"at": new_access, "exp": new_exp, "uid": user_id})
    db.commit()
    return new_access


def _auth_get(url, token, params=None):
    return requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=20,
    )


def search_person_threads(access_token, person_email):
    """Busca threads recentes em que o e-mail do participante aparece.

    Retorna lista de dicts: subject, from, to, date, snippet, body.
    Levanta RuntimeError em falha da API.
    """
    email = (person_email or "").strip().lower()
    if not email:
        return []

    query = f"(from:{email} OR to:{email} OR cc:{email}) newer_than:{_NEWER_THAN}"
    listed = _auth_get(GMAIL_THREADS_URL, access_token, {
        "q": query,
        "maxResults": _MAX_THREADS,
    })
    if listed.status_code == 401:
        raise RuntimeError("token Gmail expirado")
    if listed.status_code >= 400:
        raise RuntimeError(f"Gmail threads.list HTTP {listed.status_code}")

    threads = (listed.json() or {}).get("threads") or []
    out = []
    for t in threads[:_MAX_THREADS]:
        tid = t.get("id")
        if not tid:
            continue
        got = _auth_get(f"{GMAIL_THREADS_URL}/{tid}", access_token, {
            "format": "full",
            "metadataHeaders": ["From", "To", "Cc", "Subject", "Date"],
        })
        if got.status_code >= 400:
            continue
        data = got.json() or {}
        messages = data.get("messages") or []
        if not messages:
            continue
        last = messages[-1]
        payload = last.get("payload") or {}
        headers = payload.get("headers") or []
        body = (_walk_body(payload) or "").strip()
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT].rsplit(" ", 1)[0] + "…"
        out.append({
            "subject": _header(headers, "Subject") or "(sem assunto)",
            "from": _header(headers, "From"),
            "to": _header(headers, "To"),
            "date": _header(headers, "Date"),
            "snippet": (last.get("snippet") or t.get("snippet") or "").strip(),
            "body": body,
            "message_count": len(messages),
        })
    return out


def scan_person_inbox(db, user_id, person_email):
    """Resultado pronto para o card: scanned/skipped + threads.

    Nunca levanta: erros viram skipped='erro'.
    """
    email = (person_email or "").strip().lower()
    if not email:
        return {"scanned": False, "skipped": "sem_email", "threads": [], "thread_count": 0}

    try:
        token = get_access_token(db, user_id)
    except Exception as e:
        print(f"Gmail: erro ao obter token: {e}")
        return {"scanned": False, "skipped": "erro", "threads": [], "thread_count": 0}

    if not token:
        return {"scanned": False, "skipped": "gmail_nao_conectado", "threads": [], "thread_count": 0}

    try:
        threads = search_person_threads(token, email)
    except Exception as e:
        print(f"Gmail: falha ao buscar threads de {email}: {e}")
        return {"scanned": False, "skipped": "erro", "threads": [], "thread_count": 0}

    return {
        "scanned": True,
        "skipped": None,
        "threads": threads,
        "thread_count": len(threads),
    }
