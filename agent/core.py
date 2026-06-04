import re
import json
import logging
from langchain.agents import create_agent
from langchain.agents.middleware import ToolRetryMiddleware, ModelCallLimitMiddleware

from agent.prompts import SYSTEM_PROMPT
from config.llm_config import groq_llm
from tools.avinode_tool import search_flights

logger = logging.getLogger(__name__)


# ── Alternative airport intent detection ──────────────────────────────────────
_ALT_AIRPORT_PATTERNS = re.compile(
    r'\b(alternative|alternate|other\s+airport|different\s+airport|'
    r'show\s+(me\s+)?alternative|yes\s+please|yes,?\s+show|'
    r'show\s+more|see\s+more|other\s+option|another\s+airport)\b',
    re.IGNORECASE
)

_SIMPLE_YES = re.compile(
    r'^(show\s+alternative|alternative|other\s+airport|yes\s+please|'
    r'show\s+me|show\s+more|see\s+more|yes,?\s+show)[\s!.,]*$',
    re.IGNORECASE
)

_NL_DATE_RE = re.compile(
    r'\b\d{1,2}(?:st|nd|rd|th)?[\s\-]+(?:of[\s\-]+)?'
    r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
    r'jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
    r'(?:[\s\-]+\d{4})?\b'
    r'|'
    r'\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
    r'jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
    r'[\s\-]+\d{1,2}(?:st|nd|rd|th)?(?:,?[\s\-]+\d{4})?\b',
    re.IGNORECASE
)

_RELATIVE_DATE_RE = re.compile(
    r'\b(today|tomorrow|day after tomorrow|next\s+\w+|this\s+\w+|in\s+\d+\s+(?:days?|weeks?))\b',
    re.IGNORECASE
)


def _is_alternative_airport_request(text: str) -> bool:
    t = text.strip()
    return bool(_ALT_AIRPORT_PATTERNS.search(t) or _SIMPLE_YES.match(t))


def _has_date(text: str) -> bool:
    t = text.lower()
    return bool(
        re.search(r'\d{2,4}-\d{1,2}-\d{1,2}', t) or
        re.search(r'\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b', t) or
        _NL_DATE_RE.search(t) or
        _RELATIVE_DATE_RE.search(t)
    )


def _has_all_flight_details(text: str) -> bool:
    t = text.lower()
    has_date  = _has_date(t)
    has_pax   = bool(re.search(r'\b\d+\s*(?:pax|passenger|people|person|travell?er)', t))
    has_route = bool(re.search(r'\bfrom\b.+\bto\b|\bto\b.+\bon\b', t))
    return has_date and has_pax and has_route


def _extract_partial_route(text: str) -> dict:
    t = text.strip()
    m = re.search(
        r'\bfrom\s+([A-Za-z][A-Za-z\s\-]{1,30}?)\s+to\s+([A-Za-z][A-Za-z\s\-]{1,30}?)(?:\s|$|[.,!?])',
        t, re.IGNORECASE
    )
    if m:
        return {"from": m.group(1).strip(), "to": m.group(2).strip()}

    m = re.search(
        r'\b([A-Za-z][A-Za-z\s\-]{1,25}?)\s+to\s+([A-Za-z][A-Za-z\s\-]{1,25}?)(?:\s|$|[.,!?])',
        t, re.IGNORECASE
    )
    if m:
        dep = m.group(1).strip()
        dst = m.group(2).strip()
        noise = {"one", "want", "need", "fly", "travel", "going", "get", "way", "how", "what"}
        if dep.lower().split()[-1] not in noise:
            return {"from": dep, "to": dst}

    return {}


def _make_checkpointer():
    try:
        from redis_store import create_checkpointer
        cp = create_checkpointer()
        logger.info("Checkpointer: using Redis (persistent memory).")
        return cp
    except Exception as e:
        logger.warning(f"Redis unavailable ({e}). Falling back to InMemorySaver.")
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()


def _make_agent():
    checkpointer = _make_checkpointer()
    return create_agent(
        model=groq_llm,
        tools=[search_flights],
        checkpointer=checkpointer,
        system_prompt=SYSTEM_PROMPT,
        middleware=[
            ToolRetryMiddleware(
                max_retries=3,
                backoff_factor=2.0,
                initial_delay=1.0,
                tools=["search_flights"],
                retry_on=(ConnectionError, TimeoutError),
            ),
            ModelCallLimitMiddleware(
                thread_limit=30,
                run_limit=10,
                exit_behavior="end",
            ),
        ],
    )


_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        _agent = _make_agent()
    return _agent


def _count_completed_legs(history: list) -> int:
    """Count how many leg dates have been collected so far in a multi-leg conversation."""
    leg_date_count = 0
    for msg in history:
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            # Each time the bot asks for a leg date, a leg route was just confirmed
            if re.search(r'departure date for Leg \d+', content, re.IGNORECASE):
                leg_date_count += 1
    return leg_date_count


