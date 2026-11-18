"""Memória canônica e processamento durável em etapas HTTP curtas.

Nenhum trabalho é abandonado após a resposta. O cursor e os candidatos de cada
segmento ficam no Postgres; só a publicação final altera o conhecimento ativo.
"""
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import text

from .database import session_factory
from .ai.knowledge_service import (CLASSIFICATIONS, RELATIONS, REVIEWS, extract_candidates,
                                   fold, segments)

log = logging.getLogger('cortex.knowledge')
SOURCE_TABLES = {'one_on_one': 'one_on_ones', 'meeting': 'meetings'}
TASK_TABLES = {'commitment': 'commitments', 'meeting_action': 'meeting_actions'}


def enabled():
    return os.environ.get('KNOWLEDGE_ENABLED', 'false').lower() in {'1', 'true', 'yes'}


def json_default(value):
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


def row_json(value):
    return json.loads(json.dumps(dict(value), ensure_ascii=False, default=json_default))


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=json_default)


def audit(db, record_type, record_id, before, after, user_id=None, reason=None, origin='human'):
    db.execute(text('''INSERT INTO knowledge_revisions
        (record_type,record_id,previous_version,actor_user_id,origin,before_json,after_json,reason)
        VALUES (:type,:id,:version,:uid,:origin,CAST(:before AS JSONB),CAST(:after AS JSONB),:reason)'''),
        {'type': record_type, 'id': record_id, 'version': (before or {}).get('version', (before or {}).get('knowledge_version')),
         'uid': user_id, 'origin': origin, 'before': _json(before), 'after': _json(after), 'reason': reason})


def sync_people(db):
    """Uma entidade por ID de people, independentemente de homônimos."""
    people = db.execute(text('''SELECT id,full_name,preferred_name,transcript_aliases FROM people ORDER BY id''')).mappings().all()
    for person in people:
        aliases = [person.get('preferred_name'), *(person.get('transcript_aliases') or []), person['full_name'].split()[0]]
        aliases = list(dict.fromkeys(a for a in aliases if isinstance(a, str) and a.strip()))
        db.execute(text('''INSERT INTO knowledge_entities (entity_key,kind,label,person_id,aliases)
            VALUES (:key,'person',:label,:pid,CAST(:aliases AS JSONB))
            ON CONFLICT (entity_key) DO UPDATE SET label=EXCLUDED.label,
            aliases=EXCLUDED.aliases,version=knowledge_entities.version+1,updated_at=CURRENT_TIMESTAMP
            WHERE knowledge_entities.label IS DISTINCT FROM EXCLUDED.label
               OR knowledge_entities.aliases IS DISTINCT FROM EXCLUDED.aliases'''),
            {'key': f"person:{person['id']}", 'label': person['full_name'], 'pid': person['id'], 'aliases': _json(aliases)})
    return [dict(r) for r in db.execute(text('SELECT * FROM knowledge_entities ORDER BY id')).mappings().all()]


def invalidate_derived(db, person_ids=None):
    """Outbox persistente: cards são reconstruídos pelo fluxo já existente."""
    ids = person_ids
    if ids is None:
        ids = [r[0] for r in db.execute(text('SELECT id FROM people')).fetchall()]
    token = uuid.uuid4().hex
    for pid in set(ids):
        db.execute(text('''INSERT INTO knowledge_outbox (kind,person_id,revision_token)
            VALUES ('cards',:pid,:token)'''), {'pid': pid, 'token': token})
    return token


def _source_record(db, kind, record_id, lock=False):
    table = SOURCE_TABLES.get(kind)
    if table is None or type(record_id) is not int or record_id <= 0:
        raise ValueError('Fonte inválida.')
    return db.execute(text(f'SELECT * FROM {table} WHERE id=:id' + (' FOR UPDATE' if lock else '')),
                      {'id': record_id}).mappings().first()


def _source_text(kind, record):
    # Não inclui summaries/cards: projeções não são evidência independente.
    if kind == 'one_on_one':
        fields = [('Transcrição', 'transcript_raw'), ('Notas privadas', 'private_notes'), ('Notas públicas', 'public_notes')]
    else:
        fields = [('Transcrição', 'transcript_raw'), ('Notas', 'notes')]
    pieces = [str(record.get(field) or '') for _, field in fields if record.get(field)]
    # Sem cabeçalhos artificiais quando há somente transcrição: offsets são os originais.
    return '\n\n'.join(pieces)


