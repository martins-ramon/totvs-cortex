"""Exercita o cliente de curadoria sem depender de CDN, navegador ou IA."""
import pathlib
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node não disponível para validar o cliente JavaScript')
class KnowledgeClientTests(unittest.TestCase):
    def run_javascript(self, script):
        source = pathlib.Path(__file__).resolve().parents[1] / 'static/js/knowledge.js'
        completed = subprocess.run(['node', '-e', script, str(source)], capture_output=True,
                                   text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_partial_edits_and_conflicts(self):
        self.run_javascript(r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const context = vm.createContext({ URLSearchParams, showToast() {}, Date });
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
(async () => {
    const app = context.cortexKnowledge();
    app.$refs = {};
    app.$nextTick = callback => callback();
    app.knowledgeDetail = { claim: { id: 7, version: 4 } };
    app.knowledgeOriginal = {
        content: 'Jean aceita entregar a proposta', classification: 'commitment', review_state: 'accepted',
        speaker_entity_id: '11', subject_entity_id: '11', owner_entity_id: '11',
        entity_ids: ['11'], due_date: '2026-11-24', status: 'open'
    };
    app.knowledgeForm = { ...app.knowledgeOriginal, entity_ids: ['11'], due_date: '2026-11-26' };
    app.knowledgeReason = 'Novo combinado fora das transcrições';
    let request;
    app.api = async (path, options) => { request = { path, payload: JSON.parse(options.body) }; return {}; };
    app.selectKnowledgeClaim = async () => {};
    app.loadKnowledge = async () => {};
    app.knowledgeRefreshConsumers = () => {};
    await app.saveKnowledgeClaim();
    assert.equal(request.path, '/api/knowledge/claims/7');
    assert.deepEqual(request.payload, {
        version: 4, reason: 'Novo combinado fora das transcrições', due_date: '2026-11-26'
    });
    // O cliente não bloqueia em lote autoria/classificação que o humano não editou.
    assert.equal(Object.hasOwn(request.payload, 'speaker_entity_id'), false);
    const conflict = new Error('Este registro mudou');
    conflict.data = { conflict: true };
    app.api = async () => { throw conflict; };
    app.knowledgeForm.due_date = '2026-11-27';
    await app.saveKnowledgeClaim();
    assert.match(app.knowledgeDetailError, /Recarregue/);
    assert.equal(app.knowledgeForm.due_date, '2026-11-27');
    assert.equal(app.knowledgeDetail.claim.version, 4);
    assert.equal(app.knowledgeSaving, false);
    // O detalhe da relação manual inclui auditoria sem descartar o rascunho em andamento.
    let finishRelation;
    app.api = async () => new Promise(resolve => { finishRelation = resolve; });
    const initial = { id: 9, version: 1, from_entity_id: 11, to_entity_id: 12, relation_type: 'related_to', review_state: 'accepted' };
    const loading = app.selectKnowledgeRelation(9, initial);
    app.knowledgeRelationForm.relation_type = 'depends_on';
    finishRelation({ relation: { ...initial, version: 2 }, evidence: null, claim: null, revisions: [{ id: 1 }] });
    await loading;
    assert.equal(app.knowledgeRelationForm.relation_type, 'depends_on');
    assert.equal(app.knowledgeRelationForm.version, 1);
    assert.equal(app.knowledgeRelationDetail.relation.version, 2);
    assert.equal(app.knowledgeRelationDetail.revisions.length, 1);
    let selectedRelation;
    app.goKnowledge = async () => {};
    app.selectKnowledgeRelation = async id => { selectedRelation = id; };
    app.closeAssistant = () => {};
    await app.openKnowledgeFromAssistant({ id: 'relation:9', record_id: 9, type: 'relation' });
    assert.equal(selectedRelation, 9);
    assert.equal(app.knowledgeClaimTypeLabel({ classification: 'commitment', task_accepted: false }), 'Compromisso · aceite a confirmar');
    assert.equal(app.knowledgeClaimTypeLabel({ classification: 'commitment', task_accepted: true }), 'Compromisso aceito');
    app.knowledgeEntities = [{ id: 11, kind: 'person', person_id: 1, label: 'Jean' }];
    app.knowledgeForm = { ...app.knowledgeOriginal, entity_ids: ['11'] };
    app.knowledgeOriginalTaskSelection = '';
    let taskPaths = [];
    app.api = async path => {
        taskPaths.push(path);
        return path.includes('offset=200')
            ? { tasks: [{ kind: 'meeting_action', id: 100, version: 2, description: 'Outra entrega' }], next_offset: null, total: 201 }
            : { tasks: [{ kind: 'commitment', id: 99, version: 3, description: 'A proposta' }], next_offset: 200, total: 201 };
    };
    await app.loadKnowledgeTasks();
    await app.loadKnowledgeTasks(true);
    assert.match(taskPaths[0], /person_id=1/);
    assert.match(taskPaths[1], /offset=200/);
    assert.equal(app.knowledgeTaskOptions.length, 2);
    assert.equal(app.knowledgeTaskNextOffset, null);
    app.knowledgeTaskSelection = 'commitment:99';
    app.knowledgeDetail = { claim: { id: 7, version: 4 } };
    app.api = async (path, options) => { request = { path, payload: JSON.parse(options.body) }; return {}; };
    await app.saveKnowledgeClaim();
    assert.deepEqual(request.payload.task_ref, { kind: 'commitment', id: 99, version: 3 });
    assert.equal(Object.hasOwn(request.payload, 'task_accepted'), false);
})().catch(error => { process.stderr.write(error.stack); process.exitCode = 1; });
''')

    def test_automatic_sync_and_common_connections(self):
        self.run_javascript(r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const context = vm.createContext({ URLSearchParams, setTimeout: callback => callback() });
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
(async () => {
    const app = context.cortexKnowledge();
    app.view = 'knowledge';
    app.$refs = {};
    app.$nextTick = callback => callback();
    let refreshed = 0;
    app.knowledgeRefreshConsumers = () => { refreshed++; };
    const entity = { id: 'entity:1', record_id: 1, kind: 'project', label: 'Atlas' };
    const claims = [
        { id: 'claim:2', kind: 'claim', source_ref: 'meeting:3', source_title: 'Revisão semanal' },
        { id: 'claim:3', kind: 'claim', source_ref: 'meeting:3', source_title: 'Revisão semanal' },
        { id: 'claim:4', kind: 'claim', source_ref: 'one_on_one:3', source_title: '1:1 com Luana' },
        { id: 'claim:5', kind: 'claim', source_ref: 'knowledge:5', source_title: 'Observação' },
    ];
    const edges = claims.map(claim => ({ source: entity.id, target: claim.id }));
    const requests = [];
    let steps = [];
    app.api = async (path, options) => {
        requests.push(path);
        if (path === '/api/knowledge/status') return { enabled: true, has_pending: true, can_sync: true };
        if (path.startsWith('/api/knowledge/claims?')) return { claims: [{ content: 'Atlas aprovado' }] };
        if (path.startsWith('/api/knowledge/graph?'))
            return { nodes: [entity, ...claims], edges };
        if (path.startsWith('/api/knowledge/entities?')) return { entities: [] };
        if (path === '/api/knowledge/sync') {
            assert.equal(options.method, 'POST');
            steps.push(JSON.parse(options.body));
            app.knowledgeFilters.q = 'busca ainda não enviada';
            return { has_pending: steps.length === 1, can_sync: true, updated: true };
        }
        throw new Error('Rota inesperada: ' + path);
    };
    await app.enterKnowledge();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.knowledgeMode, 'list');
    assert.deepEqual(steps, [{}, {}]);
    assert.equal(app.knowledgeSyncing, false);
    assert.equal(refreshed, 1);
    assert.equal(app.knowledgeClaims[0].content, 'Atlas aprovado');
    assert.deepEqual(JSON.parse(JSON.stringify(app.knowledgeShared)), [
        { id: 1, label: 'Atlas', count: 2, titles: ['Revisão semanal', '1:1 com Luana'] }
    ]);
    assert.equal(requests.some(path => path.includes('/jobs') || path.includes('/backfill')), false);
    assert.equal(requests.some(path => path.includes('q=')), false);

    steps = [];
    await app.syncKnowledge(true);
    assert.deepEqual(steps, [{ retry: true }, {}]);
    assert.equal(app.knowledgeSyncing, false);
    assert.equal(refreshed, 2);
})().catch(error => { process.stderr.write(error.stack); process.exitCode = 1; });
''')