def _is_multileg_session(history: list) -> bool:
    """Return True if this conversation is a confirmed multi-leg trip."""
    for msg in history:
        if msg.get("role") == "user":
            content = (msg.get("resolved") or msg.get("content", "")).lower()
            if "multi" in content and "leg" in content:
                return True
            if "[system note: user selected multi-leg" in content.lower():
                return True
    return False


def _build_enriched(user_input: str, history: list = None) -> str:
    text = user_input.strip()
    history = history or []

    # ── Guard: already enriched by the frontend (button click carries a
    #    [System note:] block) — pass through untouched so we never inject
    #    a second, conflicting note.
    if "[System note:" in text:
        return text

    # ── Modify search: structured message from the modify panel ───────────────
    # One-way: "Search from X to Y on DATE, N passengers[, amenities: A | no amenities]"
    _mod_re = re.compile(
        r'^Search from (.+?) to (.+?) on (.+?),\s*(\d+)\s*passengers?(?:,\s*(?:amenities:\s*(.+?)|no amenities))?[\s.,]*$',
        re.IGNORECASE
    )
    _mod_m = _mod_re.match(text)
    if _mod_m:
        dep  = _mod_m.group(1).strip()
        dst  = _mod_m.group(2).strip()
        date = _mod_m.group(3).strip()
        pax  = _mod_m.group(4).strip()
        am   = (_mod_m.group(5) or "").strip()
        am_note = f"amenities: {am}" if am else "no amenities required"
        return (
            f"{text}\n\n"
            f"[System note: User modified their one-way search via the modify panel. "
            f"All details confirmed — departure='{dep}', destination='{dst}', "
            f"date='{date}', passengers={pax}, {am_note}. "
            f"Do NOT ask for trip type, route, date, pax, or amenities again. "
            f"Call search_flights immediately with all these parameters.]"
        )

    # Round-trip: "Search round-trip from X to Y, outbound DATE1, return DATE2, N passengers[, ...]"
    _rt_re = re.compile(
        r'^Search round-trip from (.+?) to (.+?),\s*outbound (.+?),\s*return (.+?),\s*(\d+)\s*passengers?(?:,\s*(?:amenities:\s*(.+?)|no amenities))?[\s.,]*$',
        re.IGNORECASE
    )
    _rt_m = _rt_re.match(text)
    if _rt_m:
        dep   = _rt_m.group(1).strip()
        dst   = _rt_m.group(2).strip()
        date1 = _rt_m.group(3).strip()
        date2 = _rt_m.group(4).strip()
        pax   = _rt_m.group(5).strip()
        am    = (_rt_m.group(6) or "").strip()
        am_note = f"amenities: {am}" if am else "no amenities required"
        return (
            f"{text}\n\n"
            f"[System note: User modified their round-trip search via the modify panel. "
            f"All details confirmed — departure='{dep}', destination='{dst}', "
            f"outbound date='{date1}', return date='{date2}', passengers={pax}, {am_note}. "
            f"Do NOT ask for trip type, route, dates, pax, or amenities again. "
            f"Call search_flights immediately for both outbound and return legs.]"
        )

    # Multi-leg: "Search multi-leg: X to Y on D1, Y to Z on D2, ..., N passengers[, ...]"
    _ml_re = re.compile(
        r'^Search multi-leg:\s*(.+?),\s*(\d+)\s*passengers?(?:,\s*(?:amenities:\s*(.+?)|no amenities))?[\s.,]*$',
        re.IGNORECASE
    )
    _ml_m = _ml_re.match(text)
    if _ml_m:
        legs_raw = _ml_m.group(1).strip()
        pax      = _ml_m.group(2).strip()
        am       = (_ml_m.group(3) or "").strip()
        am_note  = f"amenities: {am}" if am else "no amenities required"
        return (
            f"{text}\n\n"
            f"[System note: User modified their multi-leg search via the modify panel. "
            f"Legs confirmed: {legs_raw}. Passengers={pax}, {am_note}. "
            f"Do NOT ask for trip type, routes, dates, pax, or amenities again. "
            f"Parse each leg from the legs string and call search_flights for each leg in order.]"
        )

    if _is_alternative_airport_request(text):
        legs = re.findall(r'leg\s*(\d+)', text, re.IGNORECASE)
        all_m = re.search(r'all\s*legs?', text, re.IGNORECASE)
        
        if legs or all_m or _is_multileg_session(history):
            if legs:
                target = f"for leg(s) {', '.join(legs)}"
                condition = f"For any leg matching the user's requested numbers ({', '.join(legs)})"
            elif all_m:
                target = "for ALL legs"
                condition = "For EVERY leg"
            else:
                target = "for the applicable legs"
                condition = "For any leg that you previously identified as having alternative airports available"
            
            return (
                f"{text}\n\n"
                f"[System note: The user wants to see alternative airport options {target} of this multi-leg trip. "
                f"You MUST call search_flights for EVERY leg of the itinerary in order. "
                f"{condition}, set use_alternative_airport=True. For all other legs, set use_alternative_airport=False. "
                f"STRICT RULE: Display ONLY the results for the legs where you used alternative airports. "
                f"Do NOT re-display legs that did not change. "
                f"Keep ALL other parameters (routes, dates, pax) IDENTICAL to the previous search.]"
            )
            
        return (
            f"{text}\n\n"
            f"[System note: The user wants to see alternative airport options. "
            f"Call search_flights immediately with use_alternative_airport=True. "
            f"Keep ALL other parameters (departure_city, destination_city, date, pax) "
            f"IDENTICAL to the previous search. "
            f"Do NOT ask for trip type, route, pax, date, or amenities again. "
            f"Do NOT re-run the collection flow. Just search and show results.]"
        )

    # ── Multi-leg: after Leg 1 date is given, skip yes/no and go directly to Leg 2
    if _has_date(text) and _is_multileg_session(history):
        completed = _count_completed_legs(history)
        if completed == 1:
            # User just answered the Leg 1 date question — force go to Leg 2
            return (
                f"{text}\n\n"
                f"[System note: This is a multi-leg trip. The user just provided the Leg 1 date. "
                f"STRICT RULE: You MUST collect at least 2 legs before searching. "
                f"Do NOT call search_flights yet. Do NOT ask passengers or amenities. "
                f"Immediately proceed to Leg 2: output '**Leg 2:**' then on the next line ask "
                f"'Please share your departure and arrival locations for this leg.']"
            )

    if _has_all_flight_details(text):
        return (
            f"{text}\n\n"
            f"[System note: user provided all flight details (route, date, pax). "
            f"Ask the amenities question ONE time, then call search_flights after their reply.]"
        )

    route = _extract_partial_route(text)
    if route.get("from") and route.get("to"):
        return (
            f"{text}\n\n"
            f"[System note: The user has already provided their route: "
            f"departure='{route['from']}', destination='{route['to']}'. "
            f"Do NOT ask for departure city or destination city again — they are already known. "
            f"Follow the collection order: ask trip type (if not given), then skip route, "
            f"then ask date(s), then pax, then amenities, then search.]"
        )

    # ── Pax count: 1-19 reminder ──────────────────────────────────────────────
    pax_match = re.search(r'^\s*(\d+)\s*$', text)
    if pax_match:
        val = int(pax_match.group(1))
        if 1 <= val <= 19:
            return (
                f"{text}\n\n"
                f"[System note: User provided {val} passengers. "
                f"STRICT RULE: This is NOT an airliner category. "
                f"Do NOT mention limits, airliner rules, or categories. "
                f"Immediately ask the Amenities Question next.]"
            )

    return text


