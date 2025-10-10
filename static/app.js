let currentUser = null;

function showToast(message, type = 'info', title = null) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    const icons = {
        success: '✓',
        error: '✕',
        warning: '⚠',
        info: 'ℹ'
    };
    
    const titles = {
        success: title || 'Sucesso',
        error: title || 'Erro',
        warning: title || 'Atenção',
        info: title || 'Informação'
    };
    
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
        const response = await fetch('/api/current-user', {
            credentials: 'include'
        });
        
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
}

function showApp() {
    document.getElementById('auth-container').style.display = 'none';
    document.getElementById('app-container').style.display = 'flex';
    document.getElementById('user-name').textContent = currentUser.name;
    document.getElementById('user-company').textContent = currentUser.company;
    showDashboard();
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
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ email, password })
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
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ name, company, phone, email, password })
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
        await fetch('/api/logout', {
            method: 'POST',
            credentials: 'include'
        });
        currentUser = null;
        showAuth();
        showToast('Logout realizado', 'info');
    } catch (error) {
        console.error('Logout failed:', error);
    }
}

function setActiveNav(viewName) {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    
    document.querySelectorAll('.view').forEach(view => {
        view.style.display = 'none';
    });
    
    const navItems = document.querySelectorAll('.nav-item');
    const viewMap = {
        'dashboard': 0,
        'feedbacks': 1,
        'account': 2
    };
    
    if (viewMap[viewName] !== undefined) {
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

async function showDashboard() {
    setActiveNav('dashboard');
    document.getElementById('dashboard-view').style.display = 'block';
    
    const content = document.getElementById('dashboard-content');
    content.innerHTML = '<div class="loading">Carregando insights...</div>';
    
    try {
        const response = await fetch('/api/dashboard', {
            credentials: 'include'
        });
        
        const data = await response.json();
        
        if (data.dashboard && data.dashboard.length > 0) {
            content.innerHTML = data.dashboard.map((item, index) => {
                if (!item.latest_feedback) {
                    return `
                        <div class="insight-card">
                            <div class="insight-header">
                                <div class="employee-info">
                                    <h3>${item.user_name}</h3>
                                    <p>${item.company || 'Sem empresa'}</p>
                                </div>
                            </div>
                            <div class="no-data">
                                <p>Nenhum feedback cadastrado ainda</p>
                            </div>
                        </div>
                    `;
                }
                
                const insights = item.insights;
                const feedback = item.latest_feedback;
                const cardId = `card-${index}`;
                
                const riskLabels = {
                    'baixo': 'Baixo',
                    'medio': 'Médio',
                    'alto': 'Alto',
                    'low': 'Baixo',
                    'medium': 'Médio',
                    'high': 'Alto'
                };
                
                const riskLevel = insights.risco_saida?.nivel || insights.turnover_risk?.level || 'baixo';
                const riskReason = insights.risco_saida?.motivo || insights.turnover_risk?.reason || '';
                
                return `
                    <div class="insight-card">
                        <div class="insight-header">
                            <div class="employee-info">
                                <h3>${item.user_name}</h3>
                                <p>${item.company || 'Sem empresa'}</p>
                            </div>
                            <div class="feedback-date">
                                ${new Date(feedback.feedback_date || feedback.created_at).toLocaleDateString('pt-BR')}
                            </div>
                        </div>
                        
                        ${insights.resumo ? `
                            <div class="insight-section">
                                <h4>📝 Resumo do Último Feedback</h4>
                                <div class="feedback-summary">${insights.resumo}</div>
                            </div>
                        ` : ''}
                        
                        <div class="insight-section">
                            <h4>⚠️ Risco de Saída</h4>
                            <p>
                                <span class="risk-badge risk-${riskLevel.toLowerCase().replace('é', 'e')}">
                                    ${riskLabels[riskLevel.toLowerCase()] || riskLabels['baixo']}
                                </span>
                            </p>
                        </div>
                        
                        ${insights.acoes_pendencias && insights.acoes_pendencias.length > 0 ? `
                            <div class="insight-section">
                                <h4>🎯 Ações ou Pendências</h4>
                                ${insights.acoes_pendencias.map(action => 
                                    `<div class="action-badge">${action}</div>`
                                ).join('')}
                            </div>
                        ` : `
                            <div class="insight-section">
                                <h4>🎯 Ações ou Pendências</h4>
                                <p style="color: #94A3B8; font-size: 0.875rem;">Sem pendências</p>
                            </div>
                        `}
                        
                        <div class="expand-btn" id="btn-${cardId}" onclick="toggleCard('${cardId}')">
                            Ver detalhes <svg fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"></path></svg>
                        </div>
                        
                        <div class="card-expanded" id="expanded-${cardId}">
                            <div class="insight-section">
                                <h4>💪 Fortalezas</h4>
                                <ul class="insight-list">
                                    ${(insights.fortalezas || insights.strengths || []).map(s => `<li>${s}</li>`).join('')}
                                </ul>
                            </div>
                            
                            <div class="insight-section">
                                <h4>📈 Pontos de Desenvolvimento</h4>
                                <ul class="insight-list">
                                    ${(insights.pontos_desenvolvimento || insights.development_points || []).map(p => `<li>${p}</li>`).join('')}
                                </ul>
                            </div>
                            
                            <div class="insight-section">
                                <h4>💡 Detalhes do Risco</h4>
                                <p style="font-size: 0.875rem; color: #64748B;">
                                    ${riskReason}
                                </p>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            content.innerHTML = '<div class="no-data"><p>Nenhum usuário gerenciado. Os usuários devem te selecionar como gestor no perfil deles!</p></div>';
        }
    } catch (error) {
        console.error('Failed to load dashboard:', error);
        content.innerHTML = '<div class="no-data"><p>Erro ao carregar dashboard</p></div>';
    }
}

async function showEmployees() {
    setActiveNav('employees');
    document.getElementById('employees-view').style.display = 'block';
    
    const content = document.getElementById('employees-content');
    content.innerHTML = '<div class="loading">Carregando funcionários...</div>';
    
    try {
        const response = await fetch('/api/employees', {
            credentials: 'include'
        });
        
        const data = await response.json();
        
        if (data.employees && data.employees.length > 0) {
            content.innerHTML = `
                <div class="employees-table">
                    <table>
                        <thead>
                            <tr>
                                <th>Nome</th>
                                <th>Email</th>
                                <th>Cargo</th>
                                <th>Departamento</th>
                                <th>Ações</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${data.employees.map(emp => `
                                <tr>
                                    <td>${emp.name}</td>
                                    <td>${emp.email}</td>
                                    <td>${emp.position || '-'}</td>
                                    <td>${emp.department || '-'}</td>
                                    <td>
                                        <div class="action-buttons">
                                            <button class="btn-small btn-edit" onclick="editEmployee(${emp.id})">Editar</button>
                                            <button class="btn-small btn-delete" onclick="deleteEmployee(${emp.id})">Excluir</button>
                                        </div>
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            `;
        } else {
            content.innerHTML = '<div class="no-data"><p>Nenhum funcionário cadastrado</p></div>';
        }
    } catch (error) {
        console.error('Failed to load employees:', error);
        content.innerHTML = '<div class="no-data"><p>Erro ao carregar funcionários</p></div>';
    }
}

async function showFeedbacks() {
    setActiveNav('feedbacks');
    document.getElementById('feedbacks-view').style.display = 'block';
    
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('feedback-date').value = today;
    
    await loadEmployeeSelect();
}

async function loadEmployeeSelect() {
    try {
        const response = await fetch('/api/employees', {
            credentials: 'include'
        });
        
        const data = await response.json();
        const select = document.getElementById('feedback-employee');
        
        select.innerHTML = '<option value="">Selecione...</option>' +
            data.employees.map(emp => 
                `<option value="${emp.id}">${emp.name} (${emp.position || 'Sem cargo'})</option>`
            ).join('');
    } catch (error) {
        console.error('Failed to load employees:', error);
    }
}

function showAddEmployee() {
    const modalBody = document.getElementById('modal-body');
    modalBody.innerHTML = `
        <h2>Adicionar Funcionário</h2>
        <div class="form-group">
            <label>Nome Completo</label>
            <input type="text" id="new-emp-name" class="form-control">
        </div>
        <div class="form-group">
            <label>Email</label>
            <input type="email" id="new-emp-email" class="form-control">
        </div>
        <div class="form-group">
            <label>Cargo</label>
            <input type="text" id="new-emp-position" class="form-control">
        </div>
        <div class="form-group">
            <label>Departamento</label>
            <input type="text" id="new-emp-department" class="form-control">
        </div>
        <button class="btn-primary" onclick="saveNewEmployee()">Salvar</button>
    `;
    document.getElementById('modal').style.display = 'block';
}

async function saveNewEmployee() {
    const name = document.getElementById('new-emp-name').value;
    const email = document.getElementById('new-emp-email').value;
    const position = document.getElementById('new-emp-position').value;
    const department = document.getElementById('new-emp-department').value;
    
    if (!name || !email) {
        showToast('Nome e email são obrigatórios', 'warning');
        return;
    }
    
    try {
        const response = await fetch('/api/employees', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ name, email, position, department })
        });
        
        const data = await response.json();
        
        if (data.success) {
            closeModal();
            showEmployees();
            showToast('Funcionário adicionado com sucesso!', 'success');
        } else {
            showToast('Erro ao adicionar funcionário', 'error');
        }
    } catch (error) {
        console.error('Failed to add employee:', error);
        showToast('Erro ao adicionar funcionário', 'error');
    }
}

async function editEmployee(id) {
    try {
        const response = await fetch(`/api/employees/${id}`, {
            credentials: 'include'
        });
        
        const emp = await response.json();
        
        const modalBody = document.getElementById('modal-body');
        modalBody.innerHTML = `
            <h2>Editar Funcionário</h2>
            <div class="form-group">
                <label>Nome Completo</label>
                <input type="text" id="edit-emp-name" class="form-control" value="${emp.name}">
            </div>
            <div class="form-group">
                <label>Email</label>
                <input type="email" id="edit-emp-email" class="form-control" value="${emp.email}">
            </div>
            <div class="form-group">
                <label>Cargo</label>
                <input type="text" id="edit-emp-position" class="form-control" value="${emp.position || ''}">
            </div>
            <div class="form-group">
                <label>Departamento</label>
                <input type="text" id="edit-emp-department" class="form-control" value="${emp.department || ''}">
            </div>
            <button class="btn-primary" onclick="saveEditEmployee(${id})">Salvar</button>
        `;
        document.getElementById('modal').style.display = 'block';
    } catch (error) {
        console.error('Failed to load employee:', error);
    }
}

async function saveEditEmployee(id) {
    const name = document.getElementById('edit-emp-name').value;
    const email = document.getElementById('edit-emp-email').value;
    const position = document.getElementById('edit-emp-position').value;
    const department = document.getElementById('edit-emp-department').value;
    
    try {
        const response = await fetch(`/api/employees/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ name, email, position, department })
        });
        
        const data = await response.json();
        
        if (data.success) {
            closeModal();
            showEmployees();
            showToast('Funcionário atualizado com sucesso!', 'success');
        } else {
            showToast('Erro ao atualizar funcionário', 'error');
        }
    } catch (error) {
        console.error('Failed to update employee:', error);
        showToast('Erro ao atualizar funcionário', 'error');
    }
}

async function deleteEmployee(id) {
    const confirmed = confirm('Tem certeza que deseja excluir este funcionário? Todos os feedbacks relacionados também serão excluídos.');
    if (!confirmed) return;
    
    try {
        const response = await fetch(`/api/employees/${id}`, {
            method: 'DELETE',
            credentials: 'include'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showEmployees();
            showToast('Funcionário excluído', 'success');
        } else {
            showToast('Erro ao excluir funcionário', 'error');
        }
    } catch (error) {
        console.error('Failed to delete employee:', error);
        showToast('Erro ao excluir funcionário', 'error');
    }
}

async function submitFeedback() {
    const user_id = document.getElementById('feedback-user').value;
    const feedback_date = document.getElementById('feedback-date').value;
    const feedback_to_user = document.getElementById('feedback-to-employee').value;
    const feedback_to_manager = document.getElementById('feedback-to-manager').value;
    const expectations_company = document.getElementById('expectations-company').value;
    const expectations_manager = document.getElementById('expectations-manager').value;
    
    if (!user_id || !feedback_date || !feedback_to_user) {
        showToast('Preencha os campos obrigatórios (usuário, data e feedback)', 'warning');
        return;
    }
    
    const button = event.target;
    button.disabled = true;
    button.textContent = 'Processando com IA...';
    
    try {
        const response = await fetch('/api/feedbacks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                user_id: parseInt(user_id),
                feedback_date,
                feedback_to_user,
                feedback_to_manager,
                expectations_company,
                expectations_manager
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Feedback salvo e vetorizado com sucesso!', 'success');
            
            document.getElementById('feedback-user').value = '';
            document.getElementById('feedback-to-employee').value = '';
            document.getElementById('feedback-to-manager').value = '';
            document.getElementById('expectations-company').value = '';
            document.getElementById('expectations-manager').value = '';
            const today = new Date().toISOString().split('T')[0];
            document.getElementById('feedback-date').value = today;
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

async function showFeedbacks() {
    setActiveNav('feedbacks');
    document.getElementById('feedbacks-view').style.display = 'block';
    
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('feedback-date').value = today;
    
    await loadManagedUsers();
}

async function loadManagedUsers() {
    try {
        const response = await fetch('/api/managed-users', {
            credentials: 'include'
        });
        
        const data = await response.json();
        const select = document.getElementById('feedback-user');
        
        select.innerHTML = '<option value="">Selecione...</option>' +
            data.managed_users.map(user => 
                `<option value="${user.id}">${user.name} (${user.company})</option>`
            ).join('');
    } catch (error) {
        console.error('Failed to load managed users:', error);
    }
}

async function showAccount() {
    setActiveNav('account');
    document.getElementById('account-view').style.display = 'block';
    
    document.getElementById('account-name').value = currentUser.name;
    document.getElementById('account-email').value = currentUser.email;
    document.getElementById('account-company').value = currentUser.company || '';
    document.getElementById('account-phone').value = currentUser.phone || '';
    
    await loadAvailableManagers();
    
    const managerSelect = document.getElementById('account-manager');
    managerSelect.value = currentUser.manager_id || '';
}

async function loadAvailableManagers() {
    try {
        const response = await fetch('/api/users', {
            credentials: 'include'
        });
        
        const data = await response.json();
        const select = document.getElementById('account-manager');
        
        select.innerHTML = '<option value="">Nenhum</option>' +
            data.users.map(user => 
                `<option value="${user.id}">${user.name} (${user.company})</option>`
            ).join('');
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
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ 
                name, 
                company, 
                phone: phone || '',
                manager_id: manager_id || null
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Perfil atualizado com sucesso!', 'success');
            await checkAuth();
        } else {
            showToast('Erro ao atualizar perfil', 'error');
        }
    } catch (error) {
        console.error('Failed to update profile:', error);
        showToast('Erro ao atualizar perfil', 'error');
    }
}

function closeModal() {
    document.getElementById('modal').style.display = 'none';
}

window.onclick = function(event) {
    const modal = document.getElementById('modal');
    if (event.target === modal) {
        closeModal();
    }
}

function toggleChat() {
    const chatWindow = document.getElementById('chat-window');
    const chatButton = document.getElementById('chat-button');
    
    if (chatWindow.style.display === 'none') {
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
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ question })
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

checkAuth();
