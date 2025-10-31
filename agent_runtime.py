import os
import json
import requests
from datetime import date
from typing import Any, Dict

from agent_loader import load_agent_config

class AurelioRuntime:
    def __init__(self, yaml_path: str = "aurelio_agent.yaml"):
        self.cfg = load_agent_config(yaml_path)
        self.endpoints = self.cfg.get("endpoints", {}).get("cortex", {})
        self.headers = {
            "Authorization": f"Bearer {os.getenv('CORTEX_API_KEY', '')}",
            "Content-Type": "application/json"
        }
        self.base_url = os.getenv("CORTEX_BASE_URL", "").rstrip("/")

    # ---- util ----
    def _post(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        r = requests.post(url, headers=self.headers, json=body, timeout=30)
        r.raise_for_status()
        return r.json() if r.content else {"ok": True}

    def _get(self, url: str) -> Dict[str, Any]:
        r = requests.get(url, headers={"Authorization": self.headers["Authorization"]}, timeout=30)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"raw": r.text}

    # ---- workflows ----
    def run_digest_diario(self) -> Dict[str, Any]:
        dash_url = self.endpoints.get("dashboard", "")
        if dash_url.startswith("GET "):
            dash_url = dash_url.split(" ", 1)[1]
        dashboard = {}
        try:
            dashboard = self._get(dash_url)
        except Exception as e:
            dashboard = {"warning": f"dashboard fetch failed: {e}"}

        # Em produção você chamaria o LLM. Aqui geramos um card simples.
        card = {
            "title": "Digest diário do Aurélio",
            "summary": "Sugestões do dia para revisar.",
            "details": {
                "suggestions": [
                    {"title": "Reforço positivo",
                     "summary": "Reconheça o progresso do João no Projeto X.",
                     "actions": [{"label":"Enviar reconhecimento","actionId":"send_kudos","payload":{"user_id":"u_joao"}}]},
                    {"title": "Agendar 1:1",
                     "summary": "Agendar 1:1 com Maria sobre prioridades do Q4.",
                     "actions": [{"label":"Agendar 1:1","actionId":"schedule_11","payload":{"target_user_id":"u_maria"}}]}
                ]
            }
        }

        seed_url = self.endpoints.get("seed_suggestion", "")
        notify_url = self.endpoints.get("notify", "")
        seed_res = self._post(seed_url, {
            "user_id": "manager-demo",
            "title": card["title"],
            "summary": card["summary"],
            "details": card["details"]
        })
        self._post(notify_url, {
            "title": card["title"],
            "message": "Novas sugestões do agente estão prontas para revisão.",
            "category": "agent",
            "cta": [{"label":"Ver sugestões","action":"open_agent_inbox","payload":{"agent":"aurelio"}}],
            "is_read": False
        })
        return {"ok": True, "seed": seed_res, "card": card, "dashboard": dashboard}

    def run_higiene_feedback(self, feedback: Dict[str, Any]) -> Dict[str, Any]:
        # Em produção: enviar feedback ao LLM. Aqui, geramos resposta heurística.
        score = 80 if len((feedback or {}).get("description","")) > 50 else 65
        out = {
            "score": score,
            "issues": [] if score >= 75 else ["Feedback curto ou sem próximos passos"],
            "rewrite": (feedback or {}).get("description","") + " \n\nPróximo passo: combine um exemplo observável e um prazo.",
            "actions": [{"label":"Enviar versão revisada","actionId":"apply_feedback_rewrite","payload":{"feedback_id":(feedback or {}).get("id"),"rewrite":"<conteúdo acima>"}}]
        }
        seed_res = self._post(self.endpoints["seed_suggestion"], {
            "user_id":"manager-demo",
            "title":"Sugestão do Aurélio para seu feedback",
            "summary":"Reescrita e próximas ações sugeridas.",
            "details": out
        })
        self._post(self.endpoints["notify"], {
            "title":"Sugestão do Aurélio para seu feedback",
            "message":"Reescrita sugerida pronta para revisão.",
            "category":"agent",
            "cta":[{"label":"Abrir inbox","action":"open_agent_inbox","payload":{"agent":"aurelio"}}],
            "is_read": False
        })
        return {"ok": True, "seed": seed_res, "analysis": out}

    def run_followups_reuniao(self, meeting: Dict[str, Any]) -> Dict[str, Any]:
        tasks = [{
            "assignee_id": meeting.get("owner_id","u_demo"),
            "title": f"Confirmar próximos passos da reunião de {meeting.get('meeting_date','hoje')}",
            "due_date": str(date.today()),
            "notes": "Enviar resumo por Slack e coletar confirmações."
        }]
        details = {"tasks": tasks, "risks": []}
        seed_res = self._post(self.endpoints["seed_suggestion"], {
            "user_id":"manager-demo",
            "title":"Follow-ups do Aurélio para a reunião",
            "summary":"Tarefas sugeridas prontas para aprovação.",
            "details": details
        })
        self._post(self.endpoints["notify"], {
            "title":"Follow-ups do Aurélio",
            "message":"Tarefas sugeridas prontas para aprovação.",
            "category":"agent",
            "cta":[{"label":"Criar lembretes","action":"open_agent_inbox","payload":{"agent":"aurelio"}}],
            "is_read": False
        })
        return {"ok": True, "seed": seed_res, "details": details}

    def run_risco_saida(self) -> Dict[str, Any]:
        # Minimalista: cria um alerta fictício de risco médio
        risks = {"risks":[{"user_id":"u_demo","level":"médio","rationale":"Poucos registros de 1:1 e sinais de sobrecarga.","actions":[{"label":"1:1 de saúde","actionId":"schedule_11","payload":{"target_user_id":"u_demo"}}]}]}
        seed_res = self._post(self.endpoints["seed_suggestion"], {
            "user_id":"manager-demo",
            "title":"Alerta de risco de saída",
            "summary":"O Aurélio identificou colaboradores em risco. Revise o plano de ação.",
            "details": risks
        })
        self._post(self.endpoints["notify"], {
            "title":"Alerta de risco de saída",
            "message":"O Aurélio identificou colaboradores em risco. Revise as ações sugeridas.",
            "category":"agent",
            "cta":[{"label":"Ver plano de ação","action":"open_agent_inbox","payload":{"agent":"aurelio","view":"attrition"}}],
            "is_read": False
        })
        return {"ok": True, "seed": seed_res, "details": risks}
