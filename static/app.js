let currentUser = null;

async function checkAuth() {
    try {
        const response = await fetch('/api/current-user', {
            credentials: 'include'
        });
        
        if (response.ok) {
            const data = await response.json();
            if (data.authenticated) {
                currentUser = data.manager;
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
    document.getElementById('user-company').textContent = currentUser.company_name;
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
        alert('Por favor, preencha todos os campos');
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
            currentUser = data.manager;
            showApp();
        } else {
            alert('Credenciais inválidas');
        }
    } catch (error) {
        console.error('Login failed:', error);
        alert('Erro ao fazer login');
    }
}

async function register() {
    const name = document.getElementById('register-name').value;
    const company_name = document.getElementById('register-company').value;
    const email = document.getElementById('register-email').value;
    const password = document.getElementById('register-password').value;
    
    if (!name || !company_name || !email || !password) {
        alert('Por favor, preencha todos os campos');
        return;
    }
    
    try {
        const response = await fetch('/api/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ name, company_name, email, password })
        });
        
        const data = await response.json();
        
        if (data.success) {
            alert('Conta criada com sucesso!');
            await checkAuth();
        } else {
            alert('Erro ao criar conta: ' + data.error);
        }
    } catch (error) {
        console.error('Registration failed:', error);
        alert('Erro ao registrar');
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
        'employees': 1,
        'feedbacks': 2
    };
    
    if (viewMap[viewName] !== undefined) {
        navItems[viewMap[viewName]].classList.add('active');
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
            content.innerHTML = data.dashboard.map(item => {
                if (!item.latest_feedback) {
                    return `
                        <div class="insight-card">
                            <div class="insight-header">
                                <div class="employee-info">
                                    <h3>${item.employee_name}</h3>
                                    <p>${item.position || 'Sem cargo'} ${item.department ? '• ' + item.department : ''}</p>
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
                
                return `
                    <div class="insight-card">
                        <div class="insight-header">
                            <div class="employee-info">
                                <h3>${item.employee_name}</h3>
                                <p>${item.position || 'Sem cargo'} ${item.department ? '• ' + item.department : ''}</p>
                            </div>
                            <div class="feedback-date">
                                ${new Date(feedback.created_at).toLocaleDateString('pt-BR')}
                            </div>
                        </div>
                        
                        <div class="insight-section">
                            <h4>💪 Fortalezas</h4>
                            <ul class="insight-list">
                                ${insights.strengths.map(s => `<li>${s}</li>`).join('')}
                            </ul>
                        </div>
                        
                        <div class="insight-section">
                            <h4>📈 Pontos de Desenvolvimento</h4>
                            <ul class="insight-list">
                                ${insights.development_points.map(p => `<li>${p}</li>`).join('')}
                            </ul>
                        </div>
                        
                        <div class="insight-section">
                            <h4>⚠️ Risco de Saída</h4>
                            <p>
                                <span class="risk-badge risk-${insights.turnover_risk.level}">
                                    ${insights.turnover_risk.level === 'low' ? 'Baixo' : insights.turnover_risk.level === 'medium' ? 'Médio' : 'Alto'}
                                </span>
                            </p>
                            <p style="margin-top: 0.5rem; font-size: 0.875rem; color: #64748B;">
                                ${insights.turnover_risk.reason}
                            </p>
                        </div>
                        
                        ${insights.requires_attention.length > 0 ? `
                            <div class="insight-section">
                                <h4>🎯 Requer Atenção</h4>
                                ${insights.requires_attention.map(item => 
                                    `<div class="attention-item">${item}</div>`
                                ).join('')}
                            </div>
                        ` : ''}
                    </div>
                `;
            }).join('');
        } else {
            content.innerHTML = '<div class="no-data"><p>Nenhum funcionário cadastrado. Adicione funcionários e feedbacks para ver insights!</p></div>';
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
        alert('Nome e email são obrigatórios');
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
        } else {
            alert('Erro ao adicionar funcionário');
        }
    } catch (error) {
        console.error('Failed to add employee:', error);
        alert('Erro ao adicionar funcionário');
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
        } else {
            alert('Erro ao atualizar funcionário');
        }
    } catch (error) {
        console.error('Failed to update employee:', error);
        alert('Erro ao atualizar funcionário');
    }
}

async function deleteEmployee(id) {
    if (!confirm('Tem certeza que deseja excluir este funcionário? Todos os feedbacks relacionados também serão excluídos.')) {
        return;
    }
    
    try {
        const response = await fetch(`/api/employees/${id}`, {
            method: 'DELETE',
            credentials: 'include'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showEmployees();
        } else {
            alert('Erro ao excluir funcionário');
        }
    } catch (error) {
        console.error('Failed to delete employee:', error);
        alert('Erro ao excluir funcionário');
    }
}

async function submitFeedback() {
    const employee_id = document.getElementById('feedback-employee').value;
    const feedback_to_employee = document.getElementById('feedback-to-employee').value;
    const feedback_to_manager = document.getElementById('feedback-to-manager').value;
    const expectations_company = document.getElementById('expectations-company').value;
    const expectations_manager = document.getElementById('expectations-manager').value;
    
    if (!employee_id || !feedback_to_employee || !feedback_to_manager || !expectations_company || !expectations_manager) {
        alert('Por favor, preencha todos os campos');
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
                employee_id: parseInt(employee_id),
                feedback_to_employee,
                feedback_to_manager,
                expectations_company,
                expectations_manager
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            alert('Feedback salvo e vetorizado com sucesso!');
            
            document.getElementById('feedback-employee').value = '';
            document.getElementById('feedback-to-employee').value = '';
            document.getElementById('feedback-to-manager').value = '';
            document.getElementById('expectations-company').value = '';
            document.getElementById('expectations-manager').value = '';
        } else {
            alert('Erro ao salvar feedback');
        }
    } catch (error) {
        console.error('Failed to submit feedback:', error);
        alert('Erro ao salvar feedback');
    } finally {
        button.disabled = false;
        button.textContent = 'Salvar Feedback com IA';
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

checkAuth();
