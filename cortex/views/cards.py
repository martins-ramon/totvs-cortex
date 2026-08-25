"""Cards do time: geração assíncrona (batch orquestrado pelo frontend).

Fluxo:
1. POST /api/cards/generate-start  -> cria job, retorna {job_id, people[]}
2. POST /api/cards/person/<id>     -> gera o card de UMA pessoa (idempotente)
3. POST /api/cards/generate-finish -> fecha o job

Cada passo é uma requisição curta — robusto no Autoscale do Replit (sem
workers persistentes) e com progresso real na barra do frontend.
"""
import json
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, request, jsonify
from sqlalchemy import text

from ..database import session_factory
from .. import ai
from ..security import login_required
from .sessions import _collect_prep_data, _PERSON_FOR_PREP

bp = Blueprint("cards", __name__, url_prefix="/api")

_VALID_HEALTH = {"verde", "amarelo", "vermelho"}
_VALID_TREND = {"subindo", "estavel", "caindo"}


def _build_context(prep):
    """Monta o contexto textual para a IA a partir dos dados consolidados."""
    lines = [f"Pessoa: {prep['person']['full_name']}"]
    if prep['person'].get('role_title'):
        lines.append(f"Cargo: {prep['person']['role_title']}")
    st = prep['stats']
    lines.append(f"Dias desde o último 1:1: "
                 f"{st['days_since_last'] if st['days_since_last'] is not None else 'sem registro'}")
    lines.append(f"Combinados em aberto: {st['open_count']} (vencidos: {st['overdue_count']})")
    if st.get('sentiment_trend'):
        lines.append(f"Tendência de sentimento nas conversas: {st['sentiment_trend']}")

    for s in prep['recent_sessions'][:6]:
        lines.append(f"\n1:1 de {s['occurred_on']} ({s['title'] or 'sem título'}) "
                     f"— sentimento: {s['sentiment'] or 'n/a'}")
        if s['summary_ai']:
            lines.append(s['summary_ai'])
        ext = s.get('extraction') or {}
        for key, label in (('pontos_atencao', 'Atenção'),
                           ('pontos_desenvolvimento', 'Desenvolvimento'),
                           ('conquistas', 'Conquistas')):
            vals = ext.get(key) or []
            if vals:
                lines.append(f"{label}: " + "; ".join(vals))

    cp = prep.get('last_checkpoint')
    if cp:
        lines.append(f"\nÚltimo checkpoint ({cp['checkpoint_date']}):")
        for a in cp['actions']:
            lines.append(f"- {a.get('acao')} (resp.: {a.get('responsavel')})")
        if cp['private_notes']:
            lines.append(f"Notas privadas: {cp['private_notes']}")
    return "\n".join(lines)


def _normalize_card(raw):
    """Defende o JSON da IA contra formatos inesperados."""
    if not isinstance(raw, dict):
        raise ValueError("Resposta da IA não é um objeto válido.")
    saude = str(raw.get('saude') or '').strip().lower()
    if saude not in _VALID_HEALTH:
        saude = 'amarelo'
    tendencia = str(raw.get('tendencia') or '').strip().lower()
    if tendencia not in _VALID_TREND:
        tendencia = 'estavel'

    riscos = []
    for r in raw.get('riscos') or []:
        if isinstance(r, dict) and str(r.get('descricao') or '').strip():
            nivel = str(r.get('nivel') or 'baixo').strip().lower()
            riscos.append({
                "nivel": nivel if nivel in ('baixo', 'medio', 'alto') else 'baixo',
                "descricao": str(r['descricao']).strip(),
            })
    riscos.sort(key=lambda x: {'alto': 0, 'medio': 1, 'baixo': 2}[x['nivel']])

    def _str_list(v):
        return [str(x).strip() for x in v if str(x).strip()][:10] if isinstance(v, list) else []

    return {
        "saude": saude,
        "tendencia": tendencia,
        "resumo": str(raw.get('resumo') or '').strip(),
        "pontos_atencao": _str_list(raw.get('pontos_atencao')),
        "desenvolvimento": _str_list(raw.get('desenvolvimento')),
        "conquistas": _str_list(raw.get('conquistas')),
        "riscos": riscos,
        "foco_sugerido": str(raw.get('foco_sugerido') or '').strip(),
    }


@bp.route('/people/<int:person_id>/card', methods=['GET'])
@login_required
def latest_person_card(person_id):
    db = session_factory()
    try:
        row = db.execute(text("""
            SELECT card_json, generated_at, valid_until
            FROM member_cards WHERE person_id = :pid
            ORDER BY generated_at DESC LIMIT 1
        """), {"pid": person_id}).fetchone()
        if not row:
            return jsonify({"card": None})
        return jsonify({"card": {
            "card_json": row[0] if isinstance(row[0], dict) else {},
            "generated_at": row[1].isoformat(),
            "valid_until": row[2].isoformat() if row[2] else None,
            "stale": bool(row[2] and row[2] < date.today()),
        }})
    finally:
        db.close()


