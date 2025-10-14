let currentUser = null;

function showToast(message, type = 'info', title = null) {
    const container = document.getElementById('toast-container');
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
    setTimeout(() => {
        toast.style.animation = 'slideInRight 0.3s ease reverse';
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}

async function checkAuth() {
    try {
        const response = await fetch('/api/current-user', { credentials: 'include' });
        if (response.ok) {
            const data = await response.json();
            if (data.authenticated) {
                currentUser = data.user;
                showApp();
                return true;
            }
        }
        showAuth();
        return false;
    } catch (error) {
        console.error('Auth check failed:', error);
        showAuth();
        return false;
    }
}

function showAuth() {
    document.getElementById('auth-container').style.display = 'flex';
    document.getElementById('app-container').style.display = 'none';
    document.getElementById('chat-button').style.display = 'none';
    document.getElementById('chat-window').style.display = 'none';
}

function showApp() {
    document.getElementById('auth-container').style.display = 'none';
    document.getElementById('app-container').style.display = 'flex';
    document.getElementById('user-name').textContent = currentUser.name;
    document.getElementById('user-company').textContent = currentUser.company;
    document.getElementById('chat-button').style.display = 'flex';

    loadSidebarUserPhoto(); 

    showDashboard();
}

async function loadSidebarUserPhoto() {
    try {
        const response = await fetch('/api/profile/photo', { credentials: 'include' });
        const data = await response.json();
        const photoElement = document.getElementById('sidebar-user-photo');
        if (data.photo) {
            photoElement.src = data.photo;
            photoElement.style.display = 'block';
        } else {
            photoElement.style.display = 'none';
        }
    } catch (error) {
        console.error('Failed to load sidebar photo:', error);
    }
}

function showLogin() {
    document.getElementById('login-form').style.display = 'block';
    document.getElementById('register-form').style.display = 'none';
}

function showRegister() {
    document.getElementById('login-form').style.display = 'none';
    document.getElementById('register-form').style.display = 'block';
}

async function login() {
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;
    if (!email || !password) {
        showToast('Preencha todos os campos', 'warning');
        return;
    }
    try {
        const response = await fetch('/api/login', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            credentials: 'include', body: JSON.stringify({ email, password })
        });
        const data = await response.json();
        if (data.success) {
            currentUser = data.user;
            showApp();
            showToast('Login realizado com sucesso!', 'success');
        } else {
            showToast('Credenciais inválidas', 'error');
        }
    } catch (error) {
        console.error('Login failed:', error);
        showToast('Erro ao fazer login', 'error');
    }
}

async function register() {
    const name = document.getElementById('register-name').value;
    const company = document.getElementById('register-company').value;
    const phone = document.getElementById('register-phone').value;
    const email = document.getElementById('register-email').value;
    const password = document.getElementById('register-password').value;
    if (!name || !company || !email || !password) {
        showToast('Preencha todos os campos obrigatórios', 'warning');
        return;
    }
    try {
        const response = await fetch('/api/register', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            credentials: 'include', body: JSON.stringify({ name, company, phone, email, password })
        });
        const data = await response.json();
        if (data.success) {
            showToast('Conta criada com sucesso!', 'success');
            await checkAuth();
        } else {
            showToast('Erro ao criar conta: ' + data.error, 'error');
        }
    } catch (error) {
        console.error('Registration failed:', error);
        showToast('Erro ao registrar', 'error');
    }
}

async function logout() {
    try {
        await fetch('/api/logout', { method: 'POST', credentials: 'include' });
        currentUser = null;
        showAuth();
        showToast('Logout realizado', 'info');
    } catch (error) {
        console.error('Logout failed:', error);
    }
}