def _currency_note(currency: str) -> str:
    """Build the system note for non-USD currencies."""
    if currency == "USD":
        return ""
    return (
        f"\n\n[System note: The user's selected display currency is {currency}. "
        f"When showing prices, use the value from each aircraft's `prices.{currency}` field "
        f"in the search_flights result. Never show USD prices if a different currency is selected. "
        f"Do not mention exchange rates or conversion — just show the price naturally.]"
    )


def chat_with_agent(session_id: str, user_input: str, currency: str = "USD", history: list = None) -> str:
    try:
        enriched = _build_enriched(user_input, history=history) + _currency_note(currency)
        result = _get_agent().invoke(
            {"messages": [{"role": "user", "content": enriched}]},
            {"configurable": {"thread_id": session_id}},
        )
        return result["messages"][-1].content
    except Exception as e:
        logger.exception("chat_with_agent failed")
        return f"Error: {str(e)}"


def stream_with_agent(session_id: str, user_input: str, currency: str = "USD", history: list = None):
    try:
        enriched = _build_enriched(user_input, history=history) + _currency_note(currency)
        for token, metadata in _get_agent().stream(
            {"messages": [{"role": "user", "content": enriched}]},
            {"configurable": {"thread_id": session_id}},
            stream_mode="messages",
        ):
            # Yield LLM text tokens as normal
            if (
                metadata.get("langgraph_node") == "model"
                and token.content
                and isinstance(token.content, str)
            ):
                yield ("text", token.content)

            # Yield tool results as a PRICES sidecar so the frontend can
            # cache per-currency prices without re-searching
            elif (
                metadata.get("langgraph_node") == "tools"
                and hasattr(token, "content")
                and isinstance(token.content, str)
                and token.content.strip().startswith("{")
            ):
                try:
                    payload = json.loads(token.content)
                    if "aircraft" in payload:
                        # Extract just name→prices mapping to keep the event small
                        prices_map = {
                            ac["aircraft_name"]: ac.get("prices", {})
                            for ac in payload["aircraft"]
                            if ac.get("aircraft_name") and ac.get("prices")
                        }
                        if prices_map:
                            yield ("prices", json.dumps(prices_map))
                except Exception:
                    pass

    except Exception as e:
        logger.exception("stream_with_agent failed")
        yield ("text", f"Error: {str(e)}")

















