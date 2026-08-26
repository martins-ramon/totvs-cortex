/* Cortex SPA — componente principal (Alpine.js) */

function cortexApp() {
    return {
        // --- estado ---
        booted: false,
        user: null,
        view: 'dashboard',

        people: [],
        peopleFilter: 'active',
        recentSessions: [],

        currentPerson: null,
        currentTab: 'sessions',
        personSessions: [],
        personCommitments: [],
        personCheckpoints: [],
        expandedId: null,
        expandedCpId: null,

        showCpModal: false,
        editingCp: null,
        cpForm: {},
        feedzText: '',
        parsingFeedz: false,
        savingCp: false,

        showPersonModal: false,
        editingPerson: null,
        personForm: {},
        savingPerson: false,

        showSessionModal: false,
        sessionForm: {},
        savingSession: false,
        editingSessionId: null,
        editingOriginalTranscript: '',

        sessionDetail: null,
        extracting: false,

        prepPersonId: '',
        prep: null,
        prepLoading: false,
        agendaMd: null,
        agendaLoading: false,

        cards: {},
        cardsLoaded: false,
        cardJob: {
            running: false, jobId: null, total: 0, done: 0, errors: 0,
            currentName: '', currentEmail: '', currentStep: '', stepKey: '',
            stepDetail: '', stepsDone: []
        },
        cardSteps: [
            { key: 'sessions', label: 'Avaliando registros de 1:1s e checkpoints' },
            { key: 'emails', label: 'Avaliando e-mails da caixa de entrada' },
            { key: 'synthesize', label: 'Sintetizando o card com IA' }
        ],
        _cardPollTimer: null,

        connections: [],
        connectionsLoading: false,

        // --- inicialização ---
        async init() {
            try {
                const r = await fetch('/api/current-user', { credentials: 'include' });
                if (!r.ok) { window.location.href = '/login'; return; }
                const d = await r.json();
                this.user = d.user;
            } catch (e) {
                window.location.href = '/login';
                return;
            }
            // Mensagens de retorno das conexões (OAuth redirect)
            const q = new URLSearchParams(window.location.search);
            if (q.get('connected') === 'gmail') {
                showToast('Gmail conectado com sucesso! ✓', 'success');
                this.view = 'connections';
            } else if (q.get('error')) {
                const msgs = {
                    'gmail_denied': 'Autorização do Gmail negada. Você pode tentar novamente quando quiser.',
                    'invalid_state': 'Sessão de conexão expirada. Tente conectar novamente.'
                };
                showToast(msgs[q.get('error')] || 'Falha na conexão.', 'error');
                this.view = 'connections';
            }
            if (q.get('connected') || q.get('error')) {
                history.replaceState({}, '', window.location.pathname);
            }
            await this.loadPeople();
            this.loadRecentSessions();
            this.booted = true;
        },

        async api(path, opts = {}) {
            const r = await fetch(path, {
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                ...opts
            });
            if (r.status === 401) { window.location.href = '/login'; throw new Error('Não autenticado'); }
            let data = {};
            try { data = await r.json(); } catch (e) { /* sem corpo */ }
            if (!r.ok) {
                const err = new Error(data.error || ('Erro ' + r.status));
                err.data = data;
                throw err;
            }
            return data;
        },

        logout() {
            fetch('/api/logout', { method: 'POST', credentials: 'include' })
                .finally(() => { window.location.href = '/login'; });
        },

        go(view) {
            this.view = view;
            if (view !== 'person') this.expandedId = null;
            if (view === 'people') this.loadPeople();
            if (view === 'dashboard') this.loadRecentSessions();
        },

        // --- carregamentos ---
        async loadPeople() {
            const d = await this.api('/api/people');
            this.people = d.people;
        },

        async loadRecentSessions() {
            const d = await this.api('/api/oneonones?limit=200');
            this.recentSessions = d.sessions;
        },

        async openPerson(p) {
            this.currentPerson = p;
            this.currentTab = 'sessions';
            this.expandedId = null;
            this.view = 'person';
            await this.loadPersonData();
        },

        async loadPersonData() {
            if (!this.currentPerson) return;
            const pid = this.currentPerson.id;
            const [s, c, cps] = await Promise.all([
                this.api('/api/oneonones?person_id=' + pid),
                this.api('/api/people/' + pid + '/commitments'),
                this.api('/api/people/' + pid + '/checkpoints')
            ]);
            this.personSessions = s.sessions;
            this.personCommitments = c.commitments;
            this.personCheckpoints = cps.checkpoints;
        },

        toggleExpand(id) {
            this.expandedId = this.expandedId === id ? null : id;
        },
        toggleCpExpand(id) {
            this.expandedCpId = this.expandedCpId === id ? null : id;
        },
        cpOwnerLabel(resp) {
            if (resp === 'gestor') return 'Você (gestor)';
            if (resp === 'liderado') {
                return this.currentPerson ? (this.currentPerson.preferred_name || this.currentPerson.full_name) : 'Liderado';
            }
            return resp || '?';
        },

        // --- checkpoints (Feedz) ---
        openCpModal(cp = null) {
            this.editingCp = cp;
            this.feedzText = '';
            if (cp) {
                this.cpForm = {
                    checkpoint_date: cp.checkpoint_date,
                    period_start: cp.period_start || '',
                    period_end: cp.period_end || '',
                    actions: (cp.actions || []).map(a => ({ acao: a.acao, responsavel: a.responsavel })),
                    private_notes: cp.private_notes || '',
                    public_notes: cp.public_notes || ''
                };
            } else {
                this.cpForm = {
                    checkpoint_date: new Date().toISOString().slice(0, 10),
                    period_start: '', period_end: '',
                    actions: [{ acao: '', responsavel: 'gestor' }],
                    private_notes: '', public_notes: ''
                };
            }
            this.showCpModal = true;
        },
        async parseFeedz() {
            if (!this.feedzText.trim()) return;
            this.parsingFeedz = true;
            try {
                const d = await this.api('/api/checkpoints/parse', {
                    method: 'POST',
                    body: JSON.stringify({
                        raw_text: this.feedzText,
                        person_name: this.currentPerson ? this.currentPerson.full_name : null,
                        person_id: this.currentPerson ? this.currentPerson.id : null
                    })
                });
                const p = d.parsed;
                this.cpForm.actions = (p.actions && p.actions.length)
                    ? p.actions.map(a => ({ ...a }))
                    : [{ acao: '', responsavel: 'gestor' }];
                if (p.private_notes) this.cpForm.private_notes = p.private_notes;
                if (p.public_notes) this.cpForm.public_notes = p.public_notes;
                showToast('Conteúdo estruturado! Revise abaixo antes de salvar.', 'success');
            } catch (e) {
                showToast('Falha ao estruturar: ' + e.message, 'error');
            } finally {
                this.parsingFeedz = false;
            }
        },
        async saveCp() {
            const f = this.cpForm;
            if (!f.checkpoint_date) { showToast('Informe a data do checkpoint.', 'warning'); return; }
            const actions = f.actions.filter(a => a.acao.trim());
            if (!actions.length && !f.private_notes.trim() && !f.public_notes.trim()) {
                showToast('Preencha ao menos uma ação ou anotação.', 'warning');
                return;
            }
            this.savingCp = true;
            try {
                const payload = { ...f, actions };
                if (this.editingCp) {
                    await this.api('/api/checkpoints/' + this.editingCp.id, {
                        method: 'PUT', body: JSON.stringify(payload)
                    });
                    showToast('Checkpoint atualizado.', 'success');
                } else {
                    payload.person_id = this.currentPerson ? this.currentPerson.id : null;
                    payload.source = this.feedzText.trim() ? 'feedz_paste' : 'manual';
                    payload.raw_input = this.feedzText.trim() || null;
                    await this.api('/api/checkpoints', { method: 'POST', body: JSON.stringify(payload) });
                    showToast('Checkpoint salvo!', 'success');
                }
                this.showCpModal = false;
                await this.loadPersonData();
            } catch (e) {
                showToast(e.message, 'error');
            } finally {
                this.savingCp = false;
            }
        },
        async deleteCp(cp) {
            if (!confirm('Excluir este checkpoint?')) return;
            try {
                await this.api('/api/checkpoints/' + cp.id, { method: 'DELETE' });
                showToast('Checkpoint excluído.', 'success');
                await this.loadPersonData();
            } catch (e) {
                showToast(e.message, 'error');
            }
        },

        // --- derivados ---
        get firstName() {
            return (this.user && this.user.name ? this.user.name : '').split(' ')[0];
        },
        get userInitials() {
            return this.initials(this.user ? this.user.name : '');
        },
        get activePeople() {
            return this.people.filter(p => p.active);
        },
        get filteredPeople() {
            return this.peopleFilter === 'active'
                ? this.people.filter(p => p.active)
                : this.people;
        },
        get stats() {
            const today = new Date();
            const byPerson = {};
            let sessions30d = 0;
            const THIRTY = 30 * 24 * 3600 * 1000;
            for (const s of this.recentSessions) {
                const t = new Date(s.occurred_on + 'T12:00:00');
                if (!byPerson[s.person_id] || t > byPerson[s.person_id]) byPerson[s.person_id] = t;
                if (today - t <= THIRTY) sessions30d++;
            }
            let stale = 0;
            for (const p of this.people.filter(x => x.active)) {
                const last = byPerson[p.id];
                if (!last || (today - last) > 21 * 24 * 3600 * 1000) stale++;
            }
            return {
                activePeople: this.activePeople.length,
                stalePeople: stale,
                sessions30d
            };
        },
        get trendEmoji() {
            const t = this.prep && this.prep.stats.sentiment_trend;
            if (t === 'melhorando') return '📈';
            if (t === 'piorando') return '📉';
            if (t === 'estável') return '➡️';
            return '—';
        },
        get trendLabel() {
            return this.prep && this.prep.stats.sentiment_trend
                ? 'vs. conversa anterior'
                : 'sem base comparativa';
        },
        get agendaHtml() {
            return this.agendaMd ? marked.parse(this.agendaMd) : '';
        },
        get progressPct() {
            if (!this.cardJob.total) return 0;
            const stepFrac = { sessions: 0.2, emails: 0.5, synthesize: 0.85 };
            const frac = stepFrac[this.cardJob.stepKey] || 0;
            return Math.min(99, Math.round(((this.cardJob.done + frac) / this.cardJob.total) * 100));
        },
        get sortedCardIds() {
            const healthOrder = { vermelho: 0, amarelo: 1, verde: 2 };
            return Object.keys(this.cards).sort((a, b) => {
                const ha = this.cards[a].card_json.ai?.saude || 'amarelo';
                const hb = this.cards[b].card_json.ai?.saude || 'amarelo';
                if (healthOrder[ha] !== healthOrder[hb]) return healthOrder[ha] - healthOrder[hb];
                return this.personName(a).localeCompare(this.personName(b));
            });
        },

        // --- utilidades ---
        initials(name) {
            return (name || '?').trim().split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
        },
        colorClass(name) {
            const styles = ['style-blue', 'style-purple', 'style-green', 'style-orange', 'style-pink'];
            let h = 0;
            for (const ch of (name || '')) h = (h * 31 + ch.charCodeAt(0)) % 997;
            return styles[h % styles.length];
        },
        fmtDate(iso) {
            if (!iso) return '—';
            const d = new Date(iso + (iso.length === 10 ? 'T12:00:00' : ''));
            return d.toLocaleDateString('pt-BR');
        },
        fmtDateLong(iso) {
            if (!iso) return '—';
            const d = new Date(iso + (iso.length === 10 ? 'T12:00:00' : ''));
            return d.toLocaleDateString('pt-BR', { day: 'numeric', month: 'long', year: 'numeric' });
        },
        daysSince(iso) {
            if (!iso) return null;
            return Math.floor((Date.now() - new Date(iso + 'T12:00:00')) / 86400000);
        },
        personName(pid) {
            const p = this.people.find(x => String(x.id) === String(pid));
            return p ? p.full_name : '(pessoa removida)';
        },
        ownerLabel(owner) {
            if (owner === 'manager') return 'Você (gestor)';
            if (owner === 'person') {
                return this.currentPerson ? (this.currentPerson.preferred_name || this.currentPerson.full_name) : 'Liderado';
            }
            return owner;
        },
        lastSessionInfo(personId) {
            const s = this.recentSessions.find(x => x.person_id === personId);
            if (!s) return '<span class="form-hint">Nenhum 1:1 registrado</span>';
            const days = this.daysSince(s.occurred_on);
            if (days > 21) {
                return `<span class="text-warn">Último 1:1 há ${days} dias ⚠️</span>`;
            }
            return `<span class="form-hint">Último 1:1 há ${days} dia${days === 1 ? '' : 's'}</span>`;
        },
        extractionOf(s) {
            const e = s && s.extraction;
            if (!e || typeof e !== 'object') return null;
            const out = {
                combinados: e.combinados || [],
                pontos_atencao: e.pontos_atencao || [],
                pontos_desenvolvimento: e.pontos_desenvolvimento || [],
                conquistas: e.conquistas || []
            };
            if (!out.combinados.length && !out.pontos_atencao.length &&
                !out.pontos_desenvolvimento.length && !out.conquistas.length) return null;
            return out;
        },
        dotFor(sentiment) {
            if (sentiment === 'positivo') return 'dot-green';
            if (sentiment === 'preocupante') return 'dot-red';
            return 'dot-yellow';
        },

        // --- modal pessoa ---
        openPersonModal(p = null) {
            this.editingPerson = p;
            this.personForm = p ? {
                full_name: p.full_name,
                preferred_name: p.preferred_name || '',
                email: p.email || '',
                role_title: p.role_title || '',
                hired_at: p.hired_at || '',
                notes: p.notes || ''
            } : { full_name: '', preferred_name: '', email: '', role_title: '', hired_at: '', notes: '' };
            this.showPersonModal = true;
        },
        async savePerson() {
            if (!this.personForm.full_name.trim()) { showToast('Informe o nome.', 'warning'); return; }
            const em = (this.personForm.email || '').trim();
            if (em && (em.indexOf('@') < 1 || em.indexOf(' ') >= 0)) {
                showToast('Informe um e-mail válido.', 'warning');
                return;
            }
            try {
                if (this.editingPerson) {
                    await this.api('/api/people/' + this.editingPerson.id, {
                        method: 'PUT', body: JSON.stringify(this.personForm)
                    });
                    showToast('Pessoa atualizada.', 'success');
                    if (this.currentPerson && this.currentPerson.id === this.editingPerson.id) {
                        Object.assign(this.currentPerson, this.personForm);
                    }
                } else {
                    await this.api('/api/people', { method: 'POST', body: JSON.stringify(this.personForm) });
                    showToast('Pessoa adicionada ao time! 🎉', 'success');
                }
                this.showPersonModal = false;
                await this.loadPeople();
            } catch (e) {
                showToast(e.message, 'error');
            }
        },

        // --- modal 1:1 ---
        openSessionModal(personId = null) {
            this.editingSessionId = null;
            this.editingOriginalTranscript = '';
            this.sessionForm = {
                person_id: personId || '',
                occurred_on: new Date().toISOString().slice(0, 10),
                title: '',
                transcript_raw: ''
            };
            this.showSessionModal = true;
        },
        async openEditSession(s) {
            try {
                const d = await this.api('/api/oneonones/' + s.id);
                const sess = d.session;
                this.editingSessionId = sess.id;
                this.editingOriginalTranscript = sess.transcript_raw || '';
                this.sessionForm = {
                    person_id: sess.person_id,
                    occurred_on: sess.occurred_on,
                    title: sess.title || '',
                    transcript_raw: sess.transcript_raw || ''
                };
                this.showSessionModal = true;
            } catch (e) {
                showToast(e.message, 'error');
            }
        },
        async deleteSession(s) {
            if (!confirm('Excluir este registro de 1:1? Os combinados vinculados permanecem no histórico.')) return;
            try {
                await this.api('/api/oneonones/' + s.id, { method: 'DELETE' });
                showToast('1:1 excluído.', 'success');
                if (this.sessionDetail && this.sessionDetail.id === s.id) this.sessionDetail = null;
                await this.loadRecentSessions();
                if (this.currentPerson) await this.loadPersonData();
                if (this.prepPersonId) this.loadPrep();
            } catch (e) {
                showToast(e.message, 'error');
            }
        },
        async saveSession() {
            const f = this.sessionForm;
            if (!f.person_id) { showToast('Selecione a pessoa.', 'warning'); return; }
            if (!f.occurred_on) { showToast('Informe a data.', 'warning'); return; }
            this.savingSession = true;
            let sessionId;
            try {
                if (this.editingSessionId) {
                    await this.api('/api/oneonones/' + this.editingSessionId, {
                        method: 'PUT', body: JSON.stringify(f)
                    });
                    sessionId = this.editingSessionId;
                    showToast('1:1 atualizado!', 'success');
                } else {
                    const d = await this.api('/api/oneonones', {
                        method: 'POST', body: JSON.stringify(f)
                    });
                    sessionId = d.session_id;
                    showToast('1:1 registrado!', 'success');
                }
                this.showSessionModal = false;
                await this.loadRecentSessions();
                if (this.currentPerson && String(this.currentPerson.id) === String(f.person_id)) {
                    await this.loadPersonData();
                }
                if (this.prepPersonId === String(f.person_id)) this.loadPrep();

                // Transcrição nova ou alterada -> refaz os insights.
                // Se a modal da reunião estiver aberta, atualiza-a; senão, apenas notifica.
                const hasTranscript = (f.transcript_raw || '').trim().length > 40;
                const transcriptChanged = !this.editingSessionId ||
                    (f.transcript_raw || '') !== this.editingOriginalTranscript;
                if (hasTranscript && transcriptChanged) {
                    const detailOpen = this.sessionDetail && this.sessionDetail.id === sessionId;
                    showToast('✨ Extraindo insights da transcrição…', 'info');
                    try {
                        const d = await this.api(`/api/oneonones/${sessionId}/extract`, { method: 'POST' });
                        if (detailOpen) await this.viewSession(sessionId);
                        const n = (d.session.extraction && d.session.extraction.combinados)
                            ? d.session.extraction.combinados.length : 0;
                        showToast(`Insights extraídos${n ? ` — ${n} combinado(s) criado(s)` : ''}!`, 'success');
                        await this.loadRecentSessions();
                        if (this.currentPerson && String(this.currentPerson.id) === String(f.person_id)) {
                            await this.loadPersonData();
                        }
                        if (this.prepPersonId === String(f.person_id)) this.loadPrep();
                    } catch (e) {
                        showToast('Falha na extração: ' + e.message, 'error');
                    }
                }
            } catch (e) {
                showToast(e.message, 'error');
            } finally {
                this.savingSession = false;
                this.editingSessionId = null;
                this.editingOriginalTranscript = '';
            }
        },
        async viewSession(id) {
            try {
                const d = await this.api('/api/oneonones/' + id);
                this.sessionDetail = d.session;
            } catch (e) {
                showToast(e.message, 'error');
            }
        },

        // --- IA: extração e agenda ---
        async extractSession(silent = false) {
            if (!this.sessionDetail) return;
            if (!this.sessionDetail.transcript_raw) {
                if (!silent) showToast('Esta sessão não tem transcrição para analisar.', 'warning');
                return;
            }
            this.extracting = true;
            try {
                const d = await this.api(`/api/oneonones/${this.sessionDetail.id}/extract`, { method: 'POST' });
                this.sessionDetail = d.session;
                const n = (d.session.extraction && d.session.extraction.combinados ? d.session.extraction.combinados.length : 0);
                showToast(`Insights extraídos! ${n} combinado(s) criado(s).`, 'success');
                await this.loadRecentSessions();
                if (this.currentPerson) await this.loadPersonData();
                if (this.prepPersonId) this.loadPrep();
            } catch (e) {
                showToast('Falha na extração: ' + e.message, 'error');
            } finally {
                this.extracting = false;
            }
        },
        async generateAgenda() {
            if (!this.prep) return;
            this.agendaLoading = true;
            this.agendaMd = null;
            try {
                const d = await this.api(`/api/people/${this.prep.person.id}/agenda`, { method: 'POST' });
                this.agendaMd = d.agenda_md;
            } catch (e) {
                showToast('Falha ao gerar agenda: ' + e.message, 'error');
            } finally {
                this.agendaLoading = false;
            }
        },
        async saveSessionNotes() {
            if (!this.sessionDetail) return;
            try {
                await this.api('/api/oneonones/' + this.sessionDetail.id, {
                    method: 'PUT',
                    body: JSON.stringify({
                        private_notes: this.sessionDetail.private_notes || '',
                        public_notes: this.sessionDetail.public_notes || ''
                    })
                });
                showToast('Anotações salvas.', 'success');
            } catch (e) {
                showToast(e.message, 'error');
            }
        },

        // --- preparação 1:1 ---
        async loadPrep() {
            if (!this.prepPersonId) { this.prep = null; return; }
            this.prepLoading = true;
            this.agendaMd = null;
            try {
                this.prep = await this.api('/api/people/' + this.prepPersonId + '/prep');
            } catch (e) {
                showToast(e.message, 'error');
            } finally {
                this.prepLoading = false;
            }
        },
        async setCommitmentStatus(c, status) {
            try {
                await this.api('/api/commitments/' + c.id, {
                    method: 'PUT', body: JSON.stringify({ status })
                });
                c.status = status;
                if (status !== 'open') {
                    this.personCommitments = await this.reloadCommitmentsSafe();
                }
            } catch (e) {
                showToast(e.message, 'error');
            }
        },
        async reloadCommitmentsSafe() {
            if (!this.currentPerson) return this.personCommitments;
            const d = await this.api('/api/people/' + this.currentPerson.id + '/commitments');
            return d.commitments;
        },
        async completeFromPrep(c) {
            try {
                await this.api('/api/commitments/' + c.id, {
                    method: 'PUT', body: JSON.stringify({ status: 'done' })
                });
                showToast('Combinado concluído! ✓', 'success');
                this.loadPrep();
            } catch (e) {
                showToast(e.message, 'error');
            }
        },

        // --- cards do time (geração assíncrona) ---
        async goCards() {
            this.view = 'cards';
            if (!this.cardsLoaded) {
                try {
                    const d = await this.api('/api/cards/latest');
                    this.cards = d.cards;
                    this.cardsLoaded = true;
                } catch (e) {
                    showToast(e.message, 'error');
                }
            }
        },
        personRole(pid) {
            const p = this.people.find(x => x.id === parseInt(pid, 10));
            return p ? (p.role_title || '') : '';
        },
        trendInfo(card) {
            const t = card.card_json.ai?.tendencia;
            if (t === 'subindo') return { emoji: '📈', label: 'em alta' };
            if (t === 'caindo') return { emoji: '📉', label: 'em queda' };
            return { emoji: '➡️', label: 'estável' };
        },
        cardStepClass(key) {
            if ((this.cardJob.stepsDone || []).includes(key)) return 'done';
            if (this.cardJob.stepKey === key) return 'active';
            return '';
        },
        cardStepMark(key) {
            if ((this.cardJob.stepsDone || []).includes(key)) return '✓';
            if (this.cardJob.stepKey === key) return '●';
            return '○';
        },
        cardHasEmailInsights(card) {
            const e = card && card.card_json && card.card_json.emails;
            if (!e) return false;
            return (e.pendencias || []).length + (e.todos || []).length + (e.assuntos || []).length > 0;
        },
        emailThreadHint(card) {
            const n = card && card.card_json && card.card_json.emails && card.card_json.emails.thread_count;
            return n ? `(${n} thread${n === 1 ? '' : 's'})` : '';
        },
        emailSkipLabel(code) {
            const map = {
                sem_email: '✉️ Inbox não analisada — cadastre o e-mail da pessoa.',
                gmail_nao_conectado: '✉️ Inbox não analisada — conecte o Gmail em Conexões.',
                erro: '✉️ Inbox não analisada — falha ao ler a caixa de entrada.'
            };
            return map[code] || '';
        },
        startCardProgressPoll() {
            this.stopCardProgressPoll();
            const tick = async () => {
                if (!this.cardJob.running || !this.cardJob.jobId) return;
                try {
                    const d = await this.api('/api/cards/job/' + this.cardJob.jobId);
                    const p = d.progress || {};
                    if (p.person_name) this.cardJob.currentName = p.person_name;
                    if (p.person_email !== undefined) this.cardJob.currentEmail = p.person_email || '';
                    this.cardJob.currentStep = p.label || this.cardJob.currentStep;
                    this.cardJob.stepKey = p.step || '';
                    this.cardJob.stepDetail = p.detail || '';
                    this.cardJob.stepsDone = p.steps_done || [];
                } catch (e) { /* poll é best-effort */ }
                if (this.cardJob.running) {
                    this._cardPollTimer = setTimeout(tick, 450);
                }
            };
            tick();
        },
        stopCardProgressPoll() {
            if (this._cardPollTimer) {
                clearTimeout(this._cardPollTimer);
                this._cardPollTimer = null;
            }
        },
        async startCardJob() {
            if (!this.activePeople.length) {
                showToast('Cadastre pessoas no time primeiro.', 'warning');
                return;
            }
            if (!confirm('Gerar/atualizar os cards de todas as pessoas ativas? A IA analisará 1:1s, checkpoints e, se o Gmail estiver conectado, os e-mails de cada uma.')) return;
            try {
                const d = await this.api('/api/cards/generate-start', { method: 'POST' });
                this.cardJob = {
                    running: true, jobId: d.job_id, total: d.people.length, done: 0, errors: 0,
                    currentName: '', currentEmail: '', currentStep: 'Preparando atualização…',
                    stepKey: '', stepDetail: '', stepsDone: []
                };
                this.startCardProgressPoll();
                for (const person of d.people) {
                    this.cardJob.currentName = person.full_name;
                    this.cardJob.currentEmail = person.email || '';
                    this.cardJob.stepKey = 'sessions';
                    this.cardJob.currentStep = 'Avaliando registros de 1:1s e checkpoints';
                    this.cardJob.stepDetail = '';
                    this.cardJob.stepsDone = [];
                    try {
                        const r = await this.api('/api/cards/person/' + person.id, {
                            method: 'POST', body: JSON.stringify({ job_id: d.job_id })
                        });
                        this.cards[person.id] = r.card;
                    } catch (e) {
                        this.cardJob.errors++;
                        console.error('Falha no card de', person.full_name, e);
                    }
                    this.cardJob.done++;
                }
                const fin = await this.api('/api/cards/generate-finish', {
                    method: 'POST', body: JSON.stringify({ job_id: d.job_id })
                });
                if (fin.had_errors) {
                    showToast(`Cards atualizados com ${this.cardJob.errors} falha(s). Tente novamente para as que faltaram.`, 'warning');
                } else {
                    showToast(`✅ ${fin.done}/${fin.total} cards gerados com sucesso!`, 'success');
                }
            } catch (e) {
                showToast(e.message, 'error');
            } finally {
                this.stopCardProgressPoll();
                this.cardJob.running = false;
                this.cardJob.currentName = '';
                this.cardJob.stepKey = '';
                this.cardJob.stepDetail = '';
            }
        },
        async regenCard(pid) {
            try {
                showToast('Regenerando card de ' + this.personName(pid) + '…', 'info');
                const r = await this.api('/api/cards/person/' + pid, {
                    method: 'POST', body: JSON.stringify({})
                });
                this.cards[pid] = r.card;
                showToast('Card atualizado!', 'success');
            } catch (e) {
                showToast(e.message, 'error');
            }
        },

        async completeFromPrep(c) {
            try {
                await this.api('/api/commitments/' + c.id, {
                    method: 'PUT', body: JSON.stringify({ status: 'done' })
                });
                showToast('Combinado concluído! ✓', 'success');
                this.loadPrep();
            } catch (e) {
                showToast(e.message, 'error');
            }
        },

        // --- conexões ---
        async goConnections() {
            this.view = 'connections';
            this.connectionsLoading = true;
            try {
                const d = await this.api('/api/connections');
                this.connections = d.connections;
            } catch (e) {
                showToast(e.message, 'error');
            } finally {
                this.connectionsLoading = false;
            }
        },
        async connectTool(tool) {
            try {
                const d = await this.api(`/api/connections/${tool.id}/start`);
                window.location.href = d.redirect_url;
            } catch (e) {
                showToast(e.message, 'error');
            }
        },
        async disconnectTool(tool) {
            if (!confirm(`Desconectar o ${tool.name}? O Cortex perderá o acesso a ele.`)) return;
            try {
                await this.api(`/api/connections/${tool.id}/disconnect`, { method: 'POST' });
                showToast(`${tool.name} desconectado.`, 'success');
                await this.loadConnections();
            } catch (e) {
                showToast(e.message, 'error');
            }
        },
        async loadConnections() {
            try {
                const d = await this.api('/api/connections');
                this.connections = d.connections;
            } catch (e) {
                showToast(e.message, 'error');
            }
        },

    };
}

window.cortexApp = cortexApp;

/* Toast global (mesmo padrão do auth.js) */
function showToast(message, type = 'info', title = null) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    const icons = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
    const titles = { success: title || 'Sucesso', error: title || 'Erro', warning: title || 'Atenção', info: title || 'Informação' };
    toast.innerHTML = `
        <div class="toast-icon">${icons[type] || icons.info}</div>
        <div class="toast-content">
            <div class="toast-title">${titles[type]}</div>
            <div class="toast-message">${message}</div>
        </div>
        <div class="toast-close" onclick="this.parentElement.remove()">×</div>
    `;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 6000);
}
