"""Atualização da memória em requisições duráveis, sem trabalho após a resposta."""

import os

from sqlalchemy import text

from . import knowledge
from .database import session_factory


_CURRENT_JOBS = """FROM knowledge_jobs j JOIN knowledge_sources s ON s.id=j.source_id
    JOIN knowledge_versions v ON v.id=j.version_id
    WHERE NOT s.deleted AND j.version_id=s.current_version_id
        AND (CAST(:kind AS TEXT) IS NULL OR (s.kind=:kind AND s.record_id=:record_id))"""
_LEASED = "j.status='running' AND COALESCE(j.lease_until>CURRENT_TIMESTAMP, FALSE)"
_EXHAUSTED = f"j.status IN ('error','running') AND j.attempts>=3 AND NOT ({_LEASED})"
_RUNNABLE = f"j.status IN ('pending','error','running') AND j.attempts<3 AND NOT ({_LEASED})"
_LEGACY = f"""j.metrics_json->>'extraction_revision' IS DISTINCT FROM :revision AND
    (({_EXHAUSTED}) OR (j.status='done' AND btrim(v.source_text,:source_space)<>'' AND NOT EXISTS
        (SELECT 1 FROM knowledge_claims c WHERE c.version_id=j.version_id)))"""
_WAITING = f"({_RUNNABLE}) OR ({_LEASED}) OR ({_LEGACY})"
_DISCOVER = """SELECT r.kind,r.id,r.occurred_on FROM (
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
             OR r.title IS DISTINCT FROM s.title)"""
_PROGRESS = f"""WITH discovered AS ({_DISCOVER}), queued AS (
    SELECT j.id,s.kind,s.record_id,s.title,j.status,j.updated_at,
        (j.cursor_json->>'segment')::integer AS cursor,
        (j.metrics_json->>'segments_total')::integer AS segments_total,
        j.metrics_json->>'sync_phase' AS phase,
        ({_LEASED}) AS leased, ({_WAITING}) AS waiting
    {_CURRENT_JOBS}
    AND (({_WAITING}) OR ({_EXHAUSTED}))
    AND NOT EXISTS (SELECT 1 FROM discovered d WHERE d.kind=s.kind AND d.id=s.record_id)
), counts AS (
    SELECT COUNT(*) FILTER (WHERE waiting) + (SELECT COUNT(*) FROM discovered) AS remaining,
        COUNT(*) FILTER (WHERE NOT waiting) AS failed,
        COALESCE(SUM(cursor::double precision / NULLIF(segments_total, 0)) FILTER (
            WHERE status<>'done' AND cursor>0 AND cursor<segments_total), 0) AS partial
    FROM queued
), active AS (
    SELECT kind,record_id,title,cursor,segments_total,phase FROM queued
    WHERE leased ORDER BY updated_at DESC,id DESC LIMIT 1
)
SELECT counts.*,active.* FROM counts LEFT JOIN active ON TRUE"""


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
    row = db.execute(text(_PROGRESS), _params(kind, record_id)).mappings().one()
    active = None
    if row['record_id'] is not None:
        phase = row['phase'] or 'extracting'
        total = row['segments_total'] or 0
        completed = min((row['cursor'] or 0) + (phase == 'publishing'), total)
        active = {'kind': row['kind'], 'id': row['record_id'], 'title': row['title'],
                  'phase': phase, 'segments_completed': completed, 'segments_total': total}
    pending = bool(row['remaining'])
    can_sync = _can_sync()
    return {'has_pending': pending, 'needs_attention': bool(row['failed']) or (pending and not can_sync),
            'can_sync': can_sync, 'sync_progress': {'remaining': row['remaining'], 'failed': row['failed'],
                                                  'partial': row['partial'], 'active': active}}


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
    source = db.execute(text(_DISCOVER + ' ORDER BY r.occurred_on DESC,r.id DESC,r.kind LIMIT 1'),
                        params).mappings().first()
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
