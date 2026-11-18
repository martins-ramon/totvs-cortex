"""Exercita o cliente de curadoria sem depender de CDN, navegador ou IA."""
import pathlib
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node não disponível para validar o cliente JavaScript')
class KnowledgeClientTests(unittest.TestCase):
    def run_javascript(self, script):
        source = pathlib.Path(__file__).resolve().parents[1] / 'static/js'
        files = [str(source / file) for file in ('knowledge.js', 'meetings.js', 'app.js')]
        completed = subprocess.run(['node', '-e', script, *files], capture_output=True,
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
const context = vm.createContext({
    URLSearchParams,
    setTimeout: (callback, delay) => { if (delay !== 800) callback(); },
    clearTimeout() {}
});
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
        if (path === '/api/knowledge/status') return {
            enabled: true, has_pending: true, can_sync: true,
            sync_progress: { remaining: 2, failed: 0, active: null }
        };
        if (path.startsWith('/api/knowledge/claims?')) return { claims: [{ content: 'Atlas aprovado' }] };
        if (path.startsWith('/api/knowledge/graph?'))
            return { nodes: [entity, ...claims], edges };
        if (path.startsWith('/api/knowledge/entities?')) return { entities: [] };
        if (path === '/api/knowledge/sync') {
            assert.equal(options.method, 'POST');
            steps.push(JSON.parse(options.body));
            app.knowledgeFilters.q = 'busca ainda não enviada';
            return {
                has_pending: steps.length === 1, can_sync: true, updated: true,
                sync_progress: { remaining: 2 - steps.length, failed: 0, active: null }
            };
        }
        throw new Error('Rota inesperada: ' + path);
    };
    await app.enterKnowledge();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.knowledgeMode, 'list');
    assert.deepEqual(steps, [{}, {}]);
    assert.equal(app.knowledgeSyncing, false);
    assert.equal(app.knowledgeSyncMessage(), 'Memória atualizada.');
    assert.equal(app.knowledgeSyncValue(), 2);
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

    def test_sync_progress_live_phases_partial_results_and_retry(self):
        self.run_javascript(r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const timers = new Map();
let timerId = 0;
const context = vm.createContext({
    window: {},
    setTimeout: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; },
    clearTimeout: id => timers.delete(id)
});
for (const source of process.argv.slice(1)) vm.runInContext(fs.readFileSync(source, 'utf8'), context);
function advance(delay) {
    const entry = [...timers].find(([, timer]) => timer.delay === delay);
    assert.ok(entry, 'Temporizador ausente: ' + delay);
    const [id, timer] = entry;
    timers.delete(id);
    return timer.callback();
}
function state(remaining, failed = 0, active = null, partial = 0) {
    return {
        can_sync: true, has_pending: remaining > 0, needs_attention: failed > 0,
        sync_progress: { remaining, failed, active, partial }
    };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    const app = context.window.cortexApp();
    app.view = 'knowledge';
    app.knowledgeStatus = state(2);
    app.loadKnowledge = async () => {};
    app.knowledgeRefreshConsumers = () => {};
    const posts = [];
    const polls = [];
    app.api = (path, options) => {
        assert.equal(path, '/api/knowledge/sync');
        return new Promise((resolve, reject) => {
            if (options.method === 'POST') posts.push({ payload: JSON.parse(options.body), resolve, reject });
            else polls.push(resolve);
        });
    };

    const active = { kind: 'meeting', id: 17, title: 'Revisão semanal', phase: 'extracting',
        segments_completed: 1, segments_total: 3 };
    const syncing = app.syncKnowledge();
    await app.syncKnowledge(true);
    assert.equal(posts.length, 1);
    assert.equal(app.knowledgeSyncTotal, 2);
    assert.equal(app.knowledgeSyncValue(), 0);
    assert.equal(app.knowledgeSyncQueueLabel(), '0% do processamento · 2 conversas a concluir');
    assert.match(app.knowledgeSyncMessage(), /Verificando/);

    const extracting = advance(800);
    polls[0](state(2, 0, active, 1 / 3));
    await extracting;
    assert.match(app.knowledgeSyncMessage(), /Analisando/);
    assert.equal(app.knowledgeSyncDetail(), 'Reunião · Revisão semanal · Trecho 2 de 3');
    assert.equal(app.knowledgeSyncValue(), 1 / 3);
    assert.equal(app.knowledgeSyncQueueLabel(), '16,6% do processamento · 2 conversas a concluir');

    const publishing = advance(1500);
    polls[1](state(2, 0, { ...active, phase: 'publishing' }, 1 / 3));
    await publishing;
    assert.match(app.knowledgeSyncMessage(), /Salvando/);

    const delayed = advance(1500);
    posts[0].resolve({ ...state(1, 0, null, 0.5), updated: true });
    await flush();
    assert.match(app.knowledgeSyncMessage(), /Preparando/);
    assert.equal(app.knowledgeSyncValue(), 1.5);
    assert.equal(app.knowledgeSyncQueueLabel(), '75% do processamento · 1 conversa a concluir');
    polls[2](state(2, 0, active));
    await delayed;
    assert.equal(app.knowledgeSyncValue(), 1.5);
    assert.equal(app.knowledgeSyncDetail(), '');

    advance(250);
    await flush();
    assert.equal(posts.length, 2);
    posts[1].resolve(state(0, 1));
    await syncing;
    assert.equal(app.knowledgeSyncing, false);
    assert.equal(timers.size, 0);
    assert.equal(app.knowledgeSyncValue(), 1);
    assert.equal(app.knowledgeSyncTotal, 2);
    assert.match(app.knowledgeSyncMessage(), /com pendências/);
    assert.equal(app.knowledgeSyncQueueLabel(), '50% do processamento');
    assert.equal(app.knowledgeSyncDetail(), '1 conversa precisa de nova tentativa.');

    const retry = app.syncKnowledge(true);
    assert.deepEqual(posts[2].payload, { retry: true });
    assert.equal(app.knowledgeSyncValue(), 0);
    assert.equal(app.knowledgeSyncTotal, 1);
    posts[2].resolve({ ...state(0), updated: true });
    await retry;
    assert.equal(app.knowledgeSyncValue(), 1);
    assert.equal(app.knowledgeSyncMessage(), 'Memória atualizada.');
    assert.equal(app.knowledgeSyncQueueLabel(), '100% do processamento');
    assert.equal(timers.size, 0);

    const disabled = app.syncKnowledge();
    posts[3].resolve({ ...state(0), can_sync: false });
    await disabled;
    assert.equal(app.knowledgeSyncMessage(), 'A atualização automática está indisponível.');

    const disconnected = app.syncKnowledge();
    posts[4].reject(new Error('Falha simulada de conexão'));
    await disconnected;
    assert.match(app.knowledgeSyncMessage(), /Não foi possível atualizar/);
    assert.equal(app.knowledgeSyncing, false);
    assert.equal(timers.size, 0);
})().catch(error => { process.stderr.write(error.stack); process.exitCode = 1; });
''')
