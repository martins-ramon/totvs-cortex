let currentUser = null;
let allNotifications = [];
let notificationEventSource = null;

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

    renderChatWidget();
    const chatWidget = document.getElementById('chat-widget');
    if (chatWidget) {
        chatWidget.style.display = 'block';
    }

    loadSidebarUserPhoto();
    fetchInitialNotifications();

    // ✅ LÓGICA DE POSICIONAMENTO DO PAINEL
    const bell = document.getElementById('notification-bell');
    const panel = document.getElementById('notifications-panel');
    bell.addEventListener('click', (e) => {
        e.stopPropagation();
        if (panel.style.display === 'block') {
            panel.style.display = 'none';
        } else {
            const bellRect = bell.getBoundingClientRect();
            panel.style.top = `${bellRect.bottom + 10}px`;
            panel.style.left = `${bellRect.left - panel.offsetWidth + bell.offsetWidth}px`;
            panel.style.display = 'block';
        }
    });
    document.addEventListener('click', (e) => {
        if (!panel.contains(e.target) && e.target !== bell) {
            panel.style.display = 'none';
        }
    });

    showMyTeam();
}

async function logout() {
    if (notificationEventSource) {
        notificationEventSource.close();
    }
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

    // ✅ REMOVIDO 'notifications' do mapa de navegação
    const viewMap = { 'my-team': 0, 'feedbacks': 1, 'meetings': 2, 'digital-staff': 3 }; 
    const navItems = document.querySelectorAll('.sidebar-nav .nav-item');
    if (viewMap[viewName] !== undefined && navItems[viewMap[viewName]]) {
        navItems[viewMap[viewName]].classList.add('active');
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

// --- COMPONENTE DE AUTOCOMPLETE REUTILIZÁVEL ---
function createAutocomplete(
    containerId,
    items,
    {
        placeholder = 'Selecione...',
        isMulti = false,
        initialValue = null,
        onSelectionChange = () => {}
    }
) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    let selectedItems = [];
    const hiddenInput = document.createElement('input');
    hiddenInput.type = 'hidden';
    hiddenInput.id = `${containerId}-value`;
    container.appendChild(hiddenInput);

    const autocompleteContainer = document.createElement('div');
    autocompleteContainer.className = 'autocomplete-container';
    container.appendChild(autocompleteContainer);

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'autocomplete-input';
    input.placeholder = placeholder;

    const suggestionsPanel = document.createElement('div');
    suggestionsPanel.className = 'autocomplete-suggestions';
    suggestionsPanel.style.display = 'none';
    container.appendChild(suggestionsPanel);

    const renderTokens = () => {
        autocompleteContainer.querySelectorAll('.token').forEach(t => t.remove());
        selectedItems.forEach(item => {
            const token = document.createElement('div');
            token.className = 'token';
            token.textContent = item.name;
            const removeBtn = document.createElement('span');
            removeBtn.className = 'token-remove';
            removeBtn.innerHTML = '&times;';
            removeBtn.onclick = () => {
                selectedItems = selectedItems.filter(i => i.id !== item.id);
                updateHiddenInput();
                renderTokens();
                onSelectionChange(null);
            };
            token.appendChild(removeBtn);
            autocompleteContainer.insertBefore(token, input);
        });
        updateHiddenInput();
    };

    const updateHiddenInput = () => {
        if (isMulti) {
            hiddenInput.value = JSON.stringify(selectedItems.map(item => item.id));
        } else {
            hiddenInput.value = selectedItems.length > 0 ? selectedItems[0].id : '';
        }
    };

    const handleSelection = (item) => {
        if (isMulti) {
            if (!selectedItems.some(i => i.id === item.id)) {
                selectedItems.push(item);
            }
            input.value = '';
            renderTokens();
        } else {
            selectedItems = [item];
            input.value = item.name;
        }
        updateHiddenInput();
        onSelectionChange(item.id);
        suggestionsPanel.style.display = 'none';
    };

    input.addEventListener('input', () => {
        const query = input.value.toLowerCase();
        if (!query && !isMulti) {
             selectedItems = [];
             updateHiddenInput();
             onSelectionChange(null);
        }

        const filteredItems = items.filter(item =>
            item.name.toLowerCase().includes(query) &&
            !selectedItems.some(sel => sel.id === item.id)
        );

        suggestionsPanel.innerHTML = '';
        if (filteredItems.length) {
            filteredItems.forEach(item => {
                const suggestionItem = document.createElement('div');
                suggestionItem.className = 'suggestion-item';
                suggestionItem.textContent = item.name;
                if (item.company) {
                    const companySpan = document.createElement('small');
                    companySpan.textContent = `(${item.company})`;
                    suggestionItem.appendChild(companySpan);
                }
                suggestionItem.onclick = () => handleSelection(item);
                suggestionsPanel.appendChild(suggestionItem);
            });
            suggestionsPanel.style.display = 'block';
        } else {
            suggestionsPanel.style.display = 'none';
        }
    });

    input.addEventListener('focus', () => {
        if (input.value) input.dispatchEvent(new Event('input'));
    });

    document.addEventListener('click', (e) => {
        if (!container.contains(e.target)) {
            suggestionsPanel.style.display = 'none';
            if (!isMulti && selectedItems.length > 0 && input.value !== selectedItems[0].name) {
                input.value = selectedItems[0].name;
            }
        }
    });

    autocompleteContainer.appendChild(input);

    if (initialValue) {
        if (isMulti) {
            selectedItems = items.filter(item => initialValue.includes(item.id));
            renderTokens();
        } else {
            const initialItem = items.find(item => item.id === initialValue);
            if (initialItem) {
                handleSelection(initialItem);
            }
        }
    }
}

// --- SEÇÃO DE NOTIFICAÇÕES ---

let latestNotificationId = null;

async function pollForNotifications() {
    // Se não tivermos um ID de referência inicial, não fazemos polling para evitar inconsistência
    if (!latestNotificationId) return;

    try {
        const response = await fetch(`/api/notifications/poll?since_id=${latestNotificationId}`);
        if (!response.ok) {
            // Silencia erros de console comuns em polling para não poluir, a menos que seja crítico
            if (response.status !== 304) console.error(`Erro polling: ${response.status}`);
            return;
        }

        const newNotifications = await response.json();

        if (newNotifications && newNotifications.length > 0) {
            let listChanged = false;

            // Itera sobre as novas notificações recebidas
            newNotifications.forEach(notification => {
                // ✅ CORREÇÃO: Verifica se este ID já existe na lista local
                const alreadyExists = allNotifications.some(n => n.id === notification.id);

                if (!alreadyExists) {
                    // Se não existe, adiciona ao topo da lista
                    allNotifications.unshift(notification);

                    // Exibe o Toast apenas se for realmente nova na tela
                    // Se for do tipo AI, usa o título do agente, senão o título padrão
                    const toastTitle = notification.agent_name ? `🤖 ${notification.agent_name}` : notification.title;
                    showToast(notification.message, 'info', toastTitle);

                    listChanged = true;
                }
            });

            if (listChanged) {
                // ✅ GARANTIA: Reordena a lista completa por ID (do maior/recente para o menor)
                // Isso corrige qualquer problema visual de ordem
                allNotifications.sort((a, b) => b.id - a.id);

                // Atualiza o ponteiro do ID mais recente baseado na lista sanitizada
                latestNotificationId = allNotifications[0].id;

                // Recalcula o badge baseado na lista única (sem duplicados)
                const unreadCount = allNotifications.filter(n => !n.is_read).length;
                updateNotificationBadge(unreadCount);

                // Atualiza a interface
                renderNotificationsPanel();
            }
        }
    } catch (error) {
        console.error('Erro durante o polling de notificações:', error);
    }
}

// ✅ ALTERADO: A função de carga inicial agora configura o ID para o polling
async function fetchInitialNotifications() {
    try {
        const response = await fetch('/api/notifications', { credentials: 'include' });
        const data = await response.json();
        allNotifications = data.notifications || [];
        updateNotificationBadge(data.unread_count || 0);
        renderNotificationsPanel();

        // Se houver notificações, guarda o ID da mais recente
        if (allNotifications.length > 0) {
            latestNotificationId = allNotifications[0].id;
        }

        // Inicia o loop de polling
        setInterval(pollForNotifications, 5000);

    } catch (error) {
        console.error('Falha ao buscar o estado inicial das notificações:', error);
    }
}

function updateNotificationBadge(count) {
    const badge = document.getElementById('notification-badge');
    if (count > 0) {
        badge.textContent = count > 9 ? '9+' : count;
        badge.style.display = 'flex';
    } else {
        badge.style.display = 'none';
    }
}

function renderNotificationsPanel() {
    const body = document.getElementById('notifications-body');

    if (allNotifications.length === 0) {
        body.innerHTML = '<div class="no-notifications">Nenhuma notificação.</div>';
        return;
    }

    body.innerHTML = allNotifications.map(n => `
        <div class="notification-item ${!n.is_read ? 'unread' : ''}" onclick="handleNotificationClick(${n.id}, '${n.link || ''}')">
            <strong>${n.title}</strong>
            <p>${n.message}</p>
            <small>${new Date(n.created_at).toLocaleString('pt-BR')}</small>
        </div>
    `).join('');
}

async function handleNotificationClick(id, link) {
    const notification = allNotifications.find(n => n.id === id);
    if (!notification) return;

    // Fecha o painel suspenso
    document.getElementById('notifications-panel').style.display = 'none';

    // Ao abrir, marcamos visualmente como lida (padrão de e-mail), 
    // mas o usuário pode reverter com o toggle.
    if (!notification.is_read) {
        await toggleNotificationStatus(id, true); 
    }

    viewNotificationDetails(notification);
}

function viewNotificationDetails(notification) {
    const modalBody = document.getElementById('modal-body');
    const dateStr = new Date(notification.created_at).toLocaleString('pt-BR');

    // Layout limpo e padrão para TODAS as notificações
    // A complexidade visual de IA agora vive exclusivamente no Drawer da Staff Digital
    const contentHTML = `
        <h3 style="margin-bottom: 0.25rem; color: #1E293B; padding-right: 20px;">${notification.title}</h3>
        <small style="color: #94A3B8; display: block; margin-bottom: 1.5rem; border-bottom: 1px solid #F1F5F9; padding-bottom: 1rem;">
            Recebido em: ${dateStr}
        </small>

        <div style="color: #475569; line-height: 1.8; font-size: 1rem;">
            ${notification.message.replace(/\n/g, '<br>')}
        </div>
    `;

    const toggleHTML = `
        <div class="toggle-wrapper">
            <span class="toggle-label">Marcar como lida</span>
            <label class="switch">
                <input type="checkbox" id="notif-toggle-${notification.id}" 
                    ${notification.is_read ? 'checked' : ''} 
                    onchange="handleToggleChange(${notification.id}, this.checked)">
                <span class="slider"></span>
            </label>
        </div>
    `;

    modalBody.innerHTML = contentHTML + toggleHTML;
    document.getElementById('modal').style.display = 'flex';
}

async function handleToggleChange(id, isChecked) {
    // Feedback visual imediato (Toast)
    if (isChecked) {
        showToast('Notificação marcada como lida.', 'success');
    } else {
        showToast('Notificação marcada como não lida.', 'info');
    }

    await toggleNotificationStatus(id, isChecked);
}

async function toggleNotificationStatus(id, isRead) {
    try {
        // 1. Atualiza estado local
        const notification = allNotifications.find(n => n.id === id);
        if (notification) notification.is_read = isRead;

        // 2. Atualiza Badge do sino
        const unreadCount = allNotifications.filter(n => !n.is_read).length;
        updateNotificationBadge(unreadCount);

        // 3. Atualiza a lista do painel em background
        renderNotificationsPanel();

        // 4. Persiste no Backend
        await fetch(`/api/notifications/${id}/status`, { 
            method: 'PUT', 
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ is_read: isRead })
        });

    } catch (error) {
        console.error('Erro ao sincronizar status da notificação:', error);
    }
}

