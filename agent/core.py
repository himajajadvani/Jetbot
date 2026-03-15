import re
import json
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

from agent.prompts import SYSTEM_PROMPT
from config.llm_config import groq_llm
from tools.avinode_tool import search_flights, AMENITIES
from results_cache import store_results

AMENITY_KEYWORDS = {
    "wifi":         "wifi",
    "wi-fi":        "wifi",
    "wi fi":        "wifi",
    "internet":     "wifi",
    "catering":     "catering",
    "food":         "catering",
    "meal":         "catering",
    "meals":        "catering",
    "dining":       "catering",
    "drinks":       "catering",
    "vip lounge":   "vip_lounge",
    "vip":          "vip_lounge",
    "lounge":       "vip_lounge",
    "hangar":       "hangar",
    "hanger":       "hangar",
    "storage":      "hangar",
    "customs":      "customs",
    "immigration":  "customs",
    "pet friendly": "pet_friendly",
    "pet-friendly": "pet_friendly",
    "pets":         "pet_friendly",
    "pet":          "pet_friendly",
    "dog":          "pet_friendly",
    "dogs":         "pet_friendly",
    "cat":          "pet_friendly",
    "gpu":          "gpu",
    "ground power": "gpu",
}

WHOLE_WORD_ONLY = {"vip", "dog", "dogs", "pets", "pet", "cat", "gpu", "food", "meal", "meals", "storage"}


def extract_amenities(text: str) -> str:
    text_lower = text.lower()
    found = set()
    for keyword, key in AMENITY_KEYWORDS.items():
        if keyword in WHOLE_WORD_ONLY:
            if re.search(r'\b' + re.escape(keyword) + r'\b', text_lower):
                found.add(key)
        else:
            if keyword in text_lower:
                found.add(key)
    return ",".join(sorted(found))


def _has_all_flight_details(text: str) -> bool:
    t = text.lower()
    has_date  = bool(re.search(r'\d{4}-\d{2}-\d{2}|\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},?\s+\d{4}', t))
    has_pax   = bool(re.search(r'\b\d+\s*(?:pax|passenger|people|person|travell?er)', t))
    has_route = bool(re.search(r'\bfrom\b.+\bto\b|\bto\b.+\bon\b', t))
    return has_date and has_pax and has_route


checkpointer = InMemorySaver()

agent = create_agent(
    model=groq_llm,
    tools=[search_flights],
    checkpointer=checkpointer,
    system_prompt=SYSTEM_PROMPT,
)


def _build_enriched(user_input: str) -> str:
    amenities_str = extract_amenities(user_input)
    has_details   = _has_all_flight_details(user_input)

    # Short conversational reply — no note needed, keeps tokens minimal
    is_conversational = (
        len(user_input.strip()) < 60
        and not amenities_str
        and not has_details
    )
    if is_conversational:
        return user_input

    if amenities_str and has_details:
        return (
            f"{user_input}\n\n"
            f"[System note: all flight details and amenities provided. "
            f"amenities detected = \"{amenities_str}\". "
            f"Call search_flights immediately with these amenities. Do NOT ask any more questions.]"
        )
    elif amenities_str:
        return (
            f"{user_input}\n\n"
            f"[System note: amenities detected = \"{amenities_str}\". "
            f"Pass ONLY these amenities=\"{amenities_str}\" to search_flights. "
            f"Do NOT add any other amenities not listed here.]"
        )
    elif has_details:
        return (
            f"{user_input}\n\n"
            f"[System note: user provided all flight details. "
            f"Ask the amenities question ONE time, then call search_flights immediately after the user replies.]"
        )
    else:
        return user_input


def _cache_results_from_messages(session_id: str, messages: list):
    try:
        for msg in reversed(messages):
            content = getattr(msg, "content", "")
            if isinstance(content, str) and '"aircraft"' in content:
                data     = json.loads(content)
                aircraft = data.get("aircraft", [])
                total    = data.get("total_results", len(aircraft))
                if aircraft:
                    store_results(session_id, aircraft, total)
                break
    except Exception:
        pass


def chat_with_agent(session_id: str, user_input: str) -> str:
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": _build_enriched(user_input)}]},
            {"configurable": {"thread_id": session_id}},
        )
        _cache_results_from_messages(session_id, result.get("messages", []))
        return result["messages"][-1].content
    except Exception as e:
        return f"Error: {str(e)}"


def stream_with_agent(session_id: str, user_input: str):
    try:
        all_messages = []
        for token, metadata in agent.stream(
            {"messages": [{"role": "user", "content": _build_enriched(user_input)}]},
            {"configurable": {"thread_id": session_id}},
            stream_mode="messages",
        ):
            all_messages.append(token)
            if (
                metadata.get("langgraph_node") == "model"
                and token.content
                and isinstance(token.content, str)
            ):
                yield token.content
        _cache_results_from_messages(session_id, all_messages)
    except Exception as e:
        yield f"Error: {str(e)}"