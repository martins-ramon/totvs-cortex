"""Correções de nomes são previsíveis e preservam transcrições de origem."""
import unittest
from unittest.mock import Mock

from cortex.transcripts import (
    load_transcript_people,
    normalization_metadata,
    normalize_transcript,
    transcript_preview,
    validate_transcript_aliases,
)


def person(person_id, full_name, aliases=(), **extra):
    return {
        'id': person_id,
        'full_name': full_name,
        'preferred_name': None,
        'transcript_aliases': list(aliases),
        **extra,
    }


class NormalizeTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.people = [
            person(1, 'Adriano Forni', ['FORN', 'FORNE']),
            person(2, 'Rafael Quines', ['KINIS']),
        ]

    def test_google_meet_examples_use_registered_names(self):
        source = 'FORN falou com FORNE. KINIS respondeu a FORN.'
        result = normalize_transcript(source, self.people)
        self.assertEqual(result['text'],
                         'Adriano Forni falou com Adriano Forni. '
                         'Rafael Quines respondeu a Adriano Forni.')
        self.assertEqual(result['corrections'], [
            {'original': 'FORN', 'replacement': 'Adriano Forni', 'count': 2},
            {'original': 'FORNE', 'replacement': 'Adriano Forni', 'count': 1},
            {'original': 'KINIS', 'replacement': 'Rafael Quines', 'count': 1},
        ])
        self.assertEqual(result['ambiguities'], [])
        self.assertEqual(source, 'FORN falou com FORNE. KINIS respondeu a FORN.')

    def test_names_with_first_name_do_not_duplicate_it(self):
        result = normalize_transcript(
            'Adriano FORN; Adriano FORNE; Rafael KINIS.', self.people)
        self.assertEqual(result['text'],
                         'Adriano Forni; Adriano Forni; Rafael Quines.')
        self.assertEqual([c['original'] for c in result['corrections']],
                         ['Adriano FORN', 'Adriano FORNE', 'Rafael KINIS'])

    def test_case_accents_and_horizontal_whitespace_are_ignored(self):
        result = normalize_transcript(
            'adriáno\t FÓRNE e rAfAeL  kíNis.', self.people)
        self.assertEqual(result['text'], 'Adriano Forni e Rafael Quines.')

    def test_decomposed_unicode_keeps_original_offsets_and_punctuation(self):
        source = 'Antes: Fo\u0301rn; Rafa\u0301el\tKI\u0301NIS! Depois.'
        result = normalize_transcript(source, self.people)
        self.assertEqual(result['text'],
                         'Antes: Adriano Forni; Rafael Quines! Depois.')
        self.assertEqual([c['original'] for c in result['corrections']],
                         ['Fo\u0301rn', 'Rafa\u0301el\tKI\u0301NIS'])

    def test_registered_multiword_alias_accepts_spaces_and_tabs(self):
        people = [person(3, 'José Ávila', ['Jose Avilla'])]
        result = normalize_transcript('JOSE\t  AVILLA chegou.', people)
        self.assertEqual(result['text'], 'José Ávila chegou.')

    def test_longer_words_and_identifiers_are_not_changed(self):
        source = 'FORNALHA FORNECER KINISTA xFORN FORN2 _FORN FORN_'
        result = normalize_transcript(source, self.people)
        self.assertEqual(result['text'], source)
        self.assertEqual(result['corrections'], [])

    def test_punctuation_and_speaker_labels_delimit_whole_names(self):
        result = normalize_transcript('(FORN): [KINIS], “FORNE”.', self.people)
        self.assertEqual(result['text'],
                         '(Adriano Forni): [Rafael Quines], “Adriano Forni”.')

    def test_email_addresses_and_urls_are_preserved(self):
        source = ('FORN@exemplo.com; pessoa+KINIS@exemplo.com; '
                  'https://exemplo.com/FORNE?nome=KINIS '
                  'HTTP://FORN.exemplo.com www.exemplo.com/KINIS. FORN falou.')
        result = normalize_transcript(source, self.people)
        self.assertEqual(result['text'], source[:-len('FORN falou.')] + 'Adriano Forni falou.')
        self.assertEqual(result['corrections'], [
            {'original': 'FORN', 'replacement': 'Adriano Forni', 'count': 1},
        ])

    def test_bare_domains_and_other_url_schemes_are_preserved(self):
        source = 'FORN.com, KINIS.com.br/FORNE e ftp://servidor/FORN. FORN falou.'
        result = normalize_transcript(source, self.people)
        self.assertEqual(result['text'], source[:-len('FORN falou.')] + 'Adriano Forni falou.')

    def test_mentions_of_multiple_people_include_inactive_profiles(self):
        people = [
            person(1, 'Adriano Forni', ['FORN'], active=True),
            person(2, 'Rafael Quines', ['KINIS'], active=False),
        ]
        self.assertEqual(normalize_transcript('FORN consultou KINIS.', people)['text'],
                         'Adriano Forni consultou Rafael Quines.')

    def test_longest_overlapping_variant_wins(self):
        people = [person(1, 'Adriano Forni', ['FORN', 'Adriano FORN'])]
        result = normalize_transcript('Adriano FORN confirmou com FORN.', people)
        self.assertEqual(result['text'], 'Adriano Forni confirmou com Adriano Forni.')
        self.assertEqual([c['original'] for c in result['corrections']],
                         ['Adriano FORN', 'FORN'])

    def test_replacements_are_single_pass_and_idempotent(self):
        people = [
            person(1, 'Adriano Forni', ['FORN']),
            person(2, 'Rafael Quines', ['Adriano']),
        ]
        first = normalize_transcript('FORN falou com Rafael Quines.', people)
        self.assertEqual(first['text'], 'Adriano Forni falou com Rafael Quines.')
        second = normalize_transcript(first['text'], people)
        self.assertEqual(second['text'], first['text'])
        self.assertEqual(second['corrections'], [])
        self.assertEqual(second['fingerprint'], first['fingerprint'])

    def test_shared_alias_is_ambiguous_but_first_name_can_resolve_it(self):
        people = [
            person(1, 'Adriano Forni', ['FORN']),
            person(3, 'Marcos Silva', ['FORN']),
        ]
        result = normalize_transcript('FORN; FORN; Adriano FORN; Marcos FORN.', people)
        self.assertEqual(result['text'],
                         'FORN; FORN; Adriano Forni; Marcos Silva.')
        self.assertEqual(result['ambiguities'], [
            {'original': 'FORN', 'candidates': ['Adriano Forni', 'Marcos Silva'], 'count': 2},
        ])

    def test_alias_that_is_another_person_official_or_preferred_name_is_ambiguous(self):
        people = [
            person(1, 'Adriano Forni', preferred_name='Dri'),
            person(2, 'Rafael Quines', ['Adriano', 'Forni', 'Dri']),
        ]
        result = normalize_transcript('Adriano; Forni; Dri.', people)
        self.assertEqual(result['text'], 'Adriano; Forni; Dri.')
        self.assertEqual(result['corrections'], [])
        self.assertEqual([a['original'] for a in result['ambiguities']],
                         ['Adriano', 'Forni', 'Dri'])
        for ambiguity in result['ambiguities']:
            self.assertEqual(ambiguity['candidates'], ['Adriano Forni', 'Rafael Quines'])

    def test_complete_official_names_are_protected_from_other_people_aliases(self):
        people = [
            person(1, 'Adriano Forni'),
            person(2, 'Rafael Quines', ['Adriano Forni', 'Adriano', 'Forni']),
        ]
        source = 'Adriano Forni falou. ADRIÁNO\tFORNI ouviu.'
        result = normalize_transcript(source, people)
        self.assertEqual(result['text'], source)
        self.assertEqual(result['corrections'], [])

    def test_variant_cannot_consume_part_of_official_name_starting_later(self):
        people = [person(1, 'Adriano Forni'), person(2, 'Rafael Quines', ['Dr Adriano'])]
        source = 'Dr Adriano Forni chegou. Dr Adriano saiu.'
        self.assertEqual(normalize_transcript(source, people)['text'],
                         'Dr Adriano Forni chegou. Rafael Quines saiu.')

    def test_no_transcript_or_dictionary_is_safe(self):
        self.assertEqual(normalize_transcript(None, self.people)['text'], '')
        self.assertEqual(normalize_transcript('FORN', [])['text'], 'FORN')
        self.assertEqual(normalize_transcript('Texto intacto.', self.people)['corrections'], [])