async function markNotificationAsRead(id) {
    try {
        await fetch(`/api/notifications/${id}/read`, { method: 'POST', credentials: 'include' });
        const notification = allNotifications.find(n => n.id === id);
        if (notification) notification.is_read = true;
        const unreadCount = allNotifications.filter(n => !n.is_read).length;
        updateNotificationBadge(unreadCount);
        renderNotificationsPanel();
    } catch (error) {
        console.error('Falha ao marcar notificação como lida:', error);
    }
}

async function markAllAsRead() {
    try {
        await fetch('/api/notifications/read-all', { method: 'POST', credentials: 'include' });
        allNotifications.forEach(n => n.is_read = true);
        updateNotificationBadge(0);
        renderNotificationsPanel();
    } catch (error) {
        console.error('Falha ao marcar todas como lidas:', error);
    }
}


// --- VIEW: MEU TIME ---
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

    // Tratamento de risco
    const riskLabels = { 'baixo': 'Baixo', 'medio': 'Médio', 'alto': 'Alto', 'low': 'Baixo', 'medium': 'Médio', 'high': 'Alto' };
    const riskLevelRaw = insights.risco_saida?.nivel || insights.turnover_risk?.level || 'baixo';
    const riskLevel = riskLabels[riskLevelRaw.toLowerCase()] || 'Baixo';
    const riskReason = insights.risco_saida?.motivo || insights.turnover_risk?.reason || '';
    const riskClass = riskLevelRaw.toLowerCase().replace('é', 'e'); // normaliza 'médio' para css 'medio'

    // Nome do Agente
    const agentName = insights.agent_name || "Sarah";

    return `
    <div class="insight-card" id="card-container-${item.user_id}">
        <div class="insight-header">
            <div class="employee-info" style="display: flex; align-items: center; gap: 1rem;">
                ${photoElement}
                <div>
                    <h3>${item.user_name}</h3>
                    <p>${item.company || 'Sem empresa'}</p>
                </div>
            </div>
            <div style="text-align: right;">
                <div class="feedback-date">${new Date(feedback.feedback_date.replace(/-/g, '/')).toLocaleDateString('pt-BR')}</div>
                <div style="font-size: 0.7rem; color: #6366F1; font-weight: 600; margin-top: 4px;">🤖 ${agentName}</div>
            </div>
        </div>

        ${insights.resumo ? `
        <div class="insight-section">
            <h4>📝 Resumo do Último Feedback</h4>
            <div class="feedback-summary">${insights.resumo}</div>
        </div>` : ''}

        <div class="insight-section">
            <h4>⚠️ Risco de Saída</h4>
            <p><span class="risk-badge risk-${riskClass}">${riskLevel}</span></p>
        </div>

        <div class="insight-section">
            <h4>🎯 Ações Sugeridas</h4>
            ${(insights.acoes_pendencias || []).length > 0 
                ? (insights.acoes_pendencias || []).map(action => `<div class="action-badge">${action}</div>`).join('')
                : `<p style="color: #94A3B8; font-size: 0.875rem;">Nenhuma ação pendente</p>`
            }
        </div>

        <div class="expand-btn" id="btn-${cardId}" onclick="toggleCard('${cardId}')">
            Ver análise completa <svg fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"></path></svg>
        </div>

        <div class="card-expanded" id="expanded-${cardId}">
            <div class="insight-section">
                <h4>💪 Fortalezas</h4>
                <ul class="insight-list">
                    ${(insights.fortalezas || []).map(s => `<li>${s}</li>`).join('')}
                </ul>
            </div>
            <div class="insight-section">
                <h4>📈 Pontos de Desenvolvimento</h4>
                <ul class="insight-list">
                    ${(insights.pontos_desenvolvimento || []).map(p => `<li>${p}</li>`).join('')}
                </ul>
            </div>
            ${riskReason ? `
            <div class="insight-section">
                <h4>💡 Contexto do Risco</h4>
                <p style="font-size: 0.875rem; color: #64748B; background: #FFF1F2; padding: 0.75rem; border-radius: 6px; border-left: 3px solid #F43F5E;">${riskReason}</p>
            </div>` : ''}
        </div>
    </div>`;
}

