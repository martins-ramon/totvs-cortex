"""Aliases de transcrições são parte do cadastro e preservam updates parciais."""
import json
import unittest
from unittest.mock import Mock, patch

from flask import Flask

from cortex import database
from cortex.views import people


class PeopleTranscriptAliasesTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="test")
        app.register_blueprint(people.bp)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = 7

    def db(self):
        db = Mock()
        db.execute.return_value.fetchone.return_value = (41,)
        db.execute.return_value.rowcount = 1
        return db

    def test_create_person_normalizes_and_persists_aliases(self):
        db = self.db()
        with patch.object(people, "session_factory", return_value=db):
            response = self.client.post("/api/people", json={
                "full_name": "Adriano Forni",
                "transcript_aliases": ["  FORN  ", "forn", "FORNE"],
            })
        self.assertEqual(response.status_code, 201)
        statement, params = db.execute.call_args.args
        self.assertIn("CAST(:aliases AS jsonb)", str(statement))
        self.assertEqual(json.loads(params["aliases"]), ["FORN", "FORNE"])
        db.commit.assert_called_once_with()
        db.close.assert_called_once_with()

    def test_create_without_aliases_uses_empty_list(self):
        db = self.db()
        with patch.object(people, "session_factory", return_value=db):
            response = self.client.post("/api/people", json={"full_name": "Ana"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(json.loads(db.execute.call_args.args[1]["aliases"]), [])

    def test_invalid_aliases_are_rejected_before_opening_database(self):
        invalid = [None, "FORN", [1], [""], ["---"], ["x" * 101], [f"nome{i}" for i in range(21)]]
        for value in invalid:
            for method, path, body in (
                ("post", "/api/people", {"full_name": "Ana", "transcript_aliases": value}),
                ("put", "/api/people/41", {"transcript_aliases": value}),
                ("patch", "/api/people/41", {"transcript_aliases": value}),
            ):
                with self.subTest(value=value, method=method), \
                        patch.object(people, "session_factory") as factory:
                    response = getattr(self.client, method)(path, json=body)
                    self.assertEqual(response.status_code, 400)
                    self.assertTrue(response.json["error"])
                    factory.assert_not_called()

    def test_partial_updates_preserve_aliases_when_omitted(self):
        for method in ("put", "patch"):
            db = self.db()
            with self.subTest(method=method), patch.object(people, "session_factory", return_value=db):
                response = getattr(self.client, method)("/api/people/41", json={"notes": "Contexto"})
            self.assertEqual(response.status_code, 200)
            statement, params = db.execute.call_args.args
            self.assertNotIn("transcript_aliases", str(statement))
            self.assertNotIn("aliases", params)

    def test_empty_list_explicitly_clears_aliases(self):
        db = self.db()
        with patch.object(people, "session_factory", return_value=db):
            response = self.client.patch("/api/people/41", json={"transcript_aliases": []})
        self.assertEqual(response.status_code, 200)
        statement, params = db.execute.call_args.args
        self.assertIn("transcript_aliases = CAST(:aliases AS jsonb)", str(statement))
        self.assertEqual(json.loads(params["aliases"]), [])

    def test_list_and_detail_return_configured_aliases(self):
        row = (41, "Adriano Forni", None, None, None, True, None, None, False, None, ["FORN", "FORNE"])
        for path, result_key in (("/api/people", "people"), ("/api/people/41", "person")):
            db = self.db()
            db.execute.return_value.fetchone.return_value = row
            db.execute.return_value.fetchall.return_value = [row]
            with self.subTest(path=path), patch.object(people, "session_factory", return_value=db):
                response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            record = response.json[result_key]
            if isinstance(record, list):
                record = record[0]
            self.assertEqual(record["transcript_aliases"], ["FORN", "FORNE"])
            self.assertEqual(record["full_name"], "Adriano Forni")


class PeopleTranscriptAliasesSchemaTests(unittest.TestCase):
    def test_new_and_existing_schemas_define_non_nullable_default(self):
        create_people = next(ddl for ddl, name in database._NEW_DDL if name == "people")
        evolution = next(ddl for ddl, name in database._EVOLUTION_DDL if name == "people.transcript_aliases")
        self.assertIn("transcript_aliases JSONB NOT NULL DEFAULT '[]'::jsonb", create_people)
        self.assertIn("ADD COLUMN IF NOT EXISTS transcript_aliases JSONB NOT NULL DEFAULT '[]'::jsonb", evolution)

    @patch.dict("os.environ", {"DATABASE_URL": "postgresql://test:test@localhost:6543/cortex"})
    @patch.object(database, "get_engine")
    def test_readiness_probe_checks_alias_column_without_reading_records(self, engine):
        conn = engine.return_value.connect.return_value.__enter__.return_value
        self.assertEqual(database.check_database(), "ok")
        probe = str(conn.execute.call_args_list[1].args[0])
        self.assertIn("people.transcript_aliases", probe)
        self.assertIn("WHERE FALSE", probe)
        conn.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
