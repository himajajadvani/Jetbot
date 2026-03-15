from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uuid
import re

from agent.core import chat_with_agent, stream_with_agent
from results_cache import get_cache, delete_cache, has_cache

app = FastAPI()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    response: str


def _is_show_more(message: str) -> bool:
    t = message.strip().lower()
    return any(k in t for k in ["show more", "more options", "see more", "next", "yes", "more", "show next"])


def _is_no(message: str) -> bool:
    t = message.strip().lower()
    return t in {"no", "nope", "nah", "no thanks", "that's fine", "not now", "stop"}


def _format_page(aircraft: list, page: int, total: int) -> str:
    start = (page - 1) * 5
    end   = start + 5
    batch = aircraft[start:end]

    if not batch:
        return "No more aircraft available for this search."

    lines = []
    for i, ac in enumerate(batch, start=start + 1):
        amenities = ", ".join(ac.get("amenities_available", [])) or "None listed"
        lines.append(
            f"{i}. **{ac['aircraft_name']}**\n"
            f"   * Capacity: {ac['capacity']}\n"
            f"   * Price: {ac['price_usd']}\n"
            f"   * Flight Time: {ac.get('flight_time', 'N/A')}\n"
            f"   * Departure: {ac['departure_airport']}\n"
            f"   * Arrival: {ac['arrival_airport']}\n"
            f"   * Amenities: {amenities}"
        )

    shown  = min(end, total)
    result = "\n\n".join(lines)
    result += f"\n\nShowing {shown} of {total} available aircraft."
    result += " Would you like to see more options?" if shown < total else " That's all available aircraft for this route."
    return result


def _truncate_to_five(reply: str) -> str:
    """Cut the reply down to only the first 5 numbered aircraft blocks."""
    # Find position of the 6th numbered item if it exists
    matches = list(re.finditer(r'^\d+\.\s+\*\*', reply, re.MULTILINE))
    if len(matches) > 5:
        reply = reply[:matches[5].start()].rstrip()
    return reply


def _append_showing_line(reply: str, session_id: str) -> str:
    if not has_cache(session_id):
        return reply
    if not re.search(r'^\d+\.\s+\*\*', reply, re.MULTILINE):
        return reply

    cache = get_cache(session_id)
    total = cache["total"]

    # Strip everything from first numbered item onwards — replace with formatted data
    first_item = re.search(r'^\d+\.\s+\*\*', reply, re.MULTILINE)
    intro = reply[:first_item.start()].rstrip() if first_item else ""

    # Format first 5 directly from cache (guaranteed full airport labels)
    formatted = _format_page(cache["aircraft"], 1, total)

    return (intro + "\n\n" + formatted).strip()


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

    if has_cache(session_id) and _is_no(req.message):
        delete_cache(session_id)
        return ChatResponse(
            session_id=session_id,
            response="Sure! Let me know if you'd like to search for a new flight.",
        )

    if has_cache(session_id) and _is_show_more(req.message):
        cache = get_cache(session_id)
        cache["page"] += 1
        reply = _format_page(cache["aircraft"], cache["page"], cache["total"])
        return ChatResponse(session_id=session_id, response=reply)

    reply = chat_with_agent(session_id, req.message)
    # Replace LLM aircraft list with clean cache data (guaranteed full airport labels)
    if has_cache(session_id) and re.search(r'^\d+\.\s+\*\*', reply, re.MULTILINE):
        cache = get_cache(session_id)
        first_item = re.search(r'^\d+\.\s+\*\*', reply, re.MULTILINE)
        intro = reply[:first_item.start()].rstrip() if first_item else ""
        formatted = _format_page(cache["aircraft"], 1, cache["total"])
        reply = (intro + "\n\n" + formatted).strip()
    return ChatResponse(session_id=session_id, response=reply)


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())

    def generate():
        yield f"data: [SESSION:{session_id}]\n\n"

        if has_cache(session_id) and _is_no(req.message):
            delete_cache(session_id)
            yield "data: Sure! Let me know if you'd like to search for a new flight.\n\n"
            yield "data: [DONE]\n\n"
            return

        if has_cache(session_id) and _is_show_more(req.message):
            cache = get_cache(session_id)
            cache["page"] += 1
            reply = _format_page(cache["aircraft"], cache["page"], cache["total"])
            safe  = reply.replace("\n", "\\n")
            yield f"data: {safe}\n\n"
            yield "data: [DONE]\n\n"
            return

        full_reply = ""
        intro_sent = False
        for token in stream_with_agent(session_id, req.message):
            full_reply += token
            if not intro_sent:
                first_item = re.search(r'^\d+\.\s+\*\*', full_reply, re.MULTILINE)
                if first_item:
                    # Send only the intro text — hold all aircraft tokens
                    intro = full_reply[:first_item.start()].rstrip()
                    if intro:
                        safe = intro.replace("\n", "\\n")
                        yield f"data: {safe}\n\n"
                    intro_sent = True
                else:
                    safe = token.replace("\n", "\\n")
                    yield f"data: {safe}\n\n"
            # Once intro_sent, hold all aircraft tokens — clean version sent below

        # Stream complete — cache now populated. Send clean formatted aircraft block.
        if has_cache(session_id) and re.search(r'^\d+\.\s+\*\*', full_reply, re.MULTILINE):
            cache = get_cache(session_id)
            formatted = _format_page(cache["aircraft"], 1, cache["total"])
            safe = ("\n\n" + formatted).replace("\n", "\\n")
            yield f"data: {safe}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")