// --- VIEW: FEEDBACKS ---

let selectedUserForFeedback = null;

async function showFeedbacks() {
    setActiveNav('feedbacks');
    document.getElementById('feedbacks-view').style.display = 'block';

    const container = document.getElementById('feedbacks-content');
    container.innerHTML = '<div class="loading">Verificando permissões...</div>';

    try {
        // Verifica se é gestor tentando carregar liderados
        const response = await fetch('/api/managed-users', { credentials: 'include' });
        const data = await response.json();

        // --- VISÃO DE GESTOR (Tem liderados) ---
        if (data.managed_users && data.managed_users.length > 0) {
            renderManagerFeedbackView(data.managed_users);
        } else {
            // --- VISÃO DE COLABORADOR (Não tem liderados) ---
            await renderEmployeeFeedbackView();
        }
    } catch (error) {
        console.error(error);
        container.innerHTML = '<div class="no-data">Erro ao carregar módulo de feedbacks.</div>';
    }
}

// --- VISÃO GESTOR ---
function renderManagerFeedbackView(managedUsers) {
    const container = document.getElementById('feedbacks-content');
    container.innerHTML = `
        <form id="feedback-form">
            <div class="form-group">
                <label>Selecionar Funcionário *</label>
                <div id="feedback-user-container" class="autocomplete-wrapper"></div>
                <div id="last-feedback-info" style="display: none; margin-top: 0.5rem;">
                    <small style="color: #64748B;">Último feedback: <span id="last-feedback-date"></span></small>
                    <button type="button" onclick="showFeedbackHistory()" class="btn-link" style="margin-left: 1rem;">Ver histórico</button>
                </div>
            </div>

            <div class="form-group">
                <label>Data do Feedback *</label>
                <input type="date" id="feedback-date" class="form-control" value="${new Date().toISOString().split('T')[0]}" required>
            </div>

            <div class="form-group" style="background: #F8FAFC; padding: 1rem; border-radius: 8px; border: 1px dashed #CBD5E1;">
                <label>Transcrição / Anotações Brutas (Opcional)</label>
                <textarea id="feedback-transcription" class="form-control" rows="4" placeholder="Cole a transcrição da reunião ou anotações rápidas aqui..."></textarea>
                <button type="button" onclick="generateFeedbackSummary(event)" class="btn-ai-action" style="margin-top: 0.75rem;">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                        <path d="M13 6a3 3 0 11-6 0 3 3 0 016 0zM18 8a2 2 0 11-4 0 2 2 0 014 0zM14 15a4 4 0 00-8 0v3h8v-3zM6 8a2 2 0 11-4 0 2 2 0 014 0zM16 18v-3a5.972 5.972 0 00-.75-2.906A3.005 3.005 0 0119 15v3h-3zM4.75 12.094A5.973 5.973 0 004 15v3H1v-3a3 3 0 013.75-2.906z" />
                    </svg>
                    ✨ Sarah: Extrair Inteligência
                </button>
            </div>

            <div class="form-group">
                <label>Registro Gerencial (Visão do Gestor) *</label>
                <div id="feedback-description" class="form-control rich-editor" contenteditable="true" placeholder="O registro oficial técnico e comportamental..."></div>
            </div>

            <div class="form-group" style="background: #F0FDF4; padding: 1rem; border-radius: 8px; border: 1px solid #BBF7D0;">
                <label style="color: #15803D;">Feedback para o Participante (Visível para ele)</label>
                <div id="feedback-employee-msg" class="form-control rich-editor" contenteditable="true" style="background: white;" placeholder="Mensagem de desenvolvimento..."></div>
                <button type="button" onclick="generateEmployeeMsg(event)" class="btn-ai-action" style="margin-top: 0.75rem;">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-11a1 1 0 10-2 0v2H7a1 1 0 100 2h2v2a1 1 0 102 0v-2h2a1 1 0 100-2h-2V7z" clip-rule="evenodd" />
                    </svg>
                    ✍️ Sarah: Rascunhar PDI
                </button>
            </div>

            <button type="submit" class="btn-primary btn-large">Salvar Feedback</button>
        </form>
    `;

    // Re-inicializa o autocomplete e o listener do form
    createAutocomplete('feedback-user-container', managedUsers, {
        placeholder: 'Digite para buscar um liderado...',
        isMulti: false,
        onSelectionChange: (selectedId) => {
            selectedUserForFeedback = selectedId;
            if (selectedId) loadUserLastFeedback();
            else document.getElementById('last-feedback-info').style.display = 'none';
        }
    });

    document.getElementById('feedback-form').addEventListener('submit', submitFeedbackUpdated);
}

