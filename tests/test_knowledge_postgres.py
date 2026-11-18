"""Integração em PostgreSQL descartável, com resposta de IA simulada.

Execute com KNOWLEDGE_TEST_DATABASE_URL apontando exclusivamente para um banco
local chamado cortex_knowledge_test. As fixtures são inteiramente fictícias.
"""
import io
import json
import os
import re
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import PurePosixPath
from unittest.mock import patch
from urllib.parse import unquote

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from cortex import create_app, database, knowledge
from cortex.ai.knowledge_service import validate_candidates
from cortex.knowledge_query import context_snapshot, memory_source_context, recover

TEST_URL = os.environ.get('KNOWLEDGE_TEST_DATABASE_URL')


@unittest.skipUnless(TEST_URL, 'PostgreSQL local de testes não configurado')
class KnowledgePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(TEST_URL)
        if url.host not in {'localhost', '127.0.0.1'} or url.database != 'cortex_knowledge_test':
            raise RuntimeError('Use somente o PostgreSQL local descartável cortex_knowledge_test.')
        cls.engine = create_engine(TEST_URL)
        cls.factory = sessionmaker(bind=cls.engine)
        with patch.object(database, 'get_engine', return_value=cls.engine), patch.dict(os.environ, {'DATABASE_URL': TEST_URL}):
            if not database.init_db():
                raise RuntimeError('Schema de testes não inicializado.')

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.env = patch.dict(os.environ, {'AUTO_INIT_DB': 'false', 'KNOWLEDGE_ENABLED': '1',
            'KNOWLEDGE_OWNER_USER_ID': '1', 'DATABASE_URL': TEST_URL,
            'ALLOWED_EMAILS': 'diretor@example.test', 'VERCEL': '0'})
        self.env.start()
        self.addCleanup(self.env.stop)
        for attr, value in (('_engine', self.engine), ('_SessionLocal', self.factory)):
            p = patch.object(database, attr, value)
            p.start()
            self.addCleanup(p.stop)
        with self.engine.begin() as conn:
            conn.execute(text('TRUNCATE users,people,one_on_ones,meetings,commitments,meeting_actions,checkpoints,card_jobs,member_cards,knowledge_entities,knowledge_sources,knowledge_versions,knowledge_claims,knowledge_relations,knowledge_revisions,knowledge_jobs,knowledge_outbox RESTART IDENTITY CASCADE'))
            conn.execute(text("""INSERT INTO users(id,email,password_hash,name,company)
                VALUES(1,'diretor@example.test',:password,'Diretor fictício','Teste'),
                      (2,'sem-acesso@example.test',:password,'Outro usuário','Teste')"""),
                {'password': generate_password_hash('cortex-test-only')})
            conn.execute(text("INSERT INTO people(id,full_name) VALUES(1,'Jean Silva'),(2,'Marina Costa')"))
            # Explicit fixture IDs must not leave SERIAL behind them.
            conn.execute(text("SELECT setval(pg_get_serial_sequence('people','id'),2)"))
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 1

    def sql(self, statement, params=None):
        with self.engine.begin() as conn:
            result = conn.execute(text(statement), params or {})
            return result.mappings().all() if result.returns_rows else []

    def source(self, transcript, occurred='2026-11-10', kind='one_on_one'):
        path = '/api/oneonones' if kind == 'one_on_one' else '/api/meetings'
        payload = {'person_id': 1, 'title': 'Conversa fictícia', 'occurred_on': occurred,
                   'transcript_raw': transcript}
        if kind == 'meeting':
            payload.update(participant_ids=[2], title='Fórum fictício')
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['session_id' if kind == 'one_on_one' else 'meeting_id']

    def process(self, sid, items, kind='one_on_one'):
        jid = self.sql('''SELECT j.id FROM knowledge_jobs j JOIN knowledge_sources s ON s.id=j.source_id
            WHERE s.kind=:kind AND s.record_id=:id AND s.current_version_id=j.version_id''', {'kind': kind, 'id': sid})[0]['id']
        def response(segment, entities, tasks, occurred_on, timezone, existing_claims=None):
            payload = items(entities, tasks, existing_claims) if callable(items) else items
            return validate_candidates({'claims': payload}, segment, entities, tasks, occurred_on,
                                       existing_claims=existing_claims), {
                'model': 'simulado', 'prompt_tokens': 100, 'completion_tokens': 50}
        with patch.object(knowledge, 'extract_candidates', side_effect=response):
            result = knowledge.process_job(jid, 1)
        self.assertEqual(result['status'], 'done', result)
        return jid

    def item(self, quote, content='Entregar a proposta', classification='commitment',
             speaker='Jean Silva', subject='Jean Silva', owner='Jean Silva', due='20/11', **extra):
        return {'quote': quote, 'content': content, 'classification': classification,
                'speaker': {'kind': 'person', 'name': speaker} if speaker else None,
                'subject': {'kind': 'person', 'name': subject} if subject else None,
                'owner': {'kind': 'person', 'name': owner} if owner else None,
                'due_expression': due, 'accepted': classification == 'commitment', **extra}

    def scenario(self):
        q10 = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(q10)
        j10 = self.process(sid, [self.item(q10)])
        task = self.sql('SELECT * FROM commitments')[0]
        self.assertEqual(task['person_id'], 1)
        q12 = 'Marina Costa: Dependo da proposta de Jean Silva e acho que Jean Silva está sobrecarregado.'
        mid = self.source(q12, '2026-11-12', 'meeting')
        self.process(mid, [self.item(q12, content='Marina acha que Jean está sobrecarregado e depende da proposta.',
            classification='perception', speaker='Marina Costa', owner=None, due=None,
            relations=[{'from': {'name': 'Marina Costa'}, 'to': {'name': 'Jean Silva'}, 'type': 'depends_on'}])], 'meeting')
        q16 = 'Jean Silva: Vou entregar a proposta em 24/11, peço o novo prazo.\nGestor: Aceito o prazo de 24/11.'
        sid16 = self.source(q16, '2026-11-16')
        self.process(sid16, [self.item(q16, due='24/11', task_ref={'kind': 'commitment', 'id': task['id']})])
        return sid, sid16, mid, j10, task['id'], q10

    def test_fictitious_end_to_end_curation_consumers_history_and_export(self):
        sid, sid16, mid, j10, tid, q10 = self.scenario()
        task = self.sql('SELECT * FROM commitments')[0]
        self.assertEqual(task['due_date'], date(2026, 11, 24))
        self.assertEqual(task['status'], 'open')
        self.assertEqual(len(self.sql('SELECT * FROM commitments')), 1)
        memory_db = self.factory()
        try:
            memory = recover(memory_db, person_id=1, as_of='2026-11-17')
            self.assertEqual(len(memory['claims']), 3)
            self.assertEqual(memory['commitments'][0]['due_date'], '2026-11-24')
            perception = next(c for c in memory['claims'] if c['classification'] == 'perception')
            self.assertNotEqual(perception['speaker_entity_id'], perception['subject_entity_id'])
        finally:
            memory_db.close()
        prep = self.client.get('/api/people/1/prep')
        self.assertEqual(prep.status_code, 200, prep.json)
        self.assertEqual(prep.json['knowledge']['commitments'][0]['due_date'], '2026-11-24')
        graph = self.client.get('/api/knowledge/graph?person_id=1&depth=2&limit=80')
        self.assertEqual(graph.status_code, 200, graph.json)
        cid = self.sql("SELECT c.id FROM knowledge_claims c JOIN knowledge_sources s ON s.id=c.source_id WHERE s.kind='one_on_one' AND s.record_id=:id", {'id': sid})[0]['id']
        self.assertTrue(any(n['id'] == f'claim:{cid}' for n in graph.json['nodes']))
        detail = self.client.get(f'/api/knowledge/claims/{cid}').json
        self.assertEqual(detail['evidence']['text'], q10)
        edit = self.client.put(f'/api/knowledge/claims/{cid}', json={
            'version': detail['claim']['version'], 'due_date': '2026-11-26',
            'reason': 'Novo combinado fora das transcrições.'})
        self.assertEqual(edit.status_code, 200, edit.json)
        self.assertEqual(edit.json['claim']['due_date'], '2026-11-26')
        self.assertEqual(edit.json['evidence']['text'], q10)
        self.assertTrue(any(r['origin'] == 'human' and r['reason'] == 'Novo combinado fora das transcrições.' for r in edit.json['revisions']))
        # Duplicate requests and explicit replay of old sources preserve human changes.
        self.assertEqual(knowledge.process_job(j10, 1)['status'], 'done')
        self.sql("UPDATE knowledge_jobs SET status='pending',cursor_json='{}'::jsonb,staged_json='[]'::jsonb WHERE id=:id", {'id': j10})
        self.process(sid, [self.item(q10)])
        self.assertEqual(self.sql('SELECT due_date FROM commitments')[0]['due_date'], date(2026, 11, 26))
        self.assertEqual(len(self.sql('SELECT * FROM knowledge_claims')), 3)
        prep = self.client.get('/api/people/1/prep').json
        self.assertEqual(prep['knowledge']['commitments'][0]['due_date'], '2026-11-26')
        # Actual card and agenda code receive the curated state (only provider mocked).
        from cortex.views import cards, sessions
        captured = []
        with patch.object(cards.ai, 'generate_member_card', side_effect=lambda name, context: captured.append(context) or {'saude': 'verde', 'resumo': 'Fictício'}):
            card = self.client.post('/api/cards/person/1', json={})
        self.assertEqual(card.status_code, 200, card.json)
        self.assertIn('2026-11-26', captured[0])
        self.assertTrue(card.json['card']['card_json']['knowledge']['claim_ids'])
        with patch.object(sessions.ai, 'generate_prep_agenda', side_effect=lambda name, context: captured.append(context) or 'Pauta fictícia'):
            agenda = self.client.post('/api/people/1/agenda', json={})
        self.assertEqual(agenda.status_code, 200, agenda.json)
        self.assertIn('2026-11-26', captured[-1])
        relation = self.sql('SELECT * FROM knowledge_relations')[0]
        rejected = self.client.put(f"/api/knowledge/relations/{relation['id']}", json={
            'version': relation['version'], 'review_state': 'rejected', 'reason': 'Interpretação incorreta.'})
        self.assertEqual(rejected.status_code, 200, rejected.json)
        graph = self.client.get('/api/knowledge/graph?depth=2').json
        self.assertFalse(any(e.get('relation_id') == relation['id'] for e in graph['edges']))
        payload = self.client.get('/api/knowledge/export')
        self.assertEqual(payload.status_code, 200, payload.json if payload.is_json else payload.status)
        self.assertEqual(payload.headers['Cache-Control'], 'no-store')
        with zipfile.ZipFile(io.BytesIO(payload.data)) as archive:
            names = set(archive.namelist())
            self.assertIn(f'commitments/commitment-{tid}.md', names)
            content = archive.read(f'commitments/commitment-{tid}.md').decode()
            self.assertIn('2026-11-26', content)
            self.assertIn('2026-11-20', content)
            for path in names:
                self.assertNotIn('..', PurePosixPath(path).parts)
                value = archive.read(path).decode()
                self.assertTrue(value.startswith('---\n'))
                for target in re.findall(r'(?<!\\)\]\(([^)]+)\)', value):
                    resolved = os.path.normpath(str(PurePosixPath(path).parent / unquote(target)))
                    self.assertIn(resolved, names)

    def test_access_control_all_paths_and_rls(self):
        sid = self.source('Jean Silva: Aceito entregar a proposta em 20/11.')
        self.process(sid, [self.item('Jean Silva: Aceito entregar a proposta em 20/11.')])
        with self.client.session_transaction() as session:
            session['user_id'] = 2
        for method, path in [('get','/api/knowledge/entities'), ('get','/api/knowledge/graph'),
            ('get','/api/knowledge/claims/1'), ('put','/api/knowledge/claims/1'),
            ('get','/api/knowledge/export'), ('post','/api/knowledge/jobs/1/run'),
            ('get',f'/api/oneonones/{sid}'), ('get','/api/people/1/prep'),
            ('post','/api/people/1/agenda'), ('get','/api/cards/latest'), ('post','/api/assistant/chat')]:
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path, json={}).status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/api/knowledge/export').status_code, 401)
        # RLS is effective even with SELECT granted to an unprivileged local role.
        with self.engine.begin() as conn:
            conn.execute(text("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cortex_memory_reader') THEN CREATE ROLE cortex_memory_reader; END IF; END $$"))
            conn.execute(text('GRANT SELECT ON knowledge_claims TO cortex_memory_reader'))
            conn.execute(text('SET LOCAL ROLE cortex_memory_reader'))
            self.assertEqual(conn.execute(text('SELECT COUNT(*) FROM knowledge_claims')).scalar(), 0)

    def test_task_pagination_has_complete_coverage_and_existing_ids_reused(self):
        self.sql("INSERT INTO commitments(person_id,description,due_date) VALUES(1,'Entregar a proposta','2026-11-20')")
        original = self.sql('SELECT id FROM commitments')[0]['id']
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(quote)
        self.process(sid, [self.item(quote)])
        self.assertEqual(len(self.sql('SELECT * FROM commitments')), 1)
        self.assertEqual(self.sql('SELECT commitment_id FROM knowledge_claims')[0]['commitment_id'], original)
        self.sql("INSERT INTO commitments(person_id,description) SELECT 1,'Tarefa fictícia '||generate_series FROM generate_series(1,204)")
        first = self.client.get('/api/knowledge/tasks?person_id=1&limit=200').json
        second = self.client.get('/api/knowledge/tasks?person_id=1&limit=200&offset=200').json
        self.assertEqual(first['total'], 205)
        self.assertEqual(len(first['tasks']) + len(second['tasks']), 205)
        self.assertEqual(len({t['id'] for t in first['tasks'] + second['tasks']}), 205)
        db = self.factory()
        try:
            self.assertEqual(len(recover(db, person_id=1)['commitments']), 205)
        finally:
            db.close()

    def test_concurrent_edits_and_runs_are_identifiable_and_idempotent(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(quote)
        jid = self.sql('SELECT id FROM knowledge_jobs')[0]['id']
        started, release = threading.Event(), threading.Event()
        def extraction(segment, entities, tasks, occurred, timezone, existing_claims=None):
            started.set()
            release.wait(timeout=5)
            return validate_candidates({'claims': [self.item(quote)]}, segment, entities, tasks, occurred,
                                       existing_claims=existing_claims), {}
        with patch.object(knowledge, 'extract_candidates', side_effect=extraction), ThreadPoolExecutor(max_workers=2) as executor:
            one = executor.submit(knowledge.process_job, jid, 1)
            self.assertTrue(started.wait(timeout=5))
            two = executor.submit(knowledge.process_job, jid, 1)
            self.assertEqual(two.result(timeout=5)['status'], 'running')
            release.set()
            self.assertEqual(one.result(timeout=5)['status'], 'done')
        self.assertEqual(len(self.sql('SELECT * FROM commitments')), 1)
        detail = self.client.get('/api/knowledge/claims/1').json
        payload = {'version': detail['claim']['version'], 'content': 'Interpretação corrigida', 'reason': 'Revisão fictícia'}
        self.assertEqual(self.client.put('/api/knowledge/claims/1', json=payload).status_code, 200)
        conflict = self.client.put('/api/knowledge/claims/1', json=payload)
        self.assertEqual(conflict.status_code, 409)
        self.assertTrue(conflict.json['conflict'])

    def test_editing_source_supersedes_job_without_restoring_rejected_interpretation(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(quote)
        jid = self.process(sid, [self.item(quote)])
        detail = self.client.get('/api/knowledge/claims/1').json
        rejected = self.client.put('/api/knowledge/claims/1', json={'version': detail['claim']['version'], 'review_state': 'rejected'})
        self.assertEqual(rejected.status_code, 200, rejected.json)
        changed = self.client.put(f'/api/oneonones/{sid}', json={'transcript_raw': 'Contexto adicional.\n' + quote})
        self.assertEqual(changed.status_code, 200, changed.json)
        db = self.factory()
        try:
            context = memory_source_context(db)
            self.assertIn(('one_on_one', sid), context['sources'])
            self.assertFalse(context['sources'][('one_on_one', sid)]['claims'])
            self.assertNotIn('Aceito entregar', context['sources'][('one_on_one', sid)]['text'])
        finally:
            db.close()
        self.assertEqual(knowledge.process_job(jid, 1)['status'], 'superseded')
        self.process(sid, [self.item(quote)])
        self.assertEqual(self.sql('SELECT review_state FROM knowledge_claims')[0]['review_state'], 'rejected')
        self.assertEqual(self.client.get('/api/knowledge/claims').json['total'], 0)

    def test_deletion_while_disabled_purges_evidence_and_stale_jobs(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(quote)
        jid = self.process(sid, [self.item(quote)])
        with patch.dict(os.environ, {'KNOWLEDGE_ENABLED': '0'}):
            deleted = self.client.delete(f'/api/oneonones/{sid}')
        self.assertEqual(deleted.status_code, 200, deleted.json)
        self.assertEqual(self.sql('SELECT * FROM knowledge_versions'), [])
        self.assertEqual(self.sql('SELECT * FROM knowledge_claims'), [])
        self.assertEqual(self.sql('SELECT * FROM knowledge_jobs'), [])
        self.assertEqual(self.client.get('/api/knowledge/claims/1').status_code, 404)
        self.assertEqual(self.sql('SELECT status FROM commitments')[0]['status'], 'cancelled')

    def test_failure_retries_backfill_dry_run_and_manual_relations(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(quote)
        jid = self.sql('SELECT id FROM knowledge_jobs')[0]['id']
        with patch.object(knowledge, 'extract_candidates', side_effect=RuntimeError('simulado')):
            for _ in range(3):
                self.assertEqual(knowledge.process_job(jid, 1)['status'], 'error')
        self.assertEqual(self.sql('SELECT * FROM knowledge_claims'), [])
        self.assertEqual(self.sql('SELECT attempts FROM knowledge_jobs')[0]['attempts'], 3)
        simulated = self.client.post('/api/knowledge/backfill', json={'dry_run': True, 'limit': 1})
        self.assertEqual(simulated.status_code, 200, simulated.json)
        self.assertEqual(len(self.sql('SELECT * FROM knowledge_jobs')), 1)
        first = self.client.post('/api/knowledge/entities', json={'kind': 'project', 'label': 'Projeto / "[fictício]"', 'aliases': []}).json['entity']
        second = self.client.post('/api/knowledge/entities', json={'kind': 'topic', 'label': 'Assunto fictício'}).json['entity']
        relation = self.client.post('/api/knowledge/relations', json={'from_entity_id': first['id'], 'to_entity_id': second['id'], 'relation_type': 'related_to', 'reason': 'Observação humana'})
        self.assertEqual(relation.status_code, 201, relation.json)
        graph = self.client.get(f"/api/knowledge/graph?entity_id={first['id']}").json
        self.assertTrue(any(n['id'] == f"entity:{second['id']}" for n in graph['nodes']))
        manual = self.client.post('/api/knowledge/claims', json={'content': 'Anotação humana independente', 'entity_ids': [first['id']]})
        self.assertEqual(manual.status_code, 201, manual.json)
        self.assertIsNone(manual.json['evidence'])
        self.assertEqual(manual.json['claim']['origin'], 'manual')

    def test_retry_is_explicit_audited_and_keeps_the_same_job(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(quote)
        jid = self.sql('SELECT id FROM knowledge_jobs')[0]['id']
        with patch.object(knowledge, 'extract_candidates', side_effect=RuntimeError('simulado')):
            for _ in range(3):
                self.assertEqual(knowledge.process_job(jid, 1)['status'], 'error')
        resumed = self.client.post(f'/api/knowledge/jobs/{jid}/retry', json={})
        self.assertEqual(resumed.status_code, 200, resumed.json)
        self.assertEqual(resumed.json['job_id'], jid)
        self.process(sid, [self.item(quote)])
        self.assertEqual(len(self.sql('SELECT * FROM knowledge_jobs')), 1)
        audit = self.sql("SELECT * FROM knowledge_revisions WHERE record_type='job'")[0]
        self.assertEqual(audit['actor_user_id'], 1)
        self.assertEqual(audit['before_json']['attempts'], 3)
        self.assertNotIn('staged_json', audit['before_json'])

    def test_new_task_event_invalidates_old_claim_edit_and_late_import_keeps_state(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(quote)
        self.process(sid, [self.item(quote)])
        old = self.client.get('/api/knowledge/claims/1').json['claim']
        tid = old['commitment_id']
        quote24 = 'Jean Silva: Vou entregar a proposta em 24/11, peço o novo prazo.\nGestor: Aceito o prazo de 24/11.'
        sid24 = self.source(quote24, '2026-11-16')
        self.process(sid24, [self.item(quote24, due='24/11', task_ref={'kind': 'commitment', 'id': tid})])
        stale = self.client.put('/api/knowledge/claims/1', json={
            'version': old['version'], 'due_date': '2026-11-26', 'reason': 'Edição com leitura antiga'})
        self.assertEqual(stale.status_code, 409, stale.json)
        late_quote = 'Jean Silva: Confirmo a proposta em 19/11.'
        late = self.source(late_quote, '2026-11-09')
        self.process(late, [self.item(late_quote, due='19/11', task_ref={'kind': 'commitment', 'id': tid})])
        self.assertEqual(self.sql('SELECT due_date FROM commitments')[0]['due_date'], date(2026, 11, 24))
        db = self.factory()
        try:
            memory = recover(db, person_id=1, as_of='2026-11-17')
            self.assertEqual(memory['since_last_conversation'], '2026-11-16')
            self.assertTrue(any(c['after'].get('due_date') == '2026-11-24' for c in memory['changes']))
            self.assertFalse(any(c['event_on'] == '2026-11-09' for c in memory['changes']))
            late_claim = next(c for c in memory['claims'] if c['record_id'] == late)
            self.assertIn('task_update', late_claim['conflict'])
        finally:
            db.close()

    def test_content_correction_reaches_canonical_task_and_preparation(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(quote)
        self.process(sid, [self.item(quote)])
        detail = self.client.get('/api/knowledge/claims/1').json
        updated = self.client.put('/api/knowledge/claims/1', json={
            'version': detail['claim']['version'], 'content': 'Entregar a proposta revisada de escopo',
            'reason': 'A entrega acordada é a revisão do escopo.'})
        self.assertEqual(updated.status_code, 200, updated.json)
        task = self.sql('SELECT * FROM commitments')[0]
        self.assertEqual(task['id'], detail['claim']['commitment_id'])
        self.assertEqual(task['description'], 'Entregar a proposta revisada de escopo')
        self.assertTrue(task['knowledge_human_fields']['description'])
        prep = self.client.get('/api/people/1/prep')
        self.assertEqual(prep.status_code, 200, prep.json)
        self.assertEqual(prep.json['commitments'][0]['description'], task['description'])
        self.assertEqual(prep.json['knowledge']['commitments'][0]['description'], task['description'])
        self.assertEqual(updated.json['evidence']['text'], quote)

    def test_reclassifying_only_automatic_commitment_retires_operational_task(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(quote)
        self.process(sid, [self.item(quote)])
        detail = self.client.get('/api/knowledge/claims/1').json
        corrected = self.client.put('/api/knowledge/claims/1', json={
            'version': detail['claim']['version'], 'classification': 'proposal',
            'reason': 'Revisão da interpretação: havia uma proposta, sem compromisso aceito.'})
        self.assertEqual(corrected.status_code, 200, corrected.json)
        self.assertEqual(corrected.json['claim']['classification'], 'proposal')
        self.assertFalse(corrected.json['claim']['task_accepted'])
        self.assertEqual(corrected.json['claim']['status'], 'none')
        self.assertEqual(self.sql('SELECT status FROM commitments')[0]['status'], 'cancelled')
        prep = self.client.get('/api/people/1/prep').json
        self.assertEqual(prep['knowledge']['commitments'], [])
        self.assertEqual(prep['knowledge']['claims'][0]['classification'], 'proposal')

    def test_manual_relation_is_in_assistant_memory_until_rejected(self):
        entities = self.client.get('/api/knowledge/entities').json['entities']
        ids = {e['person_id']: e['id'] for e in entities if e['person_id']}
        created = self.client.post('/api/knowledge/relations', json={
            'from_entity_id': ids[2], 'to_entity_id': ids[1], 'relation_type': 'depends_on',
            'reason': 'Dependência registrada pelo diretor fora das transcrições.'})
        self.assertEqual(created.status_code, 201, created.json)
        relation = created.json['relation']
        db = self.factory()
        try:
            records = memory_source_context(db)['manual_records']
            record = next(r for r in records if r['id'] == f"relation:{relation['id']}")
            self.assertIn('depends_on', record['excerpt'])
            self.assertEqual(set(record['mentioned_person_ids']), {1, 2})
        finally:
            db.close()
        rejected = self.client.put(f"/api/knowledge/relations/{relation['id']}", json={
            'version': relation['version'], 'review_state': 'rejected', 'reason': 'Vínculo sem suporte.'})
        self.assertEqual(rejected.status_code, 200, rejected.json)
        db = self.factory()
        try:
            self.assertFalse(any(r['id'] == f"relation:{relation['id']}"
                                 for r in memory_source_context(db)['manual_records']))
        finally:
            db.close()

    def test_corrected_and_rejected_relation_identity_survives_replay(self):
        quote = 'Marina Costa: Dependo da proposta de Jean Silva.'
        sid = self.source(quote, '2026-11-12', 'meeting')
        candidate = self.item(quote, content='Marina depende da proposta de Jean.', classification='report',
            speaker='Marina Costa', owner=None, due=None,
            relations=[{'from': {'name': 'Marina Costa'}, 'to': {'name': 'Jean Silva'}, 'type': 'depends_on'}])
        jid = self.process(sid, [candidate], 'meeting')
        original = self.sql('SELECT * FROM knowledge_relations')[0]
        edited = self.client.put(f"/api/knowledge/relations/{original['id']}", json={
            'version': original['version'], 'from_entity_id': original['to_entity_id'],
            'to_entity_id': original['from_entity_id'], 'relation_type': 'related_to',
            'review_state': 'corrected', 'reason': 'Corrigidos o sentido e o significado do vínculo.'})
        self.assertEqual(edited.status_code, 200, edited.json)
        self.sql("UPDATE knowledge_jobs SET status='pending',cursor_json='{}'::jsonb WHERE id=:id", {'id': jid})
        self.process(sid, [candidate], 'meeting')
        replayed = self.sql('SELECT * FROM knowledge_relations')
        self.assertEqual(len(replayed), 1)
        current = replayed[0]
        self.assertEqual(current['id'], original['id'])
        self.assertEqual(current['source_key'], original['source_key'])
        self.assertEqual(current['from_entity_id'], original['to_entity_id'])
        self.assertEqual(current['to_entity_id'], original['from_entity_id'])
        self.assertEqual(current['relation_type'], 'related_to')
        self.assertTrue(current['active'])
        rejected = self.client.put(f"/api/knowledge/relations/{current['id']}", json={
            'version': current['version'], 'review_state': 'rejected', 'reason': 'Relação retirada após revisão.'})
        self.assertEqual(rejected.status_code, 200, rejected.json)
        self.sql("UPDATE knowledge_jobs SET status='pending',cursor_json='{}'::jsonb WHERE id=:id", {'id': jid})
        self.process(sid, [candidate], 'meeting')
        self.assertEqual(len(self.sql('SELECT * FROM knowledge_relations')), 1)
        self.assertEqual(self.sql('SELECT review_state FROM knowledge_relations')[0]['review_state'], 'rejected')
        self.assertFalse(any(e.get('relation_id') == current['id']
                             for e in self.client.get('/api/knowledge/graph').json['edges']))

    def test_same_commitment_across_source_types_reuses_task_without_model_reference(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(quote)
        self.process(sid, [self.item(quote)])
        tid = self.sql('SELECT id FROM commitments')[0]['id']
        mid = self.source(quote, '2026-11-12', 'meeting')
        self.process(mid, [self.item(quote)], 'meeting')
        self.assertEqual(self.sql('SELECT id FROM commitments'), [{'id': tid}])
        self.assertEqual(self.sql('SELECT * FROM meeting_actions'), [])
        claims = self.sql('SELECT commitment_id,meeting_action_id FROM knowledge_claims ORDER BY id')
        self.assertEqual(len(claims), 2)
        self.assertTrue(all(c['commitment_id'] == tid and c['meeting_action_id'] is None for c in claims))

    def test_deleting_original_forum_keeps_action_supported_by_another_forum(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        original = self.source(quote, kind='meeting')
        self.process(original, [self.item(quote)], 'meeting')
        action = self.sql('SELECT * FROM meeting_actions')[0]
        other = self.source(quote, '2026-11-12', 'meeting')
        self.process(other, [self.item(quote)], 'meeting')
        deleted = self.client.delete(f'/api/meetings/{original}')
        self.assertEqual(deleted.status_code, 200, deleted.json)
        survivor = self.sql('SELECT * FROM meeting_actions')
        self.assertEqual(len(survivor), 1)
        self.assertEqual(survivor[0]['id'], action['id'])
        self.assertEqual(survivor[0]['meeting_id'], other)
        self.assertEqual(survivor[0]['status'], 'open')
        self.assertEqual(survivor[0]['description'], 'Entregar a proposta')
        claims = self.sql('SELECT meeting_action_id FROM knowledge_claims')
        self.assertEqual(claims, [{'meeting_action_id': action['id']}])
        self.assertTrue(self.sql("SELECT id FROM knowledge_revisions WHERE record_type='meeting_action' AND record_id=:id AND origin='system'", {'id': action['id']}))

    def test_deleted_forum_action_migrates_to_commitment_when_only_one_on_one_support_remains(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        mid = self.source(quote, kind='meeting')
        self.process(mid, [self.item(quote)], 'meeting')
        action = self.sql('SELECT * FROM meeting_actions')[0]
        sid = self.source(quote, '2026-11-12')
        self.process(sid, [self.item(quote)])
        self.assertEqual(len(self.sql('SELECT * FROM meeting_actions')), 1)
        self.assertEqual(self.sql('SELECT * FROM commitments'), [])
        deleted = self.client.delete(f'/api/meetings/{mid}')
        self.assertEqual(deleted.status_code, 200, deleted.json)
        self.assertEqual(self.sql('SELECT * FROM meeting_actions'), [])
        migrated = self.sql('SELECT * FROM commitments')
        self.assertEqual(len(migrated), 1)
        task = migrated[0]
        self.assertEqual(task['person_id'], 1)
        self.assertEqual(task['one_on_one_id'], sid)
        self.assertEqual(task['description'], 'Entregar a proposta')
        self.assertEqual(task['due_date'], date(2026, 11, 20))
        self.assertEqual(task['status'], 'open')
        self.assertEqual(task['assistant_request_id'], f"knowledge:migrated_action:{action['id']}")
        surviving_claim = self.sql('SELECT commitment_id,meeting_action_id FROM knowledge_claims')[0]
        self.assertEqual(surviving_claim['commitment_id'], task['id'])
        self.assertIsNone(surviving_claim['meeting_action_id'])
        history = self.sql("SELECT * FROM knowledge_revisions WHERE record_type='commitment' AND record_id=:id", {'id': task['id']})
        self.assertTrue(any(r['origin'] == 'automatic' for r in history))
        self.assertTrue(any(r['origin'] == 'system' and 'histórico preservados' in r['reason'] for r in history))
        prep = self.client.get('/api/people/1/prep').json
        self.assertEqual(prep['knowledge']['commitments'][0]['id'], task['id'])

    def test_source_deletion_purges_cards_and_source_only_task_content_from_export(self):
        secret = 'Proposta fictícia de lançamento Ômega'
        quote = f'Jean Silva: Aceito entregar {secret} em 20/11.'
        sid = self.source(quote)
        self.process(sid, [self.item(quote, content=secret)])
        task = self.sql('SELECT * FROM commitments')[0]
        self.sql("INSERT INTO member_cards(person_id,card_json) VALUES(1,CAST(:card AS JSONB))", {'card': json.dumps({'ai': {'resumo': secret}})})
        deleted = self.client.delete(f'/api/oneonones/{sid}')
        self.assertEqual(deleted.status_code, 200, deleted.json)
        self.assertEqual(self.sql('SELECT * FROM member_cards'), [])
        self.assertEqual(self.sql('SELECT * FROM knowledge_versions'), [])
        self.assertEqual(self.sql('SELECT * FROM knowledge_claims'), [])
        remaining = self.sql('SELECT * FROM commitments WHERE id=:id', {'id': task['id']})
        if remaining:
            self.assertEqual(remaining[0]['description'], 'Registro removido com a fonte')
            self.assertIsNone(remaining[0]['due_date'])
            self.assertEqual(remaining[0]['status'], 'cancelled')
        history = self.sql("SELECT * FROM knowledge_revisions WHERE record_type='commitment' AND record_id=:id", {'id': task['id']})
        self.assertNotIn(secret, json.dumps([dict(r) for r in history], default=str, ensure_ascii=False))
        payload = self.client.get('/api/knowledge/export')
        self.assertEqual(payload.status_code, 200, payload.status)
        with zipfile.ZipFile(io.BytesIO(payload.data)) as archive:
            exported = '\n'.join(archive.read(name).decode() for name in archive.namelist())
        self.assertNotIn(secret, exported)
        self.assertNotIn(quote, exported)

    def test_new_source_exposes_conflict_with_human_correction_in_another_source(self):
        first_quote = 'Marina Costa: Acho que Jean Silva está sobrecarregado.'
        sid = self.source(first_quote, '2026-11-12', 'meeting')
        self.process(sid, [self.item(first_quote, content='Marina acha que Jean está sobrecarregado.',
            classification='perception', speaker='Marina Costa', owner=None, due=None)], 'meeting')
        current = self.client.get('/api/knowledge/claims/1').json['claim']
        human = 'Marina esclareceu que a percepção de sobrecarga estava equivocada.'
        corrected = self.client.put('/api/knowledge/claims/1', json={
            'version': current['version'], 'content': human, 'reason': 'Esclarecimento manual atribuído à Marina.'})
        self.assertEqual(corrected.status_code, 200, corrected.json)
        quote = 'Marina Costa: Agora acho que Jean Silva está sobrecarregado novamente.'
        newer = self.source(quote, '2026-11-16', 'meeting')
        def extracted(entities, tasks, existing_claims):
            known = next(c for c in existing_claims if c['id'] == current['id'])
            self.assertEqual(known['content'], human)
            self.assertEqual(known['review_state'], 'corrected')
            return [self.item(quote, content='Marina percebe nova sobrecarga de Jean.', classification='perception',
                speaker='Marina Costa', owner=None, due=None, conflicts_with=[current['id']])]
        self.process(newer, extracted, 'meeting')
        claims = self.sql('SELECT * FROM knowledge_claims ORDER BY id')
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0]['content'], human)
        proposed = claims[1]
        self.assertEqual(proposed['conflict']['related_claims'][0]['id'], current['id'])
        self.assertNotIn('content', proposed['conflict']['related_claims'][0])
        self.assertEqual(proposed['conflict']['proposal']['source_id'], proposed['source_id'])
        detail = self.client.get(f"/api/knowledge/claims/{proposed['id']}")
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['evidence']['text'], quote)
        self.assertEqual(detail.json['claim']['conflict']['related_claims'][0]['content'], human)
        self.assertTrue(detail.json['conflicts'])
        # A conflict is a reference, so deleting its other source removes that
        # content from inspections, revision copies and future exports too.
        deleted = self.client.delete(f'/api/meetings/{sid}')
        self.assertEqual(deleted.status_code, 200, deleted.json)
        detail = self.client.get(f"/api/knowledge/claims/{proposed['id']}").json
        self.assertTrue(detail['claim']['conflict']['related_claims'][0]['unavailable'])
        self.assertNotIn(human, json.dumps(detail, ensure_ascii=False))
        payload = self.client.get('/api/knowledge/export')
        self.assertEqual(payload.status_code, 200, payload.status)
        with zipfile.ZipFile(io.BytesIO(payload.data)) as archive:
            self.assertNotIn(human, '\n'.join(archive.read(name).decode() for name in archive.namelist()))

    def test_manual_commitment_starts_with_human_locks_and_automatic_source_cannot_change_it(self):
        entities = self.client.get('/api/knowledge/entities').json['entities']
        jean = next(e for e in entities if e['person_id'] == 1)
        created = self.client.post('/api/knowledge/claims', json={
            'content': 'Entregar a proposta', 'classification': 'commitment', 'task_accepted': True,
            'owner_entity_id': jean['id'], 'subject_entity_id': jean['id'], 'entity_ids': [jean['id']],
            'status': 'open', 'due_date': '2026-11-26', 'occurred_on': '2026-11-17',
            'reason': 'Combinado registrado pessoalmente pelo diretor.'})
        self.assertEqual(created.status_code, 201, created.json)
        manual = created.json['claim']
        self.assertEqual(manual['origin'], 'manual')
        self.assertIsNone(created.json['evidence'])
        task = self.sql('SELECT * FROM commitments')[0]
        self.assertFalse(task['knowledge_created'])
        for field in ('description', 'person_id', 'owner', 'due_date', 'status'):
            self.assertTrue(task['knowledge_human_fields'][field])
        quote = 'Jean Silva: Aceito entregar a proposta em 24/11.'
        later = self.source(quote, '2026-11-18')
        self.process(later, [self.item(quote, due='24/11')])
        tasks = self.sql('SELECT * FROM commitments')
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['id'], manual['commitment_id'])
        self.assertEqual(tasks[0]['due_date'], date(2026, 11, 26))
        automatic = self.sql("SELECT * FROM knowledge_claims WHERE origin='automatic'")[0]
        self.assertEqual(automatic['commitment_id'], task['id'])
        self.assertEqual(automatic['conflict']['task_update']['field'], 'due_date')
        self.assertEqual(automatic['conflict']['task_update']['current'], '2026-11-26')
        self.assertEqual(automatic['conflict']['task_update']['proposed'], '2026-11-24')
        prep = self.client.get('/api/people/1/prep').json
        self.assertEqual(prep['knowledge']['commitments'][0]['due_date'], '2026-11-26')

    def test_removing_current_deadline_evidence_requires_explicit_human_confirmation(self):
        sid, sid16, mid, j10, tid, quote = self.scenario()
        deleted = self.client.delete(f'/api/oneonones/{sid16}')
        self.assertEqual(deleted.status_code, 200, deleted.json)
        task = self.sql('SELECT * FROM commitments WHERE id=:id', {'id': tid})[0]
        self.assertEqual(task['due_date'], date(2026, 11, 24))
        remaining = self.sql('SELECT * FROM knowledge_claims WHERE commitment_id=:id', {'id': tid})
        self.assertEqual(len(remaining), 1)
        cid = remaining[0]['id']
        warning = remaining[0]['conflict']['task_evidence_removed']
        self.assertEqual(warning['current']['due_date'], '2026-11-24')
        self.assertEqual(set(warning['fields']), {'due_date', 'status'})
        self.assertEqual(warning['remaining_claim_ids'], [cid])
        tasks = self.client.get('/api/knowledge/tasks?person_id=1').json['tasks']
        self.assertTrue(tasks[0]['support_warning'])
        prep = self.client.get('/api/people/1/prep').json
        self.assertTrue(prep['commitments'][0]['support_warning'])
        self.assertTrue(prep['knowledge']['commitments'][0]['support_warning'])
        # Old evidence may still be inspected and replayed, but it does not
        # prove that the operational date survived removal of its latest source.
        self.sql("UPDATE knowledge_jobs SET status='pending',cursor_json='{}'::jsonb WHERE id=:id", {'id': j10})
        self.process(sid, [self.item(quote)])
        before = self.client.get(f'/api/knowledge/claims/{cid}').json
        self.assertTrue(before['claim']['conflict']['task_evidence_removed'])
        self.assertEqual(before['claim']['due_date'], '2026-11-24')
        confirmed = self.client.put(f'/api/knowledge/claims/{cid}', json={
            'version': before['claim']['version'], 'resolve_conflict': True,
            'reason': 'Confirmado fora das transcrições.'})
        self.assertEqual(confirmed.status_code, 200, confirmed.json)
        self.assertEqual(confirmed.json['claim']['due_date'], '2026-11-24')
        self.assertFalse((confirmed.json['claim']['conflict'] or {}).get('task_evidence_removed'))
        task = self.sql('SELECT * FROM commitments WHERE id=:id', {'id': tid})[0]
        self.assertEqual(task['due_date'], date(2026, 11, 24))
        self.assertTrue(task['knowledge_human_fields']['due_date'])
        self.assertTrue(task['knowledge_human_fields']['status'])
        self.assertFalse(any((c['conflict'] or {}).get('task_evidence_removed')
                             for c in self.sql('SELECT conflict FROM knowledge_claims WHERE commitment_id=:id', {'id': tid})))
        self.assertFalse(self.client.get('/api/knowledge/tasks?person_id=1').json['tasks'][0]['support_warning'])
        self.assertFalse(self.client.get('/api/people/1/prep').json['commitments'][0]['support_warning'])
        self.sql("UPDATE knowledge_jobs SET status='pending',cursor_json='{}'::jsonb WHERE id=:id", {'id': j10})
        self.process(sid, [self.item(quote)])
        self.assertEqual(self.sql('SELECT due_date FROM commitments WHERE id=:id', {'id': tid})[0]['due_date'], date(2026, 11, 24))
        self.assertFalse(self.client.get('/api/knowledge/tasks?person_id=1').json['tasks'][0]['support_warning'])

    def test_reclassification_preserves_independently_corrected_task_and_exposes_conflict(self):
        quote = 'Jean Silva: Aceito entregar a proposta em 20/11.'
        sid = self.source(quote)
        self.process(sid, [self.item(quote)])
        before = self.client.get('/api/knowledge/claims/1').json['claim']
        edited = self.client.put('/api/knowledge/claims/1', json={
            'version': before['version'], 'content': 'Entregar a revisão do escopo',
            'reason': 'Correção humana da tarefa assumida.'})
        self.assertEqual(edited.status_code, 200, edited.json)
        corrected = self.client.put('/api/knowledge/claims/1', json={
            'version': edited.json['claim']['version'], 'classification': 'proposal',
            'reason': 'A frase original era uma proposta; preservar o acordo humano separado.'})
        self.assertEqual(corrected.status_code, 200, corrected.json)
        claim = corrected.json['claim']
        self.assertEqual(claim['classification'], 'proposal')
        self.assertEqual(claim['status'], 'none')
        self.assertEqual(claim['conflict']['task_retained']['kind'], 'commitment')
        task = self.sql('SELECT * FROM commitments')[0]
        self.assertEqual(task['status'], 'open')
        self.assertEqual(task['description'], 'Entregar a revisão do escopo')
        self.assertEqual(claim['conflict']['task_retained']['id'], task['id'])
        self.assertIn('estado humano independente', claim['conflict']['task_retained']['reason'])
        snapshot = context_snapshot({'claims': [claim]})['claims'][0]
        self.assertTrue(snapshot['has_conflict'])
        self.assertIn('task_retained', snapshot['conflict_types'])
        self.assertIn('classification', snapshot['corrected_fields'])

    def test_ai_context_keeps_conflicts_human_fields_and_both_source_and_manual_relations(self):
        sid, sid16, mid, j10, tid, quote = self.scenario()
        original_relation = self.sql('SELECT * FROM knowledge_relations')[0]
        before = self.client.get('/api/knowledge/claims/1').json['claim']
        human_content = 'Entregar a revisão final do escopo'
        edited = self.client.put('/api/knowledge/claims/1', json={
            'version': before['version'], 'content': human_content, 'reason': 'Revisão humana do escopo.'})
        self.assertEqual(edited.status_code, 200, edited.json)
        # A replay proposes the old interpretation and must expose its conflict
        # without replacing the manual correction in the model context.
        self.sql("UPDATE knowledge_jobs SET status='pending',cursor_json='{}'::jsonb WHERE id=:id", {'id': j10})
        self.process(sid, [self.item(quote)])
        entities = self.client.get('/api/knowledge/entities').json['entities']
        ids = {e['person_id']: e['id'] for e in entities if e['person_id']}
        manual = self.client.post('/api/knowledge/relations', json={
            'from_entity_id': ids[1], 'to_entity_id': ids[2], 'relation_type': 'related_to',
            'reason': 'Relação manual complementar.'})
        self.assertEqual(manual.status_code, 201, manual.json)
        manual_relation = manual.json['relation']
        db = self.factory()
        try:
            snapshot = context_snapshot(recover(db, person_id=1))
            claim = next(c for c in snapshot['claims'] if c['id'] == before['id'])
            self.assertEqual(claim['content'], human_content)
            self.assertTrue(claim['has_conflict'])
            self.assertIn('content', claim['corrected_fields'])
            self.assertIn('human_correction', claim['conflict_types'])
            assistant = memory_source_context(db)
            source = assistant['sources'][('meeting', mid)]
            self.assertTrue(any(r['id'] == original_relation['id'] for r in source['relations']))
            self.assertIn('depends_on', source['text'])
            manual_record = next(r for r in assistant['manual_records'] if r['id'] == f"relation:{manual_relation['id']}")
            self.assertIn('related_to', manual_record['excerpt'])
            corrected_source = assistant['sources'][('one_on_one', sid)]['text']
            self.assertIn('"has_conflict": true', corrected_source)
            self.assertIn('"corrected_fields"', corrected_source)
            self.assertIn(human_content, corrected_source)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
