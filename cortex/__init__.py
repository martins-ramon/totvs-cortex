import os
import logging

from flask import Flask, send_from_directory, jsonify
from flask_cors import CORS

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cortex")

_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def create_app():
    """Fábrica da aplicação Cortex."""
    app = Flask(__name__, static_folder=_STATIC_DIR, static_url_path="/static")
    app.secret_key = os.environ.get(
        "SESSION_SECRET", "dev-secret-key-change-in-production")

    CORS(app, supports_credentials=True)

    from .views import auth, people
    from .views import sessions, checkpoints, cards, connections, dashboard
    app.register_blueprint(auth.bp)
    app.register_blueprint(people.bp)
    app.register_blueprint(sessions.bp)
    app.register_blueprint(checkpoints.bp)
    app.register_blueprint(cards.bp)
    app.register_blueprint(connections.bp)
    app.register_blueprint(dashboard.bp)

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
        db_status = database.INIT_DB_STATUS
        return jsonify({
            "status": "ok" if db_status == "ok" else "degraded",
            "db_init": db_status,
            "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
            "google_oauth_configured": bool(
                os.environ.get("GOOGLE_CLIENT_ID")
                and os.environ.get("GOOGLE_CLIENT_SECRET")),
            "allowlist_domains": allowed_domains(),
        })

    # Inicialização resiliente do banco: se falhar, a aplicação sobe mesmo
    # assim e o /healthz reporta o estado degradado.
    try:
        from .database import init_db
        init_db()
    except Exception as e:
        log.error(f"Falha inesperada no init_db: {e}")

    return app