def enqueue_source(db, kind, record_id, user_id=None):
    """Versão+job persistidos na MESMA transação da criação/edição da fonte."""
    if not enabled():
        return None
    record = _source_record(db, kind, record_id, lock=True)
    if record is None:
        raise ValueError('Fonte não encontrada.')
    source_text = _source_text(kind, record)
    fingerprint = hashlib.sha256(_json({'text': source_text, 'occurred_on': record['occurred_on']}).encode()).hexdigest()
    db.execute(text('''INSERT INTO knowledge_sources (kind,record_id,title)
        VALUES (:kind,:id,:title) ON CONFLICT(kind,record_id) DO NOTHING'''),
        {'kind': kind, 'id': record_id, 'title': record.get('title') or '1:1'})
    source = dict(db.execute(text('SELECT * FROM knowledge_sources WHERE kind=:kind AND record_id=:id FOR UPDATE'),
                            {'kind': kind, 'id': record_id}).mappings().first())
    version_id = db.execute(text('''INSERT INTO knowledge_versions
        (source_id,fingerprint,source_text,occurred_on) VALUES (:sid,:fingerprint,:body,:date)
        ON CONFLICT(source_id,fingerprint) DO UPDATE SET fingerprint=EXCLUDED.fingerprint RETURNING id'''),
        {'sid': source['id'], 'fingerprint': fingerprint, 'body': source_text, 'date': record['occurred_on']}).scalar()
    changed = source['current_version_id'] != version_id or source['deleted']
    db.execute(text('''UPDATE knowledge_sources SET current_version_id=:vid,title=:title,deleted=FALSE,
        updated_at=CURRENT_TIMESTAMP WHERE id=:sid'''),
        {'vid': version_id, 'sid': source['id'], 'title': record.get('title') or '1:1'})
    if changed:
        db.execute(text('''UPDATE knowledge_claims SET active=FALSE WHERE source_id=:sid AND origin='automatic' ''') , {'sid': source['id']})
        db.execute(text('''UPDATE knowledge_relations SET active=FALSE WHERE claim_id IN
            (SELECT id FROM knowledge_claims WHERE source_id=:sid) AND origin='automatic' '''), {'sid': source['id']})
        db.execute(text('''UPDATE knowledge_jobs SET status='superseded',lease_token=NULL,lease_until=NULL,
            staged_json='[]'::jsonb WHERE source_id=:sid AND version_id<>:vid AND status<>'done' '''),
            {'sid': source['id'], 'vid': version_id})
        invalidate_derived(db)
    job = db.execute(text('''INSERT INTO knowledge_jobs(source_id,version_id,status)
        VALUES (:sid,:vid,'pending') ON CONFLICT(version_id) DO UPDATE SET
        status=CASE WHEN :changed THEN 'pending' ELSE knowledge_jobs.status END,
        cursor_json=CASE WHEN :changed THEN '{}'::jsonb ELSE knowledge_jobs.cursor_json END,
        staged_json=CASE WHEN :changed THEN '[]'::jsonb ELSE knowledge_jobs.staged_json END,
        attempts=CASE WHEN :changed THEN 0 ELSE knowledge_jobs.attempts END
        RETURNING id,status'''), {'sid': source['id'], 'vid': version_id, 'changed': changed}).mappings().first()
    return dict(job)


def _cancel_unsupported_tasks(db, claims):
    for claim in claims:
        for kind, field in (('commitment', 'commitment_id'), ('meeting_action', 'meeting_action_id')):
            if not claim.get(field):
                continue
            table = TASK_TABLES[kind]
            supported = db.execute(text(f'''SELECT 1 FROM knowledge_claims c LEFT JOIN knowledge_sources s ON s.id=c.source_id
                WHERE c.{field}=:id AND c.active AND c.review_state<>'rejected'
                  AND (c.source_id IS NULL OR NOT s.deleted) LIMIT 1'''), {'id': claim[field]}).first()
            if not supported:
                removed = db.execute(text(f'''UPDATE {table} SET status='cancelled',closed_at=CURRENT_TIMESTAMP,
                    description='Registro removido com a fonte',due_date=NULL,
                    knowledge_version=knowledge_version+1,updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id AND knowledge_created AND knowledge_human_fields='{{}}'::jsonb RETURNING id'''),
                    {'id': claim[field]}).scalar()
                if removed:
                    db.execute(text('''DELETE FROM knowledge_revisions WHERE record_type=:kind
                        AND record_id=:id AND origin IN ('automatic','system') '''), {'kind': kind, 'id': removed})


