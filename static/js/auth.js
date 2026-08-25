// --- Lógica da página de login (apenas Google OAuth) ---

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
    setTimeout(() => toast.remove(), 6000);
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

// --- Mensagens vindas do OAuth (query params) ---
(function () {
    const params = new URLSearchParams(window.location.search);
    const error = params.get('error');
    const success = params.get('success');
    const messages = {
        'forbidden': 'Acesso restrito a contas TOTVS autorizadas.',
        'google_account_already_linked': 'Esta conta Google já está vinculada a outro usuário.',
        'session_expired': 'Sessão expirada. Faça login novamente.'
    };
    if (error && messages[error]) showToast(messages[error], 'error');
    else if (error) showToast('Falha na autenticação: ' + error, 'error');
    if (success === 'google_linked') showToast('Conta Google vinculada com sucesso!', 'success');
})();