function setActiveNav(viewName) {
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
    document.querySelectorAll('.view').forEach(view => view.style.display = 'none');
    const viewMap = { 'dashboard': 0, 'feedbacks': 1 };
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

async function showDashboard() {
    setActiveNav('dashboard');
    document.getElementById('dashboard-view').style.display = 'block';
    const content = document.getElementById('dashboard-content');
    content.innerHTML = '<div class="loading">Carregando usuários...</div>';
    try {
        const response = await fetch('/api/dashboard', { credentials: 'include' });
        const data = await response.json();
        if (data.users && data.users.length > 0) {
            content.innerHTML = data.users.map((user, index) => createSkeletonCard(user.user_id, index)).join('');
            data.users.forEach((user, index) => loadUserInsights(user.user_id, index));
        } else {
            content.innerHTML = '<div class="no-data"><p>Nenhum usuário gerenciado. Os usuários devem te selecionar como gestor no perfil deles!</p></div>';
        }
    } catch (error) {
        console.error('Failed to load dashboard:', error);
        content.innerHTML = '<div class="no-data"><p>Erro ao carregar dashboard</p></div>';
    }
}

function createSkeletonCard(userId, index) {
    return `
        <div class="skeleton-card" id="card-container-${userId}">
            <div class="skeleton-header">
                <div class="skeleton-avatar"></div>
                <div class="skeleton-text">
                    <div class="skeleton-line short"></div>
                    <div class="skeleton-line medium"></div>
                </div>
            </div>
            <div class="skeleton-content">
                <div class="skeleton-line long"></div>
                <div class="skeleton-line long"></div>
                <div class="skeleton-line medium"></div>
            </div>
        </div>`;
}

async function loadUserInsights(userId, index) {
    try {
        const response = await fetch(`/api/user/${userId}/insights`, { credentials: 'include' });
        const data = await response.json();
        const container = document.getElementById(`card-container-${userId}`);
        if (container) {
            container.outerHTML = renderUserCard(data, index);
            loadUserAvatarAsync(userId);
        }
    } catch (error) {
        console.error(`Failed to load insights for user ${userId}:`, error);
        const container = document.getElementById(`card-container-${userId}`);
        if (container) {
            container.outerHTML = `<div class="insight-card"><div class="no-data"><p>Erro ao carregar insights</p></div></div>`;
        }
    }
}

function renderUserCard(item, index) {
    const userInitial = item.user_name.charAt(0).toUpperCase();
    const photoElement = `<div class="user-avatar-placeholder" id="avatar-${item.user_id}">${userInitial}</div>`;
    if (!item.latest_feedback) {
        return `
            <div class="insight-card" id="card-container-${item.user_id}">
                <div class="insight-header">
                    <div class="employee-info" style="display: flex; align-items: center; gap: 1rem;">
                        ${photoElement}
                        <div><h3>${item.user_name}</h3><p>${item.company || 'Sem empresa'}</p></div>
                    </div>
                </div>
                <div class="no-data"><p>Nenhum feedback cadastrado ainda</p></div>
            </div>`;
    }
    const insights = item.insights;
    const feedback = item.latest_feedback;
    const cardId = `card-${item.user_id}`;
    const riskLabels = { 'baixo': 'Baixo', 'medio': 'Médio', 'alto': 'Alto', 'low': 'Baixo', 'medium': 'Médio', 'high': 'Alto' };
    const riskLevel = insights.risco_saida?.nivel || insights.turnover_risk?.level || 'baixo';
    const riskReason = insights.risco_saida?.motivo || insights.turnover_risk?.reason || '';
    return `
        <div class="insight-card" id="card-container-${item.user_id}">
            <div class="insight-header">
                <div class="employee-info" style="display: flex; align-items: center; gap: 1rem;">
                    ${photoElement}
                    <div><h3>${item.user_name}</h3><p>${item.company || 'Sem empresa'}</p></div>
                </div>
                <div class="feedback-date">${new Date((feedback.feedback_date || feedback.created_at).replace(/-/g, '/')).toLocaleDateString('pt-BR')}</div>
            </div>
            ${insights.resumo ? `<div class="insight-section"><h4>📝 Resumo do Último Feedback</h4><div class="feedback-summary">${insights.resumo}</div></div>` : ''}
            <div class="insight-section"><h4>⚠️ Risco de Saída</h4><p><span class="risk-badge risk-${riskLevel.toLowerCase().replace('é', 'e')}">${riskLabels[riskLevel.toLowerCase()] || riskLabels['baixo']}</span></p></div>
            ${insights.acoes_pendencias && insights.acoes_pendencias.length > 0 ? `
                <div class="insight-section"><h4>🎯 Ações ou Pendências</h4>${insights.acoes_pendencias.map(action => `<div class="action-badge">${action}</div>`).join('')}</div>
            ` : `
                <div class="insight-section"><h4>🎯 Ações ou Pendências</h4><p style="color: #94A3B8; font-size: 0.875rem;">Sem pendências</p></div>
            `}
            <div class="expand-btn" id="btn-${cardId}" onclick="toggleCard('${cardId}')">Ver detalhes <svg fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"></path></svg></div>
            <div class="card-expanded" id="expanded-${cardId}">
                <div class="insight-section"><h4>💪 Fortalezas</h4><ul class="insight-list">${(insights.fortalezas || insights.strengths || []).map(s => `<li>${s}</li>`).join('')}</ul></div>
                <div class="insight-section"><h4>📈 Pontos de Desenvolvimento</h4><ul class="insight-list">${(insights.pontos_desenvolvimento || insights.development_points || []).map(p => `<li>${p}</li>`).join('')}</ul></div>
                <div class="insight-section"><h4>💡 Detalhes do Risco</h4><p style="font-size: 0.875rem; color: #64748B;">${riskReason}</p></div>
            </div>
        </div>`;
}

async function showFeedbacks() {
    setActiveNav('feedbacks');
    document.getElementById('feedbacks-view').style.display = 'block';
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('feedback-date').value = today;
    await loadManagedUsers();
}

async function loadManagedUsers() {
    try {
        const response = await fetch('/api/managed-users', { credentials: 'include' });
        const data = await response.json();
        const select = document.getElementById('feedback-user');
        select.innerHTML = '<option value="">Selecione...</option>' +
            data.managed_users.map(user => `<option value="${user.id}">${user.name} (${user.company})</option>`).join('');
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
    const button = event.target;
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

async function showAccount() {
    await checkAuth();
    document.querySelectorAll('.view').forEach(view => view.style.display = 'none');
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
    document.getElementById('account-view').style.display = 'block';
    document.getElementById('account-name').value = currentUser.name;
    document.getElementById('account-email').value = currentUser.email;
    document.getElementById('account-company').value = currentUser.company || '';
    document.getElementById('account-phone').value = currentUser.phone || '';
    await loadAvailableManagers();
    await loadUserPhoto();
}

async function loadUserPhoto() {
    try {
        const response = await fetch('/api/profile/photo', { credentials: 'include' });
        const data = await response.json();
        if (data.photo) {
            const preview = document.getElementById('photo-preview');
            preview.src = data.photo;
            preview.style.display = 'block';
        }
    } catch (error) {
        console.error('Failed to load photo:', error);
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

async function loadAvailableManagers() {
    try {
        const response = await fetch('/api/users', { credentials: 'include' });
        const data = await response.json();
        const select = document.getElementById('account-manager');
        select.innerHTML = '<option value="">Nenhum</option>' +
            data.users.map(user => `<option value="${user.id}">${user.name} (${user.company})</option>`).join('');

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
    if (!name || !company) {
        showToast('Nome e empresa são obrigatórios', 'warning');
        return;
    }
    try {
        const response = await fetch('/api/profile', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
            body: JSON.stringify({ name, company, phone: phone || '', manager_id: manager_id ? parseInt(manager_id) : null })
        });
        const data = await response.json();
        if (data.success) {
            const photoInput = document.getElementById('photo-input');
            if (photoInput.files.length > 0) {
                const reader = new FileReader();
                reader.onload = async function(e) {
                    try {
                        await fetch('/api/profile/photo', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            credentials: 'include', body: JSON.stringify({ photo: e.target.result })
                        });
                        showToast('Perfil e foto atualizados com sucesso!', 'success');
                        await checkAuth();
                    } catch (error) {
                        console.error('Failed to update photo:', error);
                        showToast('Perfil atualizado, mas erro ao salvar foto', 'warning');
                    }
                };
                reader.readAsDataURL(photoInput.files[0]);
            } else {
                showToast('Perfil atualizado com sucesso!', 'success');
                await checkAuth();
            }
        } else {
            showToast('Erro ao atualizar perfil', 'error');
        }
    } catch (error) {
        console.error('Failed to update profile:', error);
        showToast('Erro ao atualizar perfil', 'error');
    }
}

async function changePassword() {
    // Busca os valores dos campos DENTRO DO MODAL
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
            closeModal(); // <-- FECHA O MODAL APÓS O SUCESSO
        } else {
            showToast(data.error || 'Erro ao alterar a senha', 'error');
        }
    } catch (error) {
        console.error('Failed to change password:', error);
        showToast('Ocorreu um erro na requisição', 'error');
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

let selectedUserForFeedback = null;

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
        modalBody.innerHTML = `
            <h3>Histórico de Feedbacks - ${userName}</h3>
            <div style="max-height: 500px; overflow-y: auto; margin-top: 1rem;">
                ${data.feedbacks.map(fb => `
                    <div class="feedback-history-item" style="border: 1px solid #E2E8F0; border-radius: 8px; padding: 1rem; margin-bottom: 1rem;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                            <strong>${new Date(fb.feedback_date.replace(/-/g, '/')).toLocaleDateString('pt-BR')}</strong>
                            <button onclick="editFeedback(${fb.id})" class="btn-link">Editar</button>
                        </div>
                        <div style="font-size: 0.875rem; color: #64748B; white-space: pre-wrap;">
                            <p>${fb.description}</p>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
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
        const submitButton = document.querySelector('#feedback-form .btn-primary');
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
    const button = event.target;
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
            document.getElementById('feedback-form').reset();
            document.getElementById('feedback-date').value = new Date().toISOString().split('T')[0];
            button.textContent = 'Salvar Feedback com IA';
            button.onclick = submitFeedback;
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

async function loadUserAvatarAsync(userId) {
    try {
        const response = await fetch(`/api/user/${userId}/photo`, { credentials: 'include' });
        const data = await response.json();
        const avatarElement = document.getElementById(`avatar-${userId}`);
        if (avatarElement && data.photo) {
            const img = document.createElement('img');
            img.src = data.photo;
            img.className = 'user-avatar';
            img.alt = 'Foto de perfil';
            avatarElement.replaceWith(img);
        }
    } catch (error) {
        console.error(`Failed to load avatar for user ${userId}:`, error);
    }
}

async function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const question = input.value.trim();
    if (!question) return;
    const messagesContainer = document.getElementById('chat-messages');
    const userMessage = document.createElement('div');
    userMessage.className = 'chat-message user-message';
    userMessage.textContent = question;
    messagesContainer.appendChild(userMessage);
    input.value = '';
    const typingIndicator = document.createElement('div');
    typingIndicator.className = 'chat-typing bot-message';
    typingIndicator.innerHTML = '<span></span><span></span><span></span>';
    messagesContainer.appendChild(typingIndicator);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    try {
        const response = await fetch('/api/chat', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            credentials: 'include', body: JSON.stringify({ question })
        });
        const data = await response.json();
        messagesContainer.removeChild(typingIndicator);
        const botMessage = document.createElement('div');
        botMessage.className = 'chat-message bot-message';
        botMessage.textContent = data.answer || data.error || 'Desculpe, ocorreu um erro.';
        messagesContainer.appendChild(botMessage);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    } catch (error) {
        console.error('Chat error:', error);
        messagesContainer.removeChild(typingIndicator);
        const errorMessage = document.createElement('div');
        errorMessage.className = 'chat-message bot-message';
        errorMessage.textContent = 'Desculpe, não consegui processar sua pergunta. Tente novamente.';
        messagesContainer.appendChild(errorMessage);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    checkAuth();
});

function openPasswordModal() {
    const modalBody = document.getElementById('modal-body');
    modalBody.innerHTML = `
        <h3>Alterar Senha</h3>
        <div class="form-group" style="margin-top: 1.5rem;">
            <label>Senha Atual</label>
            <input type="password" id="current-password-modal" class="form-control" placeholder="Sua senha atual">
        </div>
        <div class="form-group">
            <label>Nova Senha</label>
            <input type="password" id="new-password-modal" class="form-control" placeholder="Mínimo 6 caracteres">
        </div>
        <div class="form-group">
            <label>Confirmar Nova Senha</label>
            <input type="password" id="confirm-password-modal" class="form-control" placeholder="Repita a nova senha">
        </div>
        <div style="display: flex; justify-content: flex-end; gap: 1rem; margin-top: 2rem;">
            <button onclick="closeModal()" class="btn-secondary">Cancelar</button>
            <button onclick="changePassword()" class="btn-primary">Salvar Nova Senha</button>
        </div>
    `;
    document.getElementById('modal').style.display = 'flex';
}