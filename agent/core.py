import re
from langchain.agents import create_agent
from langchain.agents.middleware import ToolRetryMiddleware, ModelCallLimitMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from agent.prompts import SYSTEM_PROMPT
from config.llm_config import groq_llm
from tools.avinode_tool import search_flights


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


def _build_enriched(user_input: str) -> str:
    has_details = _has_all_flight_details(user_input)

    is_conversational = (
        len(user_input.strip()) < 60
        and not has_details
    )
    if is_conversational:
        return user_input

    if has_details:
        return (
            f"{user_input}\n\n"
            f"[System note: user provided all flight details. "
            f"Call search_flights immediately. Do NOT ask any more questions.]"
        )

    return user_input


def chat_with_agent(session_id: str, user_input: str) -> str:
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": _build_enriched(user_input)}]},
            {"configurable": {"thread_id": session_id}},
        )
        return result["messages"][-1].content
    except Exception as e:
        return f"Error: {str(e)}"


def stream_with_agent(session_id: str, user_input: str):
    try:
        for token, metadata in agent.stream(
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
        yield f"Error: {str(e)}"