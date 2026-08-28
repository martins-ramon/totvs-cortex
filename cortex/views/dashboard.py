"""Dashboard da home: agregações do time para a visão de alta performance.

Tudo é derivado das tabelas existentes (one_on_ones, commitments, people)
via SQL — sem custo de IA. A saúde por pessoa (member_cards) já é servida
por /api/cards/latest e é combinada no frontend.
"""
from datetime import date, timedelta

from flask import Blueprint, jsonify
from sqlalchemy import text

from ..database import session_factory
from ..security import login_required

bp = Blueprint("dashboard", __name__, url_prefix="/api")

_WEEKS = 12          # janela dos gráficos semanais
_STALE_DAYS = 21     # mesmo threshold usado no restante do app
_TOPICS_DAYS = 60    # janela dos temas quentes
_QUAL_SESSIONS = 20  # sessões recentes vasculhadas p/ conquistas e atenção


def _week_start(d):
    """Segunda-feira da semana de `d`."""
    return d - timedelta(days=d.weekday())


def _week_series(today):
    """Lista das últimas _WEEKS segundas-feiras (ordem cronológica)."""
    current = _week_start(today)
    return [current - timedelta(weeks=i) for i in range(_WEEKS - 1, -1, -1)]


@bp.route('/dashboard', methods=['GET'])
@login_required
def dashboard():
    today = date.today()
    weeks = _week_series(today)
    window_start = weeks[0]

    db = session_factory()
    try:
        # --- pessoas ativas ---
        people_rows = db.execute(text("""
            SELECT id, full_name, preferred_name, role_title
            FROM people WHERE active = TRUE
            ORDER BY full_name
        """)).fetchall()
        active_ids = [r[0] for r in people_rows]

        # --- última 1:1 por pessoa (data + sentimento) ---
        last_rows = db.execute(text("""
            SELECT DISTINCT ON (person_id) person_id, occurred_on, sentiment
            FROM one_on_ones
            ORDER BY person_id, occurred_on DESC, id DESC
        """)).fetchall()
        last_by_person = {r[0]: {"occurred_on": r[1], "sentiment": r[2]} for r in last_rows}

        # --- combinados abertos/vencidos por pessoa ---
        comm_rows = db.execute(text("""
            SELECT person_id,
                   COUNT(*) FILTER (WHERE status = 'open') AS open_count,
                   COUNT(*) FILTER (WHERE status = 'open'
                                    AND due_date IS NOT NULL
                                    AND due_date < :today) AS overdue_count
            FROM commitments
            GROUP BY person_id
        """), {"today": today}).fetchall()
        comm_by_person = {r[0]: {"open": r[1], "overdue": r[2]} for r in comm_rows}

        # --- sessões nos últimos 90 dias por pessoa (frequência) ---
        freq_rows = db.execute(text("""
            SELECT person_id, COUNT(*) FROM one_on_ones
            WHERE occurred_on >= :since GROUP BY person_id
        """), {"since": today - timedelta(days=90)}).fetchall()
        freq_by_person = {r[0]: r[1] for r in freq_rows}

        # --- KPIs de sessões ---
        sessions_30d = db.execute(text("""
            SELECT COUNT(*) FROM one_on_ones WHERE occurred_on >= :since
        """), {"since": today - timedelta(days=30)}).scalar() or 0

        # --- KPIs de combinados ---
        kpi_comm = db.execute(text("""
            SELECT COUNT(*) FILTER (WHERE status = 'open') AS open_all,
                   COUNT(*) FILTER (WHERE status = 'open'
                                    AND due_date IS NOT NULL
                                    AND due_date < :today) AS overdue_all,
                   COUNT(*) FILTER (WHERE status = 'done'
                                    AND closed_at >= :since30) AS done_30d
            FROM commitments
        """), {"today": today, "since30": today - timedelta(days=30)}).fetchone()
        open_all, overdue_all, done_30d = kpi_comm[0] or 0, kpi_comm[1] or 0, kpi_comm[2] or 0
        denom = done_30d + overdue_all
        completion_rate_30d = round(done_30d * 100 / denom) if denom else None

        # --- série semanal: sentimento + cadência de 1:1s ---
        weekly_rows = db.execute(text("""
            SELECT date_trunc('week', occurred_on)::date AS wk,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE sentiment = 'positivo') AS positivo,
                   COUNT(*) FILTER (WHERE sentiment = 'neutro') AS neutro,
                   COUNT(*) FILTER (WHERE sentiment = 'preocupante') AS preocupante
            FROM one_on_ones
            WHERE occurred_on >= :since
            GROUP BY wk
        """), {"since": window_start}).fetchall()
        weekly_map = {r[0]: r for r in weekly_rows}

        sentiment_weekly, sessions_weekly = [], []
        for wk in weeks:
            r = weekly_map.get(wk)
            sentiment_weekly.append({
                "week_start": wk.isoformat(),
                "positivo": r[2] if r else 0,
                "neutro": r[3] if r else 0,
                "preocupante": r[4] if r else 0,
            })
            sessions_weekly.append({"week_start": wk.isoformat(),
                                    "count": r[1] if r else 0})

        # --- série semanal: fluxo de combinados (criados x concluídos) ---
        created_rows = db.execute(text("""
            SELECT date_trunc('week', created_at)::date AS wk, COUNT(*)
            FROM commitments WHERE created_at >= :since GROUP BY wk
        """), {"since": window_start}).fetchall()
        closed_rows = db.execute(text("""
            SELECT date_trunc('week', closed_at)::date AS wk, COUNT(*)
            FROM commitments
            WHERE status = 'done' AND closed_at IS NOT NULL AND closed_at >= :since
            GROUP BY wk
        """), {"since": window_start}).fetchall()
        created_map = {r[0]: r[1] for r in created_rows}
        closed_map = {r[0]: r[1] for r in closed_rows}
        commitments_weekly = [{
            "week_start": wk.isoformat(),
            "created": created_map.get(wk, 0),
            "closed": closed_map.get(wk, 0),
        } for wk in weeks]

        # --- pulse por pessoa ---
        people_pulse = []
        stale_people = 0
        covered = 0
        for pid, full_name, preferred_name, role_title in people_rows:
            last = last_by_person.get(pid)
            days_since = (today - last["occurred_on"]).days if last else None
            if days_since is not None and days_since <= _STALE_DAYS:
                covered += 1
            else:
                stale_people += 1
            comm = comm_by_person.get(pid, {"open": 0, "overdue": 0})
            people_pulse.append({
                "person_id": pid,
                "name": full_name,
                "preferred_name": preferred_name,
                "role_title": role_title,
                "days_since_last": days_since,
                "last_occurred_on": last["occurred_on"].isoformat() if last else None,
                "last_sentiment": last["sentiment"] if last else None,
                "open_commitments": comm["open"],
                "overdue_commitments": comm["overdue"],
                "sessions_90d": freq_by_person.get(pid, 0),
            })
        coverage_21d = (round(covered * 100 / len(active_ids))
                        if active_ids else None)

        # --- temas quentes (topics JSONB, últimos _TOPICS_DAYS dias) ---
        topic_rows = db.execute(text("""
            SELECT topics FROM one_on_ones
            WHERE occurred_on >= :since AND topics IS NOT NULL
        """), {"since": today - timedelta(days=_TOPICS_DAYS)}).fetchall()
        topic_counts = {}
        for (topics,) in topic_rows:
            if not isinstance(topics, list):
                continue
            for t in topics:
                label = str(t).strip()
                if not label:
                    continue
                key = label.lower()
                if key in topic_counts:
                    topic_counts[key]["count"] += 1
                else:
                    topic_counts[key] = {"topic": label, "count": 1}
        top_topics = sorted(topic_counts.values(),
                            key=lambda x: (-x["count"], x["topic"]))[:10]

        # --- conquistas e pontos de atenção recentes (extraction_json) ---
        qual_rows = db.execute(text("""
            SELECT person_id, occurred_on, extraction_json
            FROM one_on_ones
            WHERE extraction_json IS NOT NULL
            ORDER BY occurred_on DESC, id DESC
            LIMIT :lim
        """), {"lim": _QUAL_SESSIONS}).fetchall()
        recent_wins, attention_points = [], []
        for pid, occurred_on, extraction in qual_rows:
            if not isinstance(extraction, dict):
                continue
            for item in (extraction.get("conquistas") or [])[:3]:
                txt = str(item).strip()
                if txt and len(recent_wins) < 8:
                    recent_wins.append({"person_id": pid,
                                        "occurred_on": occurred_on.isoformat(),
                                        "text": txt})
            for item in (extraction.get("pontos_atencao") or [])[:3]:
                txt = str(item).strip()
                if txt and len(attention_points) < 8:
                    attention_points.append({"person_id": pid,
                                             "occurred_on": occurred_on.isoformat(),
                                             "text": txt})

        return jsonify({
            "kpis": {
                "active_people": len(active_ids),
                "coverage_21d": coverage_21d,
                "stale_people": stale_people,
                "sessions_30d": sessions_30d,
                "open_commitments": open_all,
                "overdue_commitments": overdue_all,
                "done_30d": done_30d,
                "completion_rate_30d": completion_rate_30d,
            },
            "people_pulse": people_pulse,
            "sentiment_weekly": sentiment_weekly,
            "sessions_weekly": sessions_weekly,
            "commitments_weekly": commitments_weekly,
            "top_topics": top_topics,
            "recent_wins": recent_wins,
            "attention_points": attention_points,
        })
    finally:
        db.close()
