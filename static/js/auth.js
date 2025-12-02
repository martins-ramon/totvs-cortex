// --- Lógica de UI ---

function showLogin() {
    document.getElementById('login-form').style.display = 'block';
    document.getElementById('register-form').style.display = 'none';
}

function showRegister() {
    document.getElementById('login-form').style.display = 'none';
    document.getElementById('register-form').style.display = 'block';
}

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
    setTimeout(() => toast.remove(), 5000);
}


// --- Lógica de API ---

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
        if (data.success && data.user) {
            // ---- ALTERAÇÃO PRINCIPAL AQUI ----
            // Salva os dados do usuário no sessionStorage para a próxima página ler.
            sessionStorage.setItem('currentUser', JSON.stringify(data.user));

            showToast('Login realizado com sucesso!', 'success');
            window.location.href = '/';
        } else {
            showToast('Credenciais inválidas', 'error');
        }
    } catch (error) {
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
            // Para o registro, o ideal é logar o usuário em seguida para obter o objeto completo.
            // Mas, para simplificar, vamos apenas redirecionar e deixar o checkAuth da próxima página validar.
            showToast('Conta criada com sucesso! Redirecionando...', 'success');
            window.location.href = '/';
        } else {
            showToast('Erro ao criar conta: ' + (data.error || ''), 'error');
        }
    } catch (error) {
        showToast('Erro ao registrar', 'error');
    }
}

async function loginWithGoogle() {
    try {
        const response = await fetch('/api/auth/google/login');
        const data = await response.json();
        if (data.redirect_url) {
            window.location.href = data.redirect_url;
        } else {
            showToast('Erro ao iniciar login com Google', 'error');
        }
    } catch (error) {
        showToast('Erro de conexão', 'error');
    }
}