"""Memória persistente, isolada por diretor, sem serviços externos."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cortex import assistant_memory as memory


class AssistantMemoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.sessions = sessionmaker(bind=self.engine)
        with self.engine.begin() as db:
            db.execute(text("""
                CREATE TABLE assistant_memories (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                    subject TEXT NOT NULL, subject_key TEXT NOT NULL,
                    content TEXT NOT NULL, evidence TEXT NOT NULL,
                    source_message TEXT NOT NULL, conversation_context TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (user_id, subject_key))
            """))
        factory = patch.object(memory, "session_factory", side_effect=self.sessions)
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        self.addCleanup(self.engine.dispose)

    def proposal(self, **values):
        return {"memory_id": None, "subject": "Equipe do Alestan",
                "content": "O time do Alestan é Ana e Bruno.",
                "evidence": "Ana e Bruno", **values}

    def save(self, user_id=7, proposals=None, message="O time do Alestan é Ana e Bruno.",
             history=None, context=None):
        return memory.save_memories(user_id, proposals if proposals is not None else [self.proposal()],
                                    message, history or [], context or {"sources": []})

    def context(self, row, **changes):
        return {"sources": [{"id": f"memory:{row['id']}", "type": "memory",
                             "memory_version": row["version"], **changes}]}

    def test_memory_survives_new_sessions_and_retains_user_provenance(self):
        results = self.save(history=[
            {"role": "user", "content": "Quem faz parte do time do Alestan?"},
            {"role": "assistant", "content": "Não tenho evidência suficiente."}])
        self.assertEqual(results[0]["status"], "saved")
        row = memory.load_memories(7)[0]
        self.assertEqual(row["content"], "O time do Alestan é Ana e Bruno.")
        self.assertEqual(row["evidence"], "Ana e Bruno")
        self.assertEqual(row["source_message"], "O time do Alestan é Ana e Bruno.")
        self.assertEqual(row["conversation_context"], "Quem faz parte do time do Alestan?")
        self.assertEqual(row["version"], 1)
        self.assertEqual(memory.list_memories(7), memory.load_memories(7))

    def test_directors_have_separate_memory_and_cannot_delete_each_others_facts(self):
        first = self.save()[0]
        second = self.save(user_id=9)[0]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(memory.load_memories(7)), 1)
        self.assertEqual(len(memory.load_memories(9)), 1)
        self.assertEqual(memory.load_memories(11), [])
        self.assertFalse(memory.delete_memory(9, first["id"]))
        self.assertTrue(memory.delete_memory(7, first["id"]))
        self.assertFalse(memory.delete_memory(7, first["id"]))
        self.assertEqual(memory.load_memories(7), [])
        self.assertEqual(len(memory.load_memories(9)), 1)

    def test_duplicate_content_is_idempotent_with_folded_subject(self):
        original = self.save()[0]
        duplicate = self.save(proposals=[self.proposal(subject="  EQUIPE  do Álestan  ")])[0]
        self.assertEqual(duplicate["id"], original["id"])
        self.assertEqual(duplicate["status"], "unchanged")
        self.assertEqual(len(memory.load_memories(7)), 1)
        self.assertEqual(memory.load_memories(7)[0]["version"], 1)

    def test_correction_requires_consulted_version_and_replaces_content(self):
        self.save()
        old = memory.load_memories(7)[0]
        correction = self.proposal(memory_id=old["id"], content="O time do Alestan é Ana e Carla.",
                                   evidence="Ana e Carla")
        result = self.save(proposals=[correction], message="Corrigindo: o time é Ana e Carla.",
                           context=self.context(old))[0]
        self.assertEqual(result["status"], "updated")
        rows = memory.load_memories(7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "O time do Alestan é Ana e Carla.")
        self.assertEqual(rows[0]["version"], 2)
        replay = self.save(proposals=[correction], message="Corrigindo: o time é Ana e Carla.",
                           context=self.context(old))[0]
        self.assertEqual(replay["status"], "unchanged")
        self.assertEqual(memory.load_memories(7)[0]["version"], 2)

    def test_duplicate_subject_can_only_change_a_consulted_memory(self):
        self.save()
        row = memory.load_memories(7)[0]
        correction = self.proposal(content="O time do Alestan é Carla.", evidence="Carla")
        with self.assertRaises(ValueError):
            self.save(proposals=[correction], message="Agora é Carla.")
        self.assertEqual(memory.load_memories(7)[0]["version"], 1)
        result = self.save(proposals=[correction], message="Agora é Carla.",
                           context=self.context(row))[0]
        self.assertEqual(result["status"], "updated")

    def test_stale_version_and_unknown_or_other_user_ids_never_overwrite(self):
        self.save()
        row = memory.load_memories(7)[0]
        for context in (self.context(row, memory_version=2),
                        self.context(row, memory_version=True), self.context(row, type="person"), {}):
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.save(proposals=[self.proposal(memory_id=row["id"], content="Nova composição")],
                          context=context)
        with self.assertRaises(ValueError):
            self.save(user_id=9, proposals=[self.proposal(memory_id=row["id"])], context=self.context(row))
        unknown = {"id": 999, "version": 1}
        with self.assertRaises(ValueError):
            self.save(proposals=[self.proposal(memory_id=999)], context=self.context(unknown))
        self.assertEqual(memory.load_memories(7)[0]["version"], 1)

    def test_batch_is_atomic_when_a_later_proposal_conflicts(self):
        self.save()
        self.assertEqual(len(memory.load_memories(7)), 1)
        with self.assertRaises(ValueError):
            self.save(proposals=[self.proposal(subject="Novo assunto", content="Ana e Bruno participam."),
                                 self.proposal(content="Informação alterada")])
        rows = memory.load_memories(7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "O time do Alestan é Ana e Bruno.")

    def test_invalid_proposals_are_rejected_before_opening_database(self):
        invalid = [None, {}, [None], [self.proposal(memory_id=True)],
                   [self.proposal(memory_id="1")], [self.proposal(memory_id=-1)],
                   [self.proposal(subject=" ")], [self.proposal(subject="x" * 161)],
                   [self.proposal(content="x" * 2401)], [self.proposal(evidence="x" * 2401)],
                   [self.proposal(evidence="Não está na mensagem")],
                   [self.proposal(memory_id=2), self.proposal(memory_id=2, subject="Outro assunto")],
                   [self.proposal(), self.proposal()], [self.proposal()] * 4]
        self.factory.reset_mock()
        for proposals in invalid:
            with self.subTest(proposals=proposals), self.assertRaises(ValueError):
                memory.save_memories(7, proposals, "O time do Alestan é Ana e Bruno.", [], {})
        self.factory.assert_not_called()

    def test_invalid_identifiers_and_oversized_messages_never_access_database(self):
        self.factory.reset_mock()
        for value in (None, True, "7", 0, -1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    memory.load_memories(value)
                with self.assertRaises(ValueError):
                    memory.save_memories(value, [], "Olá", [], {})
                with self.assertRaises(ValueError):
                    memory.delete_memory(7, value)
        for message in (None, " ", "x" * 6001):
            with self.subTest(message_type=type(message).__name__), self.assertRaises(ValueError):
                memory.save_memories(7, [self.proposal()], message, [], {})
        self.factory.assert_not_called()

    def test_no_proposals_has_no_database_work_and_evidence_normalizes_whitespace(self):
        self.factory.reset_mock()
        self.assertEqual(memory.save_memories(7, [], "Olá", [], {}), [])
        self.factory.assert_not_called()
        self.save(message="O time tem Ana\n e    Bruno.")
        self.assertEqual(memory.load_memories(7)[0]["evidence"], "Ana e Bruno")

    def test_subject_rename_cannot_overwrite_another_memory(self):
        self.save()
        self.save(proposals=[self.proposal(subject="Outras equipes")])
        row = next(row for row in memory.load_memories(7) if row["subject"] == "Equipe do Alestan")
        with self.assertRaises(ValueError):
            self.save(proposals=[self.proposal(memory_id=row["id"], subject="Outras equipes",
                                               content="Ana lidera Bruno")], context=self.context(row))
        self.assertEqual(len(memory.load_memories(7)), 2)


if __name__ == "__main__":
    unittest.main()