class TranscriptPreviewTests(unittest.TestCase):
    def setUp(self):
        self.people = [person(1, 'Adriano Forni', ['FORN'])]

    def extraction_for(self, transcript, people=None):
        result = normalize_transcript(transcript, self.people if people is None else people)
        return {'resumo': 'Resumo anterior.',
                'transcript_normalization': normalization_metadata(result)}

    def test_preview_without_previous_extraction_does_not_request_reextraction(self):
        result = transcript_preview('FORN falou.', self.people, None)
        self.assertEqual(result['text'], 'Adriano Forni falou.')
        self.assertFalse(result['needs_reextraction'])

    def test_legacy_extraction_requests_reextraction_only_if_corrections_apply(self):
        extraction = {'resumo': 'Resumo anterior.'}
        self.assertTrue(transcript_preview('FORN falou.', self.people, extraction)['needs_reextraction'])
        self.assertFalse(transcript_preview('Adriano Forni falou.', self.people,
                                            extraction)['needs_reextraction'])

    def test_current_extraction_does_not_request_reextraction(self):
        source = 'FORN falou.'
        extraction = self.extraction_for(source)
        result = transcript_preview(source, self.people, extraction)
        self.assertFalse(result['needs_reextraction'])
        self.assertEqual(extraction['transcript_normalization']['version'], 1)
        self.assertNotIn('text', extraction['transcript_normalization'])

    def test_added_or_removed_alias_changes_analysis_fingerprint(self):
        no_aliases = [person(1, 'Adriano Forni')]
        source = 'FORN falou.'
        without_alias = self.extraction_for(source, no_aliases)
        with_alias = self.extraction_for(source)
        self.assertTrue(transcript_preview(source, self.people,
                                           without_alias)['needs_reextraction'])
        self.assertTrue(transcript_preview(source, no_aliases,
                                           with_alias)['needs_reextraction'])

    def test_unrelated_alias_change_does_not_request_reextraction(self):
        source = 'Adriano Forni falou.'
        extraction = self.extraction_for(source)
        changed_people = [person(1, 'Adriano Forni', ['FORN', 'FORNE'])]
        self.assertFalse(transcript_preview(source, changed_people,
                                            extraction)['needs_reextraction'])

    def test_changed_transcript_requests_reextraction(self):
        extraction = self.extraction_for('FORN falou.')
        self.assertTrue(transcript_preview('FORN confirmou a entrega.', self.people,
                                           extraction)['needs_reextraction'])