def remove_source(db, kind, record_id):
    """Tombstone bloqueia jobs antigos; exclusão elimina snapshots e evidências."""
    if not enabled():
        return
    source = db.execute(text('SELECT * FROM knowledge_sources WHERE kind=:kind AND record_id=:id FOR UPDATE'),
                        {'kind': kind, 'id': record_id}).mappings().first()
    if source is None:
        return
    claims = [dict(r) for r in db.execute(text('SELECT * FROM knowledge_claims WHERE source_id=:id'), {'id': source['id']}).mappings().all()]
    db.execute(text('''UPDATE knowledge_sources SET deleted=TRUE,current_version_id=NULL,title='Fonte removida',
        updated_at=CURRENT_TIMESTAMP WHERE id=:id'''), {'id': source['id']})
    db.execute(text("UPDATE knowledge_claims SET active=FALSE WHERE source_id=:id"), {'id': source['id']})
    if kind == 'meeting':
        db.execute(text('SELECT cortex_knowledge_preserve_actions(:sid,:mid)'), {'sid': source['id'], 'mid': record_id})
        claims = [dict(r) for r in db.execute(text('SELECT * FROM knowledge_claims WHERE source_id=:sid'), {'sid': source['id']}).mappings().all()]
    db.execute(text('SELECT cortex_knowledge_flag_removed_support(:sid)'), {'sid': source['id']})
    _cancel_unsupported_tasks(db, claims)
    db.execute(text('DELETE FROM member_cards'))
    # Auditoria não pode reter cópias de uma fonte apagada.
    db.execute(text('''DELETE FROM knowledge_revisions WHERE
        (record_type IN ('claim','knowledge_claim') AND record_id IN (SELECT id FROM knowledge_claims WHERE source_id=:sid))
        OR (record_type IN ('relation','knowledge_relation') AND record_id IN (SELECT r.id FROM knowledge_relations r
            JOIN knowledge_claims c ON c.id=r.claim_id WHERE c.source_id=:sid))'''), {'sid': source['id']})
    db.execute(text('DELETE FROM knowledge_versions WHERE source_id=:id'), {'id': source['id']})
    _prune_orphan_entities(db)
    invalidate_derived(db)


def _prune_orphan_entities(db):
    db.execute(text('''DELETE FROM knowledge_entities e WHERE e.kind IN ('project','topic')
        AND NOT EXISTS (SELECT 1 FROM knowledge_claims c WHERE c.speaker_entity_id=e.id
            OR c.subject_entity_id=e.id OR c.owner_entity_id=e.id OR c.entity_ids @> jsonb_build_array(e.id))
        AND NOT EXISTS (SELECT 1 FROM knowledge_relations r WHERE r.from_entity_id=e.id OR r.to_entity_id=e.id)
        AND NOT EXISTS (SELECT 1 FROM knowledge_revisions r WHERE r.record_type='entity'
            AND r.record_id=e.id AND r.origin='human')'''))


def _entity_id(db, ref):
    if not ref or 'unresolved' in ref:
        return None
    if ref.get('entity_id'):
        return ref['entity_id']
    kind, label = ref['kind'], ref['label']
    key = kind + ':' + hashlib.sha256(fold(label).encode()).hexdigest()
    return db.execute(text('''INSERT INTO knowledge_entities(entity_key,kind,label) VALUES (:key,:kind,:label)
        ON CONFLICT(entity_key) DO UPDATE SET entity_key=EXCLUDED.entity_key RETURNING id'''),
        {'key': key, 'kind': kind, 'label': label}).scalar()


def _tasks(db):
    tasks = []
    for kind, table in TASK_TABLES.items():
        tasks.extend({'kind': kind, **row_json(r)} for r in db.execute(text(f'''SELECT id,person_id,description,due_date,status,
            knowledge_event_on FROM {table} ORDER BY id''')).mappings().all())
    return tasks


def _save_task_result(db, claim):
    db.execute(text('''UPDATE knowledge_claims SET commitment_id=:cid,meeting_action_id=:aid,
        conflict=CAST(:conflict AS JSONB) WHERE id=:id'''),
        {'cid': claim.get('commitment_id'), 'aid': claim.get('meeting_action_id'),
         'conflict': _json(claim.get('conflict')), 'id': claim['id']})
    return claim


