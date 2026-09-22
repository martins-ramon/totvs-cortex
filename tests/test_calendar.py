"""Execute com: python -m unittest discover -s tests -v."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import requests
from flask import Flask

from cortex import calendar
from cortex.views import connections, people


NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
PEOPLE = [{"id": 1, "email": "ana@example.com"}, {"id": 2, "email": None}]


def event(title="1:1 com Ana", start="2026-09-23T10:00:00-03:00", **overrides):
    return {
        "summary": title, "status": "confirmed", "start": {"dateTime": start},
        "organizer": {"email": "diretor@example.com", "self": True},
        "attendees": [{"email": "diretor@example.com", "self": True, "responseStatus": "accepted"},
                      {"email": "ANA@example.com", "responseStatus": "accepted"}],
        **overrides,
    }


def response(body, status=200):
    result = Mock(status_code=status)
    result.json.return_value = body
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError()
    return result


def connected_db():
    db = Mock()
    db.execute.return_value.fetchone.return_value = (
        'token', 'refresh', datetime.now(timezone.utc) + timedelta(hours=1),
        'connected', calendar.READONLY_SCOPE, 'diretor@example.com',
    )
    return db


class MatchingTests(unittest.TestCase):
    def match(self, events):
        return calendar.match_meetings(events, PEOPLE, 'diretor@example.com', NOW, 'America/Sao_Paulo')

    def test_titles_email_case_and_nearest_with_offsets(self):
        for title in ['1:1', '1x1', '1-1', '1/1', '1on1', 'One-on-One', 'one to one', 'um a um']:
            with self.subTest(title=title):
                first = event(title, '2026-09-23T12:00:00Z')
                later = event(title, '2026-09-23T10:00:00-03:00')
                self.assertEqual(self.match([later, first])['1']['start'], first['start']['dateTime'])

    def test_excludes_past_cancelled_declined_groups_and_unrelated_events(self):
        declined_director, declined_person, group, other = [event() for _ in range(4)]
        declined_director['attendees'][0]['responseStatus'] = 'declined'
        declined_person['attendees'][1]['responseStatus'] = 'declined'
        group['attendees'].append({'email': 'terceiro@example.com'})
        other['attendees'][1]['email'] = 'outra@example.com'
        for item in [event(start='2026-09-21T10:00:00Z'), event(status='cancelled'),
                     event('Projeto com Ana'), event('11:10'), event(attendeesOmitted=True),
                     declined_director, declined_person, group, other]:
            with self.subTest(item=item):
                self.assertEqual(self.match([item]), {})

    def test_resources_and_member_as_organizer(self):
        item = event(organizer={'email': 'ana@example.com'})
        item['attendees'].append({'email': 'sala@example.com', 'resource': True})
        self.assertIn('1', self.match([item]))

    def test_all_day_date_is_preserved(self):
        item = event()
        item['start'] = {'date': '2026-09-24'}
        self.assertEqual(self.match([item])['1'], {
            'start': '2026-09-24', 'all_day': True, 'time_zone': 'America/Sao_Paulo',
        })


class ServiceTests(unittest.TestCase):
    @patch.object(calendar.requests, 'get')
    def test_no_connection_does_not_request_google(self, get):
        db = connected_db()
        db.execute.return_value.fetchone.return_value = None
        self.assertEqual(calendar.upcoming_oneonones(db, 7, PEOPLE)['status'], 'not_connected')
        get.assert_not_called()

    @patch.object(calendar, 'match_meetings', return_value={'1': {'start': 'future'}})
    @patch.object(calendar.requests, 'get')
    def test_pagination_and_recurring_instances(self, get, match):
        get.side_effect = [response({'items': [], 'nextPageToken': 'page2'}),
                           response({'items': [event()], 'timeZone': 'America/Sao_Paulo'})]
        result = calendar.upcoming_oneonones(connected_db(), 7, PEOPLE)
        self.assertEqual(result['status'], 'connected')
        self.assertIn('1', result['meetings'])
        params = get.call_args.kwargs['params']
        self.assertEqual(params['singleEvents'], 'true')
        self.assertEqual(params['orderBy'], 'startTime')
        self.assertEqual(params['pageToken'], 'page2')
        self.assertEqual(match.call_args.args[0], [event()])

    @patch.object(calendar.requests, 'get')
    def test_partial_results_and_timeouts_never_mean_no_meetings(self, get):
        for failure in [requests.Timeout(), response({'error': {}}, 503), response({'error': {}}, 403)]:
            get.side_effect = [response({'items': [event()], 'nextPageToken': 'page2'}), failure]
            result = calendar.upcoming_oneonones(connected_db(), 7, PEOPLE)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['meetings'], {})

    @patch.dict('os.environ', {'GOOGLE_CLIENT_ID': 'client', 'GOOGLE_CLIENT_SECRET': 'secret'})
    @patch.object(calendar.requests, 'post')
    @patch.object(calendar.requests, 'get')
    def test_refresh_after_401_and_persist_new_token(self, get, post):
        db = connected_db()
        get.side_effect = [response({}, 401), response({'items': []})]
        post.return_value = response({'access_token': 'renewed', 'expires_in': 3600})
        self.assertEqual(calendar.upcoming_oneonones(db, 7, PEOPLE)['status'], 'connected')
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer renewed')
        self.assertEqual(db.execute.call_args.args[1]['token'], 'renewed')
        db.commit.assert_called_once()

    @patch.dict('os.environ', {'GOOGLE_CLIENT_ID': 'client', 'GOOGLE_CLIENT_SECRET': 'secret'})
    @patch.object(calendar.requests, 'post', return_value=response({'error': 'invalid_grant'}, 400))
    def test_revoked_refresh_marks_reauth(self, post):
        db = connected_db()
        row = list(db.execute.return_value.fetchone.return_value)
        row[2] = NOW - timedelta(days=365)
        db.execute.return_value.fetchone.return_value = row
        self.assertEqual(calendar.upcoming_oneonones(db, 7, PEOPLE)['status'], 'needs_reauth')
        db.commit.assert_called_once()


class RouteTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test')
        app.register_blueprint(connections.bp)
        app.register_blueprint(people.bp)
        self.client = app.test_client()

    def login(self):
        with self.client.session_transaction() as session:
            session['user_id'] = 7

    def test_endpoint_requires_login(self):
        self.assertEqual(self.client.get('/api/people/upcoming-oneonones').status_code, 401)

    @patch.object(people, 'session_factory')
    @patch.object(calendar, 'upcoming_oneonones')
    def test_endpoint_uses_current_user_and_disables_caching(self, upcoming, factory):
        self.login()
        factory.return_value.execute.return_value.fetchall.return_value = [(1, 'ana@example.com')]
        upcoming.return_value = {'status': 'connected', 'meetings': {}, 'lookahead_days': 90}
        result = self.client.get('/api/people/upcoming-oneonones')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        self.assertEqual(upcoming.call_args.args[1:], (7, [PEOPLE[0]]))

    @patch.object(connections, '_google_creds', return_value=('client', 'secret'))
    @patch.object(connections, 'session_factory')
    def test_calendar_oauth_start_and_gmail_scope_unchanged(self, factory, creds):
        self.login()
        factory.return_value.execute.return_value.fetchone.return_value = ('diretor@example.com',)
        for tool, scope in [('calendar', calendar.READONLY_SCOPE), ('gmail', connections.GMAIL_READONLY)]:
            result = self.client.get(f'/api/connections/{tool}/start')
            query = parse_qs(urlparse(result.json['redirect_url']).query)
            self.assertIn(scope, query['scope'][0].split())
            self.assertTrue(query['redirect_uri'][0].endswith(f'/{tool}/callback'))
        self.assertNotIn(calendar.READONLY_SCOPE, query['scope'][0])

    @patch.object(connections.requests, 'post')
    def test_callback_rejects_invalid_state_without_token_exchange(self, post):
        self.login()
        result = self.client.get('/api/connections/calendar/callback?state=invalid&code=code')
        self.assertIn('invalid_state', result.location)
        post.assert_not_called()

    @patch.object(connections, 'session_factory')
    @patch.object(connections.requests, 'get')
    @patch.object(connections.requests, 'post')
    def test_callback_persists_calendar_and_rejects_missing_scope(self, post, get, factory):
        self.login()
        get.return_value = response({'email': 'diretor@example.com', 'email_verified': True})
        for granted, expected in [(calendar.READONLY_SCOPE, 'connected=calendar'), ('openid email', 'error=calendar_scope')]:
            with self.client.session_transaction() as session:
                session['calendar_conn_state'] = 'state'
            post.return_value = response({'access_token': 'token', 'refresh_token': 'refresh', 'scope': granted})
            result = self.client.get('/api/connections/calendar/callback?state=state&code=code')
            self.assertIn(expected, result.location)
        factory.return_value.commit.assert_called_once()
        self.assertEqual(factory.return_value.execute.call_args.args[1]['uid'], 7)

    @patch.object(connections, 'session_factory')
    @patch.object(connections.requests, 'post')
    def test_calendar_disconnect_does_not_revoke_gmail(self, post, factory):
        self.login()
        result = self.client.post('/api/connections/calendar/disconnect')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(factory.return_value.execute.call_args.args[1]['tool'], 'calendar')
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
