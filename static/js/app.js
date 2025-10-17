let currentUser = null;

// --- AUTENTICAÇÃO E INICIALIZAÇÃO ---

function initializeApp() {
    validateSession();
}

async function validateSession() {
    try {
        const response = await fetch('/api/current-user', { credentials: 'include' });
        if (!response.ok) {
            throw new Error('Sessão inválida ou expirada. Status: ' + response.status);
        }
        const data = await response.json();
        if (data.authenticated) {
            currentUser = data.user;
            sessionStorage.setItem('currentUser', JSON.stringify(data.user));
            showApp();
        } else {
            throw new Error('Usuário não autenticado pela API.');
        }
    } catch (error) {
        console.error('Falha na validação da sessão:', error);
        sessionStorage.removeItem('currentUser');
        if (window.location.pathname !== '/login.html' && window.location.pathname !== '/login') {
            window.location.href = '/login';
        }
    }
}

function showApp() {
    if (!currentUser) return;

    document.getElementById('app-container').style.display = 'flex';
    document.getElementById('user-name').textContent = currentUser.name;
    document.getElementById('user-company').textContent = currentUser.company;

    // ✅ ALTERAÇÃO: Renderiza e exibe o chat widget
    renderChatWidget(); // 1. Primeiro, criamos o conteúdo do chat.
    const chatWidget = document.getElementById('chat-widget');
    if (chatWidget) {
        chatWidget.style.display = 'block'; // 2. Depois, tornamos seu container visível.
    }

    loadSidebarUserPhoto();
    showMyTeam();
}

async function logout() {
    try {
        await fetch('/api/logout', { method: 'POST', credentials: 'include' });
        sessionStorage.removeItem('currentUser');
        currentUser = null;
        showToast('Logout realizado com sucesso!', 'info');
        window.location.href = '/login';
    } catch (error) {
        console.error('Logout failed:', error);
    }
}

// --- UTILITÁRIOS DE UI (Toast, Modal, Navegação) ---
// ... (nenhuma alteração nesta seção)
function showToast(message, type = 'info', title = null) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    const icons = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
    const titles = { success: title || 'Sucesso', error: title || 'Erro', warning: title || 'Atenção', info: title || 'Informação' };
    toast.innerHTML = `<div class="toast-icon">${icons[type]||icons.info}</div><div class="toast-content"><div class="toast-title">${titles[type]}</div><div class="toast-message">${message}</div></div><div class="toast-close" onclick="this.parentElement.remove()">×</div>`;
    container.appendChild(toast);
    setTimeout(() => { if(toast.parentElement) toast.parentElement.removeChild(toast); }, 5000);
}

function setActiveNav(viewName) {
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
    document.querySelectorAll('.view').forEach(view => view.style.display = 'none');
    const viewMap = { 'my-team': 0, 'feedbacks': 1, 'meetings': 2 };
    if (viewMap[viewName] !== undefined) {
        document.querySelectorAll('.nav-item')[viewMap[viewName]].classList.add('active');
    }
}

function toggleCard(cardId) {
    const expanded = document.getElementById(`expanded-${cardId}`);
    const btn = document.getElementById(`btn-${cardId}`);
    if (expanded.classList.contains('show')) {
        expanded.classList.remove('show');
        btn.classList.remove('expanded');
        btn.innerHTML = 'Ver detalhes <svg fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"></path></svg>';
    } else {
        expanded.classList.add('show');
        btn.classList.add('expanded');
        btn.innerHTML = 'Ocultar detalhes <svg fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"></path></svg>';
    }
}

function closeModal() {
    document.getElementById('modal').style.display = 'none';
}

window.onclick = function(event) {
    if (event.target === document.getElementById('modal')) {
        closeModal();
    }
}

