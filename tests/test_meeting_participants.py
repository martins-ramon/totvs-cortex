import unittest

from cortex.meeting_participants import detect_meeting_participants


class MeetingParticipantDetectionTests(unittest.TestCase):
    def setUp(self):
        self.people = [
            {'id': 1, 'full_name': 'Ana Costa', 'preferred_name': 'Aninha',
             'transcript_aliases': ['Anna Costa']},
            {'id': 2, 'full_name': 'Bruno Lima'},
            {'id': 3, 'full_name': 'André Pasold', 'transcript_aliases': ['Pasolt']},
        ]

    def detect(self, transcript='', notes='', people=None):
        return detect_meeting_participants(transcript, notes, self.people if people is None else people)

    def test_speaker_labels_tolerate_accents_case_and_horizontal_space(self):
        result = self.detect('ANA\tCOSTA: Bom dia.\nAndRE\u0301\u00a0  Pasold\u00a0(12:03): Vamos começar.')
        self.assertEqual(result, {'participant_ids': [1, 3], 'ambiguous_names': []})

    def test_timestamp_prefix_and_bracketed_timestamp(self):
        self.assertEqual(self.detect('[00:12] Bruno Lima: Concordo.\nAna Costa [00:13]: Certo.')['participant_ids'], [1, 2])

    def test_meet_standalone_name_then_timestamp(self):
        self.assertEqual(self.detect('Aninha\n12:03\nBom dia.\nAndré Pasold\n00:01:25\nConcordo.')['participant_ids'], [1, 3])

    def test_standalone_full_name_is_a_roster_or_speaker(self):
        self.assertEqual(self.detect('Bruno Lima\nVamos começar.')['participant_ids'], [2])

    def test_attendance_list(self):
        self.assertEqual(self.detect(notes='Participantes: Ana Costa, Bruno Lima e André Pasold.')['participant_ids'], [1, 2, 3])

    def test_multiline_attendance_list(self):
        text = 'Presentes:\n- Aninha\n- Bruno\nPauta: precisamos conversar com André Pasold.'
        self.assertEqual(self.detect(notes=text)['participant_ids'], [1, 2])

    def test_roster_skips_external_names_before_and_between_known_people(self):
        for text in ('Participantes: Carla Externa, Ana Costa, Bruno Lima.',
                     'Presentes: Ana Costa; Carla Externa; Bruno Lima.',
                     'Reunião com Carla Externa, Ana Costa e Bruno Lima.'):
            with self.subTest(text=text):
                self.assertEqual(self.detect(notes=text)['participant_ids'], [1, 2])

    def test_roster_skips_external_names_on_separate_lines(self):
        text = 'Participantes:\n- Carla Externa\n- Aninha\n- Daniel Convidado\n- Bruno'
        self.assertEqual(self.detect(notes=text)['participant_ids'], [1, 2])

    def test_roster_stops_at_prose_after_an_external_name(self):
        text = 'Participantes: Carla Externa, Ana Costa, Bruno Lima sobre o plano de André Pasold.'
        self.assertEqual(self.detect(notes=text)['participant_ids'], [1, 2])
        text = 'Presentes:\nCarla Externa\nPauta: falar com Bruno Lima.'
        self.assertEqual(self.detect(notes=text)['participant_ids'], [])

    def test_meeting_conversation_and_participation_cues(self):
        for cue in ('Reunião com', 'Conversa com', 'Encontro com', 'Participaram', 'Participaram da reunião'):
            with self.subTest(cue=cue):
                self.assertEqual(self.detect(notes=f'{cue} Ana Costa e Bruno Lima sobre o plano de André Pasold.')['participant_ids'], [1, 2])

    def test_direct_speech_or_participation_in_notes(self):
        result = self.detect(notes='Ana Costa comentou sobre André Pasold. Bruno Lima apresentou o plano.')
        self.assertEqual(result['participant_ids'], [1, 2])
        self.assertEqual(self.detect(notes='André Pasold esteve presente na conversa.')['participant_ids'], [3])

    def test_bare_mentions_do_not_prove_presence(self):
        result = self.detect('Precisamos falar com Bruno amanhã. Ana Costa foi citada.',
                             'O plano de André Pasold será avaliado.')
        self.assertEqual(result, {'participant_ids': [], 'ambiguous_names': []})

    def test_absent_names_do_not_become_participants(self):
        text = 'Participantes: Ana Costa, Bruno Lima (ausente), André Pasold.\nBruno Lima não participou.'
        self.assertEqual(self.detect(notes=text)['participant_ids'], [1, 3])

    def test_absence_label_is_not_a_speaker(self):
        self.assertEqual(self.detect(notes='Bruno Lima: ausente\nAna Costa: não participou.')['participant_ids'], [])

    def test_reported_speech_does_not_add_mentioned_third_party(self):
        self.assertEqual(self.detect(notes='Ana Costa comentou que André Pasold disse que vai revisar.')['participant_ids'], [1])

    def test_aliases_and_compound_transcription_variants(self):
        self.assertEqual(self.detect('Anna Costa: Bom dia.\nAndré Pasolt: Concordo.')['participant_ids'], [1, 3])
        self.assertEqual(self.detect('Pasolt: Concordo.')['participant_ids'], [3])

    def test_unique_first_names_require_presence_cues(self):
        self.assertEqual(self.detect('Ana: Bom dia.\nBruno participou.\nPrecisamos chamar André.')['participant_ids'], [1, 2])
        self.assertEqual(self.detect('Ana\nPrecisamos chamar Bruno.')['participant_ids'], [])

    def test_shared_first_names_are_ambiguous(self):
        people = self.people + [{'id': 4, 'full_name': 'Ana Silva'}]
        self.assertEqual(self.detect('Ana: Bom dia.', people=people),
                         {'participant_ids': [], 'ambiguous_names': ['Ana']})

    def test_long_full_name_resolves_shared_first_name(self):
        people = self.people + [{'id': 4, 'full_name': 'Ana Silva'}]
        self.assertEqual(self.detect('Ana Costa: Bom dia.\nAna Silva: Olá.', people=people),
                         {'participant_ids': [1, 4], 'ambiguous_names': []})

    def test_shared_alias_and_duplicate_fullname_are_not_assigned(self):
        people = self.people + [{'id': 4, 'full_name': 'Outra Pessoa', 'preferred_name': 'Aninha'},
                                {'id': 5, 'full_name': 'Bruno Lima'}]
        self.assertEqual(self.detect('Aninha: Bom dia.\nBruno Lima: Olá.', people=people),
                         {'participant_ids': [], 'ambiguous_names': ['Aninha', 'Bruno Lima']})

    def test_substrings_and_broken_names_are_not_matches(self):
        result = self.detect('Anabela: Bom dia.\nBrunolima: Sim.\nAna\nCosta: Olá.\nAndré Pasoldes: Não.')
        self.assertEqual(result['participant_ids'], [])

    def test_email_and_url_tokens_are_not_names(self):
        result = self.detect(notes='Participantes: ana@example.com, https://example.com/Bruno, andre.com.\nAninha@example.com: contato.')
        self.assertEqual(result['participant_ids'], [])

    def test_source_is_unchanged_and_results_are_deduplicated_and_sorted(self):
        source = 'André Pasold: Olá.\nAna Costa: Olá.\nAndré Pasold: Bom dia.'
        original = source
        self.assertEqual(self.detect(source, 'Ana participou.')['participant_ids'], [1, 3])
        self.assertEqual(source, original)

    def test_unknown_people_are_not_created(self):
        self.assertEqual(self.detect('Pessoa Externa: Olá.')['participant_ids'], [])

    def test_empty_sources_and_people(self):
        self.assertEqual(self.detect(None, None), {'participant_ids': [], 'ambiguous_names': []})
        self.assertEqual(self.detect('Ana Costa: Olá.', people=[]), {'participant_ids': [], 'ambiguous_names': []})

    def test_json_aliases_from_portable_database(self):
        people = [{'id': 7, 'full_name': 'Pessoa Teste', 'transcript_aliases': '["Pessoinha"]'}]
        self.assertEqual(self.detect('Pessoinha: Bom dia.', people=people)['participant_ids'], [7])

    def test_very_long_unbroken_tokens_are_ignored(self):
        self.assertEqual(self.detect('a' * 200_000 + '.\nAna: Bom dia.')['participant_ids'], [1])


if __name__ == '__main__':
    unittest.main()