// --- VISÃO COLABORADOR ---
async function renderEmployeeFeedbackView() {
    const container = document.getElementById('feedbacks-content');
    container.innerHTML = '<div class="loading">Carregando seus feedbacks recebidos...</div>';

    try {
        const response = await fetch('/api/my-received-feedbacks', { credentials: 'include' });
        const data = await response.json();

        if (data.feedbacks && data.feedbacks.length > 0) {
            container.innerHTML = `
                <h3 style="margin-bottom: 1.5rem; color: #1E293B;">Feedbacks Recebidos</h3>
                <div class="timeline-container">
                    ${data.feedbacks.map(fb => `
                        <div class="insight-card" style="margin-bottom: 1.5rem;">
                            <div class="insight-header">
                                <div style="display: flex; align-items: center; gap: 0.5rem;">
                                    <div class="user-avatar-placeholder" style="width: 32px; height: 32px; font-size: 0.9rem;">${fb.manager.charAt(0)}</div>
                                    <div>
                                        <div style="font-weight: 600; font-size: 0.9rem;">${fb.manager}</div>
                                        <div style="font-size: 0.75rem; color: #64748B;">Gestor</div>
                                    </div>
                                </div>
                                <div class="feedback-date">${new Date(fb.date).toLocaleDateString('pt-BR')}</div>
                            </div>
                            <div style="margin-top: 1rem; color: #334155; line-height: 1.6; white-space: pre-wrap;">${fb.message}</div>
                        </div>
                    `).join('')}
                </div>
            `;
        } else {
            container.innerHTML = `
                <div class="no-data">
                    <p>Você ainda não recebeu feedbacks registrados na plataforma.</p>
                </div>`;
        }
    } catch (error) {
        container.innerHTML = '<div class="no-data">Erro ao carregar feedbacks.</div>';
    }
}

// --- FUNÇÕES DE APOIO IA ---

async function generateFeedbackSummary(event) {
    const transcription = document.getElementById('feedback-transcription').value;
    // ✅ NOVO: Captura a data selecionada
    const feedbackDate = document.getElementById('feedback-date').value; 

    if (!transcription) { 
        showToast('Insira uma transcrição primeiro.', 'warning'); 
        return; 
    }

    // Validação opcional: garante que tem data para dar contexto à IA
    if (!feedbackDate) {
        showToast('Selecione a data do feedback para dar contexto à IA.', 'warning');
        return;
    }

    const btn = event.target;
    const originalText = btn.textContent;
    btn.disabled = true; btn.textContent = 'Sarah está resumindo...';

    try {
        const res = await fetch('/api/feedbacks/generate-summary', {
            method: 'POST', 
            headers: {'Content-Type': 'application/json'},
            // ✅ NOVO: Envia feedback_date no payload
            body: JSON.stringify({ transcription, feedback_date: feedbackDate }) 
        });
        const data = await res.json();
        if (data.result) {
            // ✅ NOVO: Converte e insere
            document.getElementById('feedback-description').innerHTML = marked.parse(data.result);
        }
    } catch(e) { 
        showToast('Erro na IA', 'error'); 
        console.error(e);
    } 
    finally { 
        btn.disabled = false; btn.textContent = originalText; 
    }
}

async function generateEmployeeMsg(event) {
    const description = document.getElementById('feedback-description').innerText;
    const transcription = document.getElementById('feedback-transcription').value;

    // ✅ CAPTURA DO NOME:
    // O autocomplete cria um input com a classe .autocomplete-input dentro do container
    const nameInput = document.querySelector('#feedback-user-container .autocomplete-input');
    const employeeName = nameInput ? nameInput.value : '';

    if (!description && !transcription) { 
        showToast('Preencha o registro gerencial ou a transcrição para a Sarah analisar.', 'warning'); 
        return; 
    }

    if (!employeeName) {
        showToast('Selecione um funcionário para personalizar a mensagem.', 'warning');
        return;
    }

    const btn = event.target;
    const originalText = btn.textContent;
    btn.disabled = true; 
    btn.textContent = 'Sarah está escrevendo...';

    try {
        const res = await fetch('/api/feedbacks/generate-employee-msg', {
            method: 'POST', 
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ 
                description, 
                transcription,
                employee_name: employeeName // ✅ Envia o nome
            })
        });
        const data = await res.json();
        if (data.result) {
            // ✅ NOVO: Converte e insere
            document.getElementById('feedback-employee-msg').innerHTML = marked.parse(data.result);
            showToast('Mensagem gerada com sucesso!', 'success');
        }
    } catch(e) { 
        showToast('Erro ao gerar mensagem.', 'error'); 
        console.error(e);
    } finally { 
        btn.disabled = false; 
        btn.textContent = originalText; 
    }
}

