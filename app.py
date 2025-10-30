import os
from flask import Flask, send_from_directory
from flask_cors import CORS
from database import init_db
from routes import api_bp
from agent_runner import init_scheduler

def create_app():
    """Cria e configura uma instância da aplicação Flask."""
    app = Flask(__name__, static_folder='static')
    app.secret_key = os.environ.get('SESSION_SECRET', 'dev-secret-key-change-in-production')

    CORS(app, supports_credentials=True)
    app.register_blueprint(api_bp)

    @app.route('/')
    def index():
        # Esta rota agora serve a aplicação principal protegida.
        return send_from_directory('static', 'index.html')

    @app.route('/login')
    def login_page():
        # Nova rota para servir a página de login.
        return send_from_directory('static', 'login.html')

    return app

# Cria a instância da aplicação no nível do módulo para Gunicorn encontrar
app = create_app()

# Inicializa o banco de dados (cria tabelas se não existirem)
init_db()

# Inicializa o scheduler de agentes proativos
init_scheduler(app)

if __name__ == '__main__':
    # Roda o servidor de desenvolvimento
    app.run(host='0.0.0.0', port=5000, debug=True)