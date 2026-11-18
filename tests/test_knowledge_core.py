"""Contratos de evidência e significado: fixtures fictícias, sem chamadas à IA."""
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from cortex.ai.knowledge_service import (extract_candidates, resolve_due, resolve_ref, segments, extraction_context, conflict_context,
                                         validate_candidates)


class KnowledgeValidationTests(unittest.TestCase):
    def setUp(self):
        self.entities = [
            {'id': 10, 'kind': 'person', 'person_id': 1, 'label': 'Jean Silva', 'aliases': ['Jean']},
            {'id': 11, 'kind': 'person', 'person_id': 2, 'label': 'Marina Costa', 'aliases': ['Marina']},
        ]
        self.quote = 'Jean: vou entregar a proposta em 20/11.'
        self.item = {'content': 'Entregar a proposta', 'classification': 'commitment',
                     'quote': self.quote, 'speaker': {'entity_id': 10}, 'owner': {'entity_id': 10},
                     'subject': {'entity_id': 10}, 'accepted': True, 'due_expression': '20/11'}

    def validate(self, item=None, value=None, tasks=None):
        value = value or self.quote
        return validate_candidates({'claims': [item or self.item]},
                                   {'start': 0, 'text': value}, self.entities,
                                   tasks or [], '2026-11-10')[0]

    def test_reuses_real_person_ids_and_exact_offsets(self):
        result = self.validate()
        self.assertEqual(result['owner'], {'entity_id': 10})
        self.assertEqual(result['due_date'], '2026-11-20')
        self.assertTrue(result['task_accepted'])
        self.assertEqual(self.quote[result['evidence_start']:result['evidence_end']], self.quote)

    def test_perception_keeps_author_and_subject_distinct(self):
        quote = 'Marina: acho que Jean está sobrecarregado.'
        item = {'quote': quote, 'content': 'Marina acha que Jean está sobrecarregado.',
                'classification': 'perception', 'speaker': {'entity_id': 11},
                'subject': {'entity_id': 10}, 'accepted': True}
        result = self.validate(item, quote)
        self.assertEqual(result['speaker'], {'entity_id': 11})
        self.assertEqual(result['subject'], {'entity_id': 10})
        self.assertFalse(result['task_accepted'])

    def test_mention_cannot_be_speaker(self):
        quote = 'Marina: acho que Jean deveria entregar isso.'
        item = {**self.item, 'quote': quote, 'due_expression': None}
        result = self.validate(item, quote)
        self.assertIsNone(result['speaker'])
        self.assertFalse(result['task_accepted'])
        self.assertTrue(result['unresolved'])

    def test_conditional_acceptance_is_not_task(self):
        quote = 'Jean: posso tentar entregar na sexta.'
        item = {**self.item, 'quote': quote, 'due_expression': 'sexta'}
        result = self.validate(item, quote)
        self.assertFalse(result['task_accepted'])
        self.assertEqual(result['due_date'], '2026-11-13')

    def test_proposal_is_not_task_even_when_model_marks_accepted(self):
        quote = 'Jean: poderia avaliar isso.'
        item = {**self.item, 'quote': quote, 'classification': 'proposal', 'due_expression': None}
        self.assertFalse(self.validate(item, quote)['task_accepted'])

    def test_unknown_ids_and_invalid_evidence_fail_closed(self):
        for changed in ({'subject': {'entity_id': 999}}, {'quote': 'trecho inventado'},
                        {'classification': 'objective_fact'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.validate({**self.item, **changed})

    def test_unique_quote_offset_is_computed_from_source(self):
        text = 'Abertura da conversa 😊.\n' + self.quote
        original = self.validate(value=text)
        wrong_offset = self.validate({**self.item, 'quote_start': 8}, text)
        self.assertEqual(wrong_offset['source_key'], original['source_key'])
        self.assertEqual(text[wrong_offset['evidence_start']:wrong_offset['evidence_end']], self.quote)

    def test_quote_with_normalized_spacing_preserves_the_original_evidence(self):
        raw_quote = self.quote.replace('proposta', 'proposta\r\n')
        raw_quote = raw_quote.replace('em 20/11', 'em\t20/11')
        source = 'Abertura.\n' + raw_quote
        result = self.validate(value=source)
        self.assertEqual(source[result['evidence_start']:result['evidence_end']], raw_quote)
        with self.assertRaisesRegex(ValueError, 'forma única'):
            self.validate(value=source + '\n' + raw_quote)

    def test_homonyms_never_resolve_by_name_or_model_id(self):
        self.entities.append({'id': 12, 'kind': 'person', 'person_id': 3,
                              'label': 'Jean Souza', 'aliases': ['Jean']})
        ref = resolve_ref({'name': 'Jean'}, self.entities)
        self.assertEqual(ref['candidates'], [10, 12])
        result = self.validate()
        self.assertIsNone(result['speaker'])
        self.assertIsNone(result['owner'])
        self.assertFalse(result['task_accepted'])

    def test_full_name_can_resolve_a_homonym(self):
        self.entities.append({'id': 12, 'kind': 'person', 'person_id': 3,
                              'label': 'Jean Souza', 'aliases': ['Jean']})
        quote = self.quote.replace('Jean:', 'Jean Silva:')
        self.assertTrue(self.validate({**self.item, 'quote': quote}, quote)['task_accepted'])

    def test_dependency_requires_evidence(self):
        item = deepcopy(self.item)
        item['relations'] = [{'from': {'entity_id': 11}, 'to': {'entity_id': 10}, 'type': 'depends_on'}]
        self.assertEqual(self.validate(item)['relations'], [])

    def test_task_reference_requires_real_id_and_matching_owner(self):
        tasks = [{'kind': 'commitment', 'id': 44, 'person_id': 1}]
        item = {**self.item, 'task_ref': {'kind': 'commitment', 'id': 44}}
        self.assertEqual(self.validate(item, tasks=tasks)['task_ref']['id'], 44)
        with self.assertRaises(ValueError):
            self.validate(item, tasks=[{'kind': 'commitment', 'id': 44, 'person_id': 2}])
        with self.assertRaises(ValueError):
            self.validate(item)

    def test_unchanged_evidence_identity_survives_inserting_text_before_it(self):
        before = self.validate()
        after = self.validate(value='Contexto novo.\n' + self.quote)
        self.assertEqual(before['source_key'], after['source_key'])
        self.assertNotEqual(before['evidence_start'], after['evidence_start'])

    def test_repeated_evidence_requires_exact_position(self):
        value = self.quote + '\n' + self.quote
        for item in (self.item, {**self.item, 'quote_start': 8}):
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.validate(item, value)
        first = self.validate({**self.item, 'quote_start': 0}, value)
        second = self.validate({**self.item, 'quote_start': len(self.quote) + 1}, value)
        self.assertNotEqual(first['source_key'], second['source_key'])

    def test_negated_acceptance_and_completion_never_create_or_close_task(self):
        for quote in ('Jean: não vou entregar em 20/11.', 'Jean: não aceito entregar em 20/11.'):
            with self.subTest(quote=quote):
                item = {**self.item, 'quote': quote}
                self.assertFalse(self.validate(item, quote)['task_accepted'])
        quote = 'Jean: não entreguei a proposta.'
        item = {**self.item, 'quote': quote, 'due_expression': None, 'operational_state': 'done'}
        self.assertEqual(self.validate(item, quote)['operational_state'], 'none')

    def test_another_speakers_acceptance_does_not_create_the_owners_task(self):
        quote = 'Jean: estou com dúvidas sobre a entrega.\nMarina: vou entregar a proposta em 20/11.'
        result = self.validate({**self.item, 'quote': quote}, quote)
        self.assertEqual(result['speaker'], {'entity_id': 10})
        self.assertFalse(result['task_accepted'])

    def test_relation_endpoints_and_mentions_require_literal_unambiguous_evidence(self):
        item = {**self.item, 'mentions': [{'entity_id': 11}, {'kind': 'project', 'label': 'Projeto inventado'}],
                'relations': [{'from': {'entity_id': 10}, 'to': {'entity_id': 11}, 'type': 'related_to'}]}
        result = self.validate(item)
        self.assertTrue(all('unresolved' in ref for ref in result['mentions']))
        self.assertEqual(result['relations'], [])
        self.assertFalse(result['task_accepted'])

    def test_unambiguous_people_in_evidence_keep_mention_links_when_model_omits_them(self):
        self.entities.append({'id': 12, 'kind': 'person', 'person_id': 3,
                              'label': 'Jean Nascimento', 'aliases': ['Jean']})
        quote = '[00:02] Marina Costa\nVou conversar com Jean Nascimento para alinhar o plano.'
        item = {'quote': quote, 'content': 'Marina vai alinhar o plano.', 'classification': 'report',
                'speaker': {'entity_id': 11}}
        result = self.validate(item, quote)
        self.assertEqual(result['speaker'], {'entity_id': 11})
        self.assertEqual(result['mentions'], [{'entity_id': 12}])
        self.assertIsNone(result['owner'])
        self.assertEqual(result['relations'], [])

    def test_names_inside_addresses_do_not_create_mention_links(self):
        quote = 'Marina: Para os documentos consulte jean@example.test.'
        item = {'quote': quote, 'content': 'Marina indica onde consultar os documentos.',
                'classification': 'report', 'speaker': {'entity_id': 11}}
        self.assertEqual(self.validate(item, quote)['mentions'], [])

    def test_invalid_model_candidates_do_not_hide_the_verified_connections(self):
        invalid = [{**self.item, 'quote': 'trecho inventado'}, {**self.item, 'classification': []}]
        valid = {**self.item, 'quote_start': 987}
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace())])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response)))
        segment = {'start': 0, 'text': self.quote}
        with patch('cortex.ai.knowledge_service.get_client', return_value=client):
            response.choices[0].message.content = json.dumps({'claims': [*invalid, valid]}, ensure_ascii=False)
            candidates, metrics = extract_candidates(segment, self.entities, [], '2026-11-10')
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]['owner'], {'entity_id': 10})
            self.assertEqual(metrics['rejected_candidates'], 2)

            response.choices[0].message.content = json.dumps({'claims': invalid}, ensure_ascii=False)
            with self.assertRaisesRegex(ValueError, 'evidência'):
                extract_candidates(segment, self.entities, [], '2026-11-10')

    def test_source_key_stable_when_model_reclassifies_or_changes_roles(self):
        original = self.validate()
        revised = self.validate({**self.item, 'classification': 'proposal', 'speaker': None, 'content': 'Nova interpretação'})
        self.assertEqual(original['source_key'], revised['source_key'])

    def test_empty_and_oversized_contract(self):
        self.assertEqual(validate_candidates({'claims': []}, {'start': 0, 'text': ''}, [], [], '2026-11-17'), [])
        for payload in ([], {}, {'claims': [self.item] * 81}):
            with self.assertRaises(ValueError):
                validate_candidates(payload, {'start': 0, 'text': self.quote}, self.entities, [], '2026-11-17')

    def test_absence_of_update_does_not_complete_task(self):
        item = {**self.item, 'operational_state': 'done'}
        self.assertEqual(self.validate(item)['operational_state'], 'none')

    def test_dates_use_meeting_date_and_preserve_ambiguity(self):
        self.assertEqual(resolve_due('amanhã', '2026-11-10'), '2026-11-11')
        self.assertEqual(resolve_due('sexta', '2026-11-10'), '2026-11-13')
        self.assertIsNone(resolve_due('em breve', '2026-11-10'))
        self.assertIsNone(resolve_due('31/02', '2026-11-10'))
        self.assertIsNone(resolve_due('sexta ou segunda', '2026-11-10'))

    def test_renegotiation_request_requires_explicit_manager_agreement(self):
        tasks = [{'kind': 'commitment', 'id': 44, 'person_id': 1}]
        quote = 'Jean: peço prazo até 24/11 para entregar a proposta.'
        item = {**self.item, 'quote': quote, 'due_expression': '24/11',
                'task_ref': {'kind': 'commitment', 'id': 44}, 'operational_state': 'open'}
        result = self.validate(item, quote, tasks)
        self.assertFalse(result['task_accepted'])
        self.assertEqual(result['operational_state'], 'none')
        agreed = quote + '\nGestor: aceito o novo prazo.'
        result = self.validate({**item, 'quote': agreed}, agreed, tasks)
        self.assertTrue(result['task_accepted'])

    def test_negated_dependency_does_not_create_edge(self):
        quote = 'Marina: não dependo da proposta de Jean.'
        item = {'content': 'Marina não depende da proposta de Jean.', 'classification': 'declaration',
                'quote': quote, 'speaker': {'entity_id': 11}, 'subject': {'entity_id': 10},
                'relations': [{'from': {'entity_id': 11}, 'to': {'entity_id': 10}, 'type': 'depends_on'}]}
        self.assertEqual(self.validate(item, quote)['relations'], [])

    def test_explicit_completion_updates_only_an_identified_existing_task(self):
        quote = 'Jean: entreguei a proposta.'
        item = {**self.item, 'quote': quote, 'due_expression': None, 'operational_state': 'done',
                'task_ref': {'kind': 'commitment', 'id': 44}}
        result = self.validate(item, quote, [{'kind': 'commitment', 'id': 44, 'person_id': 1}])
        self.assertTrue(result['task_accepted'])
        self.assertEqual(result['operational_state'], 'done')
        item.pop('task_ref')
        self.assertEqual(self.validate(item, quote)['operational_state'], 'none')

    def test_condition_is_literal_and_keeps_unaccepted_task_without_operational_state(self):
        quote = 'Jean: posso tentar entregar a proposta em 20/11.'
        item = {**self.item, 'quote': quote, 'condition': 'posso tentar', 'operational_state': 'open'}
        result = self.validate(item, quote)
        self.assertEqual(result['condition_text'], 'posso tentar')
        self.assertEqual(result['operational_state'], 'none')
        with self.assertRaises(ValueError):
            self.validate({**item, 'condition': 'condição inventada'}, quote)

    def test_extraction_registry_and_tasks_have_explicit_context_budget(self):
        tasks = [{'kind': 'commitment', 'id': i + 1, 'person_id': 1, 'status': 'open',
                  'description': 'Proposta ' + str(i) + ' ' + 'x' * 1100, 'knowledge_event_on': '2026-11-10'}
                 for i in range(200)]
        entities, selected, metrics = extraction_context({'text': self.quote}, self.entities, tasks)
        self.assertEqual({e['person_id'] for e in entities}, {1})
        self.assertTrue(metrics['context_truncated'])
        self.assertEqual(metrics['tasks_available'], 200)
        self.assertLess(metrics['tasks_included'], 200)
        import json
        self.assertLessEqual(len(json.dumps({'entities': entities, 'tasks': selected}, ensure_ascii=False)), 40000)

    def test_conflicts_reference_only_canonical_supplied_claim_ids(self):
        claim = {'id': 77, 'version': 2, 'content': 'Jean não está sobrecarregado.',
                 'classification': 'declaration', 'subject_entity_id': 10,
                 'entity_ids': [10], 'origin': 'manual', 'human_fields': {'content': True}}
        item = {**self.item, 'conflicts_with': [77]}
        clean = validate_candidates({'claims': [item]}, {'start': 0, 'text': self.quote},
                                    self.entities, [], '2026-11-10', [claim])[0]
        self.assertEqual(clean['conflicts_with'], [77])
        with self.assertRaises(ValueError):
            self.validate(item)
        selected, metrics = conflict_context({'text': self.quote}, self.entities, [claim])
        self.assertEqual(selected[0]['id'], 77)
        self.assertFalse(metrics['claims_context_truncated'])

    def test_segmentation_preserves_whole_source_and_offsets(self):
        value = ('Marina: informação.\n' * 2000) + self.quote
        parts = list(segments(value))
        self.assertEqual(''.join(p['text'] for p in parts), value)
        self.assertTrue(all(value[p['start']:p['end']] == p['text'] for p in parts))
        self.assertTrue(all(len(p['text']) <= 10000 for p in parts))


if __name__ == '__main__':
    unittest.main()
