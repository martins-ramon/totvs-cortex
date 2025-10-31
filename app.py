# app.py
import os
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from database import init_db_flask as init_db
from agents_flask_routes import bp_agents

def create_app():
    # Define /static como pasta de arquivos estáticos
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Inicializa banco e tabelas
    init_db(app)

    # === Rotas de API ===
    @app.route("/api/ping")
    def ping():
        return jsonify({"ok": True, "service": "flask"})

    # === Rotas de UI ===
    @app.route("/")
    def root():
        # login.html como página inicial
        login_path = os.path.join(app.static_folder, "login.html")
        if os.path.exists(login_path):
            return send_from_directory(app.static_folder, "login.html")
        return "login.html não encontrado", 404

    @app.route("/app")
    def app_index():
        # index.html como aplicação principal (SPA)
        index_path = os.path.join(app.static_folder, "index.html")
        if os.path.exists(index_path):
            return send_from_directory(app.static_folder, "index.html")
        return "index.html não encontrado", 404

    # Serve qualquer outro asset (css, js, imagens)
    @app.route("/static/<path:path>")
    def static_proxy(path):
        file_path = os.path.join(app.static_folder, path)
        if os.path.exists(file_path):
            return send_from_directory(app.static_folder, path)
        return "Arquivo não encontrado", 404

    # Blueprint dos agentes (endpoints Flask)
    app.register_blueprint(bp_agents)

    # Outras rotas do projeto (opcional)
    try:
        from routes import init_routes
        init_routes(app)
    except Exception as e:
        print(f"[app] routes.init_routes não encontrado (ok). Detalhe: {e}")

    return app

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
