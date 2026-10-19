"""INSERT ... RETURNING deve ser consumido antes do commit com NullPool."""
import os
import unittest
from unittest.mock import Mock, patch

from flask import Flask
from sqlalchemy.exc import InterfaceError

from cortex.views import auth, cards, checkpoints, people, sessions


class ClosingCursorResult:
    """Resultado cujo cursor perde validade ao encerrar a conexão."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.closed = False

    def _check_open(self):
        if self.closed:
            raise InterfaceError(None, None, RuntimeError("cursor already closed"))

    def fetchone(self):
        self._check_open()
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        self._check_open()
        rows, self.rows = self.rows, []
        return rows


def closing_session(*rowsets, commit_error=None):
    """Simula a devolução da conexão ao NullPool no commit/close."""
    db = Mock()
    pending = iter(rowsets)
    results = []

    def execute(*args, **kwargs):
        result = ClosingCursorResult(next(pending))
        results.append(result)
        return result

    def close_cursors():
        for result in results:
            result.closed = True

    def commit():
        close_cursors()
        if commit_error:
            raise commit_error

    db.execute.side_effect = execute
    db.commit.side_effect = commit
    db.rollback.side_effect = close_cursors
    db.close.side_effect = close_cursors
    return db


def http_response(body):
    response = Mock()
    response.json.return_value = body
    return response


class CreateRecordsTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "ALLOWED_EMAILS": "director@example.com",
            "GOOGLE_CLIENT_ID": "test-client",
            "GOOGLE_CLIENT_SECRET": "test-secret",
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test")
        for module in (auth, cards, checkpoints, people, sessions):
            self.app.register_blueprint(module.bp)
        self.client = self.app.test_client()

    def login(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 7

    def assert_committed_and_closed(self, db):
        db.commit.assert_called_once_with()
        db.close.assert_called_once_with()
        db.rollback.assert_not_called()

    def register_local(self, db):
        with patch.object(auth, "session_factory", return_value=db), \
                patch.object(auth, "hash_password", return_value="test-hash"):
            return self.client.post("/api/register", json={
                "name": "Diretor", "email": "director@example.com", "password": "test",
            })

    def register_google(self, db):
        with self.client.session_transaction() as session:
            session["oauth_state"] = "test-state"
            session["oauth_mode"] = "login"
        provider = {
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
        }
        profile = {"sub": "google-user", "name": "Diretor", "email": "director@example.com"}
        with patch.object(auth, "session_factory", return_value=db), \
                patch.object(auth, "hash_password", return_value="test-hash"), \
                patch.object(auth, "get_google_provider_cfg", return_value=provider), \
                patch.object(auth.requests, "post", return_value=http_response({"access_token": "test-token"})), \
                patch.object(auth.requests, "get", return_value=http_response(profile)):
            return self.client.get("/api/auth/google/callback", query_string={
                "state": "test-state", "code": "test-code",
            })

    def test_create_oneonone_returns_persisted_id(self):
        self.login()
        db = closing_session([(7,)], [(101,)])
        with patch.object(sessions, "session_factory", return_value=db):
            response = self.client.post("/api/oneonones", json={
                "person_id": 7, "occurred_on": "2026-10-19", "title": "1:1 de outubro",
            })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json, {"success": True, "session_id": 101})
        self.assert_committed_and_closed(db)

    def test_create_person_returns_persisted_id(self):
        self.login()
        db = closing_session([(102,)])
        with patch.object(people, "session_factory", return_value=db):
            response = self.client.post("/api/people", json={"full_name": "Ana"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json, {"success": True, "person_id": 102})
        self.assert_committed_and_closed(db)

    def test_create_checkpoint_returns_persisted_id(self):
        self.login()
        db = closing_session([(1,)], [(103,)])
        with patch.object(checkpoints, "session_factory", return_value=db):
            response = self.client.post("/api/checkpoints", json={
                "person_id": 7, "checkpoint_date": "2026-10-19", "private_notes": "Evolução do mês",
            })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json, {"success": True, "checkpoint_id": 103})
        self.assert_committed_and_closed(db)

    def test_start_cards_returns_job_id_and_people(self):
        self.login()
        db = closing_session([], [(7, "Ana", "ana@example.com")], [(104,)])
        with patch.object(cards, "session_factory", return_value=db):
            response = self.client.post("/api/cards/generate-start")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {
            "job_id": 104,
            "people": [{"id": 7, "full_name": "Ana", "email": "ana@example.com"}],
        })
        self.assert_committed_and_closed(db)

    def test_local_registration_returns_id_and_authenticates(self):
        db = closing_session([(105,)])
        response = self.register_local(db)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"success": True, "user_id": 105})
        with self.client.session_transaction() as session:
            self.assertEqual(session["user_id"], 105)
        self.assert_committed_and_closed(db)

    def test_google_registration_authenticates_new_user(self):
        db = closing_session([], [], [(106, "Diretor", "TOTVS", "director@example.com")])
        response = self.register_google(db)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/")
        with self.client.session_transaction() as session:
            self.assertEqual(session["user_id"], 106)
            self.assertNotIn("oauth_state", session)
        self.assert_committed_and_closed(db)

    def test_failed_oneonone_commit_never_returns_success(self):
        self.login()
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        db = closing_session([(7,)], [(101,)], commit_error=RuntimeError("commit failed"))
        with patch.object(sessions, "session_factory", return_value=db), \
                self.assertLogs(self.app.logger, level="ERROR"):
            response = self.client.post("/api/oneonones", json={
                "person_id": 7, "occurred_on": "2026-10-19",
            })
        self.assertEqual(response.status_code, 500)
        self.assertFalse((response.get_json(silent=True) or {}).get("success"))
        db.commit.assert_called_once_with()
        db.close.assert_called_once_with()

    def test_failed_registration_commit_does_not_authenticate(self):
        cases = (
            ("local", self.register_local, [[(105,)]], 400),
            ("google", self.register_google,
             [[], [], [(106, "Diretor", "TOTVS", "director@example.com")]], 500),
        )
        for name, register, rowsets, status in cases:
            with self.subTest(registration=name):
                db = closing_session(*rowsets, commit_error=RuntimeError("commit failed"))
                response = register(db)
                self.assertEqual(response.status_code, status)
                self.assertFalse(response.json.get("success"))
                with self.client.session_transaction() as session:
                    self.assertNotIn("user_id", session)
                db.commit.assert_called_once_with()
                db.rollback.assert_called_once_with()
                db.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