// --- VIEW: MEU TIME ---
// ... (nenhuma alteração nesta seção)
async function showMyTeam() {
    setActiveNav('my-team');
    document.getElementById('my-team-view').style.display = 'block';
    const content = document.getElementById('dashboard-content');
    content.innerHTML = '<div class="loading">Carregando usuários...</div>';
    try {
        const response = await fetch('/api/dashboard', { credentials: 'include' });
        const data = await response.json();
        if (data.users && data.users.length > 0) {
            content.innerHTML = data.users.map((user) => createSkeletonCard(user.user_id)).join('');
            data.users.forEach((user) => loadUserInsights(user.user_id));
        } else {
            content.innerHTML = '<div class="no-data"><p>Nenhum usuário gerenciado. Os usuários devem te selecionar como gestor no perfil deles!</p></div>';
        }
    } catch (error) {
        console.error('Failed to load dashboard:', error);
        content.innerHTML = '<div class="no-data"><p>Erro ao carregar dashboard</p></div>';
    }
}

function createSkeletonCard(userId) {
    return `<div class="skeleton-card" id="card-container-${userId}"><div class="skeleton-header"><div class="skeleton-avatar"></div><div class="skeleton-text"><div class="skeleton-line short"></div><div class="skeleton-line medium"></div></div></div><div class="skeleton-content"><div class="skeleton-line long"></div><div class="skeleton-line long"></div><div class="skeleton-line medium"></div></div></div>`;
}

async function loadUserInsights(userId) {
    try {
        const response = await fetch(`/api/user/${userId}/insights`, { credentials: 'include' });
        const data = await response.json();
        const container = document.getElementById(`card-container-${userId}`);
        if (container) {
            container.outerHTML = renderUserCard(data);
            loadUserAvatarAsync(userId, `#avatar-${userId}`);
        }
    } catch (error) {
        console.error(`Failed to load insights for user ${userId}:`, error);
        const container = document.getElementById(`card-container-${userId}`);
        if (container) container.outerHTML = `<div class="insight-card"><div class="no-data"><p>Erro ao carregar insights</p></div></div>`;
    }
}

function renderUserCard(item) {
    const userInitial = item.user_name.charAt(0).toUpperCase();
    const photoElement = `<div class="user-avatar-placeholder" id="avatar-${item.user_id}">${userInitial}</div>`;
    if (!item.latest_feedback) {
        return `<div class="insight-card" id="card-container-${item.user_id}"><div class="insight-header"><div class="employee-info" style="display: flex; align-items: center; gap: 1rem;">${photoElement}<div><h3>${item.user_name}</h3><p>${item.company || 'Sem empresa'}</p></div></div></div><div class="no-data"><p>Nenhum feedback cadastrado ainda</p></div></div>`;
    }
    const insights = item.insights;
    const feedback = item.latest_feedback;
    const cardId = `card-${item.user_id}`;
    const riskLabels = { 'baixo': 'Baixo', 'medio': 'Médio', 'alto': 'Alto', 'low': 'Baixo', 'medium': 'Médio', 'high': 'Alto' };
    const riskLevel = insights.risco_saida?.nivel || insights.turnover_risk?.level || 'baixo';
    const riskReason = insights.risco_saida?.motivo || insights.turnover_risk?.reason || '';
    return `<div class="insight-card" id="card-container-${item.user_id}"><div class="insight-header"><div class="employee-info" style="display: flex; align-items: center; gap: 1rem;">${photoElement}<div><h3>${item.user_name}</h3><p>${item.company || 'Sem empresa'}</p></div></div><div class="feedback-date">${new Date((feedback.feedback_date || feedback.created_at).replace(/-/g, '/')).toLocaleDateString('pt-BR')}</div></div>${insights.resumo ? `<div class="insight-section"><h4>📝 Resumo do Último Feedback</h4><div class="feedback-summary">${insights.resumo}</div></div>` : ''}<div class="insight-section"><h4>⚠️ Risco de Saída</h4><p><span class="risk-badge risk-${riskLevel.toLowerCase().replace('é', 'e')}">${riskLabels[riskLevel.toLowerCase()] || riskLabels['baixo']}</span></p></div>${insights.acoes_pendencias && insights.acoes_pendencias.length > 0 ? `<div class="insight-section"><h4>🎯 Ações ou Pendências</h4>${insights.acoes_pendencias.map(action => `<div class="action-badge">${action}</div>`).join('')}</div>` : `<div class="insight-section"><h4>🎯 Ações ou Pendências</h4><p style="color: #94A3B8; font-size: 0.875rem;">Sem pendências</p></div>`}<div class="expand-btn" id="btn-${cardId}" onclick="toggleCard('${cardId}')">Ver detalhes <svg fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"></path></svg></div><div class="card-expanded" id="expanded-${cardId}"><div class="insight-section"><h4>💪 Fortalezas</h4><ul class="insight-list">${(insights.fortalezas || insights.strengths || []).map(s => `<li>${s}</li>`).join('')}</ul></div><div class="insight-section"><h4>📈 Pontos de Desenvolvimento</h4><ul class="insight-list">${(insights.pontos_desenvolvimento || insights.development_points || []).map(p => `<li>${p}</li>`).join('')}</ul></div><div class="insight-section"><h4>💡 Detalhes do Risco</h4><p style="font-size: 0.875rem; color: #64748B;">${riskReason}</p></div></div></div>`;
}

