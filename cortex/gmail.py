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
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
GMAIL_THREADS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/threads"
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"

_MAX_THREADS = 10
_BODY_LIMIT = 900
_NEWER_THAN = "90d"

# Evita repetir threads.list 403 para cada pessoa do mesmo job/processo.
_AUTH_SKIP = {}


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


def _parse_google_error(resp):
    try:
        body = resp.json() or {}
    except Exception:
        return "", (resp.text or "")[:300]
    err = body.get("error") or {}
    if isinstance(err, str):
        return err, err
    reason = ""
    for item in err.get("errors") or []:
        reason = item.get("reason") or reason
    for item in err.get("details") or []:
        reason = item.get("reason") or reason
    reason = reason or err.get("status") or ""
    message = err.get("message") or (resp.text or "")[:300]
    return reason, message


def _skip_for_google_error(status_code, reason):
    reason = (reason or "").lower()
    if status_code == 401 or reason in ("autherror", "unauthorized"):
        return "gmail_sem_permissao"
    if reason in (
        "insufficientpermissions",
        "access_token_scope_insufficient",
        "forbidden",
    ):
        return "gmail_sem_permissao"
    if reason in ("domainpolicy", "service_disabled", "accessnotconfigured"):
        return "gmail_bloqueado"
    if status_code == 403:
        return "gmail_sem_permissao"
    return "erro"


def token_has_gmail_scope(access_token):
    if not access_token:
        return False
    try:
        r = requests.get(TOKENINFO_URL, params={"access_token": access_token}, timeout=10)
        data = r.json() if r.ok else {}
        scopes = (data.get("scope") or "").split()
        return GMAIL_READONLY in scopes or "https://mail.google.com/" in scopes
    except Exception as e:
        print(f"Gmail: falha no tokeninfo: {e}")
        return True


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
    return token, datetime.now(timezone.utc) + timedelta(seconds=expires_in), data.get("scope")


def _mark_needs_reauth(db, user_id):
    try:
        db.execute(text("""
            UPDATE connections SET status = 'needs_reauth', updated_at = CURRENT_TIMESTAMP
            WHERE user_id = :uid AND tool = 'gmail'
        """), {"uid": user_id})
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Gmail: falha ao marcar needs_reauth: {e}")


def get_access_token(db, user_id, force_refresh=False):
    """Devolve access_token válido ou None se Gmail não estiver conectado."""
    row = db.execute(text("""
        SELECT access_token, refresh_token, expires_at, status
        FROM connections
        WHERE user_id = :uid AND tool = 'gmail'
    """), {"uid": user_id}).fetchone()
    if not row:
        return None
    access, refresh, expires_at, status = row[0], row[1], _aware(row[2]), row[3]
    if status not in ("connected", "needs_reauth"):
        return None

    soon = datetime.now(timezone.utc) + timedelta(seconds=90)
    fresh = access and expires_at and expires_at > soon
    if fresh and not force_refresh:
        return access

    refreshed = _refresh_access_token(refresh)
    if not refreshed:
        return access if (fresh and access) else None
    new_access, new_exp, _granted = refreshed
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
    """Busca threads recentes em que o e-mail do participante aparece."""
    email = (person_email or "").strip().lower()
    if not email:
        return []

    query = f"from:{email} OR to:{email} OR cc:{email} newer_than:{_NEWER_THAN}"
    listed = _auth_get(GMAIL_THREADS_URL, access_token, {
        "q": query,
        "maxResults": _MAX_THREADS,
    })
    if listed.status_code >= 400:
        reason, message = _parse_google_error(listed)
        print(f"Gmail threads.list HTTP {listed.status_code} ({reason}): {message}")
        raise RuntimeError(
            f"HTTP {listed.status_code}",
            _skip_for_google_error(listed.status_code, reason),
        )

    threads = (listed.json() or {}).get("threads") or []
    out = []
    for t in threads[:_MAX_THREADS]:
        tid = t.get("id")
        if not tid:
            continue
        got = _auth_get(f"{GMAIL_THREADS_URL}/{tid}", access_token, {"format": "full"})
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


def _auth_skip_key(user_id, job_id):
    return f"{user_id}:{job_id or 'anon'}"


def clear_auth_skip(user_id=None):
    if user_id is None:
        _AUTH_SKIP.clear()
        return
    prefix = f"{user_id}:"
    for key in [k for k in _AUTH_SKIP if str(k).startswith(prefix)]:
        _AUTH_SKIP.pop(key, None)


def scan_person_inbox(db, user_id, person_email, job_id=None):
    """Resultado pronto para o card: scanned/skipped + threads.

    Nunca levanta: erros viram skipped.
    """
    email = (person_email or "").strip().lower()
    if not email:
        return {"scanned": False, "skipped": "sem_email", "threads": [], "thread_count": 0}

    cache_key = _auth_skip_key(user_id, job_id)
    cached = _AUTH_SKIP.get(cache_key)
    if cached:
        return {"scanned": False, "skipped": cached, "threads": [], "thread_count": 0}

    def _fail(skipped):
        if skipped in ("gmail_sem_permissao", "gmail_bloqueado", "gmail_nao_conectado"):
            _AUTH_SKIP[cache_key] = skipped
        return {"scanned": False, "skipped": skipped, "threads": [], "thread_count": 0}

    try:
        token = get_access_token(db, user_id)
    except Exception as e:
        print(f"Gmail: erro ao obter token: {e}")
        return _fail("erro")

    if not token:
        return _fail("gmail_nao_conectado")

    if not token_has_gmail_scope(token):
        token = get_access_token(db, user_id, force_refresh=True) or token
        if not token_has_gmail_scope(token):
            print("Gmail: token sem escopo gmail.readonly — reconecte em Conexões.")
            _mark_needs_reauth(db, user_id)
            return _fail("gmail_sem_permissao")

    try:
        threads = search_person_threads(token, email)
    except RuntimeError as e:
        skipped = e.args[1] if len(e.args) > 1 else "erro"
        if skipped in ("gmail_sem_permissao", "erro"):
            retry = get_access_token(db, user_id, force_refresh=True)
            if retry and retry != token:
                try:
                    threads = search_person_threads(retry, email)
                except RuntimeError as e2:
                    skipped = e2.args[1] if len(e2.args) > 1 else skipped
                    if skipped == "gmail_sem_permissao":
                        _mark_needs_reauth(db, user_id)
                    return _fail(skipped)
                except Exception as e2:
                    print(f"Gmail: falha no retry de {email}: {e2}")
                    return _fail("erro")
            else:
                if skipped == "gmail_sem_permissao":
                    _mark_needs_reauth(db, user_id)
                return _fail(skipped)
        else:
            return _fail(skipped)
    except Exception as e:
        print(f"Gmail: falha ao buscar threads de {email}: {e}")
        return _fail("erro")

    _AUTH_SKIP.pop(cache_key, None)
    return {
        "scanned": True,
        "skipped": None,
        "threads": threads,
        "thread_count": len(threads),
    }
