"""Limites das integrações externas, sem rede ou banco de dados real."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests

from cortex import calendar, gmail
from cortex.ai import openai_service


def response(body, status=200):
    result = Mock(status_code=status, ok=status < 400)
    result.json.return_value = body
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError()
    return result


class OpenAILimitsTests(unittest.TestCase):
    @patch.object(openai_service, '_client', None)
    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'})
    @patch('openai.OpenAI')
    def test_requests_have_explicit_timeout_and_no_automatic_retries(self, constructor):
        self.assertIs(openai_service.get_client(), constructor.return_value)
        self.assertIs(openai_service.get_client(), constructor.return_value)
        constructor.assert_called_once_with(api_key='test-key', timeout=60.0, max_retries=0)

    @patch.object(openai_service, '_client', None)
    @patch.dict('os.environ', {}, clear=True)
    def test_missing_key_stays_a_request_time_error(self):
        with self.assertRaisesRegex(RuntimeError, 'OPENAI_API_KEY'):
            openai_service.get_client()


class GmailLimitsTests(unittest.TestCase):
    def setUp(self):
        gmail.clear_auth_skip()
        self.addCleanup(gmail.clear_auth_skip)

    @patch.object(gmail, 'get_access_token', return_value='token')
    def test_expired_scan_discards_partial_threads_and_stops_requests(self, token):
        clock = [0.0]
        responses = iter([
            (1.0, {'scope': gmail.GMAIL_READONLY}),
            (55.0, {'threads': [{'id': 'one'}, {'id': 'two'}]}),
            (60.0, {'messages': [{'snippet': 'partial', 'payload': {}}]}),
        ])

        def get(*args, **kwargs):
            clock[0], body = next(responses)
            return response(body)

        with patch.object(gmail, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(gmail.requests, 'get', side_effect=get) as request:
            result = gmail.scan_person_inbox(Mock(), 7, 'ana@example.com')
        self.assertEqual(result, {
            'scanned': False, 'skipped': 'erro', 'threads': [], 'thread_count': 0,
        })
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args.kwargs['timeout'], 5.0)

    @patch.dict('os.environ', {'GOOGLE_CLIENT_ID': 'client', 'GOOGLE_CLIENT_SECRET': 'secret'})
    def test_refresh_time_uses_same_scan_budget(self):
        clock = [0.0]
        db = Mock()
        db.execute.return_value.fetchone.return_value = (
            'old', 'refresh', datetime.now(timezone.utc) - timedelta(hours=1), 'connected',
        )

        def discovery(*args, **kwargs):
            clock[0] = 55.0
            return response({'token_endpoint': 'https://oauth2.googleapis.com/token'})

        def refresh(*args, **kwargs):
            clock[0] = 60.0
            return response({'access_token': 'renewed'})

        with patch.object(gmail, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(gmail.requests, 'get', side_effect=discovery) as get, \
                patch.object(gmail.requests, 'post', side_effect=refresh) as post:
            result = gmail.scan_person_inbox(db, 7, 'ana@example.com')
        self.assertFalse(result['scanned'])
        self.assertEqual(result['skipped'], 'erro')
        get.assert_called_once()
        self.assertEqual(post.call_args.kwargs['timeout'], 5.0)

    @patch.object(gmail, 'get_access_token', side_effect=['token', requests.Timeout()])
    @patch.object(gmail, 'token_has_gmail_scope', return_value=True)
    @patch.object(gmail, 'search_person_threads', side_effect=RuntimeError('HTTP 401', 'gmail_sem_permissao'))
    def test_expired_retry_does_not_escape_or_mark_credentials_revoked(self, search, scope, token):
        db = Mock()
        result = gmail.scan_person_inbox(db, 7, 'ana@example.com')
        self.assertEqual(result['skipped'], 'erro')
        self.assertFalse(result['scanned'])
        db.execute.assert_not_called()


class CalendarLimitsTests(unittest.TestCase):
    def connected_db(self):
        db = Mock()
        db.execute.return_value.fetchone.return_value = (
            'token', 'refresh', datetime.now(timezone.utc) + timedelta(hours=1),
            'connected', calendar.READONLY_SCOPE, 'diretor@example.com',
        )
        return db

    def test_expired_pagination_never_reports_partial_results_as_connected(self):
        clock = [0.0]
        elapsed = iter([58.0, 60.0])

        def get(*args, **kwargs):
            clock[0] = next(elapsed)
            return response({'items': [], 'nextPageToken': 'more'})

        with patch.object(calendar, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(calendar.requests, 'get', side_effect=get) as request:
            result = calendar.upcoming_oneonones(self.connected_db(), 7, [])
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['meetings'], {})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs['timeout'], 2.0)

    @patch.dict('os.environ', {'GOOGLE_CLIENT_ID': 'client', 'GOOGLE_CLIENT_SECRET': 'secret'})
    def test_refresh_and_retry_share_pagination_budget(self):
        clock = [0.0]

        def unauthorized(*args, **kwargs):
            clock[0] = 58.0
            return response({}, 401)

        def refresh(*args, **kwargs):
            clock[0] = 60.0
            return response({'access_token': 'renewed'})

        with patch.object(calendar, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(calendar.requests, 'get', side_effect=unauthorized) as get, \
                patch.object(calendar.requests, 'post', side_effect=refresh) as post:
            result = calendar.upcoming_oneonones(self.connected_db(), 7, [])
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['meetings'], {})
        get.assert_called_once()
        self.assertEqual(post.call_args.kwargs['timeout'], 2.0)


if __name__ == '__main__':
    unittest.main()
