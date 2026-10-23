"""Fóruns usam banco local efêmero e IA mockada; não acessam integrações reais."""
import json
import unittest
from datetime import date
from unittest.mock import Mock, patch

from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cortex import database, meeting_insights
from cortex.meeting_insights import (analysis_stale, normalize_extraction,
                                    source_fingerprint)
from cortex.meetings_context import format_meeting_context, load_meeting_context
from cortex.views import meetings


class PortableSession:
    """Adapta apenas sintaxe PostgreSQL; executa o SQL real em SQLite efêmero."""
    def __init__(self, session):
        self.session = session
        self.closed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        if ' FOR UPDATE' in sql or ' AS JSONB)' in sql:
            sql = sql.replace(' FOR UPDATE', '').replace('CAST(:topics AS JSONB)', ':topics').replace('CAST(:extraction AS JSONB)', ':extraction')
            statement = text(sql)
        return self.session.execute(statement, params or {})

    def commit(self):
        self.session.commit()

    def rollback(self):
        self.session.rollback()

    def close(self):
        self.closed = True
        self.session.close()


class MeetingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as conn:
            conn.execute(text('PRAGMA foreign_keys = ON'))
            statements = [
                'CREATE TABLE people (id INTEGER PRIMARY KEY,full_name TEXT,preferred_name TEXT,transcript_aliases TEXT)',
                '''CREATE TABLE meetings (id INTEGER PRIMARY KEY,title TEXT NOT NULL,forum TEXT,
                    occurred_on DATE NOT NULL,external_participants TEXT,transcript_raw TEXT,notes TEXT,
                    summary_ai TEXT,topics TEXT DEFAULT '[]',extraction_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''',
                '''CREATE TABLE meeting_participants (meeting_id INTEGER REFERENCES meetings(id) ON DELETE CASCADE,
                    person_id INTEGER REFERENCES people(id) ON DELETE CASCADE,PRIMARY KEY(meeting_id,person_id))''',
                '''CREATE TABLE meeting_actions (id INTEGER PRIMARY KEY,
                    meeting_id INTEGER REFERENCES meetings(id) ON DELETE CASCADE,
                    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,description TEXT NOT NULL,
                    owner_name TEXT,due_date DATE,status TEXT DEFAULT 'open',source_key TEXT,closed_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(meeting_id,source_key))''',
                "INSERT INTO people VALUES (1,'André Pasold',NULL,'[]'),(2,'Outra Pessoa',NULL,'[]')",
            ]
            for statement in statements:
                conn.execute(text(statement))
        self.sessionmaker = sessionmaker(bind=self.engine)
        self.sessions = []
        self.factory = patch.object(meetings, 'session_factory', side_effect=self.new_session)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test')
        app.register_blueprint(meetings.bp)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 1

    def new_session(self):
        session = PortableSession(self.sessionmaker())
        self.sessions.append(session)
        return session

    def create(self, **updates):
        payload = {'title': 'Estratégia LYNN', 'forum': 'Fórum de produto',
                   'occurred_on': '2026-10-20', 'participant_ids': [1],
                   'notes': 'André irá revisar o plano.', 'transcript_raw': 'Discussão do fórum.'}
        payload.update(updates)
        response = self.client.post('/api/meetings', json=payload)
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['meeting_id']

    def read(self, mid):
        response = self.client.get(f'/api/meetings/{mid}')
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['meeting']

    def analyzed(self, mid):
        raw = {'summary': 'Plano discutido no fórum.', 'topics': ['Estratégia'],
               'decisions': ['Revisar plano'], 'actions': [
                   {'description': 'Revisar plano', 'owner_name': 'André Pasold', 'due_date': None}]}
        normalized = normalize_extraction(raw, [{'id': 1, 'full_name': 'André Pasold'}])
        with patch.object(meetings, 'extract_forum_insights', return_value=normalized):
            response = self.client.post(f'/api/meetings/{mid}/extract')
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['meeting']

    def test_all_routes_require_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        for method, path in [('get','/api/meetings'),('post','/api/meetings'),
                             ('get','/api/meetings/1'),('put','/api/meetings/1'),
                             ('delete','/api/meetings/1'),('post','/api/meetings/1/extract'),
                             ('post','/api/meetings/1/actions'),('put','/api/meeting-actions/1')]:
            with self.subTest(path=path, method=method):
                self.assertEqual(getattr(self.client, method)(path, json={}).status_code, 401)
        self.assertEqual(self.sessions, [])

    def test_create_read_filter_and_participant_deduplication(self):
        mid = self.create(participant_ids=[1, 1], external_participants='Consultor externo')
        meeting = self.read(mid)
        self.assertEqual(meeting['participant_ids'], [1])
        self.assertEqual(meeting['participants'], [{'id':1, 'full_name':'André Pasold'}])
        response = self.client.get('/api/meetings?q=LYNN&person_id=1&date_from=2026-10-01&limit=2000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['total'], 1)
        self.assertNotIn('transcript_raw', response.json['meetings'][0])
        self.assertEqual(response.json['forums'], ['Fórum de produto'])

    def test_invalid_fields_do_not_persist(self):
        for changes in [{'title': ''}, {'occurred_on': 'not-a-date'}, {'participant_ids': [True]},
                        {'participant_ids': ['1']}, {'participant_ids': [999]}, {'notes': []}]:
            payload = {'title':'Reunião','occurred_on':'2026-10-20', **changes}
            self.assertEqual(self.client.post('/api/meetings', json=payload).status_code, 400, changes)
        self.assertEqual(self.client.get('/api/meetings').json['total'], 0)
        self.assertEqual(self.client.put('/api/meetings/1', json={'occurred_on':'2026-99-01'}).status_code, 400)
        self.assertEqual(self.client.get('/api/meetings?date_from=wrong').status_code, 400)

    def test_extraction_closes_db_during_network_and_preserves_on_error(self):
        mid = self.create()
        def fail(*args):
            self.assertTrue(self.sessions[-1].closed)
            raise RuntimeError('secret API key / SQL details')
        with patch.object(meetings, 'extract_forum_insights', side_effect=fail):
            response = self.client.post(f'/api/meetings/{mid}/extract')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret', response.json['error'])
        self.assertEqual(self.read(mid)['notes'], 'André irá revisar o plano.')
        self.assertIsNone(self.read(mid)['extraction'])

    def test_extraction_suggests_but_creates_no_actions(self):
        meeting = self.analyzed(self.create())
        self.assertEqual(meeting['actions'], [])
        self.assertFalse(meeting['analysis_stale'])
        self.assertEqual(meeting['extraction']['actions'][0]['person_id'], 1)
        self.assertTrue(meeting['extraction']['actions'][0]['source_key'])

    def test_concurrent_edit_prevents_stale_analysis_write(self):
        mid = self.create()
        def mutate(*args):
            with self.engine.begin() as conn:
                conn.execute(text('UPDATE meetings SET notes = :notes WHERE id = :mid'),
                             {'notes':'Novo contexto', 'mid':mid})
            return {'summary':'Análise antiga', 'topics':[]}
        with patch.object(meetings, 'extract_forum_insights', side_effect=mutate):
            response = self.client.post(f'/api/meetings/{mid}/extract')
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.read(mid)['summary_ai'])
        self.assertEqual(self.read(mid)['notes'], 'Novo contexto')

    def test_edit_marks_analysis_stale_and_context_uses_current_source(self):
        mid = self.create()
        self.analyzed(mid)
        self.assertEqual(self.client.put(f'/api/meetings/{mid}', json={'notes':'Nova decisão atual'}).status_code, 200)
        self.assertTrue(self.read(mid)['analysis_stale'])
        db = self.new_session()
        try:
            context = load_meeting_context(db, person_id=1)
        finally:
            db.close()
        self.assertIsNone(context['meetings'][0]['summary_ai'])
        self.assertEqual(context['meetings'][0]['extraction'], {})
        self.assertEqual(context['meetings'][0]['notes'], 'Nova decisão atual')
        self.assertIn('Nova decisão atual', format_meeting_context(context))

    def test_adopting_suggestion_is_idempotent_and_status_updates_close(self):
        mid = self.create()
        suggested = self.analyzed(mid)['extraction']['actions'][0]
        first = self.client.post(f'/api/meetings/{mid}/actions', json=suggested)
        self.assertEqual(first.status_code, 201, first.json)
        second = self.client.post(f'/api/meetings/{mid}/actions', json=suggested)
        self.assertEqual(second.status_code, 200, second.json)
        self.assertEqual(first.json['action']['id'], second.json['action']['id'])
        self.assertEqual(len(self.read(mid)['actions']), 1)
        aid = first.json['action']['id']
        done = self.client.put(f'/api/meeting-actions/{aid}', json={'status':'done'})
        self.assertEqual(done.status_code, 200)
        self.assertTrue(done.json['action']['closed_at'])
        reopened = self.client.put(f'/api/meeting-actions/{aid}', json={'status':'open'})
        self.assertIsNone(reopened.json['action']['closed_at'])
        self.assertEqual(self.client.put(f'/api/meeting-actions/{aid}', json={'due_date':'wrong'}).status_code, 400)
        self.assertEqual(self.client.put(f'/api/meeting-actions/{aid}', json={'status':[]}).status_code, 400)

    def test_context_links_only_explicit_participant_or_assigned_action(self):
        mid = self.create()
        db = self.new_session()
        self.assertEqual(load_meeting_context(db, person_id=2)['meetings'], [])
        db.close()
        self.client.post(f'/api/meetings/{mid}/actions', json={'description':'Validar plano', 'person_id':2})
        response = self.client.get('/api/meetings?person_id=2')
        self.assertEqual(response.json['total'], 1)
        self.assertEqual(response.json['actions'][0]['description'], 'Validar plano')
        self.assertEqual(response.json['meetings'][0]['open_action_count'], 1)
        db = self.new_session()
        try:
            linked = load_meeting_context(db, person_id=2)
            self.assertEqual(len(linked['meetings']), 1)
            self.assertEqual(linked['actions'][0]['owner_name'], 'Outra Pessoa')
            self.assertEqual(linked['meetings'][0]['participant_ids'] if 'participant_ids' in linked['meetings'][0] else [p['id'] for p in linked['meetings'][0]['participants']], [1])
            self.assertEqual(load_meeting_context(db, as_of=date(2026,10,1))['meetings'], [])
        finally:
            db.close()

    def test_obsolete_or_unknown_suggestion_cannot_create_action(self):
        mid = self.create()
        suggestion = self.analyzed(mid)['extraction']['actions'][0]
        unknown = {**suggestion, 'source_key':'not-a-current-suggestion'}
        self.assertEqual(self.client.post(f'/api/meetings/{mid}/actions',json=unknown).status_code,409)
        self.assertEqual(self.client.put(f'/api/meetings/{mid}',json={'notes':'Encaminhamento cancelado'}).status_code,200)
        response = self.client.post(f'/api/meetings/{mid}/actions',json=suggestion)
        self.assertEqual(response.status_code,409,response.json)
        self.assertEqual(self.read(mid)['actions'],[])
        # O diretor pode registrar explicitamente uma ação manual na fonte atual.
        manual = {key:value for key,value in suggestion.items() if key != 'source_key'}
        self.assertEqual(self.client.post(f'/api/meetings/{mid}/actions',json=manual).status_code,201)

    def test_adoption_retry_returns_existing_action_after_source_change(self):
        mid = self.create()
        suggestion = self.analyzed(mid)['extraction']['actions'][0]
        first = self.client.post(f'/api/meetings/{mid}/actions',json=suggestion)
        self.assertEqual(first.status_code,201)
        self.client.put(f'/api/meetings/{mid}',json={'notes':'Conteúdo atualizado'})
        retry = self.client.post(f'/api/meetings/{mid}/actions',json=suggestion)
        self.assertEqual(retry.status_code,200,retry.json)
        self.assertEqual(retry.json['action']['id'],first.json['action']['id'])
        self.assertEqual(len(self.read(mid)['actions']),1)

    def test_partial_owner_name_change_requires_explicit_profile_link(self):
        mid = self.create()
        created = self.client.post(f'/api/meetings/{mid}/actions',json={'description':'Validar plano','person_id':1})
        aid = created.json['action']['id']
        rejected = self.client.put(f'/api/meeting-actions/{aid}',json={'owner_name':'Consultor externo'})
        self.assertEqual(rejected.status_code,400,rejected.json)
        unchanged = self.read(mid)['actions'][0]
        self.assertEqual(unchanged['person_id'],1)
        self.assertEqual(unchanged['owner_name'],'André Pasold')
        external = self.client.put(f'/api/meeting-actions/{aid}',json={'person_id':None,'owner_name':'Consultor externo'})
        self.assertEqual(external.status_code,200,external.json)
        self.assertIsNone(external.json['action']['person_id'])
        self.assertEqual(external.json['action']['owner_name'],'Consultor externo')
        # Alterar o perfil explicitamente usa o nome canônico cadastrado.
        reassigned = self.client.put(f'/api/meeting-actions/{aid}',json={'person_id':2,'owner_name':'Texto antigo'})
        self.assertEqual(reassigned.json['action']['person_id'],2)
        self.assertEqual(reassigned.json['action']['owner_name'],'Outra Pessoa')

    def test_delete_cascades_only_meeting_related_data(self):
        mid = self.create()
        self.client.post(f'/api/meetings/{mid}/actions', json={'description':'Ação', 'person_id':1})
        self.assertEqual(self.client.delete(f'/api/meetings/{mid}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/meetings/{mid}').status_code, 404)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT COUNT(*) FROM meeting_actions')).scalar(),0)
            self.assertEqual(conn.execute(text('SELECT COUNT(*) FROM people')).scalar(),2)


