"""Explorador, curadoria e execução retomável da memória do espaço do diretor."""
import json
import logging
from datetime import date, datetime
from functools import wraps
from io import BytesIO
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request, send_file, session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..database import session_factory
from ..security import login_required
from .. import knowledge as core
from ..knowledge_sync import sync_status, sync_step
from ..knowledge_query import (CLASSIFICATIONS, RELATIONS, REVIEWS, _claim, _COLUMNS,
                               _JOINS, knowledge_revision, list_tasks, query_claims, query_graph,
                               record_task_change, serial)

bp = Blueprint('knowledge', __name__, url_prefix='/api/knowledge')
log = logging.getLogger('cortex.knowledge.api')


class EditConflict(ValueError):
    pass


def guard(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if not core.enabled():
            return jsonify({'error': 'Memória desativada.', 'enabled': False}), 503
        db = None
        try:
            db = session_factory()
            result = fn(db, *args, **kwargs)
            db.commit()
            return result
        except EditConflict as exc:
            if db:
                db.rollback()
            return jsonify({'error': str(exc), 'conflict': True}), 409
        except (ValueError, TypeError) as exc:
            if db:
                db.rollback()
            return jsonify({'error': str(exc) or 'Dados inválidos.'}), 400
        except IntegrityError:
            if db:
                db.rollback()
            return jsonify({'error': 'Já existe um registro com esse vínculo. Recarregue e revise os registros existentes.', 'conflict': True}), 409
        except Exception as exc:
            if db:
                db.rollback()
            log.warning('Memória indisponível (%s).', type(exc).__name__)
            return jsonify({'error': 'Memória indisponível. Verifique a configuração e a inicialização do schema.'}), 503
        finally:
            if db:
                db.close()
    return wrapped


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('Informe um objeto JSON.')
    return data


def integer(value, name='identificador', optional=False):
    if optional and value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError('Valor inválido: ' + name)
    return value


def string(value, name, maximum=4000, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('Texto inválido: ' + name)
    return value.strip()


def check_version(data, row):
    integer(data.get('version'), 'revisão')
    if data['version'] != row['version']:
        raise EditConflict('Este registro mudou. Recarregue os detalhes antes de salvar sua correção.')


def check_entities(db, values):
    ids = set()
    for value in values:
        if value is not None:
            ids.add(integer(value, 'entidade'))
    if ids:
        count = db.execute(text('SELECT COUNT(*) FROM knowledge_entities WHERE id=ANY(CAST(:ids AS BIGINT[]))'),
                           {'ids': list(ids)}).scalar()
        if count != len(ids):
            raise ValueError('Uma das entidades não existe neste espaço.')


def filters():
    values = {key: request.args[key] for key in
              ('q', 'classification', 'review_state', 'status', 'date_from', 'date_to') if request.args.get(key)}
    for key in ('entity_id', 'person_id', 'limit', 'offset'):
        if request.args.get(key):
            values[key] = int(request.args[key])
            if values[key] < (0 if key == 'offset' else 1):
                raise ValueError('Filtro inválido: ' + key)
    if values.get('review_state') == 'rejected':
        values['include_rejected'] = True
    return values


@bp.get('/status')
@login_required
def status():
    if not core.enabled():
        return jsonify({'enabled': False, 'jobs': [], 'counts': {}, 'stale': False,
                        'has_pending': False, 'needs_attention': False, 'can_sync': False})
    return _status()


@guard
def _status(db):
    counts = {key: db.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar()
              for key, table in (('entities', 'knowledge_entities'), ('claims', 'knowledge_claims'),
                                  ('sources', 'knowledge_sources'))}
    jobs = [serial(r) for r in db.execute(text("""SELECT id,source_id,version_id,status,attempts,error,
        metrics_json,updated_at FROM knowledge_jobs ORDER BY id DESC LIMIT 40""")).mappings().all()]
    pending = db.execute(text("SELECT COUNT(*) FROM knowledge_outbox WHERE status='pending'")).scalar()
    return jsonify({'enabled': True, 'counts': counts, 'jobs': jobs, 'stale': bool(pending),
                    'pending_projections': pending, 'revision_token': knowledge_revision(db),
                    'search_mode': 'structured_text', 'semantic_available': False, **sync_status(db)})


@bp.post('/sync')
@login_required
def sync():
    if not core.enabled():
        return jsonify({'enabled': False, 'has_pending': False, 'needs_attention': False,
                        'can_sync': False, 'updated': False})
    try:
        data = body() if request.get_data() else {}
        retry = data.get('retry', False)
        if type(retry) is not bool:
            raise ValueError('Pedido de atualização inválido.')
        kind, record_id = None, None
        if 'source' in data:
            source = data['source']
            if not isinstance(source, dict) or source.get('kind') not in ('one_on_one', 'meeting'):
                raise ValueError('Fonte inválida.')
            kind = source['kind']
            record_id = integer(source.get('id'), 'fonte')
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    try:
        return jsonify(sync_step(session['user_id'], retry=retry, kind=kind, record_id=record_id))
    except Exception as exc:
        log.warning('Atualização da memória indisponível (%s).', type(exc).__name__)
        return jsonify({'error': 'Não foi possível atualizar a memória agora. Tente novamente em instantes.'}), 503


@bp.get('/entities')
@guard
def entities(db):
    core.sync_people(db)
    q = '%' + request.args.get('q', '').lower()[:300] + '%'
    rows = db.execute(text('''SELECT * FROM knowledge_entities
        WHERE LOWER(label) LIKE :q OR LOWER(aliases::text) LIKE :q
        ORDER BY CASE WHEN kind='person' THEN 0 ELSE 1 END,label,id LIMIT 100'''), {'q': q}).mappings().all()
    return jsonify({'entities': [serial(r) for r in rows]})


@bp.post('/entities')
@guard
def create_entity(db):
    import unicodedata
    data = body()
    kind = data.get('kind')
    if kind not in {'project', 'topic'}:
        raise ValueError('Pessoas são criadas pelo cadastro de pessoas. Use project ou topic.')
    label = string(data.get('label'), 'nome', 255)
    aliases = _aliases(data.get('aliases', []))
    folded = ''.join(c for c in unicodedata.normalize('NFD', label.casefold()) if not unicodedata.combining(c))
    import hashlib
    key = kind + ':' + hashlib.sha256(' '.join(folded.split()).encode()).hexdigest()
    row = db.execute(text('''INSERT INTO knowledge_entities(entity_key,kind,label,aliases)
       VALUES(:key,:kind,:label,CAST(:aliases AS JSONB)) ON CONFLICT(entity_key)
       DO UPDATE SET entity_key=EXCLUDED.entity_key RETURNING *'''),
       {'key': key, 'kind': kind, 'label': label, 'aliases': json.dumps(aliases)}).mappings().first()
    core.audit(db, 'entity', row['id'], None, dict(row), session['user_id'], 'Cadastro manual')
    core.invalidate_derived(db)
    return jsonify({'entity': serial(row)}), 201


def _aliases(value):
    if not isinstance(value, list) or len(value) > 30:
        raise ValueError('Informe até 30 aliases.')
    return list(dict.fromkeys(string(v, 'alias', 120) for v in value))


@bp.put('/entities/<int:eid>')
@guard
def update_entity(db, eid):
    data = body()
    row = db.execute(text('SELECT * FROM knowledge_entities WHERE id=:id FOR UPDATE'), {'id': eid}).mappings().first()
    if not row:
        return jsonify({'error': 'Entidade não encontrada.'}), 404
    check_version(data, row)
    label = string(data.get('label', row['label']), 'nome', 255)
    if row['kind'] == 'person' and label != row['label']:
        raise ValueError('Altere o nome no cadastro da pessoa. Aqui você pode corrigir aliases.')
    aliases = _aliases(data.get('aliases', row['aliases']))
    after = db.execute(text('''UPDATE knowledge_entities SET label=:label,aliases=CAST(:aliases AS JSONB),
        version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=:id RETURNING *'''),
        {'id': eid, 'label': label, 'aliases': json.dumps(aliases)}).mappings().first()
    if row['person_id']:
        db.execute(text('UPDATE people SET transcript_aliases=CAST(:aliases AS JSONB),updated_at=CURRENT_TIMESTAMP WHERE id=:id'),
                   {'aliases': json.dumps(aliases), 'id': row['person_id']})
    core.audit(db, 'entity', eid, dict(row), dict(after), session['user_id'], data.get('reason'))
    core.invalidate_derived(db)
    return jsonify({'entity': serial(after)})


@bp.get('/claims')
@guard
def claims(db):
    return jsonify(query_claims(db, **filters()))


def _detail(db, cid):
    row = db.execute(text('SELECT ' + _COLUMNS + ' ' + _JOINS +
                         ' WHERE c.id=:id AND (c.source_id IS NULL OR NOT s.deleted)'), {'id': cid}).mappings().first()
    if not row:
        return None
    claim = _claim(row)
    # Conflicts store references, not another copy of a different source. Resolve
    # authorized/current values on read so deleting that source also removes it here.
    if claim.get('conflict') and claim['conflict'].get('related_claims'):
        resolved = []
        for reference in claim['conflict']['related_claims']:
            related = db.execute(text('SELECT ' + _COLUMNS + ' ' + _JOINS +
                ' WHERE c.id=:id AND (c.source_id IS NULL OR NOT s.deleted)'), {'id': reference.get('id')}).mappings().first()
            if not related:
                resolved.append({'id': reference.get('id'), 'unavailable': True})
                continue
            current = _claim(related)
            item = {k: current.get(k) for k in ('id', 'version', 'content', 'classification', 'origin',
                'review_state', 'source_ref', 'source_kind', 'record_id', 'source_id', 'version_id', 'active')}
            item['compared_revision'] = reference.get('version')
            if related['version_id']:
                source_text = db.execute(text('SELECT source_text FROM knowledge_versions WHERE id=:id'), {'id': related['version_id']}).scalar()
                item['original_evidence'] = {'text': (source_text or '')[related['evidence_start']:related['evidence_end']],
                    'version_id': related['version_id'], 'start': related['evidence_start'], 'end': related['evidence_end']}
            resolved.append(item)
        claim['conflict']['related_claims'] = resolved
    evidence = None
    if row['version_id']:
        version = db.execute(text('SELECT source_text,occurred_on,timezone FROM knowledge_versions WHERE id=:id'),
                             {'id': row['version_id']}).mappings().first()
        evidence = {'text': version['source_text'][row['evidence_start']:row['evidence_end']],
                    'source_id': row['source_id'], 'source_kind': row['source_kind'], 'record_id': row['record_id'],
                    'version_id': row['version_id'], 'start': row['evidence_start'], 'end': row['evidence_end'],
                    'occurred_on': str(version['occurred_on']), 'timezone': version['timezone']}
    revisions = [serial(r) for r in db.execute(text("""SELECT r.id,r.record_type,r.previous_version,
        r.actor_user_id,u.name AS actor_name,r.origin,r.before_json,r.after_json,r.reason,r.created_at
        FROM knowledge_revisions r LEFT JOIN users u ON u.id=r.actor_user_id
        WHERE (r.record_type='claim' AND r.record_id=:id)
          OR (r.record_type='commitment' AND r.record_id=:tid)
          OR (r.record_type='meeting_action' AND r.record_id=:aid)
          OR (r.record_type='relation' AND r.record_id IN
              (SELECT id FROM knowledge_relations WHERE claim_id=:id))
        ORDER BY r.id DESC LIMIT 100"""), {'id': cid, 'tid': row['commitment_id'], 'aid': row['meeting_action_id']}).mappings().all()]
    relations = [serial(r) for r in db.execute(text('SELECT * FROM knowledge_relations WHERE claim_id=:id ORDER BY id'), {'id': cid}).mappings().all()]
    return {'claim': claim, 'evidence': evidence, 'revisions': revisions, 'relations': relations,
            'conflicts': [claim['conflict']] if claim.get('conflict') else []}


@bp.get('/claims/<int:cid>')
@guard
def claim_detail(db, cid):
    detail = _detail(db, cid)
    return jsonify(detail) if detail else (jsonify({'error': 'Registro não encontrado.'}), 404)


def claim_values(db, data, create=False):
    values = {}
    if 'content' in data or create:
        values['content'] = string(data.get('content'), 'conteúdo')
    for key, allowed in (('classification', CLASSIFICATIONS), ('review_state', REVIEWS)):
        if key in data:
            if data[key] not in allowed:
                raise ValueError('Classificação ou revisão inválida.')
            values[key] = data[key]
    if 'status' in data:
        if data['status'] not in {'open', 'done', 'cancelled', 'none'}:
            raise ValueError('Estado inválido.')
        values['operational_state'] = data['status']
    for key in ('speaker_entity_id', 'subject_entity_id', 'owner_entity_id'):
        if key in data:
            values[key] = integer(data[key], key, optional=True)
    if 'entity_ids' in data:
        if not isinstance(data['entity_ids'], list) or len(data['entity_ids']) > 30:
            raise ValueError('Entidades inválidas.')
        values['entity_ids'] = list(dict.fromkeys(integer(e, 'entidade') for e in data['entity_ids']))
    check_entities(db, [values.get(k) for k in ('speaker_entity_id', 'subject_entity_id', 'owner_entity_id')] + values.get('entity_ids', []))
    for key in ('due_date', 'occurred_on'):
        if key in data:
            values[key] = date.fromisoformat(data[key]) if data[key] else None
    if 'task_accepted' in data:
        if type(data['task_accepted']) is not bool:
            raise ValueError('Aceite inválido.')
        values['task_accepted'] = data['task_accepted']
    if 'condition_text' in data:
        values['condition_text'] = string(data['condition_text'] or None, 'condição', 500, optional=True)
    return values


@bp.post('/claims')
@guard
def create_claim(db):
    data = body()
    values = claim_values(db, data, True)
    values.setdefault('classification', 'declaration')
    values.setdefault('review_state', 'accepted')
    values.setdefault('occurred_on', datetime.now(ZoneInfo('America/Sao_Paulo')).date())
    values.setdefault('entity_ids', [])
    values.update(origin='manual', human_fields={k: True for k in values})
    keys = list(values)
    params = {k: json.dumps(v) if k in {'entity_ids', 'human_fields'} else v for k, v in values.items()}
    placeholders = [f'CAST(:{k} AS JSONB)' if k in {'entity_ids', 'human_fields'} else ':' + k for k in keys]
    row = db.execute(text(f"INSERT INTO knowledge_claims({','.join(keys)}) VALUES({','.join(placeholders)}) RETURNING *"), params).mappings().first()
    core.audit(db, 'claim', row['id'], None, dict(row), session['user_id'], data.get('reason') or 'Observação manual')
    if row['classification'] == 'commitment' and row['task_accepted']:
        core.sync_claim_task(db, dict(row), session['user_id'])
    core.invalidate_derived(db)
    return jsonify(_detail(db, row['id'])), 201


@bp.put('/claims/<int:cid>')
@guard
def update_claim(db, cid):
    data = body()
    row = db.execute(text('SELECT * FROM knowledge_claims WHERE id=:id FOR UPDATE'), {'id': cid}).mappings().first()
    if not row:
        return jsonify({'error': 'Registro não encontrado.'}), 404
    if row['source_id'] and db.execute(text('SELECT deleted FROM knowledge_sources WHERE id=:id'), {'id': row['source_id']}).scalar():
        return jsonify({'error': 'Fonte excluída.'}), 404
    check_version(data, row)
    values = claim_values(db, data)
    if values.get('classification', row['classification']) != 'commitment':
        values.update(task_accepted=False, operational_state='none')
    reason = string(data.get('reason') or None, 'justificativa', 2000, optional=True)
    confirms_task = bool(data.get('resolve_conflict') is True and
                         (row.get('conflict') or {}).get('task_evidence_removed') and
                         (row['commitment_id'] or row['meeting_action_id']))
    if confirms_task:
        table = 'commitments' if row['commitment_id'] else 'meeting_actions'
        task = db.execute(text(f'SELECT due_date,status FROM {table} WHERE id=:id FOR UPDATE'),
                          {'id': row['commitment_id'] or row['meeting_action_id']}).mappings().first()
        if not task:
            raise EditConflict('A tarefa foi removida. Recarregue o registro.')
        values.setdefault('due_date', task['due_date'])
        values.setdefault('operational_state', task['status'])
    if 'task_ref' in data:
        ref = data['task_ref']
        if not isinstance(ref, dict) or ref.get('kind') not in {'commitment', 'meeting_action'}:
            raise ValueError('Referência de tarefa inválida.')
        tid = integer(ref.get('id'), 'tarefa')
        table = 'commitments' if ref['kind'] == 'commitment' else 'meeting_actions'
        task = db.execute(text(f'SELECT * FROM {table} WHERE id=:id FOR UPDATE'), {'id': tid}).mappings().first()
        if not task:
            raise ValueError('Tarefa não encontrada.')
        if ref.get('version') != task['knowledge_version']:
            raise EditConflict('A tarefa mudou. Atualize a lista antes de vincular.')
        owner = values.get('owner_entity_id', row['owner_entity_id'])
        pid = db.execute(text('SELECT person_id FROM knowledge_entities WHERE id=:id'), {'id': owner}).scalar()
        if not pid or pid != task['person_id']:
            raise ValueError('A tarefa deve pertencer ao responsável cadastrado na afirmação.')
        if values.get('classification', row['classification']) != 'commitment':
            raise ValueError('Somente compromissos podem ser vinculados como suporte de uma tarefa.')
        values.update(commitment_id=tid if ref['kind'] == 'commitment' else None,
                      meeting_action_id=tid if ref['kind'] == 'meeting_action' else None)
    if data.get('resolve_conflict') is True:
        if not reason:
            raise ValueError('Informe a justificativa para resolver o conflito.')
        values['conflict'] = None
    if not values:
        raise ValueError('Nenhuma alteração informada.')
    changed = {k: v for k, v in values.items() if row[k] != v}
    human = dict(row['human_fields'] or {})
    human.update({k: True for k in changed if k != 'conflict'})
    if confirms_task:
        human.update(due_date=True, operational_state=True)
    if changed and 'review_state' not in values:
        values['review_state'] = 'corrected'
        human['review_state'] = True
    values['human_fields'] = human
    sets, params = [], {'id': cid}
    for key, value in values.items():
        if key in {'entity_ids', 'human_fields', 'conflict'}:
            sets.append(key + '=CAST(:' + key + ' AS JSONB)')
            params[key] = json.dumps(value) if value is not None else None
        else:
            sets.append(key + '=:' + key)
            params[key] = value
    updated = db.execute(text('UPDATE knowledge_claims SET ' + ','.join(sets) +
        ',version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=:id RETURNING *'), params).mappings().first()
    # Task edits use the very same canonical tables as the profile task flows.
    kind = 'commitment' if updated['commitment_id'] else 'meeting_action' if updated['meeting_action_id'] else None
    if kind and updated['classification'] == 'commitment' and (confirms_task or any(k in changed for k in ('content', 'due_date', 'operational_state', 'owner_entity_id'))):
        effective_changes = {**changed, **({'due_date': updated['due_date'], 'operational_state': updated['operational_state']} if confirms_task else {})}
        _edit_task(db, kind, updated['commitment_id'] or updated['meeting_action_id'], updated, effective_changes, reason, confirms_task)
    elif not kind and updated['task_accepted'] and updated['classification'] == 'commitment' and updated['review_state'] != 'rejected':
        core.sync_claim_task(db, dict(updated), session['user_id'])
    if updated['review_state'] == 'rejected' or updated['classification'] != 'commitment' or not updated['task_accepted']:
        _cancel_unsupported_task(db, updated, reason)
    if 'task_ref' in data:
        _cancel_unsupported_task(db, row, reason)
    final = db.execute(text('SELECT * FROM knowledge_claims WHERE id=:id'), {'id': cid}).mappings().first()
    core.audit(db, 'claim', cid, dict(row), dict(final), session['user_id'], reason)
    core.invalidate_derived(db)
    return jsonify(_detail(db, cid))


def _edit_task(db, kind, tid, claim, changed, reason, confirmed=False):
    table = 'commitments' if kind == 'commitment' else 'meeting_actions'
    before = db.execute(text(f'SELECT * FROM {table} WHERE id=:id FOR UPDATE'), {'id': tid}).mappings().first()
    if not before:
        raise EditConflict('A tarefa foi excluída. Recarregue o registro.')
    task_changes = {}
    for key, field in (('content', 'description'), ('due_date', 'due_date'), ('operational_state', 'status')):
        if key in changed:
            task_changes[field] = claim[key]
    if task_changes.get('status') == 'none':
        raise ValueError('Tarefas precisam de estado open, done ou cancelled.')
    if 'owner_entity_id' in changed:
        pid = db.execute(text('SELECT person_id FROM knowledge_entities WHERE id=:id'), {'id': claim['owner_entity_id']}).scalar()
        if not pid:
            raise ValueError('Selecione uma pessoa cadastrada como responsável pela tarefa.')
        task_changes['person_id'] = pid
        if kind == 'commitment':
            task_changes['owner'] = 'person'
        else:
            task_changes['owner_name'] = db.execute(text('SELECT full_name FROM people WHERE id=:id'), {'id': pid}).scalar()
    sets = [k + '=:' + k for k in task_changes]
    if 'status' in task_changes:
        sets.append("closed_at=NULL" if task_changes['status'] == 'open' else 'closed_at=COALESCE(closed_at,CURRENT_TIMESTAMP)')
    after = db.execute(text(f"UPDATE {table} SET {','.join(sets)},updated_at=CURRENT_TIMESTAMP WHERE id=:id RETURNING *"),
                       {**task_changes, 'id': tid}).mappings().first()
    record_task_change(db, kind, tid, dict(before), dict(after), session['user_id'], reason or 'Curadoria da memória',
                       force_fields=['due_date', 'status'] if confirmed else None)


def _cancel_unsupported_task(db, claim, reason):
    for table, column, kind in (('commitments', 'commitment_id', 'commitment'), ('meeting_actions', 'meeting_action_id', 'meeting_action')):
        tid = claim[column]
        if not tid:
            continue
        support = db.execute(text(f"""SELECT COUNT(*) FROM knowledge_claims c LEFT JOIN knowledge_sources s ON s.id=c.source_id
            WHERE c.{column}=:id AND c.active AND c.review_state<>'rejected'
            AND c.classification='commitment' AND c.task_accepted
            AND (c.source_id IS NULL OR NOT s.deleted)"""), {'id': tid}).scalar()
        if not support:
            before = db.execute(text(f'SELECT * FROM {table} WHERE id=:id FOR UPDATE'), {'id': tid}).mappings().first()
            if before and before.get('knowledge_created') and not before['knowledge_human_fields'] and before['status'] == 'open':
                after = db.execute(text(f"UPDATE {table} SET status='cancelled',closed_at=CURRENT_TIMESTAMP,knowledge_version=knowledge_version+1 WHERE id=:id RETURNING *"), {'id': tid}).mappings().first()
                core.audit(db, kind, tid, dict(before), dict(after), session['user_id'], reason or 'Única interpretação rejeitada')
            elif before and before['status'] == 'open':
                conflict = {**dict(claim.get('conflict') or {}), 'task_retained': {
                    'kind': kind, 'id': tid, 'status': before['status'], 'due_date': str(before['due_date']) if before['due_date'] else None,
                    'reason': 'A tarefa possui estado humano independente. A revisão da interpretação não cancela a tarefa; revise o estado operacional explicitamente.'}}
                db.execute(text('UPDATE knowledge_claims SET conflict=CAST(:conflict AS JSONB) WHERE id=:id'),
                           {'conflict': json.dumps(conflict), 'id': claim['id']})


@bp.post('/relations')
@guard
def create_relation(db):
    data = body()
    values = relation_values(db, data, True)
    values['origin'] = 'manual'
    values.setdefault('review_state', 'accepted')
    key = json.dumps([values['from_entity_id'], values['to_entity_id'], values['relation_type'], values.get('claim_id')])
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': key})
    existing = db.execute(text('''SELECT * FROM knowledge_relations WHERE from_entity_id=:from_entity_id
        AND to_entity_id=:to_entity_id AND relation_type=:relation_type
        AND claim_id IS NOT DISTINCT FROM :cid FOR UPDATE'''), {**values, 'cid': values.get('claim_id')}).mappings().first()
    if existing:
        return jsonify({'relation': serial(existing), 'existing': True})
    keys = list(values)
    row = db.execute(text(f"INSERT INTO knowledge_relations({','.join(keys)}) VALUES({','.join(':'+k for k in keys)}) RETURNING *"), values).mappings().first()
    core.audit(db, 'relation', row['id'], None, dict(row), session['user_id'], data.get('reason') or 'Relação manual')
    core.invalidate_derived(db)
    return jsonify({'relation': serial(row)}), 201


def relation_values(db, data, create=False):
    result = {}
    for key in ('from_entity_id', 'to_entity_id'):
        if key in data or create:
            result[key] = integer(data.get(key), key)
    check_entities(db, result.values())
    if 'relation_type' in data or create:
        if data.get('relation_type') not in RELATIONS:
            raise ValueError('Tipo de relação inválido.')
        result['relation_type'] = data['relation_type']
    if 'review_state' in data:
        if data['review_state'] not in REVIEWS:
            raise ValueError('Revisão inválida.')
        result['review_state'] = data['review_state']
    if 'claim_id' in data:
        cid = integer(data['claim_id'], 'afirmação', optional=True)
        if cid and not _detail(db, cid):
            raise ValueError('Afirmação não encontrada.')
        result['claim_id'] = cid
    return result


@bp.put('/relations/<int:rid>')
@guard
def update_relation(db, rid):
    data = body()
    row = db.execute(text('SELECT * FROM knowledge_relations WHERE id=:id FOR UPDATE'), {'id': rid}).mappings().first()
    if not row:
        return jsonify({'error': 'Relação não encontrada.'}), 404
    if row['claim_id'] and not _detail(db, row['claim_id']):
        return jsonify({'error': 'Fonte excluída.'}), 404
    check_version(data, row)
    values = relation_values(db, data)
    if not values:
        raise ValueError('Nenhuma alteração informada.')
    human = {**dict(row['human_fields'] or {}), **{k: True for k in values}}
    sets = [k + '=:' + k for k in values]
    after = db.execute(text('UPDATE knowledge_relations SET ' + ','.join(sets) +
        ',human_fields=CAST(:human AS JSONB),version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=:id RETURNING *'),
        {**values, 'id': rid, 'human': json.dumps(human)}).mappings().first()
    core.audit(db, 'relation', rid, dict(row), dict(after), session['user_id'], data.get('reason'))
    core.invalidate_derived(db)
    return jsonify({'relation': serial(after)})


@bp.get('/relations/<int:rid>')
@guard
def relation_detail(db, rid):
    row = db.execute(text('SELECT * FROM knowledge_relations WHERE id=:id'), {'id': rid}).mappings().first()
    if not row:
        return jsonify({'error': 'Relação não encontrada.'}), 404
    detail = _detail(db, row['claim_id']) if row['claim_id'] else None
    if row['claim_id'] and not detail:
        return jsonify({'error': 'Fonte excluída.'}), 404
    revisions = [serial(r) for r in db.execute(text("""SELECT r.*,u.name AS actor_name FROM knowledge_revisions r
        LEFT JOIN users u ON u.id=r.actor_user_id WHERE record_type='relation' AND record_id=:id
        ORDER BY r.id DESC LIMIT 100"""), {'id': rid}).mappings().all()]
    return jsonify({'relation': serial(row), 'evidence': detail['evidence'] if detail else None,
                    'claim': detail['claim'] if detail else None, 'revisions': revisions})


@bp.get('/tasks')
@guard
def tasks(db):
    return jsonify(list_tasks(db, person_id=int(request.args['person_id']) if request.args.get('person_id') else None,
        status=request.args.get('status', 'open'), limit=int(request.args.get('limit', 100)), offset=int(request.args.get('offset', 0))))


@bp.get('/graph')
@guard
def graph(db):
    core.sync_people(db)
    args = filters()
    args.pop('offset', None)
    return jsonify(query_graph(db, **args, depth=request.args.get('depth', 1)))


@bp.get('/jobs')
@guard
def jobs(db):
    offset = max(0, int(request.args.get('offset', 0)))
    limit = max(1, min(int(request.args.get('limit', 100)), 100))
    rows = db.execute(text("""SELECT j.id,j.source_id,j.version_id,j.status,j.attempts,j.error,
        j.metrics_json,j.cursor_json,j.lease_until,j.updated_at,s.kind AS source_kind,s.record_id,s.title
        FROM knowledge_jobs j JOIN knowledge_sources s ON s.id=j.source_id
        WHERE NOT s.deleted ORDER BY CASE WHEN j.status IN ('pending','running','error') THEN 0 ELSE 1 END,
        j.id DESC LIMIT :limit OFFSET :offset"""), {'limit': limit, 'offset': offset}).mappings().all()
    total = db.execute(text('SELECT COUNT(*) FROM knowledge_jobs j JOIN knowledge_sources s ON s.id=j.source_id WHERE NOT s.deleted')).scalar()
    return jsonify({'jobs': [serial(r) for r in rows], 'total': total,
                    'next_offset': offset + len(rows) if offset + len(rows) < total else None})


@bp.post('/jobs/<int:jid>/run')
@login_required
def run_job(jid):
    if not core.enabled():
        return jsonify({'error': 'Memória desativada.'}), 503
    try:
        result = core.process_job(jid, session['user_id'])
        return jsonify(result)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        log.warning('Etapa da memória falhou (%s).', type(exc).__name__)
        return jsonify({'error': 'Não foi possível processar esta etapa. O progresso confirmado está preservado.'}), 502


@bp.post('/jobs/<int:jid>/retry')
@guard
def retry_job(db, jid):
    job = db.execute(text('''SELECT j.* FROM knowledge_jobs j JOIN knowledge_sources s ON s.id=j.source_id
        WHERE j.id=:id AND NOT s.deleted AND s.current_version_id=j.version_id FOR UPDATE OF j'''), {'id': jid}).mappings().first()
    if not job:
        return jsonify({'error': 'Processamento atual não encontrado.'}), 404
    if job['status'] != 'error':
        raise EditConflict('Somente um processamento com falha pode ter as tentativas reabertas.')
    after = db.execute(text("""UPDATE knowledge_jobs SET status='pending',attempts=0,error=NULL,
        lease_token=NULL,lease_until=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=:id RETURNING *"""), {'id': jid}).mappings().first()
    # Do not duplicate staged transcript-derived candidates in the audit.
    core.audit(db, 'job', jid, {'status': job['status'], 'attempts': job['attempts']},
               {'status': after['status'], 'attempts': after['attempts']}, session['user_id'], 'Retomada manual após falha')
    return jsonify({'job_id': jid, 'status': 'pending'})


@bp.post('/backfill')
@guard
def backfill(db):
    data = body()
    kind = data.get('kind')
    if kind not in {None, 'one_on_one', 'meeting'}:
        raise ValueError('Tipo de fonte inválido.')
    if type(data.get('dry_run', True)) is not bool:
        raise ValueError('Simulação inválida.')
    limit = min(integer(data.get('limit', 10), 'limite'), 20)
    after = data.get('after_id', 0)
    if type(after) is not int or after < 0:
        raise ValueError('Cursor inválido.')
    # Deterministic global cursor independent of per-table ID collisions.
    rows = db.execute(text("""SELECT * FROM (
       SELECT id*2 AS cursor,id,'one_on_one' AS kind,title,occurred_on FROM one_on_ones
         WHERE COALESCE(transcript_raw,'')<>'' OR COALESCE(private_notes,'')<>'' OR COALESCE(public_notes,'')<>''
       UNION ALL SELECT id*2+1,id,'meeting',title,occurred_on FROM meetings
         WHERE COALESCE(transcript_raw,'')<>'' OR COALESCE(notes,'')<>''
       ) sources WHERE cursor>:after AND (:kind IS NULL OR kind=:kind)
       ORDER BY cursor LIMIT :limit"""), {'after': after, 'kind': kind, 'limit': limit}).mappings().all()
    results = []
    for row in rows:
        item = serial(row)
        if not data.get('dry_run', True):
            item['job'] = core.enqueue_source(db, row['kind'], row['id'], session['user_id'])
        results.append(item)
    return jsonify({'dry_run': data.get('dry_run', True), 'sources': results,
                    'next_after_id': rows[-1]['cursor'] if rows else None, 'limit': limit})


@bp.get('/export')
@guard
def export(db):
    from ..knowledge_export import export_vault
    # One MVCC snapshot prevents mixed revisions in a downloaded projection.
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
    data = export_vault(db)
    return send_file(BytesIO(data), mimetype='application/zip', as_attachment=True,
                     download_name='cortex-memoria.zip', max_age=0)