class ValidateTranscriptAliasesTests(unittest.TestCase):
    def test_normalizes_whitespace_and_deduplicates_case_and_accents(self):
        self.assertEqual(validate_transcript_aliases([
            '  FORN  ', 'forn', 'FÓRN', 'Fo\u0301rn', '  Rafael\t  Kinis\n', 'rafael kinis',
        ]), ['FORN', 'Rafael Kinis'])

    def test_empty_list_clears_aliases(self):
        self.assertEqual(validate_transcript_aliases([]), [])

    def test_list_and_item_limits_are_accepted_at_boundary(self):
        aliases = ['Variação ' + str(i) for i in range(20)]
        self.assertEqual(validate_transcript_aliases(aliases), aliases)
        self.assertEqual(validate_transcript_aliases(['A' * 100]), ['A' * 100])

    def test_invalid_types_empty_numeric_and_oversize_values_are_rejected(self):
        for invalid in (None, '', 'FORN', {}, [None], [123], [''], ['  \t\n'],
                        ['123'], ['?!'], ['A' * 101], ['Nome'] * 21):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    validate_transcript_aliases(invalid)


class LoadTranscriptPeopleTests(unittest.TestCase):
    def test_dictionary_loads_every_profile_without_active_filter(self):
        db = Mock()
        db.execute.return_value.fetchall.return_value = [
            (1, 'Adriano Forni', 'Adriano', ['FORN']),
            (2, 'Rafael Quines', None, ['KINIS']),
        ]
        result = load_transcript_people(db)
        self.assertEqual(result, [
            person(1, 'Adriano Forni', ['FORN'], preferred_name='Adriano'),
            person(2, 'Rafael Quines', ['KINIS']),
        ])
        query = str(db.execute.call_args.args[0]).lower()
        self.assertNotIn('where', query)
        db.commit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
