"""Evidence retrieval checks with an isolated SQLite history and mocked Gmail."""
import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cortex import assistant_context as context, assistant_gmail
from cortex.meeting_insights import source_fingerprint


def response(body, status=200):
    result = Mock(status_code=status)
    result.json.return_value = body
    return result


class AssistantContextTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.sessions = sessionmaker(bind=self.engine)
        ddl = [
            """CREATE TABLE people (id INTEGER PRIMARY KEY, full_name TEXT, preferred_name TEXT,
               email TEXT, role_title TEXT, notes TEXT, active BOOLEAN, transcript_aliases TEXT)""",
            """CREATE TABLE one_on_ones (id INTEGER PRIMARY KEY, person_id INTEGER, occurred_on TEXT,
               title TEXT, transcript_raw TEXT, summary_ai TEXT, private_notes TEXT, public_notes TEXT,
               topics TEXT, extraction_json TEXT)""",
            """CREATE TABLE commitments (id INTEGER PRIMARY KEY, person_id INTEGER, one_on_one_id INTEGER,
               description TEXT, owner TEXT, due_date TEXT, status TEXT, closed_at TEXT, created_at TEXT)""",
            """CREATE TABLE checkpoints (id INTEGER PRIMARY KEY, person_id INTEGER, checkpoint_date TEXT,
               period_start TEXT, period_end TEXT, actions_json TEXT, private_notes TEXT,
               public_notes TEXT, raw_input TEXT)""",
            """CREATE TABLE meetings (id INTEGER PRIMARY KEY, title TEXT, forum TEXT, occurred_on TEXT,
               external_participants TEXT, transcript_raw TEXT, notes TEXT, summary_ai TEXT,
               topics TEXT, extraction_json TEXT)""",
            """CREATE TABLE meeting_participants (meeting_id INTEGER, person_id INTEGER,
               PRIMARY KEY (meeting_id, person_id))""",
            """CREATE TABLE meeting_actions (id INTEGER PRIMARY KEY, meeting_id INTEGER, person_id INTEGER,
               description TEXT, owner_name TEXT, due_date TEXT, status TEXT, closed_at TEXT,
               created_at TEXT)""",
        ]
        with self.engine.begin() as db:
            for statement in ddl:
                db.execute(text(statement))
        self.addCleanup(self.engine.dispose)
        self.factory = patch.object(context, "session_factory", side_effect=self.sessions).start()
        self.addCleanup(patch.stopall)
        self.mail = patch.object(context, "search_gmail", return_value={
            "sources": [], "coverage": {"status": "not_connected", "partial": False}, "warnings": [],
        }).start()
        self.person(1, "André Pasold", "Pasold", ["Andre P", "ANDREH"])
        self.person(2, "Ana Maria", "Ana", [])

    def insert(self, table, values):
        columns = ", ".join(values)
        placeholders = ", ".join(":" + key for key in values)
        with self.engine.begin() as db:
            db.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"), values)

    def person(self, person_id, name, preferred, aliases, notes=""):
        self.insert("people", {"id": person_id, "full_name": name, "preferred_name": preferred,
                              "email": f"person{person_id}@example.com", "role_title": "Liderança",
                              "notes": notes, "active": True, "transcript_aliases": json.dumps(aliases)})

    def meeting(self, record_id, person_id, date, transcript, **extra):
        self.insert("one_on_ones", {"id": record_id, "person_id": person_id,
                                   "occurred_on": date, "transcript_raw": transcript, **extra})

    def forum_meeting(self, record_id, participant_ids, **extra):
        values = {"id": record_id, "title": "Fórum de produto", "forum": "Planejamento",
                  "occurred_on": "2018-01-10", "transcript_raw": "Discussão sobre capacidade.", **extra}
        self.insert("meetings", values)
        for person_id in participant_ids:
            self.insert("meeting_participants", {"meeting_id": record_id, "person_id": person_id})
        return values

    def test_group_history_uses_declared_participant_alias_without_duplicating_meeting(self):
        transcript = "Tema de rotina. " * 5000 + "LYNN Studio precisa ampliar autonomia."
        self.forum_meeting(1, [1, 2], transcript_raw=transcript)
        for record_id in range(2, 13):
            self.forum_meeting(record_id, [2], occurred_on="2026-10-20")
        result = context.gather_context(7, "Quais são os desafios de ANDREH no LYNN Studio?")
        group_sources = [s for s in result["sources"] if s["id"] == "meeting:1"]
        self.assertEqual(len(group_sources), 1)
        self.assertIn("ampliar autonomia", group_sources[0]["excerpt"])
        self.assertEqual(group_sources[0]["participant_ids"], [1, 2])
        self.assertEqual(group_sources[0]["meeting_id"], 1)
        self.assertEqual(result["coverage"]["database"]["scanned_by_type"]["meeting"], 12)
        # An unrelated alias-only query still finds participation, with no name in the transcript.
        result = context.gather_context(7, "ANDREH")
        self.assertIn("meeting:1", {s["id"] for s in result["sources"]})

    def test_assigned_group_actions_retrieve_all_statuses_and_meeting_without_attendance(self):
        self.forum_meeting(1, [2])
        for record_id, status in enumerate(("open", "done", "cancelled"), 1):
            self.insert("meeting_actions", {"id": record_id, "meeting_id": 1, "person_id": 1,
                                           "description": "Revisar capacidade", "status": status})
        result = context.gather_context(7, "ANDREH")
        sources = {s["id"]: s for s in result["sources"]}
        self.assertIn("meeting:1", sources)
        self.assertEqual(sources["meeting:1"]["participant_ids"], [2])
        labels = {"open": "em aberto", "done": "concluído", "cancelled": "cancelado"}
        for record_id, status in enumerate(("open", "done", "cancelled"), 1):
            source = sources[f"meeting_action:{record_id}"]
            self.assertIn(f"Status: {labels[status]}", source["excerpt"])
            self.assertEqual(source["person_id"], 1)

    def test_group_excerpts_keep_readable_evidence_without_internal_analysis_metadata(self):
        meeting = self.forum_meeting(1, [1, 2], notes="Comparar as alternativas.",
                                     transcript_raw="ANDREH propôs revisar dependências.",
                                     external_participants="Consultoria de produto", summary_ai="Resumo do fórum.",
                                     topics=json.dumps(["Qualidade"]))
        fingerprint = source_fingerprint(meeting, [{"id": 1, "full_name": "André Pasold"},
                                                  {"id": 2, "full_name": "Ana Maria"}])
        extraction = {"summary": "Resumo do fórum.", "topics": ["Autonomia"],
                      "decisions": ["Revisar dependências de plataforma"],
                      "risks": ["Capacidade limitada"], "opportunities": ["Reduzir retrabalho"],
                      "strategic_themes": ["Evolução do LYNN Studio"],
                      "open_questions": ["Qual a capacidade disponível?"],
                      "source_fingerprint": fingerprint, "internal_secret": "do-not-expose",
                      "actions": [{"description": "Mapear dependências", "owner_name": "André Pasold",
                                   "person_id": 1, "due_date": "2026-11-01", "source_key": "secret-key"}]}
        with self.engine.begin() as db:
            db.execute(text("UPDATE meetings SET extraction_json = :extraction WHERE id = 1"),
                       {"extraction": json.dumps(extraction)})
        self.insert("meeting_actions", {"id": 1, "meeting_id": 1, "person_id": 1,
                                       "description": "Validar prioridades", "status": "open", "due_date": "2026-11-01"})
        result = context.gather_context(7, "ANDREH")
        sources = {s["id"]: s for s in result["sources"]}
        excerpt = sources["meeting:1"]["excerpt"]
        for expected in ("Participantes: André Pasold; Ana Maria", "Fórum: Planejamento",
                         "Convidados: Consultoria de produto", "Anotações:", "Transcrição:",
                         "André Pasold propôs", "Resumo:", "- Qualidade", "- Autonomia",
                         "Decisões:\n- Revisar dependências de plataforma", "- Capacidade limitada",
                         "- Reduzir retrabalho", "- Evolução do LYNN Studio",
                         "- Qual a capacidade disponível?", "Mapear dependências",
                         "Responsável: André Pasold; prazo: 2026-11-01"):
            self.assertIn(expected, excerpt)
        for internal in ("source_fingerprint", fingerprint, "source_key", "secret-key", "do-not-expose",
                         "transcript_raw", "extraction_json", "person_id", '"full_name"'):
            self.assertNotIn(internal, excerpt)
        action = sources["meeting_action:1"]
        self.assertIn("Encaminhamento: Validar prioridades", action["excerpt"])
        self.assertIn("Responsável: André Pasold", action["excerpt"])
        self.assertIn("Reunião: Fórum de produto", action["excerpt"])
        self.assertIn("Prazo: 2026-11-01", action["excerpt"])
        self.assertNotIn("person_id", action["excerpt"])
        self.assertNotIn("meeting_id", action["excerpt"])
        self.assertEqual(action["meeting_id"], 1)
        self.assertEqual(sources["meeting:1"]["participant_ids"], [1, 2])

    def test_group_source_edit_omits_stale_analysis_and_keeps_current_raw_evidence(self):
        meeting = {"id": 1, "title": "Fórum de produto", "forum": "Planejamento",
                   "occurred_on": "2018-01-10", "transcript_raw": "Hipótese superada."}
        extraction = {"summary": "Resumo inválido: unicórnios", "risks": ["unicórnios"],
                      "source_fingerprint": source_fingerprint(meeting, [{"id": 1, "full_name": "André Pasold"}])}
        self.forum_meeting(1, [1], transcript_raw="ANDREH definiu a revisão de qualidade.",
                           notes="Analisar capacidade atual.", summary_ai="unicórnios",
                           topics=json.dumps(["unicórnios"]), extraction_json=json.dumps(extraction))
        result = context.gather_context(7, "ANDREH")
        source = next(s for s in result["sources"] if s["id"] == "meeting:1")
        self.assertIn("André Pasold definiu", source["excerpt"])
        self.assertIn("capacidade atual", source["excerpt"])
        self.assertIn("desatualizada omitida", source["excerpt"])
        self.assertNotIn("unicórnios", source["excerpt"])
        result = context.gather_context(7, "unicórnios")
        self.assertNotIn("meeting:1", {s["id"] for s in result["sources"]})

    def test_group_rich_followup_uses_real_sources_and_attendee_scope(self):
        self.forum_meeting(1, [1, 2])
        self.insert("meeting_actions", {"id": 1, "meeting_id": 1, "person_id": 1,
                                       "description": "Revisar capacidade", "status": "open"})
        history = [{"role": "user", "content": "Qual o plano de capacidade do André Pasold?"},
                   {"role": "assistant", "content": json.dumps({
                       "source_ids": ["meeting:1", "meeting_action:1", "meeting_action:99999"]})}]
        result = context.gather_context(7, "Detalhe o primeiro item da tabela do André Pasold", history)
        # A named person narrows follow-ups only when represented in the sources.
        self.assertEqual(result["coverage"]["conversation_source_hints"], ["meeting:1", "meeting_action:1"])
        result = context.gather_context(7, "Detalhe o primeiro item da tabela", history)
        self.assertEqual(result["coverage"]["conversation_source_hints"], ["meeting:1", "meeting_action:1"])

    def test_old_long_transcript_is_searched_beyond_prefix_and_recent_records(self):
        long_text = ("Conversa de rotina sobre entregas. " * 5000
                     + "No Alestan, a estrutura é: Beatriz lidera Caio, que lidera Diana. "
                     + "Demais assuntos sem relação. " * 1000)
        self.meeting(1, 1, "2018-01-10", long_text)
        for record_id in range(2, 32):
            self.meeting(record_id, 2, "2026-10-20", "Planejamento e entregas da semana.")
        result = context.gather_context(7, "Como está a estrutura do Alestan?")
        evidence = next(source for source in result["sources"] if source["id"] == "one_on_one:1")
        self.assertIn("Beatriz lidera Caio", evidence["excerpt"])
        self.assertEqual(evidence["date"], "2018-01-10")
        self.assertTrue(evidence["excerpt_partial"])
        self.assertLess(len(evidence["excerpt"]), 3800)
        coverage = result["coverage"]["database"]
        self.assertEqual(coverage["scanned_by_type"]["one_on_one"], 31)
        self.assertIsNone(coverage["date_filter"])
        self.assertEqual(coverage["date_range"]["from"], "2018-01-10")
        self.assertTrue(coverage["partial"])
        self.assertEqual(self.mail.call_args.args[0], 7)

    def test_diacritics_configured_alias_and_unique_name_retrieve_all_record_types(self):
        self.meeting(1, 1, "2020-02-01", "Maturidade de gestão foi a preocupação registrada.")
        self.insert("commitments", {"id": 1, "person_id": 1, "description": "Criar plano de autonomia",
                                     "owner": "person", "status": "open", "created_at": "2020-02-01"})
        self.insert("checkpoints", {"id": 1, "person_id": 1, "checkpoint_date": "2020-02-02",
                                    "raw_input": "Fortalecer a delegação no time."})
        for spelling in ("Andre Pasold", "André", "ANDREH", "Andre P"):
            with self.subTest(spelling=spelling):
                result = context.gather_context(7, f"Quais são os pontos de atenção de {spelling}?")
                self.assertEqual(result["coverage"]["matched_person_ids"], [1])
                self.assertTrue({"person", "one_on_one", "commitment", "checkpoint"}.issubset(
                    {source["type"] for source in result["sources"]}))
                self.assertEqual(self.mail.call_args.args[1][0]["email"], "person1@example.com")

    def test_followup_uses_user_history_but_explicit_new_topic_does_not(self):
        self.meeting(1, 1, "2020-02-01", "Necessidade de delegação.")
        history = [{"role": "user", "content": "O que preocupou o André Pasold?"},
                   {"role": "assistant", "content": "Ana Maria também aparece no histórico."}]
        result = context.gather_context(7, "E quais foram os combinados dele?", history)
        self.assertEqual(result["coverage"]["matched_person_ids"], [1])
        result = context.gather_context(7, "Como está a estrutura do Alestan?", history)
        self.assertEqual(result["coverage"]["matched_person_ids"], [])

    def test_alias_mentioned_in_another_persons_transcript_is_searchable_without_changing_original(self):
        self.meeting(1, 2, "2020-02-01", "ANDREH comentou que precisamos ampliar a autonomia.")
        result = context.gather_context(7, "Quais alertas André Pasold levantou?")
        source = next(s for s in result["sources"] if s["id"] == "one_on_one:1")
        self.assertIn("André Pasold comentou", source["excerpt"])
        self.assertIn("original preservado", source["excerpt"])
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("SELECT transcript_raw FROM one_on_ones WHERE id = 1")).scalar(),
                             "ANDREH comentou que precisamos ampliar a autonomia.")

    def test_shared_first_name_is_explicitly_ambiguous_but_full_name_disambiguates(self):
        self.person(3, "André Ramos", "André", [])
        result = context.gather_context(7, "Quais os pontos de atenção do André?")
        self.assertEqual(result["coverage"]["ambiguous_person_ids"], [1, 3])
        self.assertTrue(any("nome completo" in warning for warning in result["warnings"]))
        result = context.gather_context(7, "Quais os pontos de atenção do André Pasold?")
        self.assertEqual(result["coverage"]["matched_person_ids"], [1])
        self.assertEqual(result["coverage"]["ambiguous_person_ids"], [])

    def test_person_scoped_mail_does_not_require_the_person_name_in_body(self):
        self.mail.return_value = {"sources": [{"id": "email:m1", "type": "email", "title": "Plano",
            "excerpt": "De: person1@example.com\nAutonomia é o próximo passo.", "date": "2020-02-01",
            "person_id": None, "record_id": "m1"}], "coverage": {"status": "connected"}, "warnings": []}
        result = context.gather_context(7, "Quais os pontos de atenção do André Pasold?")
        self.assertIn("email:m1", {source["id"] for source in result["sources"]})
        self.assertEqual(result["coverage"]["gmail"]["selected_message_count"], 1)

    def test_table_followup_reloads_current_commitment_state_from_known_source_ids(self):
        self.insert("commitments", {"id": 1, "person_id": 1, "description": "Revisar o plano de autonomia",
                                     "owner": "person", "status": "done", "created_at": "2020-02-01"})
        history = [{"role": "user", "content": "Quais são os combinados abertos do time?"},
                   {"role": "assistant", "content": json.dumps({
                       "answer": "Há um combinado aberto.",
                       "blocks": [{"type": "table", "rows": [["Revisar plano", "aberto"]],
                                   "source_ids": ["commitment:1", "commitment:999999"]}],
                   })}]
        result = context.gather_context(7, "Conclua o primeiro da tabela", history)
        source = next(s for s in result["sources"] if s["id"] == "commitment:1")
        self.assertIn("status: done", source["excerpt"])
        self.assertNotIn("combinado aberto", source["excerpt"])
        self.assertEqual(result["coverage"]["conversation_source_hints"], ["commitment:1"])
        self.assertIn(1, {person["id"] for person in result["people"]})

    def test_followup_source_hints_only_use_latest_assistant_and_never_user_ids(self):
        for record_id in (1, 2, 3):
            self.insert("commitments", {"id": record_id, "person_id": 1,
                                         "description": f"Plano {record_id}", "status": "open"})
        history = [{"role": "assistant", "content": '{"source_ids":["commitment:1"]}'},
                   {"role": "user", "content": "commitment:2"},
                   {"role": "assistant", "content": '{"source_ids":["commitment:3"]}'}]
        result = context.gather_context(7, "Conclua o primeiro da tabela", history)
        self.assertEqual(result["coverage"]["conversation_source_hints"], ["commitment:3"])
        self.assertEqual({source["id"] for source in result["sources"]}, {"commitment:3"})
        result = context.gather_context(7, "Marque como feito", history)
        self.assertEqual(result["coverage"]["conversation_source_hints"], ["commitment:3"])

    def test_new_topic_or_new_named_person_does_not_reuse_prior_table_sources(self):
        self.insert("commitments", {"id": 1, "person_id": 1, "description": "Plano de autonomia", "status": "open"})
        history = [{"role": "user", "content": "Quais os combinados do André?"},
                   {"role": "assistant", "content": '{"source_ids":["commitment:1"]}'}]
        for message in ("Como está a estrutura do Alestan?", "Crie um organograma do Alestan", "Conclua o primeiro da tabela da Ana"):
            with self.subTest(message=message):
                result = context.gather_context(7, message, history)
                self.assertEqual(result["coverage"]["conversation_source_hints"], [])

    def test_followup_hints_are_bounded_and_reject_unknown_or_malformed_ids(self):
        for record_id in range(1, 76):
            self.insert("commitments", {"id": record_id, "person_id": 1, "description": "Plano", "status": "open"})
        ids = ["commitment:99999", "person:-1", "commitment:1oops"] + [f"commitment:{n}" for n in range(1, 76)]
        history = [{"role": "assistant", "content": json.dumps({"source_ids": ids})}]
        result = context.gather_context(7, "Conclua o primeiro da tabela", history)
        self.assertEqual(len(result["coverage"]["conversation_source_hints"]), 70)
        self.assertEqual(result["coverage"]["conversation_source_hints"][0], "commitment:1")

    def test_notes_extraction_and_checkpoint_json_are_searchable(self):
        self.person(3, "Bruno", None, [], notes="A divisão Alestan responde à diretoria de produto.")
        self.meeting(1, 1, "2019-02-01", "", extraction_json=json.dumps({"risks": ["LYNN Studio precisa de foco"]}))
        self.insert("checkpoints", {"id": 1, "person_id": 2, "checkpoint_date": "2022-02-02",
                                    "actions_json": json.dumps([{"description": "LYNN Studio: reduzir retrabalho"}])})
        result = context.gather_context(7, "LYNN Studio")
        self.assertEqual({s["type"] for s in result["sources"]}, {"one_on_one", "checkpoint"})
        result = context.gather_context(7, "Como está a estrutura do Alestan?")
        self.assertIn("diretoria de produto", result["sources"][0]["excerpt"])

    def test_oversized_result_reports_selection_and_balances_old_evidence(self):
        self.meeting(1, 1, "2018-01-01", "Evolução do LYNN Studio: autonomia.")
        for record_id in range(2, 102):
            self.meeting(record_id, 2, "2026-10-20", "Evolução do LYNN Studio: qualidade.")
        result = context.gather_context(7, "Desafios de evolução do LYNN Studio")
        ids = {source["id"] for source in result["sources"]}
        self.assertIn("one_on_one:1", ids)
        self.assertLess(len(result["sources"]), 101)
        coverage = result["coverage"]["database"]
        self.assertEqual(coverage["matching_by_type"]["one_on_one"], 101)
        self.assertTrue(coverage["partial"])
        self.assertLessEqual(coverage["context_chars"], coverage["context_char_budget"])
        self.assertTrue(any("seleção" in warning for warning in result["warnings"]))

    def test_gmail_failure_keeps_database_evidence_and_omits_error_secret(self):
        self.meeting(1, 1, "2026-10-20", "LYNN Studio precisa fortalecer autonomia.")
        self.mail.side_effect = RuntimeError("access_token=SUPERSECRET")
        result = context.gather_context(7, "LYNN Studio")
        self.assertEqual(result["sources"][0]["id"], "one_on_one:1")
        self.assertEqual(result["coverage"]["gmail"]["status"], "unavailable")
        self.assertNotIn("SUPERSECRET", json.dumps(result))

    def test_no_chat_content_is_interpolated_into_sql(self):
        result = context.gather_context(7, "'; DROP TABLE people; --")
        self.assertIsInstance(result["sources"], list)
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM people")).scalar(), 2)


class AssistantGmailTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.db.execute.return_value.fetchone.return_value = (
            "private-token", "private-refresh", datetime.now(timezone.utc) + timedelta(hours=1), "connected")
        self.factory = patch.object(assistant_gmail, "session_factory", return_value=self.db).start()
        self.addCleanup(patch.stopall)

    def message(self, message_id, body):
        return {"id": message_id, "internalDate": "1514764800000", "payload": {
            "mimeType": "text/plain", "headers": [{"name": "Subject", "value": "Estrutura Alestan"}],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}

    def test_user_scoped_connection_closed_before_network_and_multiple_old_messages_read(self):
        responses = iter([
            response({"threads": [{"id": "thread123"}]}),
            response({"messages": [self.message("first", "Beatriz lidera Caio."),
                                   self.message("reply", "Diana também integra o time.")]}),
        ])

        def get(*args, **kwargs):
            self.db.close.assert_called_once_with()
            self.assertIn("deadline", kwargs)
            return next(responses)

        with patch.object(assistant_gmail, "_auth_get", side_effect=get) as request:
            result = assistant_gmail.search_gmail(39, [{"email": "andre@example.com"}], ["estrutura"])
        statement, parameters = self.db.execute.call_args.args
        self.assertIn("WHERE user_id = :uid", str(statement))
        self.assertEqual(parameters, {"uid": 39})
        query = request.call_args_list[0].args[2]["q"]
        self.assertIn("from:andre@example.com", query)
        self.assertNotIn("newer_than", query)
        self.assertEqual(len(result["sources"]), 2)
        self.assertTrue(result["sources"][0]["date"].startswith("2018-01-01"))
        self.assertEqual(result["sources"][0]["thread_id"], "thread123")
        self.assertEqual(result["sources"][0]["url"], "https://mail.google.com/mail/u/0/#all/thread123")
        self.assertNotIn("private-token", json.dumps(result))

    def test_not_connected_never_issues_requests(self):
        self.db.execute.return_value.fetchone.return_value = None
        with patch.object(assistant_gmail, "_auth_get") as request:
            result = assistant_gmail.search_gmail(39, [], ["alestan"])
        request.assert_not_called()
        self.assertEqual(result["coverage"]["status"], "not_connected")
        self.db.close.assert_called_once_with()

    def test_timeout_preserves_partial_evidence_and_has_one_shared_deadline(self):
        responses = [response({"threads": [{"id": "one"}, {"id": "two"}]}),
                     response({"messages": [self.message("m1", "Evidência antiga útil.")]}),
                     requests.Timeout("private-token")]
        with patch.object(assistant_gmail, "_auth_get", side_effect=responses) as request:
            result = assistant_gmail.search_gmail(39, [], ["alestan"])
        self.assertEqual(result["coverage"]["status"], "partial")
        self.assertTrue(result["coverage"]["partial"])
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(len({call.kwargs["deadline"] for call in request.call_args_list}), 1)
        self.assertNotIn("private-token", json.dumps(result))

    def test_pagination_is_reported_and_no_arbitrary_date_filter_is_used(self):
        with patch.object(assistant_gmail, "_auth_get", return_value=response({
            "threads": [], "nextPageToken": "more", "resultSizeEstimate": 200,
        })) as request:
            result = assistant_gmail.search_gmail(39, [], ["estrutura", "alestan"])
        self.assertEqual(request.call_args.args[2]["q"], "{alestan}")
        self.assertTrue(result["coverage"]["partial"])
        self.assertIsNone(result["coverage"]["date_filter"])
        self.assertTrue(result["warnings"])


if __name__ == "__main__":
    unittest.main()
