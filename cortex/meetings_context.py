"""Contexto compartilhado de reuniões diversas, sem atribuição por presença."""
import json
from datetime import date

from sqlalchemy import text

from .meeting_insights import analysis_stale, decode_json


def iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


def load_participants(db, meeting_id):
    return [dict(r) for r in db.execute(text('''
        SELECT p.id, p.full_name FROM people p
        JOIN meeting_participants mp ON mp.person_id = p.id
        WHERE mp.meeting_id = :mid ORDER BY p.full_name, p.id
    '''), {'mid': meeting_id}).mappings().all()]


def serialize_action(row):
    result = dict(row)
    for key in ('due_date', 'closed_at', 'created_at', 'updated_at'):
        if key in result:
            result[key] = iso(result[key])
    result['is_overdue'] = bool(result.get('status') == 'open' and result.get('due_date')
                                and str(result['due_date']) < date.today().isoformat())
    return result


def load_meeting_context(db, person_id=None, limit=8, as_of=None):
    limit = max(1, min(int(limit), 50))
    params = {'limit': limit}
    conditions = []
    if person_id is not None:
        params['pid'] = person_id
        conditions.append('''(EXISTS (SELECT 1 FROM meeting_participants mp
                       WHERE mp.meeting_id = m.id AND mp.person_id = :pid)
                   OR EXISTS (SELECT 1 FROM meeting_actions ma
                       WHERE ma.meeting_id = m.id AND ma.person_id = :pid))''')
    if as_of is not None:
        conditions.append('m.occurred_on <= :as_of')
        params['as_of'] = as_of
    where = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
    rows = db.execute(text(f'''
        SELECT m.id, m.title, m.forum, m.occurred_on, m.external_participants,
               m.transcript_raw, m.notes, m.summary_ai, m.extraction_json
        FROM meetings m {where}
        ORDER BY m.occurred_on DESC, m.id DESC LIMIT :limit
    '''), params).mappings().all()
    meetings = []
    for row in rows:
        meeting = dict(row)
        participants = load_participants(db, meeting['id'])
        stale = analysis_stale(meeting, participants)
        extraction = decode_json(meeting.pop('extraction_json'), {})
        if not isinstance(extraction, dict):
            extraction = {}
        current_summary = not stale and bool(meeting.get('summary_ai'))
        meeting.update(occurred_on=iso(meeting['occurred_on']), participants=participants,
                       analysis_stale=stale, extraction={} if stale else extraction,
                       # Fonte atual disponível mesmo sem análise ou após edição.
                       notes='' if current_summary else (meeting.get('notes') or '')[:1500],
                       transcript_excerpt='' if current_summary else (meeting.pop('transcript_raw') or '')[:4000])
        meeting.pop('transcript_raw', None)
        if stale:
            meeting['summary_ai'] = None
        meetings.append(meeting)
    action_params = {'limit': min(limit * 8, 200)}
    action_filter = ''
    if person_id is not None:
        action_filter = 'AND a.person_id = :pid'
        action_params['pid'] = person_id
    if as_of is not None:
        action_filter += ' AND m.occurred_on <= :as_of'
        action_params['as_of'] = as_of
    actions = [serialize_action(r) for r in db.execute(text(f'''
        SELECT a.*, m.title AS meeting_title, m.forum
        FROM meeting_actions a JOIN meetings m ON m.id = a.meeting_id
        WHERE a.status = 'open' {action_filter}
        ORDER BY CASE WHEN a.due_date IS NULL THEN 1 ELSE 0 END,
                 a.due_date, a.id DESC LIMIT :limit
    '''), action_params).mappings().all()]
    return {'meetings': meetings, 'actions': actions}


def format_meeting_context(data):
    if not data.get('meetings') and not data.get('actions'):
        return ''
    # A agenda/cartão precisa de contexto complementar e cabe no mesmo orçamento
    # dos 1:1s. Mantém objetos completos e explicita o recorte, sem JSON cortado.
    compact = {'meetings': [], 'actions': []}
    for section, budget in (('meetings', 35000), ('actions', 10000)):
        for item in data.get(section, []):
            item = dict(item)
            item['source_id'] = ('meeting:' if section == 'meetings' else 'meeting_action:') + str(item['id'])
            if section == 'meetings':
                extraction = item.get('extraction')
                if isinstance(extraction, dict):
                    item['extraction'] = {
                        key: ([v[:300] for v in value[:4] if isinstance(v, str)]
                              if isinstance(value, list) and key != 'actions'
                              else value[:2000] if isinstance(value, str) else [])
                        for key, value in extraction.items()
                        if key in {'summary', 'topics', 'decisions', 'risks', 'opportunities',
                                   'strategic_themes', 'open_questions'}}
                item['summary_ai'] = (item.get('summary_ai') or '')[:2000]
            else:
                item['description'] = (item.get('description') or '')[:1200]
            encoded = json.dumps(item, ensure_ascii=False, default=str)
            if len(encoded) > budget:
                break
            compact[section].append(item)
            budget -= len(encoded)
    compact['coverage'] = {
        'meetings_included': len(compact['meetings']),
        'meetings_available': len(data.get('meetings', [])),
        'actions_included': len(compact['actions']),
        'actions_available': len(data.get('actions', [])),
        'note': 'Recorte complementar de reuniões recentes; transcrições sem análise são trechos.'}
    return ('\nREUNIÕES DIVERSAS / FÓRUNS E ENCAMINHAMENTOS\n'
            'Use como contexto complementar aos 1:1s. Presença ou menção não prova '
            'autoria de falas, responsabilidade, sentimento ou desempenho individual. '
            'Responsabilidade existe somente quando explicitamente atribuída. '
            'Análises desatualizadas foram omitidas; notas/transcrição refletem a fonte atual. '
            'Referencie reuniões por meeting:ID e ações por meeting_action:ID.\n'
            + json.dumps(compact, ensure_ascii=False, default=str))