async function submitFeedbackUpdated(event) {
    event.preventDefault();
    const user_id = document.getElementById('feedback-user-container-value').value;
    const feedback_date = document.getElementById('feedback-date').value;
    const description = document.getElementById('feedback-description').innerText;
    const transcription = document.getElementById('feedback-transcription').value;
    const feedback_for_employee = document.getElementById('feedback-employee-msg').innerText;

    if (!user_id || !feedback_date || !description) {
        showToast('Campos obrigatórios: Usuário, Data e Registro Gerencial', 'warning');
        return;
    }

    const button = event.target.querySelector('button[type="submit"]');
    button.disabled = true; button.textContent = 'Salvando...';

    try {
        const response = await fetch('/api/feedbacks', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
            body: JSON.stringify({ 
                user_id: parseInt(user_id), 
                feedback_date, 
                description,
                transcription, 
                feedback_for_employee 
            })
        });
        const data = await response.json();
        if (data.success) {
            showToast('Feedback salvo com sucesso!', 'success');
            showFeedbacks(); // Recarrega
        } else {
            showToast('Erro ao salvar', 'error');
        }
    } catch (error) {
        showToast('Erro de comunicação', 'error');
    } finally {
        button.disabled = false; button.textContent = 'Salvar Feedback';
    }
}

async function loadManagedUsersForFeedback() {
    try {
        const response = await fetch('/api/managed-users', { credentials: 'include' });
        const data = await response.json();
        createAutocomplete('feedback-user-container', data.managed_users, {
            placeholder: 'Digite para buscar um liderado...',
            isMulti: false,
            onSelectionChange: (selectedId) => {
                selectedUserForFeedback = selectedId;
                if (selectedId) {
                    loadUserLastFeedback();
                } else {
                    document.getElementById('last-feedback-info').style.display = 'none';
                }
            }
        });
    } catch (error) {
        console.error('Failed to load managed users:', error);
    }
}

async function submitFeedback(event) {
    event.preventDefault();
    const user_id = document.getElementById('feedback-user-container-value').value;
    const feedback_date = document.getElementById('feedback-date').value;
    const description = document.getElementById('feedback-description').value;
    if (!user_id || !feedback_date || !description) {
        showToast('Preencha os campos obrigatórios (usuário, data e descrição)', 'warning');
        return;
    }

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
            showFeedbacks();
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
    const userId = selectedUserForFeedback;
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
        const usersResponse = await fetch('/api/managed-users', { credentials: 'include' });
        const usersData = await usersResponse.json();
        const selectedUser = usersData.managed_users.find(u => u.id == selectedUserForFeedback);
        const userName = selectedUser ? selectedUser.name : 'Usuário';

        const response = await fetch(`/api/user/${selectedUserForFeedback}/feedbacks`, { credentials: 'include' });
        const data = await response.json();
        if (!data.feedbacks || data.feedbacks.length === 0) {
            showToast('Nenhum feedback anterior encontrado', 'info');
            return;
        }

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

        closeModal(); // Fecha o histórico

        // Preenche os campos do formulário principal
        document.getElementById('feedback-description').value = feedback.description || '';
        document.getElementById('feedback-date').value = feedback.feedback_date.split('T')[0];

        // ✅ Preenche os novos campos (verifica se o elemento existe na tela antes)
        const transInput = document.getElementById('feedback-transcription');
        if (transInput) transInput.value = feedback.transcription || '';

        const empMsgInput = document.getElementById('feedback-employee-msg');
        if (empMsgInput) empMsgInput.value = feedback.feedback_for_employee || '';

        // Muda o comportamento do botão Salvar
        const submitButton = document.querySelector('#feedback-form button[type="submit"]');
        submitButton.textContent = 'Atualizar Feedback';

        // Remove listener antigo (clonando) e adiciona o novo para evitar duplicidade de submit
        const newBtn = submitButton.cloneNode(true);
        submitButton.parentNode.replaceChild(newBtn, submitButton);

        newBtn.onclick = (event) => updateFeedbackSubmit(event, feedbackId);

        showToast('Feedback carregado para edição. Role para cima.', 'info');

        // Rola suavemente para o topo do formulário
        document.getElementById('feedback-form').scrollIntoView({ behavior: 'smooth' });

    } catch (error) {
        console.error('Failed to edit feedback:', error);
        showToast('Erro ao carregar feedback', 'error');
    }
}

async function updateFeedbackSubmit(event, feedbackId) {
    event.preventDefault();

    const description = document.getElementById('feedback-description').value;
    const feedback_date = document.getElementById('feedback-date').value;
    // ✅ Captura novos campos
    const transcription = document.getElementById('feedback-transcription')?.value || '';
    const feedback_for_employee = document.getElementById('feedback-employee-msg')?.value || '';

    if (!feedback_date || !description) {
        showToast('Data e Registro Gerencial são obrigatórios', 'warning');
        return;
    }

    const button = event.target; // O botão que foi clicado
    button.disabled = true;
    button.textContent = 'Atualizando...';

    try {
        const response = await fetch(`/api/feedbacks/${feedbackId}`, {
            method: 'PUT', 
            headers: { 'Content-Type': 'application/json' }, 
            credentials: 'include',
            body: JSON.stringify({ 
                description, 
                feedback_date,
                transcription,
                feedback_for_employee
            })
        });

        const data = await response.json();
        if (data.success) {
            showToast('Feedback atualizado com sucesso!', 'success');
            // Reseta o estado do formulário recarregando a view
            showFeedbacks(); 
        } else {
            showToast('Erro ao atualizar: ' + (data.error || ''), 'error');
            button.disabled = false;
            button.textContent = 'Atualizar Feedback';
        }
    } catch (error) {
        console.error('Failed to update:', error);
        showToast('Erro de comunicação', 'error');
        button.disabled = false;
        button.textContent = 'Atualizar Feedback';
    }
}

// --- VIEW: MINHAS REUNIÕES ---

async function showMeetings() {
    setActiveNav('meetings');
    document.getElementById('meetings-view').style.display = 'block';
    resetMeetingForm();
    await loadMeetings();
    await loadUsersForSharing();
}

