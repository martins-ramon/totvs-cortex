/* Memória do time: conexões, fontes e revisão de registros. */
function cortexKnowledge() {
    return {
        knowledgeEnabled: true,
        knowledgeLoading: false,
        knowledgeError: '',
        knowledgeSaving: false,
        knowledgeStatus: {},
        knowledgeMode: 'list',
        knowledgeRelationLimit: 3,
        knowledgeHasLoaded: false,
        knowledgeMapError: false,
        knowledgeFilters: { q: '', entity_id: '', classification: '', review_state: '', status: '', date_from: '', date_to: '', depth: '1' },
        knowledgeAppliedFilters: null,
        knowledgeEntities: [],
        knowledgeEntityMatches: [],
        knowledgeEntitySearch: '',
        knowledgeEntitySearchRequest: 0,
        knowledgeNodes: [],
        knowledgeEdges: [],
        knowledgeShared: [],
        knowledgeClaims: [],
        knowledgeTotal: 0,
        knowledgeOffset: 0,
        knowledgeTruncated: false,
        knowledgeRequest: 0,
        knowledgeSelectionRequest: 0,
        knowledgeSelected: null,
        knowledgeDetail: null,
        knowledgeDetailLoading: false,
        knowledgeDetailError: '',
        knowledgeEditing: false,
        knowledgeForm: {},
        knowledgeOriginal: {},
        knowledgeReason: '',
        knowledgeResolveConflict: false,
        knowledgeTaskOptions: [],
        knowledgeTaskSelection: '',
        knowledgeOriginalTaskSelection: '',
        knowledgeTaskOwnerId: '',
        knowledgeTaskNextOffset: null,
        knowledgeTaskTotal: 0,
        knowledgeTaskLoading: false,
        knowledgeTaskError: '',
        knowledgeTaskRequest: 0,
        knowledgeZoom: 1,
        knowledgeSyncing: false,
        knowledgeSyncError: '',
        knowledgeSyncStage: '',
        knowledgeSyncTotal: 0,
        knowledgeSyncSnapshot: null,
        knowledgeNewNote: false,
        knowledgeNote: { content: '', classification: 'declaration', subject_entity_id: '', speaker_entity_id: '', entity_ids: [], reason: '' },
        knowledgeEntityForm: null,
        knowledgeRelationForm: null,
        knowledgeRelationDetail: null,

        get knowledgeClassifications() {
            return [
                { value: 'declaration', label: 'Declaração' }, { value: 'report', label: 'Relato de terceiro' },
                { value: 'perception', label: 'Percepção' }, { value: 'hypothesis', label: 'Hipótese' },
                { value: 'proposal', label: 'Proposta' }, { value: 'decision', label: 'Decisão' },
                { value: 'commitment', label: 'Compromisso' }, { value: 'question', label: 'Questão aberta' }
            ];
        },
        knowledgeLabel(value) {
            return ({ person: 'Pessoa', project: 'Projeto', topic: 'Assunto', claim: 'Afirmação', pending: 'Pendente',
                accepted: 'Aceito', corrected: 'Corrigido', rejected: 'Rejeitado', open: 'Aberto', done: 'Concluído',
                cancelled: 'Cancelado', none: 'Sem estado', manual: 'Manual', automatic: 'Automático',
                speaker: 'Quem falou', subject: 'Sobre quem', owner: 'Responsável', mentions: 'Menciona',
                depends_on: 'Depende de', related_to: 'Relacionado a', decided: 'Decidiu', about: 'Trata de', responsible_for: 'Responsável por' })[value]
                || this.knowledgeClassifications.find(item => item.value === value)?.label || value || '—';
        },
        knowledgeClaimTypeLabel(record) {
            if (record?.classification === 'commitment') return record.task_accepted ? 'Compromisso aceito' : 'Compromisso · aceite a confirmar';
            return this.knowledgeLabel(record?.classification || record?.kind);
        },
        knowledgeEntityLabel(id) {
            return this.knowledgeEntities.find(entity => Number(entity.id) === Number(id))?.label
                || this.knowledgeNodes.find(node => node.id === `entity:${id}`)?.label || 'Não identificado';
        },
        knowledgeClaimPeople(claim) {
            return [
                { label: 'Quem falou', name: claim.speaker_name },
                { label: 'Sobre', name: claim.subject_name },
                { label: 'Responsável', name: claim.owner_name }
            ].filter(item => item.name);
        },
        knowledgeClaimTopics(claim) {
            const roles = new Set([claim.speaker_entity_id, claim.subject_entity_id, claim.owner_entity_id].map(String));
            const labels = (claim.entity_ids || []).filter(id => !roles.has(String(id)))
                .map(id => this.knowledgeEntityLabel(id)).filter(label => label !== 'Não identificado');
            return [...new Set(labels)];
        },
        knowledgeSourceLabel(record) {
            if (record?.source_title) return record.source_title;
            const kind = record?.source_kind || record?.source_ref?.split(':')[0];
            if (kind === 'meeting') return 'Reunião';
            if (kind === 'one_on_one') return '1:1';
            return 'Observação';
        },
        knowledgeRelations() {
            return this.knowledgeEdges.filter(edge => edge.relation_id);
        },
        hasKnowledgeFilters(applied = false) {
            const filters = applied ? this.knowledgeAppliedFilters || this.knowledgeFilters : this.knowledgeFilters;
            return Object.entries(filters).some(([key, value]) => key === 'depth' ? value !== '1' : !!value.trim());
        },
        knowledgeDisplay(value) {
            if (value == null) return '—';
            return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
        },
        knowledgeDate(value) {
            if (!value) return '—';
            const date = new Date(String(value).length === 10 ? value + 'T12:00:00' : value);
            return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString('pt-BR');
        },
        async goKnowledge(personId = null) {
            this.view = 'knowledge';
            if (personId) {
                try {
                    const person = this.people?.find(item => Number(item.id) === Number(personId));
                    const data = await this.api('/api/knowledge/entities?' + new URLSearchParams({ q: person?.full_name || '' }));
                    const entity = (data.entities || []).find(item => Number(item.person_id) === Number(personId));
                    if (entity) {
                        this.knowledgeFilters.entity_id = String(entity.id);
                        if (!this.knowledgeEntities.some(item => item.id === entity.id)) this.knowledgeEntities.push(entity);
                    }
                } catch { /* The main view handles unavailable memory. */ }
            }
            await this.enterKnowledge();
        },
        async enterKnowledge() {
            if (!this.knowledgeSyncing) {
                this.knowledgeSyncStage = '';
                this.knowledgeSyncTotal = 0;
                this.knowledgeSyncSnapshot = null;
            }
            await this.loadKnowledge();
            if (this.knowledgeEnabled && this.knowledgeStatus.has_pending && this.knowledgeStatus.can_sync && this.view === 'knowledge') {
                void this.syncKnowledge();
            } else if (!this.knowledgeStatus.has_pending && !this.knowledgeStatus.needs_attention) {
                this.knowledgeSyncError = '';
            }
        },
        async searchKnowledgeEntities() {
            const request = ++this.knowledgeEntitySearchRequest;
            try {
                const data = await this.api('/api/knowledge/entities?' + new URLSearchParams({ q: this.knowledgeEntitySearch.trim(), limit: '100' }));
                if (request !== this.knowledgeEntitySearchRequest) return;
                this.knowledgeEntityMatches = data.entities || [];
                const entities = new Map(this.knowledgeEntities.map(entity => [entity.id, entity]));
                this.knowledgeEntityMatches.forEach(entity => entities.set(entity.id, entity));
                this.knowledgeEntities = Array.from(entities.values());
            } catch (error) { if (request === this.knowledgeEntitySearchRequest) this.knowledgeError = error.message; }
        },
        knowledgeFocusEntities() {
            return this.knowledgeEntitySearch.trim() ? this.knowledgeEntityMatches : this.knowledgeEntities;
        },
        knowledgeParams(offset = this.knowledgeOffset) {
            const params = new URLSearchParams({ limit: '50', offset: String(offset) });
            Object.entries(this.knowledgeAppliedFilters || this.knowledgeFilters)
                .forEach(([key, value]) => { if (value) params.set(key, String(value).trim()); });
            return params;
        },
        async loadKnowledge(reset = true, background = false) {
            const request = ++this.knowledgeRequest;
            if (!background) this.knowledgeLoading = true;
            this.knowledgeError = '';
            if (reset) {
                this.knowledgeOffset = 0;
                this.knowledgeRelationLimit = 3;
                this.knowledgeAppliedFilters = { ...this.knowledgeFilters };
            }
            try {
                if (!background) {
                    const previousStatus = this.knowledgeStatus;
                    const status = await this.api('/api/knowledge/status');
                    if (request !== this.knowledgeRequest) return;
                    if (!this.knowledgeSyncing && this.knowledgeStatus === previousStatus) this.knowledgeStatus = status;
                    this.knowledgeEnabled = status.enabled !== false;
                }
                if (!this.knowledgeEnabled) return;
                const graphParams = this.knowledgeParams(0);
                graphParams.set('limit', '80');
                // The list remains usable if the map or the entity picker is temporarily unavailable.
                const graphPromise = this.api('/api/knowledge/graph?' + graphParams).catch(() => null);
                const entitiesPromise = this.api('/api/knowledge/entities?limit=100').catch(() => null);
                const [claims, graph, entities] = await Promise.all([
                    this.api('/api/knowledge/claims?' + this.knowledgeParams()), graphPromise, entitiesPromise
                ]);
                if (request !== this.knowledgeRequest) return;
                this.knowledgeNodes = graph?.nodes || [];
                this.knowledgeEdges = graph?.edges || [];
                this.knowledgeTruncated = !!graph?.truncated;
                this.knowledgeMapError = !graph;
                this.knowledgeClaims = claims.claims || [];
                this.knowledgeTotal = claims.total ?? this.knowledgeClaims.length;
                const known = new Map(this.knowledgeEntities.map(entity => [entity.id, entity]));
                (entities?.entities || []).forEach(entity => known.set(entity.id, entity));
                this.knowledgeEntities = Array.from(known.values());
                this.knowledgeHasLoaded = true;
                this.layoutKnowledgeGraph();
            } catch (error) {
                if (request === this.knowledgeRequest) this.knowledgeError = error.message;
            } finally {
                if (request === this.knowledgeRequest) this.knowledgeLoading = false;
            }
        },
        async knowledgePage(direction) {
            this.knowledgeOffset = Math.max(0, this.knowledgeOffset + direction * 50);
            await this.loadKnowledge(false);
        },
        async clearKnowledgeFilters() {
            this.knowledgeFilters = { q: '', entity_id: '', classification: '', review_state: '', status: '', date_from: '', date_to: '', depth: '1' };
            this.knowledgeZoom = 1;
            this.knowledgeEntitySearch = '';
            this.knowledgeEntityMatches = [];
            ++this.knowledgeEntitySearchRequest;
            await this.loadKnowledge();
        },
        async focusKnowledgeEntity(id) {
            this.knowledgeFilters.entity_id = id == null ? '' : String(id);
            this.knowledgeZoom = 1;
            await this.loadKnowledge();
        },
        setKnowledgeMode(mode) {
            this.knowledgeMode = mode;
            if (mode === 'map') this.$nextTick(() => this.renderKnowledgeGraph());
        },
        layoutKnowledgeGraph() {
            // Positions are presentation only. No graph gesture writes semantic relations.
            const rootId = 'entity:' + (this.knowledgeAppliedFilters || this.knowledgeFilters).entity_id;
            const root = this.knowledgeNodes.find(node => String(node.id) === rootId);
            const nodes = this.knowledgeNodes.filter(node => node !== root);
            const rings = Math.max(1, Math.ceil(nodes.length / 16));
            nodes.forEach((node, index) => {
                const ring = Math.floor(index / 16);
                const inRing = Math.min(16, nodes.length - ring * 16);
                const angle = (index % 16) / inRing * Math.PI * 2 - Math.PI / 2 + ring * 0.12;
                const radius = rings === 1 ? 205 : 110 + ring * (205 / Math.max(1, rings - 1));
                node.x = 400 + Math.cos(angle) * radius * 1.07;
                node.y = 300 + Math.sin(angle) * radius * 0.82;
            });
            if (root) { root.x = 400; root.y = 300; }
            const byId = Object.fromEntries(this.knowledgeNodes.map(node => [String(node.id), node]));
            this.knowledgeEdges = this.knowledgeEdges.map(edge => ({ ...edge,
                start: byId[String(edge.source)], end: byId[String(edge.target)]
            })).filter(edge => edge.start && edge.end);
            this.knowledgeShared = this.sharedKnowledgeEntities();
            this.$nextTick(() => this.renderKnowledgeGraph());
        },
        sharedKnowledgeEntities() {
            const references = new Map();
            this.knowledgeEdges.forEach(edge => {
                const entity = edge.start;
                const claim = edge.end;
                if (claim.kind !== 'claim' || !/^(?:meeting|one_on_one):/.test(claim.source_ref || '')) return;
                if (!references.has(entity.id)) references.set(entity.id, { entity, sources: new Map() });
                references.get(entity.id).sources.set(claim.source_ref, this.knowledgeSourceLabel(claim));
            });
            return [...references.values()]
                .filter(item => item.sources.size > 1)
                .sort((a, b) => b.sources.size - a.sources.size || a.entity.label.localeCompare(b.entity.label, 'pt-BR'))
                .slice(0, 4)
                .map(({ entity, sources }) => ({
                    id: entity.record_id, label: entity.label, count: sources.size, titles: [...sources.values()].slice(0, 2)
                }));
        },
        renderKnowledgeGraph() {
            const svg = this.$refs.knowledgeGraph;
            if (!svg) return;
            const create = (name, attrs = {}, content = null) => {
                const element = document.createElementNS('http://www.w3.org/2000/svg', name);
                Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value)));
                if (content != null) element.textContent = String(content);
                return element;
            };
            const keyboard = (element, action) => {
                element.addEventListener('click', action);
                element.addEventListener('keydown', event => {
                    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); action(); }
                });
            };
            const fragment = document.createDocumentFragment();
            const defs = create('defs');
            const arrow = create('marker', { id: 'knowledge-arrow', viewBox: '0 0 10 10', refX: '9', refY: '5',
                markerWidth: '6', markerHeight: '6', orient: 'auto-start-reverse' });
            arrow.append(create('path', { d: 'M 0 0 L 10 5 L 0 10 Z', fill: '#94a3b8' }));
            defs.append(arrow);
            fragment.append(defs);
            this.knowledgeEdges.forEach(edge => {
                const label = this.knowledgeLabel(edge.label) + ': ' + edge.start.label + ' → ' + edge.end.label;
                const group = create('g', { class: 'knowledge-edge', tabindex: '0', role: 'button', 'aria-label': label });
                group.append(create('title', {}, label));
                const dx = edge.end.x - edge.start.x, dy = edge.end.y - edge.start.y;
                const length = Math.max(1, Math.hypot(dx, dy));
                const position = { x1: edge.start.x + dx / length * 25, y1: edge.start.y + dy / length * 25,
                    x2: edge.end.x - dx / length * 27, y2: edge.end.y - dy / length * 27 };
                group.append(create('line', { ...position, class: 'knowledge-edge-hit' }),
                    create('line', { ...position, 'marker-end': 'url(#knowledge-arrow)' }));
                if (edge.relation_id && this.knowledgeEdges.length < 40) group.append(create('text', {
                    x: (edge.start.x + edge.end.x) / 2, y: (edge.start.y + edge.end.y) / 2 - 6,
                    'text-anchor': 'middle', class: 'knowledge-edge-label'
                }, this.knowledgeLabel(edge.label)));
                keyboard(group, () => this.selectKnowledgeEdge(edge));
                fragment.append(group);
            });
            this.knowledgeNodes.forEach(node => {
                const type = node.classification === 'commitment' && node.task_accepted ? 'commitment' : ['perception', 'report'].includes(node.classification) ? 'perception' : node.kind;
                const label = this.knowledgeClaimTypeLabel(node) + ': ' + node.label;
                const group = create('g', { class: 'knowledge-node knowledge-node-' + type,
                    transform: `translate(${node.x},${node.y})`, tabindex: '0', role: 'button', 'aria-label': label,
                    'data-knowledge-id': node.id
                });
                group.append(create('title', {}, label), create('circle', { r: '23' }));
                group.append(create('text', { class: 'knowledge-node-symbol', 'text-anchor': 'middle', y: '5' },
                    node.kind === 'person' ? 'P' : node.kind === 'project' ? 'PR' : node.kind === 'topic' ? '#' : node.classification === 'commitment' && node.task_accepted ? '✓' : '·'));
                group.append(create('text', { class: 'knowledge-node-label', 'text-anchor': 'middle', y: '40' },
                    node.label?.length > 23 ? node.label.slice(0, 22) + '…' : node.label));
                keyboard(group, () => this.selectKnowledgeNode(node));
                fragment.append(group);
            });
            svg.replaceChildren(fragment);
            this.paintKnowledgeSelection();
        },
        paintKnowledgeSelection() {
            const svg = this.$refs.knowledgeGraph;
            if (!svg) return;
            svg.querySelectorAll('[data-knowledge-id]').forEach(node => {
                node.classList.toggle('is-selected', node.getAttribute('data-knowledge-id') === String(this.knowledgeSelected?.id));
            });
        },
        knowledgeViewBox() {
            const width = 800 / this.knowledgeZoom, height = 600 / this.knowledgeZoom;
            return `${400 - width / 2} ${300 - height / 2} ${width} ${height}`;
        },
        knowledgeChangeZoom(step) {
            this.knowledgeZoom = Math.max(0.6, Math.min(2.5, this.knowledgeZoom + step));
        },
        async selectKnowledgeNode(node) {
            ++this.knowledgeSelectionRequest;
            this.knowledgeRelationDetail = null;
            this.knowledgeDetailError = '';
            this.knowledgeEntityForm = null;
            this.knowledgeEditing = false;
            this.knowledgeSelected = { ...node };
            this.paintKnowledgeSelection();
            this.knowledgeDetail = null;
            this.knowledgeReason = '';
            this.knowledgeResolveConflict = false;
            this.knowledgeRelationForm = null;
            if (String(node.id).startsWith('claim:') || node.kind === 'claim') {
                await this.selectKnowledgeClaim(node.record_id || String(node.id).split(':')[1]);
            } else {
                const entity = this.knowledgeEntities.find(item => Number(item.id) === Number(node.record_id || String(node.id).split(':')[1]));
                if (entity) this.knowledgeSelected = { ...node, ...entity, graph_id: node.id, id: node.id, record_id: entity.id };
                this.$nextTick(() => this.$refs.knowledgeInspector?.focus());
            }
        },
        async selectKnowledgeClaim(id) {
            const request = ++this.knowledgeSelectionRequest;
            this.knowledgeDetailLoading = true;
            this.knowledgeDetailError = '';
            this.knowledgeEntityForm = null;
            this.knowledgeRelationForm = null;
            this.knowledgeRelationDetail = null;
            this.knowledgeDetail = null;
            this.knowledgeEditing = false;
            this.knowledgeSelected = { id: `claim:${id}`, kind: 'claim', record_id: Number(id) };
            this.paintKnowledgeSelection();
            try {
                const detail = await this.api('/api/knowledge/claims/' + id);
                if (request !== this.knowledgeSelectionRequest) return;
                this.knowledgeDetail = detail;
                const claim = detail.claim;
                this.knowledgeForm = {
                    content: claim.content || '', classification: claim.classification,
                    review_state: claim.review_state || 'pending',
                    speaker_entity_id: claim.speaker_entity_id == null ? '' : String(claim.speaker_entity_id),
                    subject_entity_id: claim.subject_entity_id == null ? '' : String(claim.subject_entity_id),
                    owner_entity_id: claim.owner_entity_id == null ? '' : String(claim.owner_entity_id),
                    entity_ids: (claim.entity_ids || []).map(String),
                    due_date: claim.due_date || '', status: claim.status || claim.operational_state || 'none',
                    task_accepted: !!claim.task_accepted
                };
                this.knowledgeOriginal = JSON.parse(JSON.stringify(this.knowledgeForm));
                this.knowledgeOriginalTaskSelection = claim.commitment_id ? 'commitment:' + claim.commitment_id : claim.meeting_action_id ? 'meeting_action:' + claim.meeting_action_id : '';
                this.resetKnowledgeTaskOptions();
                this.knowledgeReason = '';
                this.knowledgeResolveConflict = false;
                this.knowledgeSelected.label = claim.content;
                this.$nextTick(() => this.$refs.knowledgeInspector?.focus());
            } catch (error) {
                if (request === this.knowledgeSelectionRequest) this.knowledgeDetailError = error.message;
            } finally {
                if (request === this.knowledgeSelectionRequest) this.knowledgeDetailLoading = false;
            }
        },
        async expandKnowledgeEntity() {
            if (!this.knowledgeSelected || this.knowledgeSelected.kind === 'claim') return;
            const id = this.knowledgeSelected.record_id;
            this.closeKnowledgeInspector();
            await this.focusKnowledgeEntity(id);
        },
        closeKnowledgeInspector() {
            ++this.knowledgeSelectionRequest;
            this.knowledgeSelected = null;
            this.paintKnowledgeSelection();
            this.knowledgeDetail = null;
            this.knowledgeDetailError = '';
            this.knowledgeEntityForm = null;
            this.knowledgeRelationForm = null;
            this.knowledgeRelationDetail = null;
            this.knowledgeDetailLoading = false;
            this.knowledgeEditing = false;
        },
        openKnowledgeEditor() {
            this.knowledgeEditing = true;
            this.$nextTick(() => (this.knowledgeRelationForm ? this.$refs.knowledgeRelationFrom : this.$refs.knowledgeClaimText)?.focus());
        },
        async saveKnowledgeClaim(reviewState = null) {
            if (!this.knowledgeDetail?.claim || this.knowledgeSaving) return;
            const claim = this.knowledgeDetail.claim;
            const form = { ...this.knowledgeForm };
            if (reviewState) form.review_state = reviewState;
            if (!form.content.trim()) { this.knowledgeDetailError = 'Informe o conteúdo interpretado.'; return; }
            const payload = { version: claim.version, reason: this.knowledgeReason.trim() || null };
            Object.entries(form).forEach(([key, value]) => {
                if (JSON.stringify(value) !== JSON.stringify(this.knowledgeOriginal[key])) {
                    payload[key] = key.endsWith('_entity_id') ? (value ? Number(value) : null)
                        : key === 'entity_ids' ? value.map(Number) : key === 'due_date' ? value || null : value;
                }
            });
            if (this.knowledgeTaskSelection && this.knowledgeTaskSelection !== this.knowledgeOriginalTaskSelection) {
                const task = this.knowledgeTaskOptions.find(item => item.kind + ':' + item.id === this.knowledgeTaskSelection);
                if (!task || this.knowledgeTaskOwnerId !== this.knowledgeForm.owner_entity_id) {
                    this.knowledgeDetailError = 'Atualize a lista de tarefas do responsável antes de vincular.';
                    return;
                }
                payload.task_ref = { kind: task.kind, id: task.id, version: task.version };
            }
            if (this.knowledgeResolveConflict) payload.resolve_conflict = true;
            if (Object.keys(payload).length <= 2) { this.knowledgeDetailError = 'Altere um campo ou o estado de revisão antes de salvar.'; return; }
            this.knowledgeSaving = true;
            this.knowledgeDetailError = '';
            try {
                await this.api('/api/knowledge/claims/' + claim.id, { method: 'PUT', body: JSON.stringify(payload) });
                await this.selectKnowledgeClaim(claim.id);
                await this.loadKnowledge(false);
                this.knowledgeRefreshConsumers();
                showToast('Memória atualizada. A fonte original foi preservada.', 'success');
            } catch (error) {
                this.knowledgeDetailError = error.message + (error.data?.current || error.data?.conflict ? ' Recarregue o registro e compare as alterações antes de salvar novamente.' : '');
            } finally { this.knowledgeSaving = false; }
        },
        resetKnowledgeTaskOptions() {
            ++this.knowledgeTaskRequest;
            this.knowledgeTaskOptions = [];
            this.knowledgeTaskSelection = this.knowledgeOriginalTaskSelection;
            this.knowledgeTaskOwnerId = '';
            this.knowledgeTaskNextOffset = null;
            this.knowledgeTaskTotal = 0;
            this.knowledgeTaskLoading = false;
            this.knowledgeTaskError = '';
        },
        knowledgeTaskLabel(task) {
            return (task.kind === 'commitment' ? 'Combinado' : 'Ação de reunião') + ' ' + task.id + ' · ' + task.description + ' · prazo ' + this.knowledgeDate(task.due_date);
        },
        async loadKnowledgeTasks(more = false) {
            if (this.knowledgeTaskLoading) return;
            const owner = this.knowledgeForm.owner_entity_id;
            const entity = this.knowledgeEntities.find(item => String(item.id) === String(owner));
            if (!entity?.person_id) { this.knowledgeTaskError = 'Escolha uma pessoa cadastrada como responsável para consultar suas tarefas.'; return; }
            const request = ++this.knowledgeTaskRequest;
            const selectionRequest = this.knowledgeSelectionRequest;
            this.knowledgeTaskLoading = true;
            this.knowledgeTaskError = '';
            if (!more) { this.knowledgeTaskOptions = []; this.knowledgeTaskSelection = this.knowledgeOriginalTaskSelection; }
            try {
                const data = await this.api('/api/knowledge/tasks?' + new URLSearchParams({
                    person_id: String(entity.person_id), status: 'open', limit: '200',
                    offset: String(more ? this.knowledgeTaskNextOffset || 0 : 0)
                }));
                if (request !== this.knowledgeTaskRequest || selectionRequest !== this.knowledgeSelectionRequest || this.knowledgeForm.owner_entity_id !== owner) return;
                const options = new Map(this.knowledgeTaskOptions.map(task => [task.kind + ':' + task.id, task]));
                (data.tasks || []).forEach(task => options.set(task.kind + ':' + task.id, task));
                this.knowledgeTaskOptions = Array.from(options.values());
                this.knowledgeTaskOwnerId = owner;
                this.knowledgeTaskNextOffset = data.next_offset ?? null;
                this.knowledgeTaskTotal = data.total ?? this.knowledgeTaskOptions.length;
            } catch (error) {
                if (request === this.knowledgeTaskRequest) this.knowledgeTaskError = error.message;
            } finally {
                if (request === this.knowledgeTaskRequest) this.knowledgeTaskLoading = false;
            }
        },
        knowledgeRefreshConsumers() {
            this.cardsLoaded = false;
            if (this.view === 'prep' && this.prepPersonId) {
                this.loadPrep();
            } else {
                this.prep = null;
                this.agendaMd = null;
            }
            if (this.currentPerson) this.loadPersonData();
            this.loadDashboard();
        },
        knowledgeHasConflict() {
            const detail = this.knowledgeDetail;
            return !!(detail?.claim?.conflict && Object.keys(detail.claim.conflict).length) || !!detail?.conflicts?.length;
        },
        async openKnowledgeFromAssistant(source) {
            const id = source?.record_id || String(source?.id || '').split(':')[1];
            if (!id) return;
            await this.goKnowledge();
            if (source.type === 'relation' || String(source.id || '').startsWith('relation:')) await this.selectKnowledgeRelation(id);
            else await this.selectKnowledgeClaim(id);
            this.closeAssistant();
        },
        async openKnowledgeEvidence(evidence = this.knowledgeDetail?.evidence) {
            if (!evidence?.record_id) return;
            if (evidence.source_kind === 'meeting' || evidence.kind === 'meeting') await this.viewMeeting(evidence.record_id);
            else await this.viewSession(evidence.record_id);
        },
        editKnowledgeEntity(entity = null) {
            this.knowledgeDetailError = '';
            this.knowledgeRelationForm = null;
            this.knowledgeEntityForm = entity ? {
                id: entity.record_id || entity.id, version: entity.version, kind: entity.kind,
                label: entity.label, aliases: (entity.aliases || []).join('\n'), reason: ''
            } : { kind: 'topic', label: '', aliases: '', reason: '' };
            this.$nextTick(() => this.$refs.knowledgeInspector?.focus());
        },
        async saveKnowledgeEntity() {
            if (this.knowledgeSaving || !this.knowledgeEntityForm) return;
            const form = this.knowledgeEntityForm;
            if (!form.label.trim()) { this.knowledgeDetailError = 'Informe o nome da entidade.'; return; }
            this.knowledgeSaving = true;
            this.knowledgeDetailError = '';
            try {
                const payload = { label: form.label.trim(), aliases: form.aliases.split('\n').map(value => value.trim()).filter(Boolean), reason: form.reason?.trim() || null };
                if (form.id) payload.version = form.version;
                else payload.kind = form.kind;
                await this.api('/api/knowledge/entities' + (form.id ? '/' + form.id : ''), { method: form.id ? 'PUT' : 'POST', body: JSON.stringify(payload) });
                this.knowledgeEntityForm = null;
                this.closeKnowledgeInspector();
                await this.loadKnowledge();
                showToast('Entidade salva.', 'success');
            } catch (error) { this.knowledgeDetailError = error.message; }
            finally { this.knowledgeSaving = false; }
        },
        openKnowledgeNote() {
            this.knowledgeNote = { content: '', classification: 'declaration', subject_entity_id: this.knowledgeFilters.entity_id || '', speaker_entity_id: '', entity_ids: [], reason: '' };
            this.knowledgeNewNote = true;
            this.knowledgeDetailError = '';
            this.$nextTick(() => {
                if (this.$refs.knowledgeNoteOptions) this.$refs.knowledgeNoteOptions.open = false;
                this.$refs.knowledgeNoteText?.focus();
            });
        },
        async saveKnowledgeNote() {
            if (this.knowledgeSaving) return;
            if (!this.knowledgeNote.content.trim()) { this.knowledgeDetailError = 'Escreva a observação manual.'; return; }
            this.knowledgeSaving = true;
            this.knowledgeDetailError = '';
            try {
                const note = this.knowledgeNote;
                const data = await this.api('/api/knowledge/claims', { method: 'POST', body: JSON.stringify({
                    ...note, subject_entity_id: note.subject_entity_id ? Number(note.subject_entity_id) : null,
                    speaker_entity_id: note.speaker_entity_id ? Number(note.speaker_entity_id) : null,
                    entity_ids: note.entity_ids.map(Number), reason: note.reason.trim() || null
                }) });
                this.knowledgeNewNote = false;
                await this.loadKnowledge();
                if (data.claim?.id || data.id) await this.selectKnowledgeClaim(data.claim?.id || data.id);
                this.knowledgeRefreshConsumers();
                showToast('Observação registrada com proveniência humana.', 'success');
            } catch (error) { this.knowledgeDetailError = error.message; }
            finally { this.knowledgeSaving = false; }
        },
        editKnowledgeRelation(relation = null, claimId = null) {
            this.knowledgeDetailError = '';
            if (relation?.id) return this.selectKnowledgeRelation(relation.id, relation);
            this.knowledgeEntityForm = null;
            this.knowledgeRelationDetail = null;
            this.knowledgeRelationForm = { from_entity_id: this.knowledgeFilters.entity_id || '', to_entity_id: '',
                relation_type: 'related_to', review_state: 'accepted', claim_id: claimId, reason: '' };
            this.$nextTick(() => this.$refs.knowledgeInspector?.focus());
        },
        async selectKnowledgeRelation(id, initial = null) {
            const request = ++this.knowledgeSelectionRequest;
            this.knowledgeDetailLoading = true;
            this.knowledgeDetailError = '';
            this.knowledgeEntityForm = null;
            this.knowledgeRelationDetail = null;
            this.knowledgeDetail = null;
            this.knowledgeEditing = false;
            this.knowledgeSelected = { kind: 'relation', id: 'relation:' + id, record_id: Number(id), label: initial?.relation_type };
            this.paintKnowledgeSelection();
            const form = relation => ({ ...relation, from_entity_id: String(relation.from_entity_id),
                to_entity_id: String(relation.to_entity_id), reason: '' });
            if (Number(this.knowledgeRelationForm?.id) !== Number(id)) this.knowledgeRelationForm = initial ? form(initial) : null;
            const draft = JSON.stringify(this.knowledgeRelationForm);
            try {
                const detail = await this.api('/api/knowledge/relations/' + id);
                if (request !== this.knowledgeSelectionRequest) return;
                this.knowledgeRelationDetail = detail;
                // Fetching evidence/history must not discard edits typed while waiting.
                if (JSON.stringify(this.knowledgeRelationForm) === draft) this.knowledgeRelationForm = form(detail.relation);
                this.$nextTick(() => this.$refs.knowledgeInspector?.focus());
            } catch (error) {
                if (request === this.knowledgeSelectionRequest) this.knowledgeDetailError = error.message;
            } finally {
                if (request === this.knowledgeSelectionRequest) this.knowledgeDetailLoading = false;
            }
        },
        async selectKnowledgeEdge(edge) {
            if (edge.relation_id) {
                const relation = edge.relation || edge;
                await this.selectKnowledgeRelation(edge.relation_id, { ...relation, id: edge.relation_id,
                    from_entity_id: relation.from_entity_id || String(edge.source).split(':')[1],
                    to_entity_id: relation.to_entity_id || String(edge.target).split(':')[1],
                    relation_type: relation.relation_type || edge.label, review_state: relation.review_state || 'pending' });
            } else {
                const claimNode = [edge.source, edge.target].find(id => String(id).startsWith('claim:'));
                if (claimNode) await this.selectKnowledgeClaim(String(claimNode).split(':')[1]);
            }
        },
        async saveKnowledgeRelation(reviewState = null) {
            if (this.knowledgeSaving || !this.knowledgeRelationForm) return;
            const form = this.knowledgeRelationForm;
            if (!form.from_entity_id || !form.to_entity_id || !form.relation_type.trim()) { this.knowledgeDetailError = 'Informe as duas entidades e o significado da relação.'; return; }
            this.knowledgeSaving = true;
            this.knowledgeDetailError = '';
            try {
                const payload = {
                    from_entity_id: Number(form.from_entity_id), to_entity_id: Number(form.to_entity_id),
                    relation_type: form.relation_type.trim(), review_state: reviewState || form.review_state,
                    reason: form.reason?.trim() || null
                };
                if (form.id) payload.version = form.version;
                else if (form.claim_id) payload.claim_id = form.claim_id;
                const data = await this.api('/api/knowledge/relations' + (form.id ? '/' + form.id : ''), { method: form.id ? 'PUT' : 'POST', body: JSON.stringify(payload) });
                this.knowledgeRelationForm = null;
                await this.loadKnowledge(false);
                if (data.relation?.id || form.id) await this.selectKnowledgeRelation(data.relation?.id || form.id, data.relation);
                this.knowledgeRefreshConsumers();
                showToast(reviewState === 'rejected' ? 'Relação rejeitada e retirada do conhecimento ativo.' : 'Relação salva.', 'success');
            } catch (error) { this.knowledgeDetailError = error.message; }
            finally { this.knowledgeSaving = false; }
        },
        knowledgeSyncValue() {
            const progress = this.knowledgeSyncSnapshot;
            return progress ? this.knowledgeSyncTotal - progress.remaining - progress.failed + (progress.partial || 0) : 0;
        },
        knowledgeSyncQueueLabel() {
            if (!this.knowledgeSyncTotal) return '';
            const percent = Math.floor(this.knowledgeSyncValue() * 1000 / this.knowledgeSyncTotal) / 10;
            const label = `${percent.toLocaleString('pt-BR')}% do processamento`;
            const remaining = this.knowledgeSyncSnapshot?.remaining;
            if (remaining === 1) return `${label} · 1 conversa a concluir`;
            if (remaining > 1) return `${label} · ${remaining} conversas a concluir`;
            return label;
        },
        knowledgeSyncMessage() {
            const stage = this.knowledgeSyncStage;
            if (this.knowledgeSyncing) {
                const active = this.knowledgeSyncSnapshot?.active;
                if (stage === 'refreshing') return 'Carregando as conexões atualizadas…';
                if (active?.phase === 'publishing') return 'Salvando as conexões na memória…';
                if (active) return 'Analisando o conteúdo da conversa…';
                if (stage === 'waiting') return 'Aguardando a próxima tentativa…';
                if (stage === 'checking') return 'Verificando conversas pendentes…';
                return 'Preparando a próxima conversa…';
            }
            if (this.knowledgeSyncError) return this.knowledgeSyncError;
            const status = this.knowledgeStatus;
            if (!status.can_sync && (status.needs_attention || stage === 'partial'))
                return 'A atualização automática está indisponível.';
            if (stage === 'partial' || status.needs_attention) return 'Atualização com pendências.';
            if (stage === 'complete') return this.knowledgeSyncTotal ? 'Memória atualizada.' : 'A memória já está atualizada.';
            return '';
        },
        knowledgeSyncDetail() {
            const progress = this.knowledgeSyncSnapshot || this.knowledgeStatus.sync_progress;
            const active = progress?.active;
            if (this.knowledgeSyncing && active && this.knowledgeSyncStage !== 'refreshing') {
                const kind = active.kind === 'one_on_one' ? '1:1' : 'Reunião';
                const title = active.title && active.title !== kind ? active.title : '';
                const segment = active.phase === 'extracting' && active.segments_total > 1
                    ? `Trecho ${Math.min(active.segments_completed + 1, active.segments_total)} de ${active.segments_total}` : '';
                return [kind, title, segment].filter(Boolean).join(' · ');
            }
            if (progress?.failed === 1) return '1 conversa precisa de nova tentativa.';
            if (progress?.failed > 1) return `${progress.failed} conversas precisam de nova tentativa.`;
            return '';
        },
        updateKnowledgeSyncProgress(state) {
            const progress = state.sync_progress;
            if (!progress) return;
            this.knowledgeSyncSnapshot = progress;
            this.knowledgeSyncTotal = Math.max(this.knowledgeSyncTotal, progress.remaining + progress.failed);
        },
        async requestKnowledgeSync(retry) {
            let inFlight = true;
            const poll = async () => {
                if (!inFlight || this.view !== 'knowledge') return;
                try {
                    const state = await this.api('/api/knowledge/sync', { cache: 'no-store' });
                    if (inFlight && this.view === 'knowledge') this.updateKnowledgeSyncProgress(state);
                } catch { /* The write response remains authoritative if a progress read fails. */ }
                if (inFlight && this.view === 'knowledge') timer = setTimeout(poll, 1500);
            };
            let timer = setTimeout(poll, 800);
            try {
                return await this.api('/api/knowledge/sync', {
                    method: 'POST', body: JSON.stringify(retry ? { retry: true } : {})
                });
            } finally {
                inFlight = false;
                clearTimeout(timer);
            }
        },
        async syncKnowledge(retry = false) {
            if (this.knowledgeSyncing || !this.knowledgeEnabled || this.view !== 'knowledge') return;
            this.knowledgeSyncing = true;
            this.knowledgeSyncError = '';
            this.knowledgeSyncStage = 'checking';
            this.knowledgeSyncTotal = 0;
            this.knowledgeSyncSnapshot = null;
            this.updateKnowledgeSyncProgress(this.knowledgeStatus);
            let changed = false;
            try {
                while (this.view === 'knowledge') {
                    const result = await this.requestKnowledgeSync(retry);
                    retry = false;
                    changed ||= !!result.updated;
                    this.knowledgeStatus = { ...this.knowledgeStatus, ...result };
                    this.updateKnowledgeSyncProgress(result);
                    if (this.view !== 'knowledge') break;
                    if (result.updated || !result.has_pending) {
                        this.knowledgeSyncStage = 'refreshing';
                        await this.loadKnowledge(false, true);
                    }
                    if (!result.has_pending || !result.can_sync) {
                        this.knowledgeSyncStage = !result.can_sync || result.needs_attention ? 'partial' : 'complete';
                        break;
                    }
                    this.knowledgeSyncStage = result.retry_after_ms ? 'waiting' : 'preparing';
                    const wait = Number(result.retry_after_ms) || (result.updated ? 250 : 1500);
                    await new Promise(resolve => setTimeout(resolve, Math.min(30000, Math.max(200, wait))));
                    this.knowledgeSyncStage = 'preparing';
                }
            } catch {
                this.knowledgeSyncError = 'Não foi possível atualizar a memória agora.';
            } finally {
                this.knowledgeSyncing = false;
                if (changed) this.knowledgeRefreshConsumers();
            }
        }
    };
}
