from fastapi import APIRouter, Body, HTTPException
from agent_runtime import AurelioRuntime

runtime_router = APIRouter()

@runtime_router.post("/run/digest")
def run_digest():
    rt = AurelioRuntime()
    try:
        return rt.run_digest_diario()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@runtime_router.post("/run/feedback")
def run_feedback(feedback: dict = Body(...)):
    rt = AurelioRuntime()
    try:
        return rt.run_higiene_feedback(feedback)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@runtime_router.post("/run/meeting")
def run_meeting(meeting: dict = Body(...)):
    rt = AurelioRuntime()
    try:
        return rt.run_followups_reuniao(meeting)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@runtime_router.post("/run/attrition")
def run_attrition():
    rt = AurelioRuntime()
    try:
        return rt.run_risco_saida()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