async function loadMeetings() {
    const listContainer = document.getElementById('meetings-list');
    listContainer.innerHTML = '<div class="loading">Carregando reuniões...</div>';
    try {
        const response = await fetch('/api/meetings', { credentials: 'include' });
        const data = await response.json();
        if (data.meetings && data.meetings.length > 0) {
            listContainer.innerHTML = `<div class="meetings-table"><table><thead><tr><th>Data</th><th>Resumo</th><th>Dono</th><th>Ações</th></tr></thead><tbody>${
                data.meetings.map(m => `
                    <tr>
                        <td>${new Date(m.meeting_date.replace(/-/g, '/')).toLocaleDateString('pt-BR')}</td>
                        <td><div class="summary-preview">${m.summary}</div></td>
                        <td>${m.is_owner ? 'Você' : m.owner_name}</td>
                        <td>
                            <div class="action-buttons">
                                <button onclick="viewMeeting(${m.id})" class="btn-small btn-view">Ver</button>
                                ${m.is_owner ? `
                                <button onclick="editMeeting(${m.id})" class="btn-small btn-edit">Editar</button>
                                <button onclick="deleteMeeting(${m.id})" class="btn-small btn-delete">Excluir</button>
                                ` : ''}
                            </div>
                        </td>
                    </tr>`
                ).join('')
            }</tbody></table></div>`;
        } else {
            listContainer.innerHTML = '<div class="no-data"><p>Nenhuma reunião registrada ainda.</p></div>';
        }
    } catch (error) {
        console.error('Failed to load meetings:', error);
        listContainer.innerHTML = '<div class="no-data"><p>Erro ao carregar reuniões.</p></div>';
    }
}

async function loadUsersForSharing(initialValue = []) {
    try {
        const response = await fetch('/api/users', { credentials: 'include' });
        const data = await response.json();
        createAutocomplete('meeting-participants-container', data.users, {
            placeholder: 'Digite para adicionar...',
            isMulti: true,
            initialValue: initialValue
        });
    } catch (error) {
        console.error('Failed to load users for sharing:', error);
    }
}

async function generateSummary(event) {
    const transcription = document.getElementById('meeting-transcription').value;
    const meetingDate = document.getElementById('meeting-date').value; // Pega a data do input

    if (!transcription.trim()) {
        showToast('Por favor, insira uma transcrição para gerar o resumo.', 'warning');
        return;
    }

    // Validação simples para garantir que temos uma data para contextualizar
    if (!meetingDate) {
        showToast('Por favor, selecione a data da reunião antes de gerar o resumo.', 'warning');
        return;
    }

    const button = event.target;
    button.disabled = true;
    button.textContent = 'Gerando...';

    try {
        const response = await fetch('/api/meetings/summarize', {
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            credentials: 'include',
            // Envia a data junto com a transcrição
            body: JSON.stringify({ transcription, meeting_date: meetingDate }) 
        });

        const data = await response.json();

        if (response.ok) {
            const htmlContent = marked.parse(data.summary);
            document.getElementById('meeting-summary').innerHTML = htmlContent;
            showToast('Resumo gerado com sucesso!', 'success');
        } else {
            showToast(data.error || 'Erro ao gerar resumo', 'error');
        }
    } catch (error) {
        console.error(error);
        showToast('Erro de comunicação ao gerar resumo', 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Gerar Resumo com IA';
    }
}

async function submitMeeting(event) {
    event.preventDefault();
    const meeting_id_val = document.getElementById('meeting-id').value;
    const meeting_date = document.getElementById('meeting-date').value;
    const summary = document.getElementById('meeting-summary').innerText;
    const transcription = document.getElementById('meeting-transcription').value;

    if (!meeting_date || !summary.trim()) {
        showToast('Data e Resumo da reunião são obrigatórios.', 'warning');
        return;
    }

    const isUpdate = !!meeting_id_val;
    const url = isUpdate ? `/api/meetings/${meeting_id_val}` : '/api/meetings';
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
            const meetingId = isUpdate ? meeting_id_val : data.meeting_id;
            const participantsValue = document.getElementById('meeting-participants-container-value').value;
            const selectedUserIds = participantsValue ? JSON.parse(participantsValue) : [];

            await fetch(`/api/meetings/${meetingId}/share`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
                body: JSON.stringify({ user_ids: selectedUserIds })
            });

            showToast(`Reunião ${isUpdate ? 'atualizada' : 'salva'} e compartilhada com sucesso!`, 'success');
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
    loadUsersForSharing();
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

        const shareResponse = await fetch(`/api/meetings/${id}/share`, { credentials: 'include' });
        const shareData = await shareResponse.json();
        loadUsersForSharing(shareData.shared_with || []);

        window.scrollTo(0, 0);
        showToast('Dados da reunião carregados para edição.', 'info');
    } catch (error) {
        showToast('Erro ao carregar reunião para edição.', 'error');
    }
}

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

    const style = document.createElement('style');
    style.innerHTML = `
        .btn-delete-confirm {
            padding: 0.75rem 1rem; background-color: #DC2626; color: white;
            border: none; border-radius: 8px; font-weight: 600; cursor: pointer; transition: all 0.2s ease;
        }
        .btn-delete-confirm:hover { background-color: #B91C1C; transform: translateY(-2px); }
    `;
    document.head.appendChild(style);
    document.getElementById('modal').style.display = 'flex';
}

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

async function linkGoogleAccount() {
    try {
        const response = await fetch('/api/auth/google/link');
        const data = await response.json();
        if (data.redirect_url) {
            window.location.href = data.redirect_url;
        }
    } catch (error) {
        showToast('Erro ao iniciar vínculo', 'error');
    }
}

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

    // Renderiza status do Google
    const googleContainer = document.getElementById('google-link-status');
    if (currentUser.has_google_linked) {
        googleContainer.innerHTML = `
            <div style="display: flex; align-items: center; gap: 10px; color: #10B981; font-weight: 500; background: #F0FDF4; padding: 10px; border-radius: 8px; border: 1px solid #BBF7D0;">
                <svg style="width:20px;" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
                Conectado ao Google (${currentUser.email})
            </div>
        `;
    } else {
        googleContainer.innerHTML = `
            <button type="button" onclick="linkGoogleAccount()" class="btn-google" style="margin: 0; font-size: 0.9rem;">
                <img src="https://www.svgrepo.com/show/475656/google-color.svg" alt="Google" class="google-icon">
                Vincular minha conta Google
            </button>
            <small style="color: #64748B; display: block; margin-top: 5px;">Use o Google para fazer login mais rápido.</small>
        `;
    }

    // Verifica parâmetros de URL para toasters de sucesso/erro
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('success') === 'google_linked') {
        showToast('Conta Google vinculada com sucesso!', 'success');
        // Limpa a URL
        window.history.replaceState({}, document.title, "/");
    }
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
        createAutocomplete('account-manager-container', data.users, {
            placeholder: 'Digite para buscar um gestor...',
            isMulti: false,
            initialValue: currentUser.manager_id || null
        });
    } catch (error) {
        console.error('Failed to load users:', error);
    }
}