def _potential_tasks(db, table, person_id, content):
    # Similaridade somente sinaliza revisão; nunca funde responsabilidades.
    ignored = {'para','com','uma','por','das','dos','entregar','enviar','fazer','realizar'}
    words = set(re.findall(r'\w{3,}', fold(content))) - ignored
    result = []
    for task in db.execute(text("SELECT 'commitment' AS kind,id,description FROM commitments WHERE person_id=:pid AND status='open' UNION ALL SELECT 'meeting_action',id,description FROM meeting_actions WHERE person_id=:pid AND status='open'"), {'pid': person_id}).mappings():
        previous = set(re.findall(r'\w{3,}', fold(task['description']))) - ignored
        union = words | previous
        if words and previous and (words == previous or (len(words & previous) >= 2 and len(words & previous) / len(union) >= .6)):
            result.append({'kind': task['kind'], 'id': task['id']})
    return result


def sync_claim_task(db, claim, user_id=None):
    """Atualiza as tabelas operacionais existentes, sem vencer correções humanas."""
    if claim.get('classification') != 'commitment' or not claim.get('task_accepted') or claim.get('review_state') == 'rejected':
        return claim
    owner = db.execute(text('SELECT person_id,label FROM knowledge_entities WHERE id=:id'),
                       {'id': claim.get('owner_entity_id')}).mappings().first()
    if owner is None or not owner['person_id']:
        return claim
    person_id = owner['person_id']
    field = 'commitment_id' if claim.get('commitment_id') else 'meeting_action_id' if claim.get('meeting_action_id') else None
    if field:
        kind = 'commitment' if field == 'commitment_id' else 'meeting_action'
    else:
        source = db.execute(text('SELECT kind,record_id FROM knowledge_sources WHERE id=:id'),
                            {'id': claim.get('source_id')}).mappings().first()
        if not source and claim.get('origin') != 'manual':
            return claim
        source = source or {'kind': 'one_on_one', 'record_id': None}
        kind = 'commitment' if source['kind'] == 'one_on_one' else 'meeting_action'
        field = 'commitment_id' if kind == 'commitment' else 'meeting_action_id'
        table = TASK_TABLES[kind]
        # Serialize equal accepted actions across different concurrently processed sources.
        lock_key = int.from_bytes(hashlib.sha256((str(person_id) + ':' + claim['content']).encode()).digest()[:8], 'big', signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_key})
        # Reuse an exact existing task even if the model omitted task_ref. Never fuzzy-merge.
        candidates = db.execute(text('''SELECT 'commitment' AS kind,id FROM commitments
            WHERE person_id=:pid AND description=:description
            UNION ALL SELECT 'meeting_action',id FROM meeting_actions
            WHERE person_id=:pid AND description=:description'''),
            {'pid': person_id, 'description': claim['content']}).mappings().all()
        if len(candidates) == 1:
            kind = candidates[0]['kind']
            field = 'commitment_id' if kind == 'commitment' else 'meeting_action_id'
            table = TASK_TABLES[kind]
            claim[field] = candidates[0]['id']
        elif len(candidates) > 1:
            claim['conflict'] = {'task_ambiguity': [dict(r) for r in candidates]}
            return _save_task_result(db, claim)
        else:
            potential = _potential_tasks(db, table, person_id, claim['content'])
            if potential or claim.get('_task_context_incomplete'):
                claim['conflict'] = {**(claim.get('conflict') or {}), 'task_resolution_pending': {
                    'candidate_task_ids': potential, 'context_incomplete': bool(claim.get('_task_context_incomplete')),
                    'reason': 'Revise a vinculação à tarefa existente antes de adotar outra.'}}
                return _save_task_result(db, claim)
            if kind == 'commitment':
                # A third-party action in a 1:1 belongs to that person's canonical commitments, too.
                task = db.execute(text('''INSERT INTO commitments
                    (one_on_one_id,person_id,description,owner,due_date,knowledge_event_on,knowledge_created,knowledge_human_fields)
                    VALUES (:source,:pid,:description,'person',:due,:event,:created,CAST(:locks AS JSONB)) RETURNING *'''),
                    {'source': source['record_id'], 'pid': person_id, 'description': claim['content'],
                     'due': claim.get('due_date'), 'event': claim.get('occurred_on'), 'created': claim.get('origin') != 'manual',
                     'locks': _json({key: True for key in ('description','person_id','owner','due_date','status')} if claim.get('origin') == 'manual' else {})}).mappings().first()
            else:
                task = db.execute(text('''INSERT INTO meeting_actions
                    (meeting_id,person_id,description,owner_name,due_date,source_key,knowledge_event_on,knowledge_created,knowledge_human_fields)
                    VALUES (:source,:pid,:description,:owner,:due,:key,:event,:created,CAST(:locks AS JSONB)) RETURNING *'''),
                    {'source': source['record_id'], 'pid': person_id, 'description': claim['content'], 'owner': owner['label'],
                     'due': claim.get('due_date'), 'key': 'knowledge:' + str(claim['id']), 'event': claim.get('occurred_on'),
                     'created': claim.get('origin') != 'manual',
                     'locks': _json({key: True for key in ('description','person_id','owner_name','due_date','status')} if claim.get('origin') == 'manual' else {})}).mappings().first()
            claim[field] = task['id']
            audit(db, kind, task['id'], None, row_json(task), user_id, 'Compromisso aceito com evidência' if claim.get('origin') != 'manual' else 'Compromisso manual',
                  'automatic' if claim.get('origin') != 'manual' else 'human')
    table = TASK_TABLES[kind]
    task = db.execute(text(f'SELECT * FROM {table} WHERE id=:id FOR UPDATE'), {'id': claim[field]}).mappings().first()
    if task is None:
        return claim
    if task['person_id'] != person_id:
        claim['conflict'] = {'task_owner_mismatch': claim[field]}
        return _save_task_result(db, claim)
    locked = task.get('knowledge_human_fields') or {}
    newer = not task.get('knowledge_event_on') or not claim.get('occurred_on') or str(claim['occurred_on']) > str(task['knowledge_event_on'])
    changes = {}
    proposed = {'due_date': claim.get('due_date'),
                'status': claim.get('operational_state') if claim.get('operational_state') != 'none' else task['status']}
    for name, value in proposed.items():
        if value is None or str(value) == str(task.get(name)):
            continue
        if name in locked or not newer or not task.get('knowledge_created'):
            claim['conflict'] = {**(claim.get('conflict') or {}), 'task_update': {
                'kind': kind, 'id': task['id'], 'field': name, 'current': json_default(task.get(name)),
                'proposed': json_default(value), 'reason': 'human_or_older_event'}}
        else:
            changes[name] = value
    if changes:
        sets = [f'{name}=:{name}' for name in changes]
        if changes.get('status'):
            sets.append("closed_at=CASE WHEN :status='open' THEN NULL ELSE CURRENT_TIMESTAMP END")
        updated = db.execute(text(f'''UPDATE {table} SET {', '.join(sets)},knowledge_event_on=:event,
            knowledge_version=knowledge_version+1,updated_at=CURRENT_TIMESTAMP WHERE id=:id RETURNING *'''),
            {**changes, 'event': claim.get('occurred_on'), 'id': task['id']}).mappings().first()
        audit(db, kind, task['id'], row_json(task), row_json(updated), user_id,
              'Atualização explicitamente aceita em nova fonte', 'automatic')
        db.execute(text(f'''UPDATE knowledge_claims SET version=version+1,updated_at=CURRENT_TIMESTAMP
            WHERE {field}=:tid AND id<>:cid'''), {'tid': task['id'], 'cid': claim['id']})
    db.execute(text(f'UPDATE knowledge_claims SET {field}=:tid,conflict=CAST(:conflict AS JSONB) WHERE id=:id'),
               {'tid': claim[field], 'conflict': _json(claim.get('conflict')), 'id': claim['id']})
    return claim