@bp.route('/cards/latest', methods=['GET'])
@login_required
def all_latest_cards():
    """Último card de cada pessoa (para o grid da view Cards)."""
    db = session_factory()
    try:
        rows = db.execute(text("""
            SELECT DISTINCT ON (person_id)
                   person_id, card_json, generated_at, valid_until
            FROM member_cards
            ORDER BY person_id, generated_at DESC
        """)).fetchall()
        cards = {}
        for pid, cj, gen, val in rows:
            cards[str(pid)] = {
                "card_json": cj if isinstance(cj, dict) else {},
                "generated_at": gen.isoformat(),
                "valid_until": val.isoformat() if val else None,
                "stale": bool(val and val < date.today()),
            }
        return jsonify({"cards": cards})
    finally:
        db.close()


@bp.route('/cards/generate-start', methods=['POST'])
@login_required
def generate_start():
    """Abre o job e devolve a fila de pessoas ativas."""
    db = session_factory()
    try:
        # Fecha jobs 'running' órfãos (ex.: aba fechada no meio da geração)
        db.execute(text("""
            UPDATE card_jobs SET status = 'error',
                   error = COALESCE(error, 'interrompido pelo usuário'),
                   finished_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
        """))
        people = db.execute(text("""
            SELECT id, full_name FROM people
            WHERE active = TRUE ORDER BY full_name
        """)).fetchall()
        result = db.execute(text("""
            INSERT INTO card_jobs (status, total, done) VALUES ('running', :total, 0)
            RETURNING id
        """), {"total": len(people)})
        db.commit()
        job_id = result.fetchone()[0]
        return jsonify({
            "job_id": job_id,
            "people": [{"id": p[0], "full_name": p[1]} for p in people],
        })
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@bp.route('/cards/person/<int:person_id>', methods=['POST'])
@login_required
def generate_person_card(person_id):
    """Gera o card de uma pessoa e contabiliza progresso no job."""
    data = request.json or {}
    job_id = data.get('job_id')

    db = session_factory()
    try:
        person = db.execute(text(_PERSON_FOR_PREP), {"pid": person_id}).fetchone()
        if not person:
            return jsonify({"error": "Pessoa não encontrada"}), 404

        prep = _collect_prep_data(db, person)
        context = _build_context(prep)

        metrics = {
            "dias_desde_ultimo_1on1": prep['stats']['days_since_last'],
            "combinados_abertos": prep['stats']['open_count'],
            "combinados_vencidos": prep['stats']['overdue_count'],
            "sentimento_trend": prep['stats']['sentiment_trend'],
        }

        raw = ai.generate_member_card(prep['person']['full_name'], context)
        analysis = _normalize_card(raw)

        card_json = {
            "metrics": metrics,
            "ai": analysis,
        }
        valid_until = date.today() + timedelta(days=7)

        db.execute(text("""
            INSERT INTO member_cards (person_id, job_id, card_json, valid_until)
            VALUES (:pid, :jid, CAST(:cj AS jsonb), :val)
        """), {
            "pid": person_id,
            "jid": job_id,
            "cj": json.dumps(card_json),
            "val": valid_until,
        })

        if job_id:
            db.execute(text("""
                UPDATE card_jobs SET done = done + 1 WHERE id = :jid
            """), {"jid": job_id})
        db.commit()

        return jsonify({
            "success": True,
            "person_id": person_id,
            "card": {
                "card_json": card_json,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "valid_until": valid_until.isoformat(),
                "stale": False,
            },
        })
    except Exception as e:
        db.rollback()
        if job_id:
            try:
                db2 = session_factory()
                db2.execute(text("UPDATE card_jobs SET error = :e WHERE id = :jid"),
                            {"e": f"erro ao gerar card da pessoa {person_id}: {e}", "jid": job_id})
                db2.commit()
                db2.close()
            except Exception:
                pass
        print(f"Erro ao gerar card da pessoa {person_id}: {e}")
        return jsonify({"error": f"Falha ao gerar card: {e}"}), 500
    finally:
        db.close()


@bp.route('/cards/generate-finish', methods=['POST'])
@login_required
def generate_finish():
    data = request.json or {}
    job_id = data.get('job_id')
    if not job_id:
        return jsonify({"error": "job_id é obrigatório"}), 400

    db = session_factory()
    try:
        row = db.execute(text("SELECT total, done, error FROM card_jobs WHERE id = :jid"),
                         {"jid": job_id}).fetchone()
        if not row:
            return jsonify({"error": "Job não encontrado"}), 404

        had_errors = bool(row[2])
        db.execute(text("""
            UPDATE card_jobs SET status = :status, finished_at = CURRENT_TIMESTAMP,
                   error = CASE WHEN :has_err THEN error ELSE NULL END
            WHERE id = :jid
        """), {"status": 'error' if had_errors else 'done', "has_err": had_errors, "jid": job_id})
        db.commit()
        return jsonify({"success": True, "total": row[0], "done": row[1],
                        "had_errors": had_errors})
    finally:
        db.close()
