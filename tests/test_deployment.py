"""Contratos de execução na Vercel, sem banco ou serviços externos."""
import os
import unittest
from unittest.mock import patch

from cortex import create_app
from cortex.oauth import callback_url


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'VERCEL': '1',
            'SESSION_SECRET': 'a-stable-test-secret-shared-between-instances',
            'OPENAI_API_KEY': 'test-key',
            'GOOGLE_CLIENT_ID': 'test-id',
            'GOOGLE_CLIENT_SECRET': 'test-secret',
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_cold_start_does_not_run_ddl(self):
        with patch('cortex.database.init_db') as init:
            app = create_app()
        init.assert_not_called()
        client = app.test_client()
        for url in ['/', '/login', '/connections?connected=gmail', '/static/js/app.js']:
            with self.subTest(url=url), client.get(url) as response:
                self.assertEqual(response.status_code, 200)

    def test_missing_secret_keeps_pages_available_and_blocks_api(self):
        os.environ.pop('SESSION_SECRET')
        app = create_app()
        with app.test_client() as client:
            with client.get('/') as response:
                self.assertEqual(response.status_code, 200)
            response = client.get('/api/current-user')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_session_survives_another_instance_and_uses_secure_cookie(self):
        first, second = create_app(), create_app()
        serializer = first.session_interface.get_signing_serializer(first)
        cookie = serializer.dumps({'user_id': 7, 'oauth_state': 'state'})
        decoded = second.session_interface.get_signing_serializer(second).loads(cookie)
        self.assertEqual(decoded['user_id'], 7)
        self.assertTrue(first.config['SESSION_COOKIE_SECURE'])
        self.assertTrue(first.config['SESSION_COOKIE_HTTPONLY'])
        self.assertEqual(first.config['SESSION_COOKIE_SAMESITE'], 'Lax')

    def test_health_checks_database_without_ddl_and_hides_internal_error(self):
        app = create_app()
        with patch('cortex.database.check_database', return_value='ok') as check, \
                patch('cortex.database.init_db') as init, \
                patch('cortex.database.INIT_DB_STATUS', None):
            response = app.test_client().get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['db_init'], 'skipped')
        check.assert_called_once()
        init.assert_not_called()
        with patch('cortex.database.check_database', return_value='error'), \
                patch('cortex.database.INIT_DB_STATUS', 'error:secret-dsn'):
            response = app.test_client().get('/healthz')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret-dsn', response.get_data(as_text=True))

    def test_incomplete_service_configuration_is_degraded(self):
        for key in ['SESSION_SECRET', 'GOOGLE_CLIENT_SECRET', 'OPENAI_API_KEY']:
            with self.subTest(key=key), patch.dict(os.environ, {key: ''}), \
                    patch('cortex.database.check_database', return_value='ok'):
                response = create_app().test_client().get('/healthz')
                self.assertEqual(response.status_code, 503)
        os.environ['APP_BASE_URL'] = 'https://example.com/invalid-path'
        with patch('cortex.database.check_database', return_value='ok'):
            response = create_app().test_client().get('/healthz')
        self.assertFalse(response.json['app_base_url_valid'])
        self.assertEqual(response.status_code, 503)

    def test_vercel_proxy_https_does_not_trust_forwarded_host(self):
        app = create_app()
        app.add_url_rule('/callback-test', view_func=lambda: callback_url('/callback'))
        response = app.test_client().get('/callback-test', base_url='http://cortex.example.com',
            headers={'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'untrusted.example.com'})
        self.assertEqual(response.get_data(as_text=True), 'https://cortex.example.com/callback')

    def test_local_start_keeps_schema_initialization_and_http_cookies(self):
        os.environ.pop('VERCEL')
        with patch('cortex.database.init_db') as init:
            app = create_app()
        init.assert_called_once()
        self.assertFalse(app.config['SESSION_COOKIE_SECURE'])
        os.environ['AUTO_INIT_DB'] = 'false'
        with patch('cortex.database.init_db') as init:
            create_app()
        init.assert_not_called()


if __name__ == '__main__':
    unittest.main()
