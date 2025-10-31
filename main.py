
import os
from fastapi import FastAPI, APIRouter, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from fastapi_agents_runtime import runtime_router

# === Importa o Flask existente do Cortex ===
try:
    from app import app as flask_app
except Exception as e:
    raise RuntimeError(f"Falha ao importar Flask app de app.py: {e}")

# === Subapp FastAPI exclusivo para /api/agents/* ===
agents = APIRouter()

@agents.get("", summary="Listar agentes disponíveis")
def list_agents():
    return [{"slug": "aurelio", "name": "Aurélio", "mode": "hitl"}]

@agents.get("/healthz", tags=["ops"], summary="Healthcheck do subapp de agentes")
def agents_healthz():
    return {"ok": True, "subapp": "agents"}

@agents.post("/actions", summary="Executar ação aprovada (HITL)")
def execute_action(payload: dict = Body(..., example={"agent":"aurelio","action":"apply_feedback_rewrite","payload":{}})):
    if not payload.get("agent") or not payload.get("action"):
        raise HTTPException(status_code=400, detail="agent e action são obrigatórios")
    return {"ok": True, "received": payload}

# === App ASGI raiz ===
app = FastAPI(title="Cortex ASGI Gateway", version="0.1.1")

# CORS liberal (ajuste para o domínio do seu front, se necessário)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Healthcheck do gateway (registrado ANTES do mount do Flask)
@app.get("/healthz", tags=["ops"])
def healthz():
    return {"ok": True, "service": "gateway"}

@app.get("/ops/healthz", tags=["ops"])
def ops_healthz():
    return {"ok": True, "service": "gateway-ops"}

# Monta o subapp de agentes em /api/agents/*
app.include_router(agents, prefix="/api/agents")

# Monta TODO o restante no Flask (WSGI) — deixar por último para não sombrear rotas FastAPI
app.mount("/", WSGIMiddleware(flask_app))

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
