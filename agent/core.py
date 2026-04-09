import re
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

# Simple yes/confirmation words that follow an alternative airport offer
_SIMPLE_YES = re.compile(
    r'^(yes|yeah|yep|yup|sure|ok|okay|show|please|go ahead|do it|show me|'
    r'show\s+alternative|alternative|other airport|yes please)[\s!.,]*$',
    re.IGNORECASE
)

# Natural language date patterns (no year required)
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

# Relative date keywords
_RELATIVE_DATE_RE = re.compile(
    r'\b(today|tomorrow|day after tomorrow|next\s+\w+|this\s+\w+|in\s+\d+\s+(?:days?|weeks?))\b',
    re.IGNORECASE
)


def _is_alternative_airport_request(text: str) -> bool:
    """Detect if user is asking for alternative airport results."""
    t = text.strip()
    return bool(_ALT_AIRPORT_PATTERNS.search(t) or _SIMPLE_YES.match(t))


def _has_date(text: str) -> bool:
    """Detect any date expression — ISO, numeric, natural language, or relative."""
    t = text.lower()
    return bool(
        re.search(r'\d{4}-\d{2}-\d{2}', t) or          # ISO: 2026-04-10
        re.search(r'\b\d{1,2}[/\-]\d{1,2}[/\-]\d{4}\b', t) or  # 10/04/2026
        _NL_DATE_RE.search(t) or                         # "10th April 2026", "April 10"
        _RELATIVE_DATE_RE.search(t)                      # "tomorrow", "next Friday"
    )


def _has_all_flight_details(text: str) -> bool:
    t = text.lower()
    has_date  = _has_date(t)
    has_pax   = bool(re.search(r'\b\d+\s*(?:pax|passenger|people|person|travell?er)', t))
    has_route = bool(re.search(r'\bfrom\b.+\bto\b|\bto\b.+\bon\b', t))
    return has_date and has_pax and has_route


def _extract_partial_route(text: str) -> dict:
    """
    Extract departure and/or destination city from a message that has a route
    but is missing date/pax (so _has_all_flight_details returns False).

    Handles:
      "from london to paris"
      "london to paris"
      "fly from NYC to Dubai"
      "i want to go from Munich to Zurich"
    Returns dict with keys 'from' and/or 'to' (both strings, may be empty).
    """
    t = text.strip()
    # Pattern: "from X to Y"
    m = re.search(
        r'\bfrom\s+([A-Za-z][A-Za-z\s\-]{1,30}?)\s+to\s+([A-Za-z][A-Za-z\s\-]{1,30}?)(?:\s|$|[.,!?])',
        t, re.IGNORECASE
    )
    if m:
        return {"from": m.group(1).strip(), "to": m.group(2).strip()}

    # Pattern: "X to Y" (no "from")
    m = re.search(
        r'\b([A-Za-z][A-Za-z\s\-]{1,25}?)\s+to\s+([A-Za-z][A-Za-z\s\-]{1,25}?)(?:\s|$|[.,!?])',
        t, re.IGNORECASE
    )
    if m:
        dep = m.group(1).strip()
        dst = m.group(2).strip()
        # Filter out false positives like "one way to" or "want to"
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


def _build_enriched(user_input: str) -> str:
    """
    Enrich user message with system notes to guide the agent correctly.
    Date normalisation is handled entirely by the LLM (see SYSTEM_PROMPT).

    Cases:
    1. Alternative airport request → inject note to re-search with use_alternative_airport=True.
    2. All flight details provided → inject note to ask amenities then search.
    3. Partial route (cities given, no date/pax yet) → inject note with known cities so
       agent skips asking for them again and continues from the right step.
    4. Conversational / partial → pass through as-is.
    """
    text = user_input.strip()

    # Case 1: user is asking for alternative airport results
    if _is_alternative_airport_request(text):
        return (
            f"{text}\n\n"
            f"[System note: The user wants to see alternative airport options. "
            f"Call search_flights immediately with use_alternative_airport=True. "
            f"Keep ALL other parameters (departure_city, destination_city, date, pax) "
            f"IDENTICAL to the previous search. "
            f"Do NOT ask for trip type, route, pax, date, or amenities again. "
            f"Do NOT re-run the collection flow. Just search and show results.]"
        )

    # Case 2: all details provided upfront
    if _has_all_flight_details(text):
        return (
            f"{text}\n\n"
            f"[System note: user provided all flight details (route, date, pax). "
            f"Ask the amenities question ONE time, then call search_flights after their reply.]"
        )

    # Case 3: partial route provided (cities known, date/pax missing)
    route = _extract_partial_route(text)
    if route.get("from") and route.get("to"):
        return (
            f"{text}\n\n"
            f"[System note: The user has already provided their route: "
            f"departure='{route['from']}', destination='{route['to']}'. "
            f"Do NOT ask for departure city or destination city again — they are already known. "
            f"Follow the collection order: ask trip type (if not given), then skip route, "
            f"then ask pax, then date(s), then amenities, then search.]"
        )

    # Case 4: normal conversational flow — pass through unchanged
    return text


def chat_with_agent(session_id: str, user_input: str) -> str:
    try:
        result = _get_agent().invoke(
            {"messages": [{"role": "user", "content": _build_enriched(user_input)}]},
            {"configurable": {"thread_id": session_id}},
        )
        return result["messages"][-1].content
    except Exception as e:
        logger.exception("chat_with_agent failed")
        return f"Error: {str(e)}"


def stream_with_agent(session_id: str, user_input: str):
    try:
        for token, metadata in _get_agent().stream(
            {"messages": [{"role": "user", "content": _build_enriched(user_input)}]},
            {"configurable": {"thread_id": session_id}},
            stream_mode="messages",
        ):
            if (
                metadata.get("langgraph_node") == "model"
                and token.content
                and isinstance(token.content, str)
            ):
                yield token.content
    except Exception as e:
        logger.exception("stream_with_agent failed")
        yield f"Error: {str(e)}"