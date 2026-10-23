"""Fóruns enriquecem a visão individual sem inferir autoria por participação."""
import json
import unittest
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cortex.ai.assistant_service import validate_reply
from cortex.meetings_context import load_meeting_context
from cortex.views import cards, sessions


class MeetingAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.factory = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        ddl = [
            """CREATE TABLE people (id INTEGER PRIMARY KEY, full_name TEXT, preferred_name TEXT,
               email TEXT, role_title TEXT, active BOOLEAN, hired_at TEXT, notes TEXT, photo TEXT)""",
            """CREATE TABLE commitments (id INTEGER PRIMARY KEY, person_id INTEGER, description TEXT,
               owner TEXT, due_date TEXT, status TEXT, one_on_one_id INTEGER)""",
            """CREATE TABLE one_on_ones (id INTEGER PRIMARY KEY, person_id INTEGER, occurred_on TEXT,
               title TEXT, source TEXT, transcript_raw TEXT, summary_ai TEXT, private_notes TEXT,
               public_notes TEXT, sentiment TEXT, topics TEXT, extraction_json TEXT)""",
            """CREATE TABLE checkpoints (id INTEGER PRIMARY KEY, person_id INTEGER, checkpoint_date TEXT,
               actions_json TEXT, private_notes TEXT, public_notes TEXT, source TEXT)""",
            """CREATE TABLE meetings (id INTEGER PRIMARY KEY, title TEXT, forum TEXT, occurred_on TEXT,
               external_participants TEXT, transcript_raw TEXT, notes TEXT, summary_ai TEXT,
               extraction_json TEXT)""",
            """CREATE TABLE meeting_participants (meeting_id INTEGER, person_id INTEGER,
               PRIMARY KEY (meeting_id, person_id))""",
            """CREATE TABLE meeting_actions (id INTEGER PRIMARY KEY, meeting_id INTEGER, person_id INTEGER,
               description TEXT, owner_name TEXT, due_date TEXT, status TEXT, source_key TEXT,
               closed_at TEXT, created_at TEXT, updated_at TEXT)""",
        ]
        with self.engine.begin() as db:
            for statement in ddl:
                db.execute(text(statement))
        for person_id, name in ((1, "André Pasold"), (2, "Ana Maria"), (3, "Bruno Almeida")):
            self.insert("people", {"id": person_id, "full_name": name, "active": True})
        self.insert("meetings", {"id": 10, "title": "Estratégia de produto", "forum": "Produto",
                                 "occurred_on": "2026-10-01", "transcript_raw": "Alinhar dependências.",
                                 "notes": "Evidência atual sobre capacidade.", "summary_ai": "OUTDATEDSECRET",
                                 "extraction_json": json.dumps({"summary": "OUTDATEDSECRET",
                                                                 "source_fingerprint": "old"})})
        self.insert("meeting_participants", {"meeting_id": 10, "person_id": 2})
        self.insert("meeting_actions", {"id": 20, "meeting_id": 10, "person_id": 1,
                                        "description": "Revisar dependências de plataforma",
                                        "owner_name": "André Pasold", "due_date": "2000-01-01", "status": "open"})
        # A mere mention cannot attach this meeting to Bruno's individual analysis.
        self.insert("meetings", {"id": 11, "title": "Outro fórum", "forum": "Operações",
                                 "occurred_on": "2026-10-02", "transcript_raw": "Bruno Almeida foi mencionado."})
        self.insert("meeting_participants", {"meeting_id": 11, "person_id": 2})

    def insert(self, table, values):
        columns = ", ".join(values)
        placeholders = ", ".join(":" + key for key in values)
        with self.engine.begin() as db:
            db.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"), values)

    def prep(self, person_id):
        with self.factory() as db:
            person = db.execute(text(sessions._PERSON_FOR_PREP), {"pid": person_id}).fetchone()
            return sessions._collect_prep_data(db, person)

    def test_assigned_action_adds_meeting_without_declaring_attendance_or_changing_oneonone_stats(self):
        prep = self.prep(1)
        self.assertEqual([m["id"] for m in prep["group_meetings"]], [10])
        self.assertEqual([p["id"] for p in prep["group_meetings"][0]["participants"]], [2])
        self.assertEqual([a["id"] for a in prep["meeting_actions"]], [20])
        self.assertEqual(prep["stats"]["open_meeting_actions"], 1)
        self.assertEqual(prep["stats"]["overdue_meeting_actions"], 1)
        self.assertIsNone(prep["stats"]["days_since_last"])
        self.assertIsNone(prep["stats"]["sentiment_trend"])
        self.assertEqual(prep["stats"]["open_count"], 0)
        self.assertEqual(prep["recent_sessions"], [])

    def test_plain_mentions_and_unknown_people_do_not_gain_group_context(self):
        prep = self.prep(3)
        self.assertEqual(prep["group_meetings"], [])
        self.assertEqual(prep["meeting_actions"], [])
        with self.factory() as db:
            self.assertEqual(load_meeting_context(db, person_id=9999), {"meetings": [], "actions": []})

    def test_profile_counts_are_complete_when_group_context_is_bounded(self):
        for record_id in range(30, 100):
            self.insert("meeting_actions", {"id": record_id, "meeting_id": 10, "person_id": 1,
                                            "description": "Revisar dependência", "status": "open"})
        for record_id in range(100, 110):
            self.insert("meetings", {"id": record_id, "title": "Revisão", "occurred_on": "2026-10-01"})
            self.insert("meeting_participants", {"meeting_id": record_id, "person_id": 1})
        prep = self.prep(1)
        self.assertEqual(prep["stats"]["open_meeting_actions"], 71)
        self.assertEqual(prep["stats"]["overdue_meeting_actions"], 1)
        self.assertEqual(prep["stats"]["group_meetings_count"], 11)
        self.assertLess(len(prep["meeting_actions"]), 71)
        self.assertLess(len(prep["group_meetings"]), 11)

    def test_card_uses_current_sources_and_attribution_caveat(self):
        context = cards._build_context(self.prep(1))
        self.assertIn("Estratégia de produto", context)
        self.assertIn("Evidência atual sobre capacidade", context)
        self.assertIn("Revisar dependências de plataforma", context)
        self.assertIn("Presença ou menção não prova", context)
        self.assertIn("meeting:ID", context)
        self.assertIn("meeting_action:ID", context)
        self.assertIn('"source_id": "meeting:10"', context)
        self.assertIn('"source_id": "meeting_action:20"', context)
        self.assertNotIn("OUTDATEDSECRET", context)
        self.assertNotIn("Outro fórum", context)

    def test_agenda_receives_same_relevant_group_sources_and_disclaimer(self):
        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test"
        app.register_blueprint(sessions.bp)
        client = app.test_client()
        with client.session_transaction() as auth:
            auth["user_id"] = 1
        with patch.object(sessions, "session_factory", side_effect=self.factory), \
                patch.object(sessions, "_collect_email_insights", return_value={}), \
                patch.object(sessions.ai, "generate_prep_agenda", return_value="Pauta") as generate:
            response = client.post("/api/people/1/agenda")
        self.assertEqual(response.status_code, 200)
        context = generate.call_args.args[1]
        self.assertIn("Estratégia de produto", context)
        self.assertIn("Revisar dependências de plataforma", context)
        self.assertIn("Presença ou menção não prova", context)
        self.assertIn('"source_id": "meeting:10"', context)
        self.assertIn('"source_id": "meeting_action:20"', context)
        self.assertNotIn("OUTDATEDSECRET", context)

    def test_assistant_validator_preserves_verified_meeting_navigation_metadata(self):
        source = {"id": "meeting:10", "type": "meeting", "title": "Estratégia de produto",
                  "excerpt": "Alinhar dependências.", "meeting_id": 10, "participant_ids": [2],
                  "record_id": 10, "person_id": None, "private_internal_key": "omit"}
        result = validate_reply({"answer": "Há dependências a alinhar.", "source_ids": ["meeting:10"]},
                                {"sources": [source]})
        self.assertEqual(result["sources"][0]["meeting_id"], 10)
        self.assertEqual(result["sources"][0]["participant_ids"], [2])
        self.assertNotIn("private_internal_key", result["sources"][0])


if __name__ == "__main__":
    unittest.main()
