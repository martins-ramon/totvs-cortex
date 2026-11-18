"""Cards do time: geração assíncrona (batch orquestrado pelo frontend).

Fluxo:
1. POST /api/cards/generate-start  -> cria job, retorna {job_id, people[]}
2. POST /api/cards/person/<id>     -> gera o card de UMA pessoa (idempotente)
3. POST /api/cards/generate-finish -> fecha o job

Cada passo é uma requisição curta — robusto no Autoscale do Replit (sem
workers persistentes) e com progresso real na barra do frontend.
"""
import json
import logging
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, request, jsonify, session
from sqlalchemy import text

from ..database import session_factory
from .. import ai
from .. import gmail as gmail_svc
from ..security import login_required
from .. import knowledge
from ..knowledge_query import format_context, knowledge_revision, context_snapshot
from ..meetings_context import format_meeting_context
from .sessions import _collect_prep_data, _PERSON_FOR_PREP

bp = Blueprint("cards", __name__, url_prefix="/api")
log = logging.getLogger('cortex.cards')

_VALID_HEALTH = {"verde", "amarelo", "vermelho"}
_VALID_TREND = {"subindo", "estavel", "caindo"}

_SKIP_LABELS = {
    "sem_email": "sem e-mail cadastrado",
    "gmail_nao_conectado": "Gmail não conectado",
    "gmail_sem_permissao": "Gmail sem permissão de leitura — reconecte em Conexões",
    "gmail_bloqueado": "Gmail bloqueado pelo domínio ou API desabilitada",
    "erro": "falha ao ler a caixa de entrada",
}


def _set_job_progress(job_id, payload):
    """Grava progresso em transação própria para o poll do frontend ver na hora."""
    if not job_id:
        return
    db = session_factory()
    try:
        db.execute(text("""
            UPDATE card_jobs SET progress_json = CAST(:p AS jsonb) WHERE id = :jid
        """), {"p": json.dumps(payload), "jid": job_id})
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning('Falha ao registrar progresso de cards (%s).', type(e).__name__)
    finally:
        db.close()


def _build_context(prep, email_scan=None):
    """Monta o contexto textual para a IA a partir dos dados consolidados."""
    person = prep['person']
    lines = [f"Pessoa: {person['full_name']}"]
    if 'knowledge' in prep:
        lines.append(format_context(prep['knowledge']))
    if person.get('role_title'):
        lines.append(f"Cargo: {person['role_title']}")
    if person.get('email'):
        lines.append(f"E-mail: {person['email']}")
    st = prep['stats']
    lines.append(f"Dias desde o último 1:1: "
                 f"{st['days_since_last'] if st['days_since_last'] is not None else 'sem registro'}")
    lines.append(f"Combinados em aberto: {st['open_count']} (vencidos: {st['overdue_count']})")
    for commitment in prep.get('commitments', []):
        lines.append(f"Combinado commitment:{commitment['id']}: {commitment['description']} "
                     f"| responsável: {commitment['owner']} | prazo atual: {commitment.get('due_date') or 'a definir'}")
    if st.get('sentiment_trend'):
        lines.append(f"Tendência de sentimento nas conversas: {st['sentiment_trend']}")

    n_sessions = len(prep['recent_sessions'])
    lines.append(f"Registros de 1:1 considerados: {n_sessions}")
    group_text = format_meeting_context({"meetings": prep.get("group_meetings") or [],
                                         "actions": prep.get("meeting_actions") or []})
    if group_text:
        lines.append(f"Reuniões diversas associadas: {st.get('group_meetings_count', len(prep.get('group_meetings') or []))} "
                     f"(contexto recente: {len(prep.get('group_meetings') or [])}).")
        lines.append(f"Encaminhamentos de fóruns atribuídos em aberto: {st.get('open_meeting_actions', 0)} "
                     f"(vencidos: {st.get('overdue_meeting_actions', 0)}).")
        lines.append(group_text)

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

    scan = email_scan or {}
    if scan.get('skipped'):
        reason = _SKIP_LABELS.get(scan['skipped'], scan['skipped'])
        lines.append(f"\nE-mails: não analisados ({reason}).")
    elif scan.get('scanned') and not scan.get('threads'):
        email = person.get('email') or ''
        lines.append(f"\nE-MAILS: nenhuma thread recente envolvendo {email} (últimos 90 dias).")
    elif scan.get('threads'):
        email = person.get('email') or ''
        lines.append(f"\nE-MAILS ENVOLVENDO {email} "
                     f"(últimos 90 dias, {len(scan['threads'])} thread(s)):")
        for t in scan['threads']:
            lines.append(
                f"- {t.get('date') or '?'} | {t.get('subject') or '(sem assunto)'} "
                f"| de: {t.get('from') or '?'} | {t.get('message_count') or 1} msg"
            )
            snippet = t.get('snippet') or ''
            if snippet:
                lines.append(f"  Resumo: {snippet[:280]}")
            body = t.get('body') or ''
            if body and body != snippet:
                lines.append(f"  Trecho: {body[:700]}")
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
        "emails": raw.get('emails') if isinstance(raw.get('emails'), dict) else {},
    }


