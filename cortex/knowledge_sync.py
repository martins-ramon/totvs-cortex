"""Atualização da memória em requisições duráveis, sem trabalho após a resposta."""

import os

from sqlalchemy import text

from . import knowledge
from .database import session_factory


_CURRENT_JOBS = """FROM knowledge_jobs j JOIN knowledge_sources s ON s.id=j.source_id
    JOIN knowledge_versions v ON v.id=j.version_id
    WHERE NOT s.deleted AND j.version_id=s.current_version_id
        AND (CAST(:kind AS TEXT) IS NULL OR (s.kind=:kind AND s.record_id=:record_id))"""
_LEASED = "j.status='running' AND j.lease_until>CURRENT_TIMESTAMP"
_EXHAUSTED = f"j.status IN ('error','running') AND j.attempts>=3 AND NOT COALESCE(({_LEASED}), FALSE)"
_RUNNABLE = f"""j.status IN ('pending','error','running') AND j.attempts<3
    AND NOT COALESCE(({_LEASED}), FALSE)"""
_LEGACY = f"""j.metrics_json->>'extraction_revision' IS DISTINCT FROM :revision AND
    (({_EXHAUSTED}) OR (j.status='done' AND btrim(v.source_text,:source_space)<>'' AND NOT EXISTS
        (SELECT 1 FROM knowledge_claims c WHERE c.version_id=j.version_id)))"""
_DISCOVER = """SELECT r.kind,r.id FROM (
    SELECT 'one_on_one' AS kind,id,occurred_on,COALESCE(NULLIF(title,''),'1:1') AS title,
        concat_ws(CHR(10) || CHR(10),NULLIF(transcript_raw,''),NULLIF(private_notes,''),NULLIF(public_notes,'')) AS body
        FROM one_on_ones WHERE CAST(:kind AS TEXT) IS NULL OR (:kind='one_on_one' AND id=:record_id)
    UNION ALL
    SELECT 'meeting',id,occurred_on,COALESCE(NULLIF(title,''),'1:1'),
        concat_ws(CHR(10) || CHR(10),NULLIF(transcript_raw,''),NULLIF(notes,''))
        FROM meetings WHERE CAST(:kind AS TEXT) IS NULL OR (:kind='meeting' AND id=:record_id)
    ) r LEFT JOIN knowledge_sources s ON s.kind=r.kind AND s.record_id=r.id AND NOT s.deleted
    LEFT JOIN knowledge_versions v ON v.id=s.current_version_id
    WHERE (s.id IS NOT NULL OR btrim(r.body,:source_space)<>'')
        AND ((CASE WHEN btrim(r.body,:source_space)<>'' THEN r.body ELSE '' END) IS DISTINCT FROM v.source_text
             OR r.occurred_on IS DISTINCT FROM v.occurred_on
             OR r.title IS DISTINCT FROM s.title)
    ORDER BY r.occurred_on DESC,r.id DESC,r.kind
    LIMIT 1"""


def _params(kind, record_id):
    return {'kind': kind, 'record_id': record_id, 'revision': knowledge.EXTRACTION_REVISION,
            'source_space': knowledge.SOURCE_WHITESPACE}


def _can_sync():
    return bool(os.environ.get('OPENAI_API_KEY', '').strip())


def source_update(job):
    if job and job['status'] in {'pending', 'running', 'error'} and _can_sync():
        return {'knowledge_pending': True}
    return {}


def sync_status(db, kind=None, record_id=None):
    params = _params(kind, record_id)
    flags = db.execute(text(f"""SELECT
        COALESCE(BOOL_OR(({_RUNNABLE}) OR ({_LEASED}) OR ({_LEGACY})), FALSE) AS waiting,
        COALESCE(BOOL_OR(({_EXHAUSTED}) AND NOT ({_LEGACY})), FALSE) AS failed
        {_CURRENT_JOBS}"""), params).mappings().one()
    pending = flags['waiting'] or db.execute(text(_DISCOVER), params).first() is not None
    can_sync = _can_sync()
    return {'has_pending': pending, 'needs_attention': flags['failed'] or (pending and not can_sync),
            'can_sync': can_sync}


def _reopen(db, condition, params, user_id, reason):
    jobs = db.execute(text(f"""SELECT j.id,j.status,j.attempts {_CURRENT_JOBS}
        AND ({condition}) ORDER BY j.updated_at,j.id FOR UPDATE OF j"""), params).mappings().all()
    for job in jobs:
        db.execute(text("""UPDATE knowledge_jobs
            SET status='pending',attempts=0,error=NULL,lease_token=NULL,lease_until=NULL,
                cursor_json=CASE WHEN status='done' THEN '{}'::jsonb ELSE cursor_json END,
                staged_json=CASE WHEN status='done' THEN '[]'::jsonb ELSE staged_json END,
                metrics_json=(CASE WHEN status='done' THEN '{}'::jsonb ELSE metrics_json END)
                    || jsonb_build_object('extraction_revision',CAST(:revision AS TEXT)),
                updated_at=CURRENT_TIMESTAMP WHERE id=:id"""), {'id': job['id'], 'revision': params['revision']})
        knowledge.audit(db, 'job', job['id'], {'status': job['status'], 'attempts': job['attempts']},
                        {'status': 'pending', 'attempts': 0}, user_id, reason)


def _next_job(db, params, user_id, retry):
    # Serializa a descoberta apenas durante a transação. A extração tem seu próprio lease.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('cortex.knowledge.sync'))"))
    if retry:
        _reopen(db, _EXHAUSTED, params, user_id, 'Atualização da memória solicitada')
    source = db.execute(text(_DISCOVER), params).mappings().first()
    if source is not None:
        # A fonte pode ter sido excluída entre a descoberta e a obtenção do lock.
        if knowledge._source_record(db, source['kind'], source['id'], lock=True) is not None:
            job = knowledge.enqueue_source(db, source['kind'], source['id'], user_id)
            if job is not None and job['status'] != 'done':
                return job['id'], True
        return None, True
    job_id = db.execute(text(f"""SELECT j.id {_CURRENT_JOBS}
        AND ({_RUNNABLE}) ORDER BY j.updated_at,j.id LIMIT 1"""), params).scalar()
    if job_id is not None:
        return job_id, False
    legacy = db.execute(text(f"""SELECT j.id {_CURRENT_JOBS}
        AND ({_LEGACY}) ORDER BY j.updated_at,j.id LIMIT 1"""), params).scalar()
    if legacy is not None:
        _reopen(db, f'j.id=:id AND ({_LEGACY})', {**params, 'id': legacy}, user_id, 'Atualização do extrator da memória')
    return legacy, False


def sync_step(user_id, *, retry=False, kind=None, record_id=None):
    """Descobre histórico ou avança uma etapa, com no máximo uma chamada de IA."""
    job_id = None
    updated = False
    if _can_sync():
        with session_factory() as db:
            job_id, updated = _next_job(db, _params(kind, record_id), user_id, retry)
            db.commit()
    result = knowledge.process_job(job_id, user_id) if job_id is not None else None
    with session_factory() as db:
        state = sync_status(db, kind, record_id)
    if state['has_pending'] and (not result or result['status'] in {'error', 'running'}):
        state['retry_after_ms'] = 4000 if not result or result['status'] == 'running' else 1500
    state['updated'] = updated or bool(result and result['status'] == 'done')
    return state
