"""Fluxo HTTP da correção de nomes, sem banco ou chamadas externas."""
import json
import unittest
from datetime import date
from unittest.mock import Mock, patch

from flask import Flask

from cortex.transcripts import normalization_metadata, normalize_transcript
from cortex.views import sessions


PEOPLE = [(7, 'Adriano Forni', None, ['FORN', 'FORNE']),
          (8, 'Rafael Quines', None, ['KINIS'])]
RAW = 'Adriano FORN: Vou alinhar com Rafael KINIS. FORNE retorna amanhã.'


def meeting(transcript=RAW, extraction=None):
    return (51, 7, date(2026, 10, 19), 'Quinzena', 'meet_paste', transcript,
            None, None, None, 'neutro', [], extraction)


def db_results(*results):
    db = Mock()
    db.execute.side_effect = [Mock(fetchone=Mock(return_value=one),
                                  fetchall=Mock(return_value=all_rows))
                              for one, all_rows in results]
    return db


class SessionTranscriptTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test')
        app.register_blueprint(sessions.bp)
        self.client = app.test_client()
        with self.client.session_transaction() as state:
            state['user_id'] = 1

    def test_detail_previews_all_names_without_writing_or_calling_ai(self):
        db = db_results((meeting(extraction={'resumo': 'Análise antiga'}), []), (None, PEOPLE))
        with patch.object(sessions, 'session_factory', return_value=db), \
                patch.object(sessions.ai, 'extract_meeting_insights') as ai:
            response = self.client.get('/api/oneonones/51')
        self.assertEqual(response.status_code, 200)
        record = response.json['session']
        self.assertEqual(record['transcript_raw'], RAW)
        preview = record['transcript_normalization']
        self.assertEqual(preview['text'], 'Adriano Forni: Vou alinhar com Rafael Quines. Adriano Forni retorna amanhã.')
        self.assertTrue(preview['needs_reextraction'])
        self.assertEqual(len(preview['corrections']), 3)
        statements = [str(call.args[0]) for call in db.execute.call_args_list]
        self.assertTrue(all(sql.strip().startswith('SELECT') for sql in statements))
        self.assertNotIn('WHERE active', statements[1])
        db.commit.assert_not_called()
        db.close.assert_called_once_with()
        ai.assert_not_called()

    def test_extraction_uses_corrected_text_and_stores_snapshot_preserving_original(self):
        db = db_results((meeting(), []), (None, PEOPLE), (None, []), (None, []),
                        (None, []), (meeting(extraction={'resumo': 'Resumo novo'}), []))
        raw_ai = {'resumo': 'Alinhamento com Rafael Quines', 'combinados': [
            {'descricao': 'Alinhar com Rafael Quines', 'responsavel': 'liderado'}]}
        with patch.object(sessions, 'session_factory', return_value=db), \
                patch.object(sessions.ai, 'extract_meeting_insights', return_value=raw_ai) as ai:
            response = self.client.post('/api/oneonones/51/extract')
        self.assertEqual(response.status_code, 200)
        record = response.json['session']
        ai.assert_called_once_with('Adriano Forni', '2026-10-19', record['transcript_normalization']['text'])
        self.assertNotIn('KINIS', ai.call_args.args[2])
        self.assertEqual(record['transcript_raw'], RAW)
        self.assertFalse(record['transcript_normalization']['needs_reextraction'])
        statement, params = db.execute.call_args_list[2].args
        self.assertNotIn('transcript_raw', str(statement))
        persisted = json.loads(params['ext'])['transcript_normalization']
        self.assertEqual(persisted['fingerprint'], record['transcript_normalization']['fingerprint'])
        self.assertEqual(persisted['corrections'], record['transcript_normalization']['corrections'])
        self.assertNotIn('text', persisted)
        # A reextração continua preservando combinados finalizados.
        self.assertIn("status = 'open'", str(db.execute.call_args_list[3].args[0]))
        db.commit.assert_called_once_with()
        db.rollback.assert_not_called()
        db.close.assert_called_once_with()

    def test_dictionary_change_marks_analyzed_meeting_as_outdated(self):
        previous = normalize_transcript(RAW, [])
        ext = {'resumo': 'Antigo', 'transcript_normalization': normalization_metadata(previous)}
        db = db_results((meeting(extraction=ext), []), (None, PEOPLE))
        with patch.object(sessions, 'session_factory', return_value=db):
            response = self.client.get('/api/oneonones/51')
        self.assertTrue(response.json['session']['transcript_normalization']['needs_reextraction'])

    def test_ai_failure_rolls_back_without_writing_original_or_insights(self):
        db = db_results((meeting(), []), (None, PEOPLE))
        with patch.object(sessions, 'session_factory', return_value=db), \
                patch.object(sessions.ai, 'extract_meeting_insights', side_effect=RuntimeError('indisponível')):
            response = self.client.post('/api/oneonones/51/extract')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(db.execute.call_count, 2)
        db.commit.assert_not_called()
        db.rollback.assert_called_once_with()
        db.close.assert_called_once_with()

    def test_missing_meeting_or_transcript_never_loads_dictionary_or_calls_ai(self):
        for row, status in ((None, 404), (meeting(transcript=None), 400)):
            db = db_results((row, []))
            with self.subTest(status=status), \
                    patch.object(sessions, 'session_factory', return_value=db), \
                    patch.object(sessions.ai, 'extract_meeting_insights') as ai:
                response = self.client.post('/api/oneonones/51/extract')
            self.assertEqual(response.status_code, status)
            self.assertEqual(db.execute.call_count, 1)
            ai.assert_not_called()
            db.close.assert_called_once_with()

    def test_save_and_edit_keep_exact_original_text(self):
        db = db_results(((7,), []), ((51,), []))
        with patch.object(sessions, 'session_factory', return_value=db):
            response = self.client.post('/api/oneonones', json={
                'person_id': 7, 'occurred_on': '2026-10-19', 'transcript_raw': RAW})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(db.execute.call_args.args[1]['transcript'], RAW)
        db = Mock()
        db.execute.return_value.rowcount = 1
        with patch.object(sessions, 'session_factory', return_value=db):
            response = self.client.put('/api/oneonones/51', json={'transcript_raw': RAW})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.execute.call_args.args[1]['trans'], RAW)

    def test_removed_transcript_keeps_outdated_warning_without_loading_dictionary(self):
        db = db_results((meeting(transcript=None, extraction={'resumo': 'Anterior'}), []))
        with patch.object(sessions, 'session_factory', return_value=db):
            response = self.client.get('/api/oneonones/51')
        self.assertTrue(response.json['session']['transcript_normalization']['needs_reextraction'])
        self.assertEqual(db.execute.call_count, 1)

    def test_transcript_and_dictionary_require_login(self):
        with self.client.session_transaction() as state:
            state.clear()
        with patch.object(sessions, 'session_factory') as factory:
            self.assertEqual(self.client.get('/api/oneonones/51').status_code, 401)
            self.assertEqual(self.client.post('/api/oneonones/51/extract').status_code, 401)
        factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