async function updateProfile() {
    const name = document.getElementById('account-name').value;
    const company = document.getElementById('account-company').value;
    const phone = document.getElementById('account-phone').value;
    const manager_id = document.getElementById('account-manager-container-value').value;
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
    if (!container || container.innerHTML.trim() !== '') return;

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
                    Olá. Sou seu copiloto de inteligência. O que vamos realizar agora?
                </div>
            </div>
            <div class="chat-input-container">
                <input type="text" id="chat-input" placeholder="Digite sua pergunta...">
                <button class="chat-send-btn" onclick="sendChatMessage()">Enviar</button>
            </div>
        </div>
    `;

    container.innerHTML = chatHTML;

    const chatInput = document.getElementById('chat-input');
    if (chatInput) {
        chatInput.addEventListener('keydown', function(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
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

        console.log('Resposta recebida do /api/chat:', data);

        document.getElementById('typing-indicator').remove();
        messagesContainer.innerHTML += `<div class="chat-message bot-message">${data[0].output || data.error || 'Desculpe, ocorreu um erro.'}</div>`;
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

async function showDigitalStaff() {
    // Esconde outras views e mostra a correta
    document.querySelectorAll('.view').forEach(v => v.style.display = 'none');
    document.getElementById('digital-staff-view').style.display = 'block';

    // Atualiza menu ativo (assumindo que você ajustou a lógica do setActiveNav ou fará manual)
    document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
    // (Opcional: lógica para ativar o item da sidebar correspondente)

    const grid = document.getElementById('agents-grid');
    grid.innerHTML = '<div class="loading">Conectando à Staff Digital...</div>';

    try {
        const response = await fetch('/api/agents', { credentials: 'include' });
        const data = await response.json();

        if (data.agents && data.agents.length > 0) {
            grid.innerHTML = data.agents.map(agent => renderAgentCard(agent)).join('');
        } else {
            grid.innerHTML = `
                <div class="no-data" style="grid-column: 1/-1;">
                    <p>Sua staff digital está sendo montada automaticamente.</p>
                </div>`;
        }
    } catch (error) {
        console.error(error);
        grid.innerHTML = '<div class="no-data">Erro ao carregar agentes.</div>';
    }
}

function renderAgentCard(agent) {
    // Lógica para determinar se está "Online" (ativo nos últimos 15 min)
    const lastActive = new Date(agent.last_active);
    const now = new Date();
    const diffMinutes = (now - lastActive) / 1000 / 60;
    const isOnline = diffMinutes < 60; // Considera "Online" se trabalhou na última hora

    const statusClass = isOnline ? '' : 'agent-inactive';
    const statusText = isOnline ? 'Online e Monitorando' : 'Aguardando Tarefas';

    // Formatação amigável da data
    const timeString = lastActive.toLocaleTimeString('pt-BR', {hour: '2-digit', minute:'2-digit'});
    const dateString = lastActive.toLocaleDateString('pt-BR');

    return `
    <div class="agent-card ${statusClass}" onclick="openAgentDrawer('${agent.name}', '${agent.role}', '${agent.style}')" style="cursor: pointer;">
        <div class="agent-avatar-container style-${agent.style || 'blue'}">
            🤖
            <div class="status-indicator" title="${statusText}"></div>
        </div>

        <div class="agent-name">${agent.name}</div>
        <div class="agent-role">${agent.role || 'Assistente de IA'}</div>

        ${agent.insights_today > 0 
            ? `<div class="activity-badge">⚡ ${agent.insights_today} insights hoje</div>` 
            : `<div class="activity-badge">💤 Sem atividade hoje</div>`
        }

        <p style="font-size: 0.875rem; color: #64748B; margin-bottom: 1rem; line-height: 1.4;">
            ${agent.description}
        </p>

        <div class="agent-last-seen">
            Última atividade: ${dateString} às ${timeString}
        </div>
    </div>
    `;
}

// --- AGENT DRAWER & INSIGHTS ---

let currentAgentInsights = []; // Armazena localmente para filtrar sem requisição

async function openAgentDrawer(name, role, style) {
    // 1. Configura Visual do Header
    document.getElementById('drawer-agent-name').textContent = name;
    document.getElementById('drawer-agent-role').textContent = role || 'Assistente de IA';
    const avatarDiv = document.getElementById('drawer-avatar');
    avatarDiv.className = `drawer-avatar style-${style || 'blue'}`; // Reutiliza as cores do CSS

    // 2. Abre o Drawer (Animação)
    document.getElementById('agent-drawer').classList.add('open');
    document.getElementById('drawer-overlay').classList.add('open');

    // 3. Carrega Dados
    const container = document.getElementById('drawer-content');
    container.innerHTML = '<div class="loading">Carregando histórico de insights...</div>';

    try {
        // Encode do nome para URL (caso tenha espaços)
        const response = await fetch(`/api/agents/${encodeURIComponent(name)}/insights`, { credentials: 'include' });
        const data = await response.json();

        currentAgentInsights = data.insights || [];
        renderInsightsList(currentAgentInsights);

        // Reseta filtros visuais
        document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
        document.querySelector('.filter-chip:first-child').classList.add('active');

    } catch (error) {
        console.error(error);
        container.innerHTML = '<div class="no-data">Erro ao carregar insights.</div>';
    }
}

function closeAgentDrawer() {
    document.getElementById('agent-drawer').classList.remove('open');
    document.getElementById('drawer-overlay').classList.remove('open');
}

function renderInsightsList(insights) {
    const container = document.getElementById('drawer-content');

    if (insights.length === 0) {
        container.innerHTML = `<div class="no-data"><p>Nenhum insight ativo encontrado.</p></div>`;
        return;
    }

    container.innerHTML = insights.map(item => {
        // Verifica se há um payload de ação (Rascunho)
        let actionBlock = '';
        if (item.action_payload) { // O backend precisa enviar isso no GET também, verifique se get_agent_insights retorna action_payload
            try {
                const payload = typeof item.action_payload === 'string' ? JSON.parse(item.action_payload) : item.action_payload;

                if (payload.type === 'UPDATE_FEEDBACK')
                {
                    actionBlock = `
                        <div style="margin-top: 1rem; background: #F8FAFC; border: 1px dashed #6366F1; border-radius: 8px; padding: 1rem;">
                            <div style="font-size: 0.75rem; font-weight: 700; color: #6366F1; margin-bottom: 0.5rem; text-transform: uppercase;">
                                ✍️ Rascunho Sugerido para o Colaborador
                            </div>
                            <div style="font-size: 0.9rem; color: #334155; font-style: italic; margin-bottom: 1rem; white-space: pre-wrap;">"${payload.draft_message}"</div>
                            <button onclick="approveInsightAction(${item.id}, event)" class="btn-primary" style="padding: 0.5rem 1rem; font-size: 0.85rem; width: 100%;">
                                ✅ Aprovar e Salvar no Feedback
                            </button>
                        </div>
                    `;
                }
                else if (payload.type === 'SEND_EMAIL')
                {
                    actionBlock = `
                        <div style="margin-top: 1rem; background: #F0F9FF; border: 1px dashed #0284C7; border-radius: 8px; padding: 1rem;">
                            <div style="font-size: 0.75rem; font-weight: 700; color: #0284C7; margin-bottom: 0.5rem; text-transform: uppercase;">
                                📧 Ata Executiva Pronta
                            </div>
                            <div style="font-size: 0.9rem; color: #334155; margin-bottom: 1rem; border-left: 3px solid #E0F2FE; padding-left: 10px;">
                                <strong>Assunto:</strong> ${payload.subject}
                            </div>
                            <button onclick="approveInsightAction(${item.id}, event)" class="btn-primary" style="padding: 0.5rem 1rem; font-size: 0.85rem; width: 100%; background-color: #0284C7;">
                                🚀 Disparar Ata por E-mail
                            </button>
                        </div>
                    `;
                }
            } catch (e) { console.error('Erro ao parsear payload', e); }
        }

        return `
        <div class="timeline-insight severity-${item.severity}" id="insight-card-${item.id}">
            <div class="timeline-dot"></div>
            <div class="insight-detail-card">
                <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 0.5rem;">
                    <div class="insight-meta">
                        <span>${item.type}</span> • <span>${new Date(item.created_at).toLocaleDateString('pt-BR')}</span>
                    </div>
                    <button onclick="archiveInsight(${item.id}, event)" class="btn-icon-small" title="Descartar">✕</button>
                </div>

                <h4 style="margin-bottom: 0.5rem; color: #1E293B; font-size: 1rem;">${item.title}</h4>
                <p style="color: #475569; font-size: 0.9rem; line-height: 1.6; margin-bottom: 1rem;">
                    ${item.observation}
                </p>

                ${item.solution ? `
                <div style="background: #F0FDF4; padding: 0.75rem; border-radius: 6px; border-left: 3px solid #10B981; margin-bottom: 0.5rem;">
                    <strong style="color: #064E3B; font-size: 0.8rem; display: block; margin-bottom: 0.25rem;">💡 Sugestão</strong>
                    <div style="color: #065F46; font-size: 0.85rem;">${item.solution}</div>
                </div>` : ''}

                ${actionBlock}
            </div>
        </div>
    `}).join('');
}

async function approveInsightAction(id, event) {
    if(event) event.stopPropagation();

    const btn = event.target;
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = 'Processando...';

    try {
        const res = await fetch(`/api/insights/${id}/approve`, { 
            method: 'POST', 
            credentials: 'include' 
        });

        if (res.ok) {
            showToast('Ação aprovada! Feedback atualizado.', 'success');
            // Remove o card visualmente
            const card = document.getElementById(`insight-card-${id}`);
            if(card) {
                card.style.opacity = '0';
                setTimeout(() => card.remove(), 300);
            }
        } else {
            showToast('Erro ao aprovar ação.', 'error');
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    } catch (e) {
        showToast('Erro de comunicação.', 'error');
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

function filterInsights(filterType) {
    // Atualiza botões
    document.querySelectorAll('.filter-chip').forEach(btn => {
        btn.classList.remove('active');
        if (btn.textContent.includes(filterType) || 
           (filterType === 'ALL' && btn.textContent === 'Todos') ||
           (filterType === 'ALTA' && btn.textContent.includes('Alta')) ||
           (filterType === 'RISCO' && btn.textContent.includes('Riscos')) ||
           (filterType === 'OPORTUNIDADE' && btn.textContent.includes('Oportunidades'))) {
            btn.classList.add('active');
        }
    });

    if (filterType === 'ALL') {
        renderInsightsList(currentAgentInsights);
    } else {
        // Filtra por Severidade OU Tipo
        const filtered = currentAgentInsights.filter(i => 
            i.severity === filterType || i.type === filterType
        );
        renderInsightsList(filtered);
    }
}

async function archiveInsight(id, event) {
    // Impede que clique no card (se houver) propague
    if(event) event.stopPropagation();

    if(!confirm('Deseja descartar este insight? Ele não será mais exibido.')) return;

    try {
        const res = await fetch(`/api/insights/${id}/archive`, { 
            method: 'PUT', 
            credentials: 'include' 
        });

        if (res.ok) {
            // Remove visualmente com uma animação simples
            const card = document.getElementById(`insight-card-${id}`);
            if(card) {
                card.style.opacity = '0';
                setTimeout(() => card.remove(), 300);
            }

            // Remove do array local para que os filtros funcionem corretamente
            currentAgentInsights = currentAgentInsights.filter(i => i.id !== id);

            showToast('Insight descartado.', 'success');
        } else {
            showToast('Erro ao descartar.', 'error');
        }
    } catch (e) {
        console.error(e);
        showToast('Erro de comunicação.', 'error');
    }
}