def _email_block(raw_ai, scan):
    """Metadados da varredura + extração da IA (só vale se houve scan)."""
    def _str_list(v):
        return [str(x).strip() for x in v if str(x).strip()][:10] if isinstance(v, list) else []

    extracted = raw_ai.get('emails') if isinstance(raw_ai, dict) else {}
    if not isinstance(extracted, dict):
        extracted = {}
    scanned = bool(scan.get('scanned'))
    return {
        "scanned": scanned,
        "skipped": scan.get('skipped'),
        "thread_count": int(scan.get('thread_count') or 0),
        "pendencias": _str_list(extracted.get('pendencias')) if scanned else [],
        "todos": _str_list(extracted.get('todos')) if scanned else [],
        "assuntos": _str_list(extracted.get('assuntos')) if scanned else [],
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
        cj = row[0] if isinstance(row[0], dict) else {}
        memory_stale = knowledge.enabled() and (
            cj.get('knowledge', {}).get('revision_token') != knowledge_revision(db)
            or cj.get('memory_stale', False))
        return jsonify({"card": {
            "card_json": cj,
            "generated_at": row[1].isoformat(),
            "valid_until": row[2].isoformat() if row[2] else None,
            "stale": bool(row[2] and row[2] < date.today()) or memory_stale,
            **({'knowledge_stale': memory_stale} if knowledge.enabled() else {}),
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
        revision = knowledge_revision(db) if knowledge.enabled() else None
        for pid, cj, gen, val in rows:
            cj = cj if isinstance(cj, dict) else {}
            memory_stale = knowledge.enabled() and (
                cj.get('knowledge', {}).get('revision_token') != revision or cj.get('memory_stale', False))
            cards[str(pid)] = {
                "card_json": cj,
                "generated_at": gen.isoformat(),
                "valid_until": val.isoformat() if val else None,
                "stale": bool(val and val < date.today()) or memory_stale,
                **({'knowledge_stale': memory_stale} if knowledge.enabled() else {}),
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
            SELECT id, full_name, email FROM people
            WHERE active = TRUE ORDER BY full_name
        """)).fetchall()
        result = db.execute(text("""
            INSERT INTO card_jobs (status, total, done) VALUES ('running', :total, 0)
            RETURNING id
        """), {"total": len(people)})
        job_id = result.fetchone()[0]
        db.commit()
        return jsonify({
            "job_id": job_id,
            "people": [{"id": p[0], "full_name": p[1], "email": p[2]} for p in people],
        })
    except Exception as e:
        db.rollback()
        log.warning('Falha ao iniciar cards (%s).', type(e).__name__)
        return jsonify(error='Não foi possível iniciar a geração dos cards.'), 500
    finally:
        db.close()


@bp.route('/cards/job/<int:job_id>', methods=['GET'])
@login_required
def job_status(job_id):
    db = session_factory()
    try:
        row = db.execute(text("""
            SELECT status, total, done, error, progress_json
            FROM card_jobs WHERE id = :jid
        """), {"jid": job_id}).fetchone()
        if not row:
            return jsonify({"error": "Job não encontrado"}), 404
        progress = row[4]
        if isinstance(progress, str):
            try:
                progress = json.loads(progress)
            except Exception:
                progress = {}
        if not isinstance(progress, dict):
            progress = {}
        return jsonify({
            "job_id": job_id,
            "status": row[0],
            "total": row[1],
            "done": row[2],
            "error": row[3],
            "progress": progress,
        })
    finally:
        db.close()


@bp.route('/cards/person/<int:person_id>', methods=['POST'])
@login_required
def generate_person_card(person_id):
    """Gera o card de uma pessoa e contabiliza progresso no job."""
    data = request.json or {}
    job_id = data.get('job_id')
    user_id = session.get('user_id')

    db = session_factory()
    try:
        person = db.execute(text(_PERSON_FOR_PREP), {"pid": person_id}).fetchone()
        if not person:
            return jsonify({"error": "Pessoa não encontrada"}), 404

        name = person[1]
        email = person[3]

        _set_job_progress(job_id, {
            "person_id": person_id,
            "person_name": name,
            "person_email": email,
            "step": "sessions",
            "label": "Avaliando registros de 1:1s e checkpoints",
            "detail": "",
            "steps_done": [],
        })

        prep = _collect_prep_data(db, person)
        outbox_through = db.execute(text("SELECT COALESCE(MAX(id),0) FROM knowledge_outbox WHERE person_id=:pid"),
                                    {'pid': person_id}).scalar() if knowledge.enabled() else None
        n_sessions = len(prep['recent_sessions'])
        n_cp = 1 if prep.get('last_checkpoint') else 0
        detail_sessions = (
            f"{n_sessions} 1:1(s)"
            + (f" e {n_cp} checkpoint" if n_cp else "")
            + " no histórico"
        )

        _set_job_progress(job_id, {
            "person_id": person_id,
            "person_name": name,
            "person_email": email,
            "step": "emails",
            "label": "Avaliando e-mails da caixa de entrada",
            "detail": (
                f"Buscando threads de {email}" if email
                else "Sem e-mail cadastrado — pulando caixa de entrada"
            ),
            "steps_done": ["sessions"],
        })

        email_scan = gmail_svc.scan_person_inbox(db, user_id, email, job_id=job_id)
        if email_scan.get("skipped"):
            email_detail = _SKIP_LABELS.get(email_scan["skipped"], email_scan["skipped"])
        else:
            n = email_scan.get("thread_count") or 0
            email_detail = (
                f"{n} thread(s) envolvendo {email}" if n
                else f"Nenhuma thread recente com {email}"
            )

        _set_job_progress(job_id, {
            "person_id": person_id,
            "person_name": name,
            "person_email": email,
            "step": "synthesize",
            "label": "Sintetizando o card com IA",
            "detail": f"{detail_sessions}. {email_detail}.",
            "steps_done": ["sessions", "emails"],
        })

        context = _build_context(prep, email_scan)

        metrics = {
            "dias_desde_ultimo_1on1": prep['stats']['days_since_last'],
            "combinados_abertos": prep['stats']['open_count'],
            "combinados_vencidos": prep['stats']['overdue_count'],
            "sentimento_trend": prep['stats']['sentiment_trend'],
        }

        raw = ai.generate_member_card(prep['person']['full_name'], context)
        analysis = _normalize_card(raw)
        analysis.pop('emails', None)

        card_json = {
            "metrics": metrics,
            "emails": _email_block(raw, email_scan),
            "ai": analysis,
        }
        if knowledge.enabled():
            memory = prep.get('knowledge') or {}
            token = memory.get('revision_token')
            if token is not None and token != knowledge_revision(db):
                db.rollback()
                return jsonify(error='A memória mudou durante a geração. Gere o card novamente.', conflict=True), 409
            if token is None:
                db.rollback()
                return jsonify(error='A memória está indisponível. Tente gerar o card novamente.'), 503
            card_json['knowledge'] = {
                'revision_token': token,
                'claim_ids': context_snapshot(memory)['used_claim_ids'],
                'coverage': memory.get('coverage', {}),
            }
        valid_until = date.today() + timedelta(days=7)

        revision_column = ', knowledge_revision' if knowledge.enabled() else ''
        revision_value = ', :knowledge_revision' if knowledge.enabled() else ''
        db.execute(text(f"""
            INSERT INTO member_cards (person_id, job_id, card_json, valid_until {revision_column})
            VALUES (:pid, :jid, CAST(:cj AS jsonb), :val {revision_value})
        """), {
            "pid": person_id,
            "jid": job_id,
            "cj": json.dumps(card_json),
            "val": valid_until,
            'knowledge_revision': card_json.get('knowledge', {}).get('revision_token'),
        })
        if outbox_through is not None:
            db.execute(text("""UPDATE knowledge_outbox SET status='done',updated_at=CURRENT_TIMESTAMP
                WHERE person_id=:pid AND status='pending' AND id<=:through"""),
                       {'pid': person_id, 'through': outbox_through})

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
                            {"e": f"Falha ao gerar card da pessoa {person_id}.", "jid": job_id})
                db2.commit()
                db2.close()
            except Exception:
                pass
        log.warning('Falha ao gerar card (%s).', type(e).__name__)
        return jsonify(error='Não foi possível gerar o card. Tente novamente.'), 500
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
