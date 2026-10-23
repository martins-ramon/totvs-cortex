"""Bounded, read-only Gmail retrieval for the floating assistant.

The account always comes from the authenticated director's connection. Tokens
never become source material, and no date filter silently excludes older mail.
"""
import re
from datetime import datetime, timezone
from time import monotonic

import requests

from cortex.database import session_factory
from cortex.gmail import (
    GMAIL_THREADS_URL, _auth_get, _header, _walk_body, get_access_token,
)

_TIME_BUDGET = 16.0
_MAX_THREADS = 8
_MAX_MESSAGES = 48
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\Z")
_GENERIC_TOPICS = {"estrutura", "evolucao", "desafio", "desafios", "situacao", "resumo", "resuma", "organograma"}


def _query(people, terms):
    emails = sorted({p.get("email", "").strip().lower() for p in people
                     if isinstance(p.get("email"), str)
                     and _EMAIL.fullmatch(p["email"].strip())})
    # Gmail OR groups preserve scope for each selected person. The same query
    # can cover several people without opening anyone else's connection.
    if emails:
        return "{" + " ".join(f"from:{email} to:{email} cc:{email}" for email in emails) + "}"
    safe_terms = [term for term in terms if re.fullmatch(r"[a-z0-9]{3,60}", term)]
    specific_terms = [term for term in safe_terms if term not in _GENERIC_TOPICS]
    safe_terms = specific_terms or safe_terms
    return "{" + " ".join(safe_terms[:12]) + "}" if safe_terms else "in:anywhere"


def _date(message, headers):
    try:
        return datetime.fromtimestamp(int(message["internalDate"]) / 1000,
                                      tz=timezone.utc).isoformat()
    except (KeyError, ValueError, TypeError, OverflowError):
        return _header(headers, "Date") or None


def search_gmail(user_id, people, terms):
    """Return sources plus explicit coverage; network failures are non-fatal."""
    coverage = {"status": "unavailable", "query": _query(people, terms),
                "thread_count": 0, "message_count": 0, "partial": False,
                "time_budget_seconds": _TIME_BUDGET, "date_filter": None}
    result = {"sources": [], "coverage": coverage, "warnings": []}
    deadline = monotonic() + _TIME_BUDGET
    try:
        db = session_factory()
        try:
            token = get_access_token(db, user_id, deadline=deadline)
        finally:
            db.close()
        if not token:
            coverage["status"] = "not_connected"
            result["warnings"].append("Gmail não está conectado ou precisa ser reconectado; e-mails não foram consultados.")
            return result

        listed = _auth_get(GMAIL_THREADS_URL, token,
                           {"q": coverage["query"], "maxResults": _MAX_THREADS},
                           deadline=deadline)
        if listed.status_code >= 400:
            coverage["status"] = "needs_reauth" if listed.status_code in (401, 403) else "unavailable"
            result["warnings"].append("Não foi possível consultar o Gmail; confira a conexão em Conexões.")
            return result
        listing = listed.json() or {}
        threads = listing.get("threads") or []
        coverage["partial"] = bool(listing.get("nextPageToken")) or len(threads) > _MAX_THREADS
        coverage["estimated_threads"] = listing.get("resultSizeEstimate")
        seen = set()
        for thread in threads[:_MAX_THREADS]:
            if monotonic() >= deadline:
                raise requests.Timeout()
            thread_id = str(thread.get("id") or "")
            if not _SAFE_ID.fullmatch(thread_id):
                continue
            response = _auth_get(f"{GMAIL_THREADS_URL}/{thread_id}", token,
                                 {"format": "full"}, deadline=deadline)
            if response.status_code >= 400:
                coverage["partial"] = True
                continue
            coverage["thread_count"] += 1
            messages = (response.json() or {}).get("messages") or []
            # Full threads are inspected, including earlier messages rather
            # than only the latest reply, which is often an acknowledgement.
            for message in messages:
                message_id = str(message.get("id") or "")
                if not _SAFE_ID.fullmatch(message_id) or message_id in seen:
                    continue
                if len(result["sources"]) >= _MAX_MESSAGES:
                    coverage["partial"] = True
                    break
                seen.add(message_id)
                payload = message.get("payload") or {}
                headers = payload.get("headers") or []
                subject = _header(headers, "Subject") or "E-mail sem assunto"
                body = _walk_body(payload) or message.get("snippet") or ""
                sender, to = _header(headers, "From"), _header(headers, "To")
                result["sources"].append({
                    "id": f"email:{message_id}", "type": "email",
                    "title": subject, "excerpt": f"De: {sender}\nPara: {to}\n{body}",
                    "date": _date(message, headers), "person_id": None,
                    "record_id": message_id, "thread_id": thread_id,
                    "url": f"https://mail.google.com/mail/u/0/#all/{thread_id}",
                })
        coverage["status"] = "connected"
    except requests.Timeout:
        coverage["status"] = "partial" if result["sources"] else "unavailable"
        coverage["partial"] = True
        result["warnings"].append("A consulta ao Gmail atingiu o tempo limite; os e-mails encontrados representam uma seleção parcial.")
    except Exception:
        # Do not return exception strings: OAuth/transport errors can contain
        # credentials or connection details.
        coverage["status"] = "partial" if result["sources"] else "unavailable"
        coverage["partial"] = True
        result["warnings"].append("Gmail indisponível nesta consulta; os registros do Cortex continuam disponíveis.")
    coverage["message_count"] = len(result["sources"])
    if coverage["partial"] and not result["warnings"]:
        result["warnings"].append("A busca do Gmail retornou uma seleção limitada de conversas; não é uma varredura completa da caixa.")
    return result
