import os
import logging
import secrets

from flask import Flask, send_from_directory, jsonify, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cortex")

_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def create_app():
    """Fábrica da aplicação Cortex."""
    app = Flask(__name__, static_folder=_STATIC_DIR, static_url_path="/static")
    on_vercel = os.environ.get("VERCEL") == "1"
    dev_secret = "dev-secret-key-change-in-production"
    session_secret = os.environ.get("SESSION_SECRET")
    secret_configured = bool(session_secret and session_secret.strip()
                             and session_secret != dev_secret)
    # Instâncias serverless precisam compartilhar o mesmo segredo. Sem ele,
    # mantenha as páginas/diagnóstico disponíveis, mas não aceite sessões na API.
    app.secret_key = (session_secret if secret_configured else
                      secrets.token_hex(32) if on_vercel else dev_secret)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=on_vercel or os.environ.get(
            "APP_BASE_URL", "").startswith("https://"),
    )
    if on_vercel:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0)

    @app.before_request
    def require_production_session_secret():
        if on_vercel and not secret_configured and request.path.startswith('/api/'):
            return jsonify({"error": "SESSION_SECRET não configurada para produção."}), 503

    @app.after_request
    def prevent_private_response_caching(response):
        if request.path.startswith('/api/') or request.path == '/healthz':
            response.headers['Cache-Control'] = 'no-store'
        return response

    CORS(app, supports_credentials=True)

    from .views import auth, people
    from .views import sessions, checkpoints, cards, connections, dashboard, assistant, meetings
    app.register_blueprint(auth.bp)
    app.register_blueprint(people.bp)
    app.register_blueprint(sessions.bp)
    app.register_blueprint(checkpoints.bp)
    app.register_blueprint(cards.bp)
    app.register_blueprint(connections.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(assistant.bp)
    app.register_blueprint(meetings.bp)

    @app.route('/')
    def index():
        return send_from_directory(_STATIC_DIR, 'index.html')

    @app.route('/login')
    def login_page():
        return serve_static('login.html')

    @app.route('/connections')
    def connections_page():
        # Página-destino do callback OAuth das integrações (ex.: Gmail).
        # O JS da SPA lê os query params (?connected=.../?error=...) e abre a view.
        return serve_static('index.html')

    def serve_static(filename):
        return send_from_directory(_STATIC_DIR, filename)

    @app.route('/healthz')
    def healthz():
        from . import database
        from .security import allowed_domains
        from .oauth import callback_url
        # Verificação somente-leitura: nunca cria/atualiza tabelas neste endpoint.
        db_status = database.check_database()
        try:
            callback_url('/api/auth/google/callback')
            base_url_valid = True
        except ValueError:
            base_url_valid = False
        openai_configured = bool(os.environ.get("OPENAI_API_KEY"))
        google_configured = bool(
                os.environ.get("GOOGLE_CLIENT_ID")
                and os.environ.get("GOOGLE_CLIENT_SECRET"))
        ready = (db_status == "ok" and openai_configured and google_configured
                 and base_url_valid and (secret_configured or not on_vercel))
        # Não exponha mensagens SQL/DSNs no diagnóstico público.
        init_status = database.INIT_DB_STATUS
        if init_status:
            init_status = init_status.split(':', 1)[0]
        return jsonify({
            "status": "ok" if ready else "degraded",
            "database": db_status,
            "db_init": init_status or "skipped",
            "openai_configured": openai_configured,
            "google_oauth_configured": google_configured,
            "session_secret_configured": secret_configured,
            "app_base_url_valid": base_url_valid,
            "allowlist_domains": allowed_domains(),
        }), 200 if ready else 503

    # Inicialização resiliente do banco: se falhar, a aplicação sobe mesmo
    # assim e o /healthz reporta o estado degradado.
    # Não execute DDL em cada cold start da Vercel nem durante o build.
    auto_init = os.environ.get("AUTO_INIT_DB", "false" if on_vercel else "true")
    if auto_init.lower() in {"1", "true", "yes"}:
        try:
            from .database import init_db
            init_db()
        except Exception as e:
            log.error("Falha inesperada no init_db (%s)", type(e).__name__)

    return app