def _publish(db, source, version, candidates, user_id):
    """Publicação inteira sob lock da fonte: reexecução não duplica registros."""
    db.execute(text("UPDATE knowledge_claims SET active=FALSE WHERE source_id=:sid AND origin='automatic'"), {'sid': source['id']})
    db.execute(text('''UPDATE knowledge_relations SET active=FALSE WHERE claim_id IN
        (SELECT id FROM knowledge_claims WHERE source_id=:sid) AND origin='automatic' '''), {'sid': source['id']})
    # Existing source/version identities win over extraction order.
    for candidate in candidates:
        roles = {role + '_entity_id': _entity_id(db, candidate.get(role)) for role in ('speaker', 'subject', 'owner')}
        mentions = [_entity_id(db, ref) for ref in candidate.get('mentions', [])]
        refs = list(dict.fromkeys(v for v in [*roles.values(), *mentions] if v))
        proposed = {'version_id': version['id'], 'evidence_start': candidate['evidence_start'],
                    'evidence_end': candidate['evidence_end'], 'content': candidate['content'],
                    'classification': candidate['classification'], **roles, 'entity_ids': refs,
                    'operational_state': candidate['operational_state'], 'due_date': candidate.get('due_date'),
                    'due_expression': candidate.get('due_expression'), 'condition_text': candidate.get('condition_text'),
                    'task_accepted': candidate['task_accepted'],
                    'occurred_on': version['occurred_on']}
        old = db.execute(text('SELECT * FROM knowledge_claims WHERE source_id=:sid AND source_key=:key FOR UPDATE'),
                         {'sid': source['id'], 'key': candidate['source_key']}).mappings().first()
        conflict = {'unresolved': candidate['unresolved']} if candidate.get('unresolved') else None
        if old:
            removed_support = (old.get('conflict') or {}).get('task_evidence_removed')
            if removed_support:
                conflict = {**(conflict or {}), 'task_evidence_removed': removed_support}
            locked = old.get('human_fields') or {}
            changes = {k: v for k, v in proposed.items() if k not in locked}
            incompatible = {k: {'current': old[k], 'proposed': v} for k, v in proposed.items()
                            if k in locked and row_json({'v': old[k]}) != row_json({'v': v})}
            if incompatible:
                conflict = {**(conflict or {}), 'human_correction': incompatible}
            sets, params = [], {'id': old['id'], 'conflict': _json(conflict)}
            for key, value in changes.items():
                sets.append(key + ('=CAST(:' + key + ' AS JSONB)' if key == 'entity_ids' else '=:' + key))
                params[key] = _json(value) if key == 'entity_ids' else value
            db.execute(text(f'''UPDATE knowledge_claims SET {', '.join(sets)},active=TRUE,
                conflict=CAST(:conflict AS JSONB),version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=:id'''), params)
            cid = old['id']
        else:
            params = {**proposed, 'entity_ids': _json(refs), 'sid': source['id'], 'key': candidate['source_key'], 'conflict': _json(conflict)}
            columns = list(proposed) + ['source_id', 'source_key', 'conflict']
            values = [('CAST(:entity_ids AS JSONB)' if k == 'entity_ids' else ':' + k) for k in proposed] + [':sid', ':key', 'CAST(:conflict AS JSONB)']
            cid = db.execute(text(f"INSERT INTO knowledge_claims ({', '.join(columns)}) VALUES ({', '.join(values)}) RETURNING id"), params).scalar()
        claim = dict(db.execute(text('SELECT * FROM knowledge_claims WHERE id=:id'), {'id': cid}).mappings().first())
        related_conflicts = []
        for related_id in candidate.get('conflicts_with', []):
            if related_id == cid:
                continue
            related = db.execute(text('''SELECT c.* FROM knowledge_claims c LEFT JOIN knowledge_sources s ON s.id=c.source_id
                WHERE c.id=:id AND (c.active OR (c.review_state='rejected' AND c.human_fields<>'{}'::jsonb))
                  AND (c.source_id IS NULL OR NOT s.deleted) FOR SHARE OF c'''),
                {'id': related_id}).mappings().first()
            if related:
                related_conflicts.append({key: row_json(related).get(key) for key in
                    ('id','version','classification','origin','source_id','version_id')})
        if related_conflicts:
            claim['conflict'] = {**(claim.get('conflict') or {}), 'related_claims': related_conflicts,
                'proposal': {'content': claim['content'], 'classification': claim['classification'],
                             'source_id': source['id'], 'version_id': version['id']}}
            db.execute(text('UPDATE knowledge_claims SET conflict=CAST(:conflict AS JSONB) WHERE id=:id'),
                       {'id': cid, 'conflict': _json(claim['conflict'])})
        ref = candidate.get('task_ref')
        if ref and not claim.get('commitment_id') and not claim.get('meeting_action_id'):
            field = 'commitment_id' if ref['kind'] == 'commitment' else 'meeting_action_id'
            claim[field] = ref['id']
        claim['_task_context_incomplete'] = candidate.get('task_context_incomplete', False)
        sync_claim_task(db, claim, user_id)
        after = dict(db.execute(text('SELECT * FROM knowledge_claims WHERE id=:id'), {'id': cid}).mappings().first())
        audit(db, 'claim', cid, row_json(old) if old else None, row_json(after), user_id, 'Extração com evidência verificada', 'automatic')
        if claim['review_state'] == 'rejected':
            continue
        for relation in candidate.get('relations', []):
            left, right = _entity_id(db, relation['from']), _entity_id(db, relation['to'])
            if left == right or left is None or right is None:
                continue
            # A chave automática permanece imutável quando o usuário corrige os
            # extremos ou o tipo. Replay não recria o vínculo incorreto original.
            relation_key = hashlib.sha256(_json([left, right, relation['type']]).encode()).hexdigest()
            params = {'left': left, 'right': right, 'type': relation['type'], 'cid': cid, 'key': relation_key}
            existing = db.execute(text('''SELECT * FROM knowledge_relations WHERE claim_id=:cid
                AND (source_key=:key OR (source_key IS NULL AND from_entity_id=:left
                AND to_entity_id=:right AND relation_type=:type)) FOR UPDATE'''), params).mappings().first()
            if existing:
                db.execute(text('''UPDATE knowledge_relations SET active=TRUE,
                    source_key=COALESCE(source_key,:key) WHERE id=:id'''),
                    {'id': existing['id'], 'key': relation_key})
            else:
                db.execute(text('''INSERT INTO knowledge_relations
                    (from_entity_id,to_entity_id,relation_type,claim_id,source_key)
                    VALUES (:left,:right,:type,:cid,:key)'''), params)
    # Claims not present in the corrected source stop supporting automatic tasks.
    unsupported = [dict(r) for r in db.execute(text('SELECT * FROM knowledge_claims WHERE source_id=:sid AND NOT active'), {'sid': source['id']}).mappings().all()]
    _cancel_unsupported_tasks(db, unsupported)
    invalidate_derived(db)


