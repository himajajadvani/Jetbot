from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uuid

from agent.core import chat_with_agent, stream_with_agent

app = FastAPI()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    currency: str = "USD"          # ← NEW
    history: list = []             # ← for multi-leg context detection


class ChatResponse(BaseModel):
    session_id: str
    response: str


@app.get("/debug/airport")
def debug_airport(q: str):
    """Debug endpoint — returns raw Avinode airport response for a query."""
    import requests as _r
    from tools.avinode_tool import headers, _airport_cache, _label_cache
    r = _r.get(f"https://apps.avinode.com/webapp/rest/airport?s={q}", headers=headers(), timeout=8)
    return {
        "status": r.status_code,
        "data": r.json() if r.status_code == 200 else r.text,
        "cache_keys": list(_airport_cache.keys())[:20],
        "label_cache": dict(list(_label_cache.items())[:20]),
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    reply = chat_with_agent(session_id, req.message, currency=req.currency, history=req.history)   # ← history passed
    return ChatResponse(session_id=session_id, response=reply)


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())

    def generate():
        yield f"data: [SESSION:{session_id}]\n\n"
        for kind, payload in stream_with_agent(session_id, req.message, currency=req.currency, history=req.history):
            if kind == "text":
                safe = payload.replace("\n", "\\n")
                yield f"data: {safe}\n\n"
            elif kind == "prices":
                # Single-line JSON — newlines already absent from json.dumps default
                yield f"data: [PRICES:{payload}]\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")