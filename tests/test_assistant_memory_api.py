"""Memórias no contrato HTTP do Assistente, sem serviços externos."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from flask import Flask

from cortex.views import assistant


def context(**coverage):
    return {"people": [], "sources": [], "warnings": [],
            "coverage": {"memory": {"status": "available", "count": 0}, **coverage}}


def reply(**values):
    return {"answer": "Obrigado por informar a composição do time.", "blocks": [],
            "source_ids": [], "actions": [], "suggested_prompts": [],
            "memory_updates": [], **values}


def proposal(**values):
    return {"memory_id": None, "subject": "Equipe do Alestan",
            "content": "O time do Alestan é formado por Ana e Bruno.",
            "evidence": "o time dele é Ana e Bruno", **values}


class AssistantMemoryApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="assistant-memory-test")
        self.app.register_blueprint(assistant.bp)
        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state["user_id"] = 7

    def test_memory_endpoints_require_authentication_before_read_or_delete(self):
        client = self.app.test_client()
        with patch.object(assistant, "load_memories") as load, \
                patch.object(assistant, "delete_memory") as delete:
            self.assertEqual(client.get("/api/assistant/memories").status_code, 401)
            self.assertEqual(client.delete("/api/assistant/memories/42", json={}).status_code, 401)
        load.assert_not_called()
        delete.assert_not_called()

    def test_memory_listing_is_scoped_and_omits_original_conversation_metadata(self):
        changed_at = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        row = {"id": 42, "subject": "Equipe do Alestan", "content": "Ana e Bruno",
               "updated_at": changed_at, "user_id": 7, "version": 2,
               "evidence": "conteúdo original", "source_message": "mensagem privada",
               "conversation_context": "contexto privado", "subject_key": "chave interna"}
        with patch.object(assistant, "load_memories", return_value=[row]) as load:
            response = self.client.get("/api/assistant/memories")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"memories": [{
            "id": 42, "subject": "Equipe do Alestan", "content": "Ana e Bruno",
            "updated_at": str(changed_at)}]})
        load.assert_called_once_with(7)

    def test_question_without_memory_updates_does_not_open_a_write_transaction(self):
        with patch.object(assistant, "gather_context", return_value=context()), \
                patch.object(assistant, "generate_assistant_reply", return_value=reply(
                    answer="Não há evidência suficiente para identificar o time.")), \
                patch.object(assistant, "save_memories") as save:
            response = self.client.post("/api/assistant/chat", json={
                "message": "Quem faz parte do time do Alestan?"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["memory_changes"], [])
        self.assertNotIn("memory_proposals", response.json)
        save.assert_not_called()

    def test_followup_passes_current_statement_and_clean_history_to_persistence(self):
        evidence = "o time dele é Ana e Bruno"
        history = [{"role": "user", "content": "Quem faz parte do time do Alestan?"},
                   {"role": "assistant", "content": "Não há fontes suficientes."}]
        current_context = context()
        updates = [proposal()]
        changes = [{"id": 42, "subject": "Equipe do Alestan",
                    "content": updates[0]["content"], "status": "saved"}]
        with patch.object(assistant, "gather_context", return_value=current_context), \
                patch.object(assistant, "generate_assistant_reply", return_value=reply(
                    memory_updates=updates)), \
                patch.object(assistant, "save_memories", return_value=changes) as save:
            response = self.client.post("/api/assistant/chat", json={
                "message": "  " + evidence + "  ", "history": history})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["memory_changes"], changes)
        self.assertEqual(response.json["warnings"], [])
        save.assert_called_once_with(7, updates, evidence, history, current_context)
        self.assertNotIn("memory_proposals", response.json)

    def test_correction_returns_only_the_persisted_updated_status(self):
        updates = [proposal(memory_id=42, content="O time do Alestan é formado por Ana e Carla.",
                            evidence="Bruno saiu e Carla entrou no time do Alestan")]
        current_context = context()
        current_context["sources"] = [{"id": "memory:42", "type": "memory", "memory_version": 1,
                                        "title": "Equipe do Alestan", "excerpt": "Ana e Bruno"}]
        changes = [{"id": 42, "subject": "Equipe do Alestan",
                    "content": updates[0]["content"], "status": "updated"}]
        with patch.object(assistant, "gather_context", return_value=current_context), \
                patch.object(assistant, "generate_assistant_reply", return_value=reply(
                    memory_updates=updates)), \
                patch.object(assistant, "save_memories", return_value=changes):
            response = self.client.post("/api/assistant/chat", json={
                "message": updates[0]["evidence"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["memory_changes"], changes)

    def test_failed_or_invalid_updates_never_report_a_memory_saved(self):
        updates = [proposal(evidence="afirmação ausente da mensagem atual")]
        for failure in (ValueError("evidência inválida"), RuntimeError("secret-db-password")):
            with self.subTest(failure=type(failure).__name__), \
                    patch.object(assistant, "gather_context", return_value=context()), \
                    patch.object(assistant, "generate_assistant_reply", return_value=reply(
                        memory_updates=updates)), \
                    patch.object(assistant, "save_memories", side_effect=failure):
                response = self.client.post("/api/assistant/chat", json={
                    "message": "O time do Alestan é formado por Ana."})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["memory_changes"], [])
            self.assertTrue(any("não foi salva" in warning for warning in response.json["warnings"]))
            self.assertNotIn("secret", response.get_data(as_text=True))
            self.assertNotIn("evidência inválida", response.get_data(as_text=True))

    def test_unavailable_memory_context_prevents_writes_and_keeps_answer_usable(self):
        with patch.object(assistant, "gather_context", return_value=context(
                memory={"status": "unavailable", "count": 0})), \
                patch.object(assistant, "generate_assistant_reply", return_value=reply(
                    memory_updates=[proposal()])), \
                patch.object(assistant, "save_memories") as save:
            response = self.client.post("/api/assistant/chat", json={
                "message": "o time dele é Ana e Bruno"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["memory_changes"], [])
        self.assertTrue(response.json["answer"])
        self.assertTrue(response.json["warnings"])
        save.assert_not_called()

    def test_forget_is_scoped_and_missing_memory_does_not_report_success(self):
        for found, status in ((True, 200), (False, 404)):
            with self.subTest(found=found), \
                    patch.object(assistant, "delete_memory", return_value=found) as delete:
                response = self.client.delete("/api/assistant/memories/42", json={})
            self.assertEqual(response.status_code, status)
            delete.assert_called_once_with(7, 42)
            if found:
                self.assertEqual(response.json, {"message": "Memória removida."})
            else:
                self.assertNotIn("message", response.json)

    def test_forget_rejects_cross_origin_or_invalid_json_before_deleting(self):
        with patch.object(assistant, "delete_memory") as delete:
            cross_origin = self.client.delete("/api/assistant/memories/42", json={},
                                              headers={"Origin": "https://other.example"})
            malformed = self.client.delete("/api/assistant/memories/42", json=[])
        self.assertEqual(cross_origin.status_code, 400)
        self.assertEqual(malformed.status_code, 400)
        delete.assert_not_called()

    def test_memory_database_failures_do_not_expose_database_details(self):
        with patch.object(assistant, "load_memories", side_effect=RuntimeError("private-db-detail")):
            listing = self.client.get("/api/assistant/memories")
        with patch.object(assistant, "delete_memory", side_effect=RuntimeError("private-db-detail")):
            deleting = self.client.delete("/api/assistant/memories/42", json={})
        for response in (listing, deleting):
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private-db-detail", response.get_data(as_text=True))
            self.assertNotIn("message", response.json)


if __name__ == "__main__":
    unittest.main()