// --- VIEW: FEEDBACKS ---

let selectedUserForFeedback = null;

async function showFeedbacks() {
    setActiveNav('feedbacks');
    document.getElementById('feedbacks-view').style.display = 'block';
    const form = document.getElementById('feedback-form');
    form.reset();
    document.getElementById('feedback-date').value = new Date().toISOString().split('T')[0];
    const submitButton = form.querySelector('button[type="submit"]');
    submitButton.textContent = 'Salvar Feedback';
    await loadManagedUsers();
}

async function loadManagedUsers() {
    try {
        const response = await fetch('/api/managed-users', { credentials: 'include' });
        const data = await response.json();
        const select = document.getElementById('feedback-user');
        select.innerHTML = '<option value="">Selecione...</option>' + data.managed_users.map(user => `<option value="${user.id}">${user.name} (${user.company})</option>`).join('');
    } catch (error) {
        console.error('Failed to load managed users:', error);
    }
}

async function submitFeedback(event) {
    event.preventDefault();
    const user_id = document.getElementById('feedback-user').value;
    const feedback_date = document.getElementById('feedback-date').value;
    const description = document.getElementById('feedback-description').value;
    if (!user_id || !feedback_date || !description) {
        showToast('Preencha os campos obrigatórios (usuário, data e descrição)', 'warning');
        return;
    }
    // const button = event.target.querySelector('button[type="submit"]');
    const button = event.target.tagName === 'FORM'
        ? event.target.querySelector('button[type="submit"]')
        : event.target;
    
    button.disabled = true;
    button.textContent = 'Processando com IA...';
    try {
        const response = await fetch('/api/feedbacks', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
            body: JSON.stringify({ user_id: parseInt(user_id), feedback_date, description })
        });
        const data = await response.json();
        if (data.success) {
            showToast('Feedback salvo e vetorizado com sucesso!', 'success');
            document.getElementById('feedback-form').reset();
            document.getElementById('feedback-date').value = new Date().toISOString().split('T')[0];
        } else {
            showToast('Erro ao salvar feedback', 'error');
        }
    } catch (error) {
        console.error('Failed to submit feedback:', error);
        showToast('Erro ao salvar feedback', 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Salvar Feedback com IA';
    }
}

async function loadUserLastFeedback() {
    const userId = document.getElementById('feedback-user').value;
    selectedUserForFeedback = userId;
    const lastFeedbackInfo = document.getElementById('last-feedback-info');
    if (!userId) {
        lastFeedbackInfo.style.display = 'none';
        return;
    }
    try {
        const response = await fetch(`/api/user/${userId}/feedbacks`, { credentials: 'include' });
        const data = await response.json();
        if (data.feedbacks && data.feedbacks.length > 0) {
            const lastFeedback = data.feedbacks[0];
            const feedbackDate = new Date(lastFeedback.feedback_date.replace(/-/g, '/')).toLocaleDateString('pt-BR');
            document.getElementById('last-feedback-date').textContent = feedbackDate;
            lastFeedbackInfo.style.display = 'block';
        } else {
            lastFeedbackInfo.style.display = 'none';
        }
    } catch (error) {
        console.error('Failed to load last feedback:', error);
    }
}

