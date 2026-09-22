"""Próximos 1:1s na agenda principal do diretor, com acesso somente de leitura."""
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text

log = logging.getLogger(__name__)
READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
TOKEN_URL = "https://oauth2.googleapis.com/token"
LOOKAHEAD_DAYS = 90
_ONE_ON_ONE = re.compile(
    r"(?<!\w)(?:1\s*[:x/\-]\s*1|1\s*on\s*1|one[\s-]+(?:on|to)[\s-]+one|"
    r"um[\s-]+a[\s-]+um)(?!\w)", re.IGNORECASE,
)


class CalendarUnavailable(Exception):
    def __init__(self, status="unavailable"):
        self.status = status
        super().__init__(status)


def _needs_reauth(db, user_id):
    db.execute(text("""
        UPDATE connections SET status = 'needs_reauth', updated_at = CURRENT_TIMESTAMP
        WHERE user_id = :uid AND tool = 'calendar'
    """), {"uid": user_id})
    db.commit()
    raise CalendarUnavailable("needs_reauth")


def _access_token(db, user_id, connection, force_refresh=False):
    access, refresh, expires_at, status, scopes, _account = connection
    if status != "connected" or READONLY_SCOPE not in (scopes or "").split():
        _needs_reauth(db, user_id)
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (not force_refresh and access and expires_at
            and expires_at > datetime.now(timezone.utc) + timedelta(seconds=90)):
        return access
    if not refresh:
        _needs_reauth(db, user_id)
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise CalendarUnavailable()
    response = requests.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh, "grant_type": "refresh_token",
    }, timeout=15)
    data = response.json()
    if data.get("error") == "invalid_grant":
        _needs_reauth(db, user_id)
    response.raise_for_status()
    if not data.get("access_token"):
        raise CalendarUnavailable()
    if data.get("scope") and READONLY_SCOPE not in data["scope"].split():
        _needs_reauth(db, user_id)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in", 3600)))
    db.execute(text("""
        UPDATE connections SET access_token = :token, expires_at = :expires,
            refresh_token = COALESCE(:refresh, refresh_token), updated_at = CURRENT_TIMESTAMP
        WHERE user_id = :uid AND tool = 'calendar'
    """), {"token": data["access_token"], "expires": expires_at,
           "refresh": data.get("refresh_token"), "uid": user_id})
    db.commit()
    return data["access_token"]


def match_meetings(events, people, account_email, now, calendar_timezone):
    """Cruza título de 1:1 + e-mail exato; nunca infere identidade só pelo nome."""
    by_email = {}
    for person in people:
        email = (person.get("email") or "").strip().lower()
        if email:
            by_email.setdefault(email, []).append(str(person["id"]))
    account = (account_email or "").strip().lower()
    meetings = {}
    for event in events:
        if (event.get("status") == "cancelled" or event.get("attendeesOmitted")
                or not _ONE_ON_ONE.search(event.get("summary") or "")):
            continue
        attendees = event.get("attendees") or []
        if any(a.get("responseStatus") == "declined" for a in attendees
               if a.get("self") or (a.get("email") or "").lower() == account):
            continue
        participants = {
            (a.get("email") or "").strip().lower()
            for a in attendees if not a.get("resource")
            and not a.get("self") and a.get("responseStatus") != "declined"
        }
        organizer = event.get("organizer") or {}
        if not organizer.get("self"):
            participants.add((organizer.get("email") or "").strip().lower())
        participants -= {"", account}
        # Um encontro de grupo não deve esconder a necessidade de agendar um 1:1.
        if len(participants) != 1:
            continue
        email = next(iter(participants))
        if email not in by_email or any(
            (a.get("email") or "").strip().lower() == email
            and a.get("responseStatus") == "declined" for a in attendees
        ):
            continue
        start = event.get("start") or {}
        raw_start = start.get("dateTime") or start.get("date")
        if not raw_start:
            continue
        all_day = not start.get("dateTime")
        if all_day:
            start_at = datetime.combine(date.fromisoformat(raw_start), datetime.min.time(),
                                        tzinfo=ZoneInfo(calendar_timezone))
        else:
            start_at = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=ZoneInfo(start.get("timeZone") or calendar_timezone))
        if start_at < now:
            continue
        for pid in by_email[email]:
            if pid not in meetings or start_at < meetings[pid][0]:
                meetings[pid] = (start_at, {
                    "start": raw_start, "all_day": all_day,
                    "time_zone": start.get("timeZone") or calendar_timezone,
                })
    return {pid: entry[1] for pid, entry in meetings.items()}


def upcoming_oneonones(db, user_id, people):
    result = {"status": "unavailable", "meetings": {}, "lookahead_days": LOOKAHEAD_DAYS}
    try:
        connection = db.execute(text("""
            SELECT access_token, refresh_token, expires_at, status, scopes, account_email
            FROM connections WHERE user_id = :uid AND tool = 'calendar'
        """), {"uid": user_id}).fetchone()
        if not connection:
            return {**result, "status": "not_connected"}
        token = _access_token(db, user_id, connection)
        now = datetime.now(timezone.utc)
        params = {
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(days=LOOKAHEAD_DAYS)).isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "showDeleted": "false",
            "maxResults": 2500, "eventTypes": "default",
            "fields": "nextPageToken,timeZone,items(status,summary,start,organizer,attendees,attendeesOmitted)",
        }
        events = []
        refreshed = False
        for _ in range(20):
            response = requests.get(EVENTS_URL, headers={"Authorization": f"Bearer {token}"},
                                    params=params, timeout=15)
            if response.status_code == 401 and not refreshed:
                token = _access_token(db, user_id, connection, force_refresh=True)
                refreshed = True
                response = requests.get(EVENTS_URL, headers={"Authorization": f"Bearer {token}"},
                                        params=params, timeout=15)
            if response.status_code == 401:
                _needs_reauth(db, user_id)
            if response.status_code == 403:
                errors = (response.json().get("error") or {}).get("errors") or []
                if any(e.get("reason") == "insufficientPermissions" for e in errors):
                    _needs_reauth(db, user_id)
            response.raise_for_status()
            data = response.json()
            events.extend(data.get("items") or [])
            if not data.get("nextPageToken"):
                return {**result, "status": "connected", "meetings": match_meetings(
                    events, people, connection[5], now, data.get("timeZone") or "UTC",
                )}
            params["pageToken"] = data["nextPageToken"]
        # Resultado parcial não pode ser apresentado como ausência de agendamento.
        return result
    except CalendarUnavailable as exc:
        return {**result, "status": exc.status}
    except Exception as exc:
        log.warning("Consulta ao Google Agenda indisponível (%s)", type(exc).__name__)
        db.rollback()
        return result
