"""Callbacks públicos estáveis, HTTP local e falhas de rede nos fluxos OAuth."""
import os
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests
from flask import Flask

from cortex import oauth
from cortex.views import auth, connections


PROVIDER = {
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
}
CREDENTIALS = {"GOOGLE_CLIENT_ID": "client", "GOOGLE_CLIENT_SECRET": "secret"}


def response(body, status=200):
    result = Mock(status_code=status)
    result.json.return_value = body
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError()
    return result


class CallbackUrlTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    @patch.dict(os.environ, {}, clear=True)
    def test_local_http_and_public_https_follow_request_without_configuration(self):
        for origin in ("http://localhost:5000", "https://cortex.vercel.app"):
            with self.subTest(origin=origin), self.app.test_request_context(base_url=origin):
                self.assertEqual(oauth.callback_url("/callback"), origin + "/callback")

    @patch.dict(os.environ, {"APP_BASE_URL": " https://cortex.example.com/ "})
    def test_explicit_origin_does_not_depend_on_internal_host(self):
        with self.app.test_request_context(base_url="http://internal.invalid"):
            self.assertEqual(oauth.callback_url("/callback"), "https://cortex.example.com/callback")

    def test_invalid_origins_are_rejected_at_request_time(self):
        for origin in ("cortex.example.com", "https://example.com/app", "https://user:pass@example.com",
                       "https://example.com?x=1", "https://example.com#x", "https://example.com:invalid"):
            with self.subTest(origin=origin), patch.dict(os.environ, {"APP_BASE_URL": origin}):
                with self.app.test_request_context(), self.assertRaises(ValueError):
                    oauth.callback_url("/callback")


@patch.dict(os.environ, CREDENTIALS, clear=True)
class GoogleLoginTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="test")
        app.register_blueprint(auth.bp)
        self.client = app.test_client()

    @patch.object(auth.requests, "get", return_value=response(PROVIDER))
    def test_credentials_are_read_after_import_and_localhost_keeps_http(self, get):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.client.get("/api/auth/google/login").status_code, 503)
        get.assert_not_called()
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "updated-client"}):
            result = self.client.get("/api/auth/google/login")
        query = parse_qs(urlsplit(result.json["redirect_url"]).query)
        self.assertEqual(query["client_id"], ["updated-client"])
        self.assertEqual(query["redirect_uri"], ["http://localhost/api/auth/google/callback"])

    @patch.dict(os.environ, {"APP_BASE_URL": "https://cortex.example.com"})
    @patch.object(auth, "email_allowed", return_value=True)
    @patch.object(auth, "session_factory")
    @patch.object(auth.requests, "post", return_value=response({"access_token": "access"}))
    @patch.object(auth.requests, "get")
    def test_login_start_and_exchange_use_the_same_public_callback(self, get, post, factory, allowed):
        get.side_effect = [response(PROVIDER), response(PROVIDER), response({
            "sub": "google-user", "email": "director@example.com", "name": "Diretor",
        })]
        factory.return_value.execute.return_value.fetchone.return_value = (
            7, "Diretor", "TOTVS", "director@example.com",
        )
        started = self.client.get("/api/auth/google/login")
        query = parse_qs(urlsplit(started.json["redirect_url"]).query)
        result = self.client.get("/api/auth/google/callback", query_string={
            "state": query["state"][0], "code": "code",
        })
        self.assertEqual(result.location, "/")
        self.assertEqual(query["redirect_uri"][0], "https://cortex.example.com/api/auth/google/callback")
        self.assertEqual(post.call_args.kwargs["data"]["redirect_uri"], query["redirect_uri"][0])
        for call in get.call_args_list + post.call_args_list:
            self.assertEqual(call.kwargs["timeout"], oauth.HTTP_TIMEOUT)
        with self.client.session_transaction() as session:
            self.assertEqual(session["user_id"], 7)
            self.assertNotIn("oauth_state", session)

    @patch.object(auth.requests, "get")
    @patch.object(auth.requests, "post")
    def test_missing_state_never_exchanges_a_code(self, post, get):
        result = self.client.get("/api/auth/google/callback?code=code")
        self.assertEqual(result.status_code, 400)
        get.assert_not_called()
        post.assert_not_called()

    @patch.object(auth.requests, "get", side_effect=requests.Timeout())
    def test_discovery_timeout_returns_recoverable_error(self, get):
        result = self.client.get("/api/auth/google/login")
        self.assertEqual(result.status_code, 503)

    @patch.object(auth.requests, "get", return_value=response(PROVIDER))
    @patch.object(auth.requests, "post", return_value=response({"error": "invalid_grant"}, 400))
    def test_failed_exchange_does_not_fetch_profile_or_write_database(self, post, get):
        with self.client.session_transaction() as session:
            session["oauth_state"] = "state"
        with patch.object(auth, "session_factory") as factory:
            result = self.client.get("/api/auth/google/callback?state=state&code=code")
        self.assertEqual(result.status_code, 502)
        get.assert_called_once()
        factory.assert_not_called()


@patch.dict(os.environ, {**CREDENTIALS, "APP_BASE_URL": "https://cortex.example.com"}, clear=True)
class ConnectionCallbackTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="test")
        app.register_blueprint(connections.bp)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = 7

    @patch.object(connections, "session_factory")
    @patch.object(connections.requests, "post")
    @patch.object(connections.requests, "get")
    def test_start_and_exchange_share_public_origin_for_both_integrations(self, get, post, factory):
        factory.return_value.execute.return_value.fetchone.return_value = ("director@example.com",)
        for tool in ("gmail", "calendar"):
            with self.subTest(tool=tool):
                scope = connections.GMAIL_READONLY if tool == "gmail" else connections.calendar_svc.READONLY_SCOPE
                post.return_value = response({"access_token": "token", "scope": scope})
                get.side_effect = (
                    [response(PROVIDER), response({"emailAddress": "director@example.com"})]
                    if tool == "gmail" else [response({"email": "director@example.com", "email_verified": True})]
                )
                started = self.client.get(f"/api/connections/{tool}/start")
                query = parse_qs(urlsplit(started.json["redirect_url"]).query)
                result = self.client.get(f"/api/connections/{tool}/callback", query_string={
                    "state": query["state"][0], "code": "code",
                })
                self.assertEqual(result.location, f"/connections?connected={tool}")
                self.assertEqual(query["redirect_uri"][0], f"https://cortex.example.com/api/connections/{tool}/callback")
                self.assertEqual(post.call_args.kwargs["data"]["redirect_uri"], query["redirect_uri"][0])
        for call in get.call_args_list + post.call_args_list:
            self.assertEqual(call.kwargs["timeout"], oauth.HTTP_TIMEOUT)

    @patch.object(connections.requests, "get", side_effect=requests.Timeout())
    @patch.object(connections.requests, "post")
    def test_gmail_timeout_returns_to_connections(self, post, get):
        with self.client.session_transaction() as session:
            session["conn_state"] = "state"
        result = self.client.get("/api/connections/gmail/callback?state=state&code=code")
        self.assertEqual(result.location, "/connections?error=token_exchange_failed")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