async function showFeedbackHistory() {
    if (!selectedUserForFeedback) return;
    try {
        const response = await fetch(`/api/user/${selectedUserForFeedback}/feedbacks`, { credentials: 'include' });
        const data = await response.json();
        if (!data.feedbacks || data.feedbacks.length === 0) {
            showToast('Nenhum feedback anterior encontrado', 'info');
            return;
        }
        const userSelect = document.getElementById('feedback-user');
        const userName = userSelect.options[userSelect.selectedIndex].text.split(' (')[0];
        const modalBody = document.getElementById('modal-body');
        modalBody.innerHTML = `<h3>Histórico de Feedbacks - ${userName}</h3><div style="max-height: 500px; overflow-y: auto; margin-top: 1rem;">${data.feedbacks.map(fb => `<div class="feedback-history-item" style="border: 1px solid #E2E8F0; border-radius: 8px; padding: 1rem; margin-bottom: 1rem;"><div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;"><strong>${new Date(fb.feedback_date.replace(/-/g, '/')).toLocaleDateString('pt-BR')}</strong><button onclick="editFeedback(${fb.id})" class="btn-link">Editar</button></div><div style="font-size: 0.875rem; color: #64748B; white-space: pre-wrap;"><p>${fb.description}</p></div></div>`).join('')}</div>`;
        document.getElementById('modal').style.display = 'flex';
    } catch (error) {
        console.error('Failed to load feedback history:', error);
        showToast('Erro ao carregar histórico', 'error');
    }
}

async function editFeedback(feedbackId) {
    try {
        const response = await fetch(`/api/user/${selectedUserForFeedback}/feedbacks`, { credentials: 'include' });
        const data = await response.json();
        const feedback = data.feedbacks.find(f => f.id === feedbackId);
        if (!feedback) return;
        closeModal();
        document.getElementById('feedback-description').value = feedback.description;
        document.getElementById('feedback-date').value = feedback.feedback_date.split('T')[0];
        const submitButton = document.querySelector('#feedback-form button[type="submit"]');
        submitButton.textContent = 'Atualizar Feedback';
        submitButton.onclick = (event) => updateFeedbackSubmit(event, feedbackId);
        showToast('Feedback carregado para edição', 'info');
    } catch (error) {
        console.error('Failed to edit feedback:', error);
        showToast('Erro ao carregar feedback', 'error');
    }
}