class MeetingInsightTests(unittest.TestCase):
    def test_suggestions_do_not_infer_ownership_and_clean_bad_dates(self):
        result = normalize_extraction({'summary':'Discussão coletiva', 'actions':[
            {'description':'Validar plano', 'person_id':1, 'due_date':'amanhã'},
            {'description':'Consultar', 'owner_name':'Externo', 'person_id':1},
            {'description':'Revisar', 'owner_name':'André Pasold'}]},
            [{'id':1,'full_name':'André Pasold'}])
        self.assertIsNone(result['actions'][0]['person_id'])
        self.assertIsNone(result['actions'][0]['due_date'])
        self.assertIsNone(result['actions'][1]['person_id'])
        self.assertEqual(result['actions'][2]['person_id'],1)
        self.assertNotIn('sentiment',result)

    def test_metadata_and_participants_invalidate_old_extraction(self):
        meeting = {'title':'Plano','occurred_on':'2026-10-20','notes':'Contexto'}
        participants = [{'id':1,'full_name':'André'}]
        meeting['extraction_json'] = {'summary':'Síntese','source_fingerprint':source_fingerprint(meeting,participants)}
        self.assertFalse(analysis_stale(meeting,participants))
        self.assertTrue(analysis_stale(meeting,[]))
        self.assertTrue(analysis_stale({**meeting,'forum':'Outro'},participants))
        self.assertTrue(analysis_stale({**meeting,'extraction_json':['inválido']},participants))
        self.assertTrue(analysis_stale({'summary_ai':'Sem metadados'},participants))

    def test_homonymous_participants_do_not_resolve_to_arbitrary_profile(self):
        result = normalize_extraction({'summary':'Discussão coletiva','actions':[
            {'description':'Validar plano','owner_name':'Alex Silva','person_id':2}]},
            [{'id':1,'full_name':'Alex Silva'},{'id':2,'full_name':'Alex Silva'}])
        self.assertIsNone(result['actions'][0]['person_id'])
        self.assertEqual(result['actions'][0]['owner_name'],'Alex Silva')

    def test_context_format_has_bounded_valid_json_and_coverage(self):
        data = {'meetings':[{'id':i,'summary_ai':'x'*12000,'extraction':{'risks':['r'*3000]*20}}
                            for i in range(50)], 'actions':[{'id':i,'description':'a'*4000} for i in range(200)]}
        formatted = format_meeting_context(data)
        self.assertLess(len(formatted),50000)
        self.assertIn('coverage',formatted)
        self.assertIn('Presença ou menção não prova',formatted)

    def test_schema_contains_cascades_and_idempotency(self):
        ddl = '\n'.join(statement for statement,name in database._NEW_DDL if name.startswith('meeting'))
        self.assertIn('UNIQUE (meeting_id, source_key)',ddl)
        self.assertIn('ON DELETE CASCADE',ddl)
        self.assertIn('ON DELETE SET NULL',ddl)

    def test_extractor_normalizes_aliases_and_keeps_full_source(self):
        client = Mock()
        client.chat.completions.create.return_value.choices = [
            Mock(message=Mock(content=json.dumps({'summary':'Síntese completa','actions':[]})))]
        meeting = {'title':'Plano','occurred_on':'2026-10-20',
                   'transcript_raw':'Pasol: ' + 'frase. '*16000 + ' ÚLTIMA DECISÃO',
                   'notes':'Notas finais'}
        people = [{'id':1,'full_name':'André Pasold','transcript_aliases':['Pasol']}]
        with patch.object(meeting_insights,'get_client',return_value=client):
            result = meeting_insights.extract_forum_insights(meeting,people,people)
        call = client.chat.completions.create.call_args.kwargs
        prompt = call['messages'][0]['content']
        self.assertIn('André Pasold:',prompt)
        self.assertIn('ÚLTIMA DECISÃO',prompt)
        self.assertIn('Não atribua',prompt)
        self.assertEqual(result['source_fingerprint'],source_fingerprint(meeting,people))
        self.assertEqual(call['reasoning_effort'],'none')

    def test_returning_result_is_fetched_before_commit_closes_cursor(self):
        db = Mock()
        result = Mock()
        result.closed = False
        def fetch():
            if result.closed:
                raise RuntimeError('Cursor fechado pelo NullPool')
            return (55,)
        result.fetchone.side_effect = fetch
        db.execute.return_value = result
        db.commit.side_effect = lambda: setattr(result,'closed',True)
        app = Flask(__name__)
        app.config.update(TESTING=True,SECRET_KEY='test')
        app.register_blueprint(meetings.bp)
        client = app.test_client()
        with client.session_transaction() as session:
            session['user_id'] = 1
        with patch.object(meetings,'session_factory',return_value=db):
            response = client.post('/api/meetings',json={'title':'Plano','occurred_on':'2026-10-20'})
        self.assertEqual(response.status_code,201,response.json)
        self.assertEqual(response.json['meeting_id'],55)
        db.commit.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
