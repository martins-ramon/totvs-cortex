"""Reuniões de fóruns e encaminhamentos revisados pelo diretor."""
import json
import logging
from datetime import date

from flask import Blueprint, jsonify, request
from sqlalchemy import bindparam, text

from ..database import session_factory
from ..meeting_insights import (analysis_stale, decode_json, extract_forum_insights,
                                source_fingerprint)
from ..meetings_context import iso, load_participants, serialize_action
from ..security import login_required
from ..transcripts import load_transcript_people

bp = Blueprint('meetings', __name__, url_prefix='/api')
log = logging.getLogger('cortex.meetings')
_FIELDS = {'title', 'forum', 'occurred_on', 'external_participants', 'transcript_raw', 'notes'}
_STATUSES = {'open', 'done', 'cancelled'}


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('Envie um objeto JSON válido.')
    return data


def _date(value, field, optional=False):
    if optional and value in (None, ''):
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(f"Data inválida em '{field}' (use YYYY-MM-DD).")


def _string(value, field, maximum, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError(f"O campo '{field}' deve ser um texto.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"O campo '{field}' é obrigatório.")
    if len(value) > maximum:
        raise ValueError(f"O campo '{field}' aceita até {maximum} caracteres.")
    return value or None


def _participant_ids(value):
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError('Informe uma lista de até 200 participantes cadastrados.')
    if any(type(v) is not int or v <= 0 for v in value):
        raise ValueError('Participantes devem ser identificadores inteiros válidos.')
    return list(dict.fromkeys(value))


def _validate_people(db, ids):
    if not ids:
        return
    statement = text('SELECT id FROM people WHERE id IN :ids').bindparams(bindparam('ids', expanding=True))
    found = {row[0] for row in db.execute(statement, {'ids': ids}).fetchall()}
    if found != set(ids):
        raise ValueError('Um ou mais participantes não foram encontrados.')


def _meeting_payload(data, create=False):
    result = {}
    for field in _FIELDS:
        if field not in data and not (create and field in {'title', 'occurred_on'}):
            continue
        if field == 'occurred_on':
            result[field] = _date(data.get(field), field)
        else:
            maximum = 255 if field in {'title', 'forum'} else 150000 if field == 'transcript_raw' else 50000
            result[field] = _string(data.get(field), field, maximum, field == 'title')
    if 'participant_ids' in data or create:
        result['participant_ids'] = _participant_ids(data.get('participant_ids', []))
    if not result:
        raise ValueError('Nenhum campo para atualizar.')
    return result


def _replace_participants(db, mid, ids):
    db.execute(text('DELETE FROM meeting_participants WHERE meeting_id = :mid'), {'mid': mid})
    for pid in ids:
        db.execute(text('INSERT INTO meeting_participants (meeting_id, person_id) VALUES (:mid, :pid)'),
                   {'mid': mid, 'pid': pid})


def _load(db, mid, lock=False):
    row = db.execute(text('SELECT * FROM meetings WHERE id = :mid' + (' FOR UPDATE' if lock else '')),
                     {'mid': mid}).mappings().first()
    if row is None:
        return None
    meeting = dict(row)
    participants = load_participants(db, mid)
    extraction = decode_json(meeting.pop('extraction_json', None))
    if not isinstance(extraction, dict):
        extraction = None
    meeting.update(occurred_on=iso(meeting['occurred_on']), participants=participants,
                   participant_ids=[p['id'] for p in participants],
                   has_transcript=bool(meeting.get('transcript_raw')),
                   topics=decode_json(meeting.get('topics'), []), extraction=extraction,
                   analysis_stale=analysis_stale({**meeting, 'extraction_json': extraction}, participants))
    for key in ('created_at', 'updated_at'):
        if key in meeting:
            meeting[key] = iso(meeting[key])
    meeting['actions'] = [serialize_action(r) for r in db.execute(text('''
        SELECT a.* FROM meeting_actions a WHERE a.meeting_id = :mid
        ORDER BY CASE WHEN a.status = 'open' THEN 0 ELSE 1 END, a.id
    '''), {'mid': mid}).mappings().all()]
    return meeting


def _error(db, exc):
    db.rollback()
    log.warning('Operação de reuniões falhou (%s).', type(exc).__name__)
    return jsonify({'error': 'Não foi possível salvar os dados da reunião.'}), 500


@bp.get('/meetings')
@login_required
def list_meetings():
    try:
        filters, params = [], {}
        for key, comparison in (('date_from', '>='), ('date_to', '<=')):
            if request.args.get(key):
                params[key] = _date(request.args[key], key)
                filters.append(f'm.occurred_on {comparison} :{key}')
        if params.get('date_from') and params.get('date_to') and params['date_from'] > params['date_to']:
            raise ValueError('A data inicial deve ser anterior à data final.')
        if request.args.get('q', '').strip():
            params['q'] = '%' + request.args['q'].strip().lower()[:300] + '%'
            filters.append("(LOWER(m.title) LIKE :q OR LOWER(COALESCE(m.forum, '')) LIKE :q OR LOWER(COALESCE(m.notes, '')) LIKE :q OR LOWER(COALESCE(m.transcript_raw, '')) LIKE :q)")
        if request.args.get('forum'):
            params['forum'] = request.args['forum']
            filters.append('m.forum = :forum')
        if request.args.get('person_id'):
            params['pid'] = int(request.args['person_id'])
            if params['pid'] <= 0:
                raise ValueError('Participante inválido.')
            filters.append('''(EXISTS (SELECT 1 FROM meeting_participants mp WHERE mp.meeting_id = m.id AND mp.person_id = :pid)
                               OR EXISTS (SELECT 1 FROM meeting_actions ma WHERE ma.meeting_id = m.id AND ma.person_id = :pid))''')
        if request.args.get('status'):
            status = request.args['status']
            if status not in _STATUSES:
                raise ValueError('Status de encaminhamento inválido.')
            params['status'] = status
            filters.append('EXISTS (SELECT 1 FROM meeting_actions ma WHERE ma.meeting_id = m.id AND ma.status = :status)')
        limit = max(1, min(int(request.args.get('limit', 50)), 200))
        offset = max(0, int(request.args.get('offset', 0)))
    except (ValueError, TypeError) as exc:
        return jsonify({'error': str(exc)}), 400
    db = session_factory()
    try:
        where = ' WHERE ' + ' AND '.join(filters) if filters else ''
        total = db.execute(text('SELECT COUNT(*) FROM meetings m' + where), params).scalar()
        rows = db.execute(text("SELECT m.*, (SELECT COUNT(*) FROM meeting_actions ma WHERE ma.meeting_id = m.id AND ma.status = 'open') AS open_action_count FROM meetings m" + where +
                              ' ORDER BY m.occurred_on DESC, m.id DESC LIMIT :limit OFFSET :offset'),
                          {**params, 'limit': limit, 'offset': offset}).mappings().all()
        meetings = []
        for row in rows:
            meeting = dict(row)
            participants = load_participants(db, row['id'])
            stale = analysis_stale(meeting, participants)
            meeting.update(occurred_on=iso(row['occurred_on']), participants=participants,
                           participant_ids=[p['id'] for p in participants],
                           has_transcript=bool(meeting.pop('transcript_raw', None)),
                           analysis_stale=stale, topics=[] if stale else decode_json(row.get('topics'), []),
                           summary_ai=None if stale else row.get('summary_ai'))
            for key in ('notes', 'extraction_json'):
                meeting.pop(key, None)
            for key in ('created_at', 'updated_at'):
                if key in meeting:
                    meeting[key] = iso(meeting[key])
            meetings.append(meeting)
        forums = [r[0] for r in db.execute(text("SELECT DISTINCT forum FROM meetings WHERE forum IS NOT NULL AND forum <> '' ORDER BY forum")).fetchall()]
        actions = []
        if params.get('pid'):
            actions = [serialize_action(r) for r in db.execute(text('''
                SELECT a.*, m.title AS meeting_title, m.forum
                FROM meeting_actions a JOIN meetings m ON m.id = a.meeting_id
                WHERE a.person_id = :pid AND a.status = 'open'
                ORDER BY CASE WHEN a.due_date IS NULL THEN 1 ELSE 0 END,
                         a.due_date, a.id DESC LIMIT 200
            '''), {'pid': params['pid']}).mappings().all()]
        return jsonify({'meetings': meetings, 'total': total, 'forums': forums, 'actions': actions})
    finally:
        db.close()


@bp.post('/meetings')
@login_required
def create_meeting():
    try:
        values = _meeting_payload(_body(), create=True)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    db = session_factory()
    try:
        ids = values.pop('participant_ids')
        _validate_people(db, ids)
        keys = list(values)
        mid = db.execute(text(f"INSERT INTO meetings ({', '.join(keys)}) VALUES ({', '.join(':' + k for k in keys)}) RETURNING id"), values).fetchone()[0]
        _replace_participants(db, mid, ids)
        db.commit()
        return jsonify({'success': True, 'meeting_id': mid}), 201
    except ValueError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return _error(db, exc)
    finally:
        db.close()


@bp.get('/meetings/<int:mid>')
@login_required
def get_meeting(mid):
    db = session_factory()
    try:
        meeting = _load(db, mid)
        if meeting is None:
            return jsonify({'error': 'Reunião não encontrada.'}), 404
        return jsonify({'meeting': meeting})
    finally:
        db.close()


@bp.put('/meetings/<int:mid>')
@login_required
def update_meeting(mid):
    try:
        values = _meeting_payload(_body())
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    db = session_factory()
    try:
        if db.execute(text('SELECT id FROM meetings WHERE id = :mid FOR UPDATE'), {'mid': mid}).fetchone() is None:
            return jsonify({'error': 'Reunião não encontrada.'}), 404
        ids = values.pop('participant_ids', None)
        if ids is not None:
            _validate_people(db, ids)
            _replace_participants(db, mid, ids)
        sets = [f'{key} = :{key}' for key in values] + ['updated_at = CURRENT_TIMESTAMP']
        db.execute(text(f"UPDATE meetings SET {', '.join(sets)} WHERE id = :mid"), {**values, 'mid': mid})
        db.commit()
        return jsonify({'success': True})
    except ValueError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return _error(db, exc)
    finally:
        db.close()


@bp.delete('/meetings/<int:mid>')
@login_required
def delete_meeting(mid):
    db = session_factory()
    try:
        count = db.execute(text('DELETE FROM meetings WHERE id = :mid'), {'mid': mid}).rowcount
        db.commit()
        return (jsonify({'success': True}) if count else
                (jsonify({'error': 'Reunião não encontrada.'}), 404))
    except Exception as exc:
        return _error(db, exc)
    finally:
        db.close()


@bp.post('/meetings/<int:mid>/extract')
@login_required
def extract_meeting(mid):
    db = session_factory()
    try:
        meeting = _load(db, mid)
        if meeting is None:
            return jsonify({'error': 'Reunião não encontrada.'}), 404
        if not (meeting.get('transcript_raw') or meeting.get('notes')):
            return jsonify({'error': 'Registre uma transcrição ou notas para analisar.'}), 400
        people = load_transcript_people(db)
        fingerprint = source_fingerprint(meeting, meeting['participants'])
    finally:
        db.close()
    try:
        extraction = extract_forum_insights(meeting, meeting['participants'], people)
    except Exception as exc:
        log.warning('Extração de reunião falhou (%s).', type(exc).__name__)
        return jsonify({'error': 'Não foi possível analisar a reunião agora. Seu registro foi preservado; tente novamente.'}), 502
    db = session_factory()
    try:
        current = _load(db, mid, lock=True)
        if current is None:
            return jsonify({'error': 'Reunião não encontrada.'}), 404
        if source_fingerprint(current, current['participants']) != fingerprint:
            return jsonify({'error': 'A reunião foi alterada durante a análise. Execute a análise novamente.'}), 409
        # IA sugere; apenas a adoção explícita cria encaminhamentos.
        extraction['source_fingerprint'] = fingerprint
        db.execute(text('''UPDATE meetings SET summary_ai = :summary,
            topics = CAST(:topics AS JSONB), extraction_json = CAST(:extraction AS JSONB),
            updated_at = CURRENT_TIMESTAMP WHERE id = :mid'''),
                   {'mid': mid, 'summary': extraction['summary'],
                    'topics': json.dumps(extraction['topics'], ensure_ascii=False),
                    'extraction': json.dumps(extraction, ensure_ascii=False)})
        db.commit()
        return jsonify({'success': True, 'meeting': _load(db, mid)})
    except Exception as exc:
        return _error(db, exc)
    finally:
        db.close()


def _action_payload(data, create=False):
    result = {}
    if 'description' in data or create:
        result['description'] = _string(data.get('description'), 'description', 4000, True)
    if 'owner_name' in data or create:
        result['owner_name'] = _string(data.get('owner_name'), 'owner_name', 255)
    if 'person_id' in data or create:
        pid = data.get('person_id')
        if pid is not None and (type(pid) is not int or pid <= 0):
            raise ValueError('Responsável cadastrado inválido.')
        result['person_id'] = pid
    if 'due_date' in data or create:
        result['due_date'] = _date(data.get('due_date'), 'due_date', optional=True)
    if 'status' in data and not create:
        if not isinstance(data['status'], str) or data['status'] not in _STATUSES:
            raise ValueError('Status inválido (use open, done ou cancelled).')
        result['status'] = data['status']
    if create:
        result['source_key'] = _string(data.get('source_key'), 'source_key', 128)
    if not result:
        raise ValueError('Nenhum campo para atualizar.')
    return result


def _resolve_owner(db, values):
    if values.get('person_id'):
        row = db.execute(text('SELECT full_name FROM people WHERE id = :pid'), {'pid': values['person_id']}).fetchone()
        if row is None:
            raise ValueError('Responsável cadastrado não encontrado.')
        values['owner_name'] = row[0]


@bp.post('/meetings/<int:mid>/actions')
@login_required
def create_action(mid):
    try:
        values = _action_payload(_body(), create=True)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    db = session_factory()
    try:
        meeting = _load(db, mid, lock=True)
        if meeting is None:
            return jsonify({'error': 'Reunião não encontrada.'}), 404
        if values.get('source_key'):
            # Repetir uma adoção já concluída não cria uma nova ação, mesmo se
            # a fonte foi editada depois. Novas adoções precisam da análise atual.
            existing = next((a for a in meeting['actions']
                             if a.get('source_key') == values['source_key']), None)
            if existing is not None:
                return jsonify({'success': True, 'action': existing})
            extraction = meeting.get('extraction')
            proposals = extraction.get('actions', []) if isinstance(extraction, dict) else []
            if not isinstance(proposals, list):
                proposals = []
            if meeting['analysis_stale'] or not any(
                    isinstance(a, dict) and a.get('source_key') == values['source_key']
                    for a in proposals):
                return jsonify({'error': 'Esta sugestão não faz parte da análise atual. Analise a reunião novamente antes de adotar encaminhamentos.'}), 409
        _resolve_owner(db, values)
        keys = list(values) + ['meeting_id']
        values['meeting_id'] = mid
        row = db.execute(text(f"INSERT INTO meeting_actions ({', '.join(keys)}) VALUES ({', '.join(':' + k for k in keys)}) RETURNING *"), values).mappings().first()
        action = serialize_action(row)
        db.commit()
        return jsonify({'success': True, 'action': action}), 201
    except ValueError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return _error(db, exc)
    finally:
        db.close()


@bp.put('/meeting-actions/<int:aid>')
@login_required
def update_action(aid):
    try:
        values = _action_payload(_body())
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    db = session_factory()
    try:
        if 'owner_name' in values and 'person_id' not in values:
            current = db.execute(text('SELECT person_id, owner_name FROM meeting_actions WHERE id = :aid FOR UPDATE'),
                                 {'aid': aid}).mappings().first()
            if current is None:
                return jsonify({'error': 'Encaminhamento não encontrado.'}), 404
            if current['person_id'] is not None and values['owner_name'] != current['owner_name']:
                return jsonify({'error': 'Informe person_id ao alterar o responsável cadastrado. Use null para um responsável externo.'}), 400
        _resolve_owner(db, values)
        sets = [f'{key} = :{key}' for key in values] + ['updated_at = CURRENT_TIMESTAMP']
        if 'status' in values:
            sets.append('closed_at = NULL' if values['status'] == 'open' else 'closed_at = COALESCE(closed_at, CURRENT_TIMESTAMP)')
        row = db.execute(text(f"UPDATE meeting_actions SET {', '.join(sets)} WHERE id = :aid RETURNING *"),
                         {**values, 'aid': aid}).mappings().first()
        if row is None:
            return jsonify({'error': 'Encaminhamento não encontrado.'}), 404
        action = serialize_action(row)
        db.commit()
        return jsonify({'success': True, 'action': action})
    except ValueError as exc:
        db.rollback()
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return _error(db, exc)
    finally:
        db.close()


@bp.delete('/meeting-actions/<int:aid>')
@login_required
def delete_action(aid):
    db = session_factory()
    try:
        count = db.execute(text('DELETE FROM meeting_actions WHERE id = :aid'), {'aid': aid}).rowcount
        db.commit()
        return (jsonify({'success': True}) if count else
                (jsonify({'error': 'Encaminhamento não encontrado.'}), 404))
    except Exception as exc:
        return _error(db, exc)
    finally:
        db.close()