async function updateFeedbackSubmit(event, feedbackId) {
    event.preventDefault();
    const description = document.getElementById('feedback-description').value;
    const feedback_date = document.getElementById('feedback-date').value;
    if (!feedback_date || !description) {
        showToast('Data e descrição são obrigatórios', 'warning');
        return;
    }
    const button = event.target.tagName === 'FORM' ? event.target.querySelector('button[type="submit"]') : event.target;
    button.disabled = true;
    button.textContent = 'Atualizando...';
    try {
        const response = await fetch(`/api/feedbacks/${feedbackId}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
            body: JSON.stringify({ description, feedback_date })
        });
        const data = await response.json();
        if (data.success) {
            showToast('Feedback atualizado com sucesso!', 'success');
            showFeedbacks();
        } else {
            showToast('Erro ao atualizar feedback: ' + data.error, 'error');
        }
    } catch (error) {
        console.error('Failed to update feedback:', error);
        showToast('Erro ao atualizar feedback', 'error');
    } finally {
        button.disabled = false;
    }
}

// --- VIEW: MINHAS REUNIÕES (BLOCO CORRIGIDO E COMPLETO) ---

async function showMeetings() {
    setActiveNav('meetings');
    document.getElementById('meetings-view').style.display = 'block';
    resetMeetingForm();
    loadMeetings();
}

async function loadMeetings() {
    const listContainer = document.getElementById('meetings-list');
    listContainer.innerHTML = '<div class="loading">Carregando reuniões...</div>';
    try {
        const response = await fetch('/api/meetings', { credentials: 'include' });
        const data = await response.json();
        if (data.meetings && data.meetings.length > 0) {
            listContainer.innerHTML = `<div class="meetings-table"><table><thead><tr><th>Data</th><th>Resumo</th><th>Ações</th></tr></thead><tbody>${data.meetings.map(m => `<tr><td>${new Date(m.meeting_date.replace(/-/g, '/')).toLocaleDateString('pt-BR')}</td><td><div class="summary-preview">${m.summary}</div></td><td><div class="action-buttons"><button onclick="viewMeeting(${m.id})" class="btn-small btn-view">Ver</button><button onclick="editMeeting(${m.id})" class="btn-small btn-edit">Editar</button><button onclick="deleteMeeting(${m.id})" class="btn-small btn-delete">Excluir</button></div></td></tr>`).join('')}</tbody></table></div>`;
        } else {
            listContainer.innerHTML = '<div class="no-data"><p>Nenhuma reunião registrada ainda.</p></div>';
        }
    } catch (error) {
        console.error('Failed to load meetings:', error);
        listContainer.innerHTML = '<div class="no-data"><p>Erro ao carregar reuniões.</p></div>';
    }
}

async function generateSummary(event) {
    const transcription = document.getElementById('meeting-transcription').value;
    if (!transcription.trim()) {
        showToast('Por favor, insira uma transcrição para gerar o resumo.', 'warning');
        return;
    }
    const button = event.target;
    button.disabled = true;
    button.textContent = 'Gerando...';
    try {
        const response = await fetch('/api/meetings/summarize', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
            body: JSON.stringify({ transcription })
        });
        const data = await response.json();
        if (response.ok) {
            document.getElementById('meeting-summary').value = data.summary;
            showToast('Resumo gerado com sucesso!', 'success');
        } else {
            showToast(data.error || 'Erro ao gerar resumo', 'error');
        }
    } catch (error) {
        showToast('Erro de comunicação ao gerar resumo', 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Gerar Resumo com IA';
    }
}

async function submitMeeting(event) {
    event.preventDefault();
    const meeting_id = document.getElementById('meeting-id').value;
    const meeting_date = document.getElementById('meeting-date').value;
    const summary = document.getElementById('meeting-summary').value;
    const transcription = document.getElementById('meeting-transcription').value;

    if (!meeting_date || !summary.trim()) {
        showToast('Data e Resumo da reunião são obrigatórios.', 'warning');
        return;
    }

    const isUpdate = !!meeting_id;
    const url = isUpdate ? `/api/meetings/${meeting_id}` : '/api/meetings';
    const method = isUpdate ? 'PUT' : 'POST';

    const button = event.target.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = 'Salvando...';

    try {
        const response = await fetch(url, {
            method: method, headers: { 'Content-Type': 'application/json' }, credentials: 'include',
            body: JSON.stringify({ meeting_date, summary, transcription })
        });
        const data = await response.json();
        if (data.success) {
            showToast(`Reunião ${isUpdate ? 'atualizada' : 'salva'} com sucesso!`, 'success');
            resetMeetingForm();
            loadMeetings();
        } else {
            showToast(`Erro ao ${isUpdate ? 'atualizar' : 'salvar'} reunião.`, 'error');
        }
    } catch (error) {
        showToast(`Erro de comunicação ao ${isUpdate ? 'atualizar' : 'salvar'} reunião.`, 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Salvar Reunião';
    }
}

function resetMeetingForm() {
    document.getElementById('meeting-form').reset();
    document.getElementById('meeting-id').value = '';
    document.getElementById('meeting-date').value = new Date().toISOString().split('T')[0];
}

async function viewMeeting(id) {
    try {
        const response = await fetch(`/api/meetings/${id}`, { credentials: 'include' });
        if (!response.ok) throw new Error('Meeting not found');
        const data = await response.json();
        const modalBody = document.getElementById('modal-body');
        modalBody.innerHTML = `<h3>Detalhes da Reunião</h3><div class="modal-details" style="margin-top: 1.5rem;"><p><strong>Data:</strong> ${new Date(data.meeting_date.replace(/-/g, '/')).toLocaleDateString('pt-BR')}</p><p><strong>Resumo:</strong></p><p>${data.summary}</p>${data.transcription ? `<p><strong>Transcrição:</strong></p><div class="transcription">${data.transcription}</div>` : ''}</div>`;
        document.getElementById('modal').style.display = 'flex';
    } catch (error) {
        showToast('Erro ao carregar detalhes da reunião.', 'error');
    }
}

async function editMeeting(id) {
    try {
        const response = await fetch(`/api/meetings/${id}`, { credentials: 'include' });
        if (!response.ok) throw new Error('Meeting not found');
        const data = await response.json();
        document.getElementById('meeting-id').value = data.id;
        document.getElementById('meeting-date').value = data.meeting_date.split('T')[0];
        document.getElementById('meeting-summary').value = data.summary;
        document.getElementById('meeting-transcription').value = data.transcription || '';
        window.scrollTo(0, 0);
        showToast('Dados da reunião carregados para edição.', 'info');
    } catch (error) {
        showToast('Erro ao carregar reunião para edição.', 'error');
    }
}

// ✅ MODIFICADO: Abre o modal de confirmação
function deleteMeeting(id) {
    const modalBody = document.getElementById('modal-body');
    modalBody.innerHTML = `
        <h3 style="text-align: center; font-size: 1.5rem; color: #DC2626;">Confirmar Exclusão</h3>
        <p style="text-align: center; color: #64748B; margin-top: 1rem;">
            Você tem certeza de que deseja excluir esta reunião? Esta ação não pode ser desfeita.
        </p>
        <div style="display: flex; justify-content: center; gap: 1rem; margin-top: 2rem;">
            <button onclick="closeModal()" class="btn-secondary" style="width: 120px;">Cancelar</button>
            <button onclick="confirmDeleteMeeting(${id})" class="btn-delete-confirm" style="width: 120px;">Sim, Excluir</button>
        </div>
    `;
    // Adicionando um estilo para o botão de confirmação para ser mais robusto
    const style = document.createElement('style');
    style.innerHTML = `
        .btn-delete-confirm {
            padding: 0.75rem 1rem;
            background-color: #DC2626;
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .btn-delete-confirm:hover {
            background-color: #B91C1C;
            transform: translateY(-2px);
        }
    `;
    document.head.appendChild(style);
    document.getElementById('modal').style.display = 'flex';
}

// ✅ NOVO: Executa a exclusão após confirmação
async function confirmDeleteMeeting(id) {
    try {
        const response = await fetch(`/api/meetings/${id}`, { method: 'DELETE', credentials: 'include' });
        const data = await response.json();
        if (data.success) {
            showToast('Reunião excluída com sucesso!', 'success');
            loadMeetings();
        } else {
            showToast('Erro ao excluir reunião.', 'error');
        }
    } catch (error) {
        showToast('Erro de comunicação ao excluir reunião.', 'error');
    } finally {
        closeModal();
    }
}

// --- VIEW: MINHA CONTA ---

async function showAccount() {
    setActiveNav('account');
    document.getElementById('account-view').style.display = 'block';

    if(!currentUser) await validateSession();

    document.getElementById('account-name').value = currentUser.name;
    document.getElementById('account-email').value = currentUser.email;
    document.getElementById('account-company').value = currentUser.company || '';
    document.getElementById('account-phone').value = currentUser.phone || '';
    document.getElementById('account-bio-final').value = currentUser.mini_bio || '';

    await loadUserPhoto();
    await loadAvailableManagers();
}

async function loadUserPhoto() {
    await loadUserAvatarAsync(currentUser.id, '#photo-preview', true);
}

async function loadSidebarUserPhoto() {
    if (currentUser && currentUser.id) {
        await loadUserAvatarAsync(currentUser.id, '#sidebar-user-photo');
    }
}

async function loadAvailableManagers() {
    try {
        const response = await fetch('/api/users', { credentials: 'include' });
        const data = await response.json();
        const select = document.getElementById('account-manager');
        select.innerHTML = '<option value="">Nenhum</option>' + data.users.map(user => `<option value="${user.id}">${user.name} (${user.company})</option>`).join('');
        select.value = currentUser.manager_id || '';
    } catch (error) {
        console.error('Failed to load users:', error);
    }
}

async function updateProfile() {
    const name = document.getElementById('account-name').value;
    const company = document.getElementById('account-company').value;
    const phone = document.getElementById('account-phone').value;
    const manager_id = document.getElementById('account-manager').value;
    const mini_bio = document.getElementById('account-bio-final').value;
    if (!name || !company) {
        showToast('Nome e empresa são obrigatórios', 'warning');
        return;
    }
    const payload = { name, company, phone: phone || '', manager_id: manager_id ? parseInt(manager_id) : null, mini_bio: mini_bio || '' };

    try {
        const response = await fetch('/api/profile', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (data.success) {
            const photoInput = document.getElementById('photo-input');
            if (photoInput.files.length > 0) {
                const reader = new FileReader();
                reader.onload = async function(e) {
                    await fetch('/api/profile/photo', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
                        body: JSON.stringify({ photo: e.target.result })
                    });
                    showToast('Perfil e foto atualizados!', 'success');
                    await validateSession();
                };
                reader.readAsDataURL(photoInput.files[0]);
            } else {
                showToast('Perfil atualizado com sucesso!', 'success');
                await validateSession();
            }
        } else {
            showToast('Erro ao atualizar perfil', 'error');
        }
    } catch (error) {
        showToast('Erro ao atualizar perfil', 'error');
    }
}

async function generateBio(event) {
    const rawText = document.getElementById('account-bio-raw').value;
    if (!rawText.trim()) {
        showToast('Por favor, insira alguns pontos-chave para a IA.', 'warning');
        return;
    }
    const button = event.target;
    button.disabled = true;
    button.textContent = 'Gerando...';
    try {
        const response = await fetch('/api/profile/generate-bio', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
            body: JSON.stringify({ raw_text: rawText })
        });
        const data = await response.json();
        if (response.ok) {
            document.getElementById('account-bio-final').value = data.bio;
            showToast('Bio gerada com sucesso!', 'success');
        } else {
            showToast(data.error || 'Erro ao gerar bio', 'error');
        }
    } catch (error) {
        showToast('Erro de comunicação ao gerar bio', 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Gerar Bio com IA';
    }
}

async function changePassword() {
    const currentPassword = document.getElementById('current-password-modal').value;
    const newPassword = document.getElementById('new-password-modal').value;
    const confirmPassword = document.getElementById('confirm-password-modal').value;
    if (!currentPassword || !newPassword || !confirmPassword) {
        showToast('Preencha todos os campos de senha', 'warning');
        return;
    }
    if (newPassword.length < 6) {
        showToast('A nova senha deve ter no mínimo 6 caracteres', 'warning');
        return;
    }
    if (newPassword !== confirmPassword) {
        showToast('A nova senha e a confirmação não correspondem', 'error');
        return;
    }
    try {
        const response = await fetch('/api/profile/password', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
            body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
        });
        const data = await response.json();
        if (response.ok && data.success) {
            showToast('Senha alterada com sucesso!', 'success');
            closeModal();
        } else {
            showToast(data.error || 'Erro ao alterar a senha', 'error');
        }
    } catch (error) {
        showToast('Ocorreu um erro na requisição', 'error');
    }
}

function openPasswordModal() {
    const modalBody = document.getElementById('modal-body');
    modalBody.innerHTML = `<h3>Alterar Senha</h3><div class="form-group" style="margin-top: 1.5rem;"><label>Senha Atual</label><input type="password" id="current-password-modal" class="form-control"></div><div class="form-group"><label>Nova Senha</label><input type="password" id="new-password-modal" class="form-control"></div><div class="form-group"><label>Confirmar Nova Senha</label><input type="password" id="confirm-password-modal" class="form-control"></div><div style="display: flex; justify-content: flex-end; gap: 1rem; margin-top: 2rem;"><button onclick="closeModal()" class="btn-secondary">Cancelar</button><button onclick="changePassword()" class="btn-primary">Salvar</button></div>`;
    document.getElementById('modal').style.display = 'flex';
}

// --- FOTOS E AVATARES ---

async function loadUserAvatarAsync(userId, elementSelector, isPreview = false) {
    try {
        const response = await fetch(`/api/user/${userId}/photo`, { credentials: 'include' });
        const data = await response.json();
        const element = document.querySelector(elementSelector);
        if (!element) return;
        if (data.photo) {
            if (element.tagName === 'IMG') {
                element.src = data.photo;
                element.style.display = 'block';
            } else {
                const img = document.createElement('img');
                img.src = data.photo;
                img.className = element.className.replace('-placeholder', '').replace('user-avatar-sidebar','').trim() + ' user-avatar';
                 if (element.id === 'sidebar-user-photo') img.className = 'user-avatar-sidebar';
                element.replaceWith(img);
            }
        } else if (isPreview) {
            element.style.display = 'none';
        }
    } catch (error) {
        console.error(`Failed to load avatar for user ${userId}:`, error);
    }
}

function previewPhoto(event) {
    const file = event.target.files[0];
    if (file) {
        if (file.size > 2 * 1024 * 1024) {
            showToast('Foto muito grande. Máximo 2MB', 'warning');
            return;
        }
        const reader = new FileReader();
        reader.onload = function(e) {
            const preview = document.getElementById('photo-preview');
            preview.src = e.target.result;
            preview.style.display = 'block';
        };
        reader.readAsDataURL(file);
    }
}

// --- CHAT WIDGET ---

function renderChatWidget() {
    const container = document.getElementById('chat-widget');
    if (!container || container.innerHTML.trim() !== '') return; // Previne renderização duplicada

    const chatHTML = `
        <button id="chat-button" class="chat-button" onclick="toggleChat()">
            <svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 0 24 24" width="24px" fill="#FFFFFF">
                <path d="M0 0h24v24H0V0z" fill="none"/>
                <path d="M21 6h-2v9H6v2c0 .55.45 1 1 1h11l4 4V7c0-.55-.45-1-1-1zm-4 6V4c0-.55-.45-1-1-1H3c-.55 0-1 .45-1 1v14l4-4h10c.55 0 1-.45 1-1z"/>
            </svg>
        </button>
        <div id="chat-window" class="chat-window" style="display: none;">
            <div class="chat-header">
                <h3>Assistente Cortex</h3>
                <button class="chat-close" onclick="toggleChat()">×</button>
            </div>
            <div id="chat-messages" class="chat-messages">
                <div class="chat-message bot-message">
                    Olá! Como posso ajudar você a encontrar insights sobre seu time hoje?
                </div>
            </div>
            <div class="chat-input-container">
                <input type="text" id="chat-input" placeholder="Pergunte sobre feedbacks...">
                <button class="chat-send-btn" onclick="sendChatMessage()">Enviar</button>
            </div>
        </div>
    `;

    container.innerHTML = chatHTML;

    // Adiciona evento para enviar com "Enter"
    const chatInput = document.getElementById('chat-input');
    if (chatInput) {
        chatInput.addEventListener('keydown', function(event) {
            if (event.key === 'Enter') {
                event.preventDefault(); // Previne quebra de linha
                sendChatMessage();
            }
        });
    }
}

function toggleChat() {
    const chatWindow = document.getElementById('chat-window');
    const chatButton = document.getElementById('chat-button');
    if (chatWindow.style.display === 'none' || chatWindow.style.display === '') {
        chatWindow.style.display = 'flex';
        chatButton.style.display = 'none';
    } else {
        chatWindow.style.display = 'none';
        chatButton.style.display = 'flex';
    }
}

async function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const question = input.value.trim();
    if (!question) return;
    const messagesContainer = document.getElementById('chat-messages');
    messagesContainer.innerHTML += `<div class="chat-message user-message">${question}</div>`;
    input.value = '';
    messagesContainer.innerHTML += `<div class="chat-typing bot-message" id="typing-indicator"><span></span><span></span><span></span></div>`;
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    try {
        const response = await fetch('/api/chat', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
            body: JSON.stringify({ question })
        });
        const data = await response.json();
        document.getElementById('typing-indicator').remove();
        messagesContainer.innerHTML += `<div class="chat-message bot-message">${data.answer || data.error || 'Desculpe, ocorreu um erro.'}</div>`;
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    } catch (error) {
        document.getElementById('typing-indicator').remove();
        messagesContainer.innerHTML += `<div class="chat-message bot-message">Não consegui processar sua pergunta. Tente novamente.</div>`;
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
}

// --- PONTO DE ENTRADA DA APLICAÇÃO ---
document.addEventListener('DOMContentLoaded', () => {
    initializeApp();

    const feedbackForm = document.getElementById('feedback-form');
    if(feedbackForm) {
        feedbackForm.addEventListener('submit', submitFeedback);
    }

    const meetingForm = document.getElementById('meeting-form');
    if(meetingForm) {
        meetingForm.addEventListener('submit', submitMeeting);
    }
});