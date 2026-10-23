/* Reuniões de fóruns: contexto coletivo e ações revisadas pelo gestor. */
function cortexMeetings() {
    return {
        meetings: [],
        meetingForums: [],
        meetingsTotal: 0,
        meetingsLoading: false,
        meetingsError: '',
        meetingFilters: { q: '', forum: '', person_id: '', date_from: '', date_to: '' },
        meetingsRequest: 0,
        personMeetings: [],
        personMeetingActions: [],
        personMeetingsLoading: false,
        personMeetingsError: '',
        personMeetingsRequest: 0,
        showMeetingForm: false,
        editingMeetingId: null,
        meetingForm: { title: '', forum: '', occurred_on: '', participant_ids: [], external_participants: '', transcript_raw: '', notes: '' },
        meetingFormError: '',
        meetingSaving: false,
        meetingDetail: null,
        meetingDetailLoading: false,
        meetingDetailRequest: 0,
        meetingDetailError: '',
        meetingAnalyzing: false,
        meetingDeleting: false,
        meetingSuggestions: [],
        meetingActionForm: null,
        meetingActionSaving: false,
        meetingActionBusy: '',
        meetingActionError: '',

        async loadMeetings() {
            const request = ++this.meetingsRequest;
            this.meetingsLoading = true;
            this.meetingsError = '';
            try {
                const params = new URLSearchParams({ limit: '100' });
                Object.entries(this.meetingFilters).forEach(([key, value]) => {
                    if (value) params.set(key, String(value).trim());
                });
                const data = await this.api('/api/meetings?' + params);
                if (request !== this.meetingsRequest) return;
                this.meetings = data.meetings || [];
                this.meetingsTotal = data.total ?? this.meetings.length;
                this.meetingForums = data.forums || [];
            } catch (error) {
                if (request === this.meetingsRequest) this.meetingsError = error.message;
            } finally {
                if (request === this.meetingsRequest) this.meetingsLoading = false;
            }
        },

        clearMeetingFilters() {
            this.meetingFilters = { q: '', forum: '', person_id: '', date_from: '', date_to: '' };
            this.loadMeetings();
        },

        async loadPersonMeetings(personId) {
            const request = ++this.personMeetingsRequest;
            this.personMeetingsLoading = true;
            this.personMeetingsError = '';
            try {
                const data = await this.api('/api/meetings?' + new URLSearchParams({ person_id: personId, limit: '100' }));
                if (request !== this.personMeetingsRequest || String(this.currentPerson?.id) !== String(personId)) return;
                this.personMeetings = data.meetings || [];
                this.personMeetingActions = data.actions || data.meeting_actions || [];
            } catch (error) {
                if (request === this.personMeetingsRequest) this.personMeetingsError = error.message;
            } finally {
                if (request === this.personMeetingsRequest) this.personMeetingsLoading = false;
            }
        },

        meetingParticipantNames(meeting) {
            const names = (meeting.participants || []).map(person => person.full_name || person.name).filter(Boolean);
            if (meeting.external_participants) names.push(meeting.external_participants);
            return names.join(' · ') || 'Participantes não informados';
        },

        meetingExtraction(meeting) {
            const extraction = meeting?.extraction;
            if (!extraction || typeof extraction !== 'object') return {};
            return extraction;
        },

        meetingInsightGroups(meeting) {
            const extraction = this.meetingExtraction(meeting);
            return [
                { key: 'strategic_themes', title: 'Temas estratégicos' },
                { key: 'decisions', title: 'Decisões' },
                { key: 'risks', title: 'Riscos e pontos de atenção' },
                { key: 'opportunities', title: 'Oportunidades' },
                { key: 'open_questions', title: 'Questões em aberto' }
            ].map(group => ({ ...group, items: Array.isArray(extraction[group.key]) ? extraction[group.key] : [] }))
                .filter(group => group.items.length);
        },

        openMeetingForm(meeting = null, personId = null) {
            this.editingMeetingId = meeting?.id || null;
            this.meetingFormError = '';
            this.meetingForm = {
                title: meeting?.title || '', forum: meeting?.forum || '',
                occurred_on: meeting?.occurred_on || new Date().toLocaleDateString('sv-SE'),
                participant_ids: (meeting?.participant_ids || (meeting?.participants || []).map(person => person.id) || (personId ? [personId] : [])).map(String),
                external_participants: meeting?.external_participants || '',
                transcript_raw: meeting?.transcript_raw || '', notes: meeting?.notes || ''
            };
            if (!meeting && personId) this.meetingForm.participant_ids = [String(personId)];
            this.showMeetingForm = true;
            this.$nextTick(() => this.$refs.meetingTitle?.focus());
        },

        closeMeetingForm() {
            if (!this.meetingSaving) this.showMeetingForm = false;
        },

        async saveMeeting() {
            if (this.meetingSaving) return;
            this.meetingFormError = '';
            if (!this.meetingForm.title.trim() || !this.meetingForm.occurred_on) {
                this.meetingFormError = 'Informe o título e a data da reunião.';
                return;
            }
            if (!/^\d{4}-\d{2}-\d{2}$/.test(this.meetingForm.occurred_on)) {
                this.meetingFormError = 'Informe uma data válida.';
                return;
            }
            const editingId = this.editingMeetingId;
            this.meetingSaving = true;
            try {
                const data = await this.api(editingId ? `/api/meetings/${editingId}` : '/api/meetings', {
                    method: editingId ? 'PUT' : 'POST',
                    body: JSON.stringify({ ...this.meetingForm, participant_ids: this.meetingForm.participant_ids.map(Number) })
                });
                this.showMeetingForm = false;
                const id = editingId || data.meeting_id || data.meeting?.id;
                showToast(editingId ? 'Reunião atualizada.' : 'Reunião registrada.', 'success');
                await this.viewMeeting(id);
                this.refreshMeetingViews();
            } catch (error) {
                this.meetingFormError = error.message;
            } finally {
                this.meetingSaving = false;
            }
        },

        async viewMeeting(id) {
            if (!id) return;
            const request = ++this.meetingDetailRequest;
            this.meetingDetailLoading = true;
            this.meetingDetailError = '';
            this.meetingDetail = null;
            this.meetingActionForm = null;
            this.meetingActionError = '';
            this.meetingSuggestions = [];
            try {
                const data = await this.api(`/api/meetings/${id}`);
                if (request !== this.meetingDetailRequest) return;
                this.setMeetingDetail(data.meeting);
            } catch (error) {
                if (request === this.meetingDetailRequest) this.meetingDetailError = error.message;
            } finally {
                if (request === this.meetingDetailRequest) this.meetingDetailLoading = false;
            }
        },

        setMeetingDetail(meeting) {
            this.meetingDetail = meeting;
            this.meetingSuggestions = (this.meetingExtraction(meeting).actions || []).map((action, index) => ({
                ...action, source_key: action.source_key || `action-${index}`,
                person_id: action.person_id == null ? '' : String(action.person_id),
                owner_name: action.owner_name || '', due_date: action.due_date || '', error: ''
            }));
        },

        closeMeetingDetail() {
            if (this.meetingAnalyzing || this.meetingDeleting || this.meetingActionSaving || this.meetingActionBusy) return;
            ++this.meetingDetailRequest;
            this.meetingDetail = null;
            this.meetingDetailLoading = false;
            this.meetingDetailError = '';
            this.meetingActionForm = null;
        },

        async analyzeMeeting() {
            if (!this.meetingDetail || this.meetingAnalyzing) return;
            this.meetingAnalyzing = true;
            this.meetingDetailError = '';
            try {
                const data = await this.api(`/api/meetings/${this.meetingDetail.id}/extract`, { method: 'POST' });
                this.setMeetingDetail(data.meeting);
                showToast('Análise concluída. Revise as ações sugeridas antes de incluí-las no plano.', 'success');
                this.refreshMeetingViews();
            } catch (error) {
                this.meetingDetailError = error.message;
            } finally {
                this.meetingAnalyzing = false;
            }
        },

        async deleteMeeting() {
            if (!this.meetingDetail || this.meetingDeleting) return;
            if (!confirm(`Excluir a reunião “${this.meetingDetail.title}” e suas ações? Esta exclusão é permanente.`)) return;
            this.meetingDeleting = true;
            try {
                await this.api(`/api/meetings/${this.meetingDetail.id}`, { method: 'DELETE' });
                this.meetingDetail = null;
                this.meetingDetailError = '';
                showToast('Reunião excluída.', 'success');
                this.refreshMeetingViews();
            } catch (error) {
                this.meetingDetailError = error.message;
            } finally {
                this.meetingDeleting = false;
            }
        },

        meetingSuggestionAdopted(suggestion) {
            return (this.meetingDetail?.actions || []).some(action => action.source_key && action.source_key === suggestion.source_key);
        },

        meetingOwnerChanged(action) {
            const person = this.people.find(person => String(person.id) === String(action.person_id));
            action.owner_name = person ? (person.preferred_name || person.full_name) : '';
        },

        meetingActionPayload(action) {
            return {
                description: (action.description || '').trim(),
                person_id: action.person_id ? Number(action.person_id) : null,
                owner_name: (action.owner_name || '').trim(), due_date: action.due_date || null,
                ...(action.source_key ? { source_key: action.source_key } : {}),
                ...(action.status ? { status: action.status } : {})
            };
        },

        async adoptMeetingSuggestion(suggestion) {
            if (!this.meetingDetail || this.meetingDetail.analysis_stale || this.meetingActionBusy || this.meetingSuggestionAdopted(suggestion)) return;
            const payload = this.meetingActionPayload(suggestion);
            if (!payload.description) { suggestion.error = 'Informe a descrição da ação.'; return; }
            this.meetingActionBusy = suggestion.source_key;
            suggestion.error = '';
            try {
                const data = await this.api(`/api/meetings/${this.meetingDetail.id}/actions`, { method: 'POST', body: JSON.stringify(payload) });
                this.meetingDetail.actions = [...(this.meetingDetail.actions || []), data.action];
                showToast('Ação incluída no plano tático.', 'success');
                this.refreshMeetingViews();
            } catch (error) {
                suggestion.error = error.message;
            } finally {
                this.meetingActionBusy = '';
            }
        },

        editMeetingAction(action = null) {
            this.meetingActionError = '';
            this.meetingActionForm = action ? { ...action, person_id: action.person_id == null ? '' : String(action.person_id), due_date: action.due_date || '' } :
                { description: '', person_id: '', owner_name: '', due_date: '', status: 'open' };
        },

        async saveMeetingAction() {
            if (!this.meetingDetail || !this.meetingActionForm || this.meetingActionSaving) return;
            const action = this.meetingActionForm;
            const payload = this.meetingActionPayload(action);
            if (!payload.description) { this.meetingActionError = 'Informe a descrição da ação.'; return; }
            this.meetingActionSaving = true;
            this.meetingActionError = '';
            try {
                const data = await this.api(action.id ? `/api/meeting-actions/${action.id}` : `/api/meetings/${this.meetingDetail.id}/actions`, {
                    method: action.id ? 'PUT' : 'POST', body: JSON.stringify(payload)
                });
                this.meetingDetail.actions = action.id ? this.meetingDetail.actions.map(item => item.id === action.id ? data.action : item) : [...(this.meetingDetail.actions || []), data.action];
                this.meetingActionForm = null;
                this.refreshMeetingViews();
            } catch (error) {
                this.meetingActionError = error.message;
            } finally {
                this.meetingActionSaving = false;
            }
        },

        async setMeetingActionStatus(action, status) {
            if (this.meetingActionBusy) return;
            this.meetingActionBusy = `status-${action.id}`;
            this.meetingActionError = '';
            try {
                const data = await this.api(`/api/meeting-actions/${action.id}`, { method: 'PUT', body: JSON.stringify({ status }) });
                if (this.meetingDetail) this.meetingDetail.actions = this.meetingDetail.actions.map(item => item.id === action.id ? data.action : item);
                this.refreshMeetingViews();
            } catch (error) {
                this.meetingActionError = error.message;
            } finally {
                this.meetingActionBusy = '';
            }
        },

        refreshMeetingViews() {
            const refresh = [this.loadDashboard()];
            if (this.view === 'meetings') refresh.push(this.loadMeetings());
            if (this.currentPerson) refresh.push(this.loadPersonMeetings(this.currentPerson.id));
            if (this.view === 'prep' && this.prepPersonId) refresh.push(this.loadPrep());
            Promise.allSettled(refresh);
        }
    };
}
