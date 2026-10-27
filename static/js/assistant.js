/* Assistente Cortex: conversa, memórias persistentes e componentes visuais sem HTML gerado pela IA. */
function cortexAssistant() {
    return {
        assistantOpen: false,
        assistantExpanded: false,
        assistantBusy: false,
        assistantDraft: '',
        assistantMessages: [],
        assistantError: '',
        assistantLastRequest: null,
        assistantLastUserId: null,
        assistantSelectedSource: null,
        assistantSourceTrigger: null,
        assistantActionBusy: '',
        assistantMemoriesOpen: false,
        assistantMemories: [],
        assistantMemoriesLoading: false,
        assistantMemoriesError: '',
        assistantMemoriesStatus: '',
        assistantMemoriesRequestSequence: 0,
        assistantMemoryDeleteId: null,
        assistantSequence: 0,
        toggleAssistant() {
            if (this.assistantOpen) return this.closeAssistant();
            this.assistantOpen = true;
            this.$nextTick(() => this.$refs.assistantInput.focus());
        },

        closeAssistant() {
            this.assistantOpen = false;
            this.assistantSelectedSource = null;
            this.assistantMemoriesOpen = false;
            this.$nextTick(() => this.$refs.assistantLauncher.focus());
        },

        assistantEscape(event) {
            if (!this.assistantOpen) return;
            // O assistente só trata Escape quando o foco está nele.
            if (!this.$refs.assistantPanel.contains(event.target)) return;
            event.stopPropagation();
            if (this.assistantSelectedSource) this.closeAssistantSource();
            else if (this.assistantMemoriesOpen) this.closeAssistantMemories();
            else this.closeAssistant();
        },

        assistantTrapFocus(event) {
            const focusables = [...this.$refs.assistantPanel.querySelectorAll(
                'button:not([disabled]), a[href], textarea:not([disabled]), summary, [tabindex="0"]'
            )].filter(element => element.getClientRects().length);
            const first = focusables[0];
            const last = focusables[focusables.length - 1];
            if (!first) return;
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        },

        clearAssistant() {
            if (this.assistantBusy || this.assistantActionBusy || this.assistantMemoryDeleteId) return;
            this.assistantMessages = [];
            this.assistantError = '';
            this.assistantLastRequest = null;
            this.assistantLastUserId = null;
            this.assistantSelectedSource = null;
            this.assistantMemoriesOpen = false;
            this.assistantDraft = '';
            this.$nextTick(() => this.$refs.assistantInput.focus());
        },

        scrollAssistant() {
            this.$nextTick(() => {
                const feed = this.$refs.assistantFeed;
                feed.scrollTop = feed.scrollHeight;
            });
        },

        async assistantRequest(path, payload, method = 'POST') {
            const response = await fetch(path, {
                method, credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                ...(payload === undefined ? {} : { body: JSON.stringify(payload) })
            });
            let data = {};
            try { data = await response.json(); } catch (_) { /* Uma falha pode não ter corpo JSON. */ }
            if (!response.ok) {
                throw new Error(response.status === 401
                    ? 'Sua sessão expirou. Entre novamente no Cortex para continuar.'
                    : (data.error || 'Não foi possível consultar o assistente. Tente novamente.'));
            }
            return data;
        },

        toggleAssistantMemories() {
            if (this.assistantMemoriesOpen) return this.closeAssistantMemories();
            this.assistantMemoriesOpen = true;
            this.assistantSelectedSource = null;
            this.assistantMemoriesStatus = '';
            this.$nextTick(() => this.$refs.assistantMemoriesClose.focus());
            this.loadAssistantMemories();
        },

        closeAssistantMemories() {
            this.assistantMemoriesOpen = false;
            this.$nextTick(() => this.$refs.assistantMemoriesButton.focus());
        },

        async loadAssistantMemories() {
            const sequence = ++this.assistantMemoriesRequestSequence;
            this.assistantMemoriesLoading = true;
            this.assistantMemoriesError = '';
            try {
                const data = await this.assistantRequest('/api/assistant/memories', undefined, 'GET');
                if (sequence === this.assistantMemoriesRequestSequence) {
                    this.assistantMemories = Array.isArray(data.memories) ? data.memories : [];
                }
            } catch (error) {
                if (sequence === this.assistantMemoriesRequestSequence) this.assistantMemoriesError = error.message;
            } finally {
                if (sequence === this.assistantMemoriesRequestSequence) this.assistantMemoriesLoading = false;
            }
        },

        async forgetAssistantMemory(memory) {
            if (this.assistantMemoryDeleteId || this.assistantBusy) return;
            this.assistantMemoryDeleteId = memory.id;
            this.assistantMemoriesError = '';
            this.assistantMemoriesStatus = '';
            try {
                const data = await this.assistantRequest('/api/assistant/memories/' + memory.id, {}, 'DELETE');
                // Ignora uma listagem iniciada antes da exclusão.
                this.assistantMemoriesRequestSequence++;
                this.assistantMemoriesLoading = false;
                this.assistantMemories = this.assistantMemories.filter(item => item.id !== memory.id);
                this.assistantMemoriesStatus = data.message || 'Memória removida.';
                this.$nextTick(() => this.$refs.assistantMemoriesClose.focus());
            } catch (error) {
                this.assistantMemoriesError = error.message;
            } finally {
                this.assistantMemoryDeleteId = null;
            }
        },

        assistantHistoryContent(message) {
            if (message.role === 'user') return message.content;
            const text = (value, limit) => String(value ?? '').slice(0, limit);
            const refs = value => Array.isArray(value) ? value.map(String).slice(0, 80) : [];
            const payload = {
                answer: text(message.content, 16000),
                blocks: (message.blocks || []).map(block => {
                    const result = { type: block.type, title: text(block.title, 200), source_ids: refs(block.source_ids) };
                    if (block.type === 'table') {
                        result.columns = block.columns.map(column => text(column, 120));
                        result.rows = block.rows.map(row => row.map(cell => text(cell, 600)));
                    } else if (block.type === 'org_chart') {
                        result.nodes = block.nodes.map(node => ({
                            id: node.id, label: text(node.label, 200), subtitle: text(node.subtitle, 300),
                            parent_id: node.parent_id, source_ids: refs(node.source_ids)
                        }));
                    } else if (block.type === 'bar_chart') {
                        result.unit = text(block.unit, 80);
                        result.items = block.items.map(item => ({ label: text(item.label, 200), value: item.value }));
                    }
                    return result;
                }),
                sources: (message.sources || []).map(source => ({
                    id: source.id, type: source.type, title: text(source.title, 200),
                    ...(source.person_id != null ? { person_id: source.person_id } : {}),
                    ...(source.record_id != null ? { record_id: source.record_id } : {}),
                    ...(source.meeting_id != null ? { meeting_id: source.meeting_id } : {})
                })),
                actions: (message.actions || []).map(action => ({
                    label: text(action.label, 240), description: text(action.description, 2000),
                    state: action.state, result: text(action.result, 1000),
                    ...(action.resultDetails ? { result_details: action.resultDetails } : {})
                }))
            };
            // Preserva IDs e a ordem visual para referências como "o primeiro da tabela".
            // O histórico não inclui tokens de execução, trechos das fontes ou posições do gráfico.
            let serialized = JSON.stringify(payload);
            if (serialized.length > 16000) {
                payload.answer = text(payload.answer, Math.max(500, 15500 - (serialized.length - payload.answer.length)));
                serialized = JSON.stringify(payload);
            }
            while (serialized.length > 16000) {
                const collections = payload.blocks.flatMap(block => ['rows', 'nodes', 'items']
                    .filter(key => block[key]?.length > 1)
                    .map(key => ({ block, key, size: JSON.stringify(block[key]).length })))
                    .sort((a, b) => b.size - a.size);
                if (!collections.length) break;
                const { block, key } = collections[0];
                block[key].pop();
                block['omitted_' + key] = (block['omitted_' + key] || 0) + 1;
                serialized = JSON.stringify(payload);
            }
            if (serialized.length > 16000) {
                payload.answer = text(payload.answer, 500);
                payload.blocks.forEach(block => {
                    if (block.rows) block.rows = block.rows.map(row => row.map(cell => text(cell, 120)));
                    if (block.nodes) block.nodes.forEach(node => { node.label = text(node.label, 80); node.subtitle = text(node.subtitle, 80); });
                });
                payload.sources.forEach(source => { source.title = text(source.title, 60); });
                payload.actions.forEach(action => { action.description = text(action.description, 400); action.result = text(action.result, 400); });
                serialized = JSON.stringify(payload);
            }
            while (serialized.length > 16000 && payload.sources.length > 1) {
                payload.sources.pop();
                payload.omitted_sources = (payload.omitted_sources || 0) + 1;
                serialized = JSON.stringify(payload);
            }
            while (serialized.length > 16000 && payload.blocks.length) {
                payload.blocks.pop();
                payload.omitted_blocks = (payload.omitted_blocks || 0) + 1;
                serialized = JSON.stringify(payload);
            }
            return serialized;
        },

        assistantHistory() {
            const result = [];
            let size = 0;
            const recent = this.assistantMessages.filter(message => !message.failed && ['user', 'assistant'].includes(message.role)).slice(-20);
            for (let index = recent.length - 1; index >= 0; index--) {
                const message = recent[index];
                const content = this.assistantHistoryContent(message);
                if (size + content.length > 40000) break;
                result.unshift({ role: message.role, content });
                size += content.length;
            }
            return result;
        },

        async sendAssistant(prompt = null, retry = false) {
            if (this.assistantBusy || this.assistantActionBusy || this.assistantMemoryDeleteId) return;
            const content = (prompt === null ? this.assistantDraft : prompt).trim();
            if (!content || content.length > 6000) return;
            if (!retry) {
                const history = this.assistantHistory();
                this.assistantMessages.push({ id: ++this.assistantSequence, role: 'user', content });
                this.assistantLastUserId = this.assistantSequence;
                this.assistantLastRequest = { message: content, history };
            }
            this.assistantDraft = '';
            this.assistantError = '';
            this.assistantBusy = true;
            this.assistantSelectedSource = null;
            this.scrollAssistant();
            try {
                const data = await this.assistantRequest('/api/assistant/chat', this.assistantLastRequest);
                const userMessage = this.assistantMessages.find(message => message.id === this.assistantLastUserId);
                if (userMessage) userMessage.failed = false;
                this.assistantMessages.push({
                    id: ++this.assistantSequence,
                    role: 'assistant', content: String(data.answer || ''),
                    blocks: this.normalizeAssistantBlocks(data.blocks),
                    sources: Array.isArray(data.sources) ? data.sources : [],
                    actions: Array.isArray(data.actions) ? data.actions.map(action => ({ ...action, state: 'pending', error: '' })) : [],
                    prompts: Array.isArray(data.suggested_prompts) ? data.suggested_prompts.filter(p => typeof p === 'string').slice(0, 3) : [],
                    warnings: Array.isArray(data.warnings) ? data.warnings.filter(w => typeof w === 'string') : [],
                    memoryChanges: Array.isArray(data.memory_changes) ? data.memory_changes.filter(change =>
                        change && ['saved', 'updated'].includes(change.status) && typeof change.subject === 'string') : [],
                    coverage: data.coverage || {}
                });
                if (this.assistantMemoriesOpen && data.memory_changes?.length) this.loadAssistantMemories();
                this.assistantLastRequest = null;
            } catch (error) {
                const userMessage = this.assistantMessages.find(message => message.id === this.assistantLastUserId);
                if (userMessage) userMessage.failed = true;
                this.assistantError = error.message;
            } finally {
                this.assistantBusy = false;
                this.scrollAssistant();
                if (this.assistantOpen && !this.assistantMemoriesOpen) this.$nextTick(() => this.$refs.assistantInput.focus());
            }
        },

        retryAssistant() {
            if (this.assistantLastRequest) this.sendAssistant(this.assistantLastRequest.message, true);
        },

        normalizeAssistantBlocks(blocks) {
            if (!Array.isArray(blocks)) return [];
            return blocks.slice(0, 8).flatMap(block => {
                if (!block || typeof block !== 'object') return [];
                if (block.type === 'org_chart' && Array.isArray(block.nodes)) {
                    const graph = this.assistantOrgGraph(block.nodes);
                    return graph.nodes.length ? [{ ...block, graph }] : [];
                }
                if (block.type === 'table' && Array.isArray(block.columns) && Array.isArray(block.rows)) {
                    return [{ ...block, columns: block.columns.slice(0, 12), rows: block.rows.filter(Array.isArray).slice(0, 100) }];
                }
                if (block.type === 'bar_chart' && Array.isArray(block.items)) {
                    const items = block.items.filter(item => item && typeof item.value === 'number' && Number.isFinite(item.value) && item.value >= 0).slice(0, 40);
                    const max = Math.max(1, ...items.map(item => item.value));
                    return [{ ...block, items: items.map(item => ({ ...item, percent: item.value / max * 100 })) }];
                }
                return [];
            });
        },

        assistantOrgGraph(rawNodes) {
            const nodes = [];
            const byId = new Map();
            rawNodes.slice(0, 80).forEach(raw => {
                if (!raw || raw.id == null || byId.has(String(raw.id))) return;
                const node = {
                    id: String(raw.id), label: String(raw.label || ''), subtitle: String(raw.subtitle || ''),
                    parent_id: raw.parent_id == null ? null : String(raw.parent_id),
                    source_ids: Array.isArray(raw.source_ids) ? raw.source_ids.map(String) : [], children: []
                };
                byId.set(node.id, node);
                nodes.push(node);
            });
            // Dados incompletos ou cíclicos continuam legíveis como raízes separadas.
            nodes.forEach(node => {
                const seen = new Set([node.id]);
                let parent = byId.get(node.parent_id);
                while (parent) {
                    if (seen.has(parent.id)) { node.parent_id = null; break; }
                    seen.add(parent.id);
                    parent = byId.get(parent.parent_id);
                }
                if (!byId.has(node.parent_id)) node.parent_id = null;
            });
            nodes.forEach(node => {
                if (node.parent_id) byId.get(node.parent_id).children.push(node);
            });
            const roots = nodes.filter(node => !node.parent_id);
            const width = 184, gap = 24, height = 100, level = 148, padding = 18;
            const measure = node => {
                node.span = Math.max(width, node.children.reduce((sum, child) => sum + measure(child), 0) + Math.max(0, node.children.length - 1) * gap);
                return node.span;
            };
            roots.forEach(measure);
            let totalWidth = padding;
            let maxDepth = 0;
            const place = (node, start, depth) => {
                node.x = start + (node.span - width) / 2;
                node.y = padding + depth * level;
                node.parentLabel = byId.get(node.parent_id)?.label || '';
                maxDepth = Math.max(maxDepth, depth);
                let childStart = start + (node.span - (node.children.reduce((sum, child) => sum + child.span, 0) + Math.max(0, node.children.length - 1) * gap)) / 2;
                node.children.forEach(child => { place(child, childStart, depth + 1); childStart += child.span + gap; });
            };
            roots.forEach(root => { place(root, totalWidth, 0); totalWidth += root.span + gap; });
            const edges = nodes.filter(node => node.parent_id).map(node => {
                const parent = byId.get(node.parent_id);
                const fromX = parent.x + width / 2, fromY = parent.y + height;
                const toX = node.x + width / 2, toY = node.y;
                return { id: node.id, path: `M ${fromX} ${fromY} V ${fromY + (toY - fromY) / 2} H ${toX} V ${toY}` };
            });
            return { nodes, edges, width: Math.max(220, totalWidth - gap + padding), height: padding * 2 + maxDepth * level + height };
        },

        assistantSourceName(type) {
            return ({ transcript: 'Transcrição', one_on_one: '1:1', session: '1:1', commitment: 'Combinado', checkpoint: 'Checkpoint', email: 'E-mail', gmail: 'Gmail', person: 'Pessoa', card: 'Card do time', member_card: 'Card do time', meeting: 'Reunião de fórum', meeting_action: 'Ação de reunião', memory: 'Memória' })[type] || 'Registro';
        },

        assistantSourceDate(value) {
            if (!value) return '';
            const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? value + 'T12:00:00' : value);
            return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString('pt-BR');
        },

        assistantGmailUrl(source) {
            if (!source || !['email', 'gmail'].includes(source.type) || typeof source.url !== 'string') return null;
            try {
                const url = new URL(source.url);
                return url.protocol === 'https:' && url.hostname === 'mail.google.com' ? url.href : null;
            } catch (_) { return null; }
        },

        showAssistantSource(source, trigger) {
            this.assistantSelectedSource = source;
            this.assistantSourceTrigger = trigger || null;
            this.$nextTick(() => this.$refs.assistantSourceClose.focus());
        },

        closeAssistantSource() {
            this.assistantSelectedSource = null;
            const trigger = this.assistantSourceTrigger;
            this.$nextTick(() => trigger?.isConnected && trigger.focus());
        },

        showAssistantNodeSource(node, message, trigger) {
            const source = message.sources.find(item => node.source_ids.includes(String(item.id)));
            if (source) this.showAssistantSource(source, trigger);
        },

        assistantNodeHasSource(node, message) {
            return message.sources.some(source => node.source_ids.includes(String(source.id)));
        },

        assistantCoverage(coverage) {
            const entries = [];
            const labels = { people: 'pessoas', transcripts: 'transcrições', sessions: '1:1s', one_on_ones: '1:1s', commitments: 'combinados', checkpoints: 'checkpoints', emails: 'e-mails', cards: 'cards' };
            const sourceLabels = { person: 'pessoas', one_on_one: '1:1s', transcript: 'transcrições', commitment: 'combinados', checkpoint: 'checkpoints', meeting: 'reuniões de fóruns', meeting_action: 'ações de reuniões', card: 'cards', member_card: 'cards', memory: 'memórias' };
            const selected = coverage.database?.selected_by_type;
            if (selected && typeof selected === 'object') {
                Object.entries(sourceLabels).forEach(([key, label]) => {
                    if (Number.isFinite(selected[key]) && selected[key] > 0) entries.push(selected[key].toLocaleString('pt-BR') + ' ' + label);
                });
                if (Number.isFinite(coverage.memory?.selected_count) && coverage.memory.selected_count > 0) {
                    entries.push(coverage.memory.selected_count.toLocaleString('pt-BR') + (coverage.memory.selected_count === 1 ? ' memória' : ' memórias'));
                }
                if (coverage.gmail?.status === 'connected' || coverage.gmail?.message_count > 0) {
                    entries.push((coverage.gmail.message_count || 0).toLocaleString('pt-BR') + ' e-mails');
                }
                if (coverage.database.partial || coverage.gmail?.partial) entries.push('seleção parcial');
                return entries.join(' · ');
            }
            Object.entries(labels).forEach(([key, label]) => {
                const value = coverage[key];
                const count = typeof value === 'number' ? value : value && typeof value === 'object' ? (value.included ?? value.count) : null;
                if (Number.isFinite(count)) entries.push(count.toLocaleString('pt-BR') + ' ' + label);
            });
            return entries.join(' · ');
        },

        async executeAssistantAction(action) {
            if (this.assistantActionBusy || this.assistantBusy || action.state !== 'pending') return;
            this.assistantActionBusy = action.token;
            action.error = '';
            try {
                const result = await this.assistantRequest('/api/assistant/actions', { token: action.token });
                action.state = 'done';
                action.result = result.message || 'Pedido concluído.';
                action.resultDetails = { commitment_id: result.commitment_id, person_id: result.person_id };
                // Mantém os painéis do Cortex coerentes com os combinados alterados.
                const refresh = [];
                if (typeof this.loadPeople === 'function') refresh.push(this.loadPeople());
                if (typeof this.loadDashboard === 'function') refresh.push(this.loadDashboard());
                if (this.currentPerson && typeof this.loadPersonData === 'function') refresh.push(this.loadPersonData());
                if (this.prepPersonId && String(this.prepPersonId) === String(result.person_id) && typeof this.loadPrep === 'function') refresh.push(this.loadPrep());
                await Promise.allSettled(refresh);
            } catch (error) {
                action.error = error.message;
            } finally {
                this.assistantActionBusy = '';
                this.scrollAssistant();
            }
        }
    };
}
