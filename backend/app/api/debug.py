"""Simple endpoint to receive client-side debug logs and print them to backend stdout."""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/debug", tags=["debug"])


@router.post("/log")
async def client_log(request: Request):
    body = await request.json()
    tag = body.get("tag", "client")
    msg = body.get("msg", "")
    print(f"[FRONTEND-LOG][{tag}] {msg}", flush=True)
    return {"ok": True}