def process_job(job_id, user_id=None):
    """Uma etapa, lease de 90s e até três tentativas por segmento.

    Se o request cair, o próximo consumidor retoma após a expiração do lease.
    Transações de publicação e estágio são independentes da chamada remota.
    """
    if not enabled():
        raise ValueError('Memória estruturada desativada.')
    token = uuid.uuid4().hex
    db = session_factory()
    try:
        job = db.execute(text('''SELECT j.*,s.current_version_id,s.deleted FROM knowledge_jobs j
            JOIN knowledge_sources s ON s.id=j.source_id WHERE j.id=:id FOR UPDATE OF j,s'''), {'id': job_id}).mappings().first()
        if not job:
            raise ValueError('Processamento não encontrado.')
        if job['deleted'] or job['current_version_id'] != job['version_id']:
            db.execute(text("UPDATE knowledge_jobs SET status='superseded' WHERE id=:id"), {'id': job_id}); db.commit()
            return {'job_id': job_id, 'status': 'superseded'}
        if job['status'] == 'done':
            return {'job_id': job_id, 'status': 'done', 'metrics': job['metrics_json']}
        now = datetime.now(timezone.utc)
        if job['status'] == 'running' and job.get('lease_until') and job['lease_until'] > now:
            return {'job_id': job_id, 'status': 'running'}
        if job['attempts'] >= 3:
            return {'job_id': job_id, 'status': 'error', 'error': 'Limite de tentativas atingido; revise a fonte e tente novamente.'}
        version = dict(db.execute(text('SELECT * FROM knowledge_versions WHERE id=:id'), {'id': job['version_id']}).mappings().first())
        parts = list(segments(version['source_text']))
        cursor = (job['cursor_json'] or {}).get('segment', 0)
        entities, tasks = sync_people(db), _tasks(db)
        from .knowledge_query import _COLUMNS, _JOINS, _ACTIVE, _claim
        existing_claims = [_claim(r) for r in db.execute(text(
            'SELECT ' + _COLUMNS + ' ' + _JOINS + " WHERE (c.active OR (c.review_state='rejected' AND c.human_fields<>'{}'::jsonb))"
            + " AND (c.source_id IS NULL OR NOT s.deleted) ORDER BY c.id")).mappings().all()]
        db.execute(text("""UPDATE knowledge_jobs SET status='running',lease_token=:token,
            lease_until=CURRENT_TIMESTAMP+INTERVAL '90 seconds',attempts=attempts+1,
            updated_at=CURRENT_TIMESTAMP WHERE id=:id"""), {'id': job_id, 'token': token})
        db.commit()
    finally:
        db.close()
    started = time.monotonic()
    try:
        if cursor < len(parts):
            candidates, metrics = extract_candidates(parts[cursor], entities, tasks, version['occurred_on'], version['timezone'], existing_claims)
        else:
            candidates, metrics = [], {'prompt_tokens': 0, 'completion_tokens': 0}
        metrics['latency_ms'] = round((time.monotonic() - started) * 1000)
    except Exception as exc:
        # Logs só contêm classe de erro; nunca texto da transcrição ou payload de IA.
        log.warning('Extração de memória falhou (%s).', type(exc).__name__)
        db = session_factory()
        try:
            db.execute(text("""UPDATE knowledge_jobs SET status='error',lease_token=NULL,lease_until=NULL,
                error='Falha na extração ou validação; a fonte foi preservada.',updated_at=CURRENT_TIMESTAMP
                WHERE id=:id AND lease_token=:token"""), {'id': job_id, 'token': token})
            db.commit()
        finally:
            db.close()
        return {'job_id': job_id, 'status': 'error', 'error': 'Falha na extração ou validação; a fonte foi preservada.'}
    db = session_factory()
    try:
        source = db.execute(text('SELECT * FROM knowledge_sources WHERE id=:id FOR UPDATE'), {'id': version['source_id']}).mappings().first()
        job = db.execute(text('SELECT * FROM knowledge_jobs WHERE id=:id FOR UPDATE'), {'id': job_id}).mappings().first()
        if not source or not job or job['lease_token'] != token or source['deleted'] or source['current_version_id'] != version['id']:
            return {'job_id': job_id, 'status': 'superseded'}
        staged = (job['staged_json'] or []) + candidates
        if len(staged) > 1200:
            raise ValueError('Fonte excedeu o limite de afirmações por processamento.')
        previous_metrics = job['metrics_json'] or {}
        combined = {key: (previous_metrics.get(key, 0) + metrics.get(key, 0)) for key in ('prompt_tokens', 'completion_tokens', 'latency_ms')}
        combined['model'] = metrics.get('model', previous_metrics.get('model'))
        combined['context_truncated'] = bool(previous_metrics.get('context_truncated') or metrics.get('context_truncated') or metrics.get('claims_context_truncated'))
        combined['context_chars_max'] = max(previous_metrics.get('context_chars_max', 0), metrics.get('context_chars', 0))
        combined['context_segments'] = (previous_metrics.get('context_segments') or []) + [{
            'segment': cursor, **{key: metrics.get(key) for key in ('entities_available', 'entities_included', 'tasks_available', 'tasks_included', 'context_truncated', 'claims_available', 'claims_included', 'claims_context_truncated')}}]
        combined['segments_completed'] = min(cursor + 1, len(parts))
        combined['segments_total'] = len(parts)
        combined['claims'] = len(staged)
        combined['needs_review'] = sum(bool(c.get('unresolved')) or c.get('classification') in {'perception','hypothesis','proposal'} for c in staged)
        done = cursor + 1 >= len(parts)
        if done:
            _publish(db, dict(source), version, staged, user_id)
        db.execute(text('''UPDATE knowledge_jobs SET status=:status,attempts=0,lease_token=NULL,lease_until=NULL,
            cursor_json=CAST(:cursor AS JSONB),staged_json=CAST(:staged AS JSONB),metrics_json=CAST(:metrics AS JSONB),
            error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=:id'''),
            {'id': job_id, 'status': 'done' if done else 'pending', 'cursor': _json({'segment': cursor + 1}),
             'staged': _json([] if done else staged), 'metrics': _json(combined)})
        db.commit()
        return {'job_id': job_id, 'status': 'done' if done else 'pending', 'metrics': combined}
    except Exception as exc:
        db.rollback()
        log.warning('Publicação de memória falhou (%s).', type(exc).__name__)
        db.execute(text("""UPDATE knowledge_jobs SET status='error',lease_token=NULL,lease_until=NULL,
            error='Falha ao publicar; nenhuma alteração parcial foi aplicada.' WHERE id=:id AND lease_token=:token"""),
            {'id': job_id, 'token': token})
        db.commit()
        return {'job_id': job_id, 'status': 'error', 'error': 'Falha ao publicar; nenhuma alteração parcial foi aplicada.'}
    finally:
        db.close()
