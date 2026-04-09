"""
redis_store.py
──────────────
Two responsibilities:
  1. create_checkpointer()  — returns a LangGraph RedisSaver for agent memory
  2. SessionStore           — thin wrapper for persisting Streamlit UI messages

IMPORTANT: RedisSaver requires decode_responses=False (it handles bytes internally).
           SessionStore needs decode_responses=True (it works with JSON strings).
           These use two separate Redis client instances.
"""

import json
import os
import logging

import redis
from langgraph.checkpoint.redis import RedisSaver   # pip install langgraph-checkpoint-redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── Two separate clients ───────────────────────────────────────────────────────

_bytes_client:  redis.Redis | None = None   # for RedisSaver  (decode_responses=False)
_str_client:    redis.Redis | None = None   # for SessionStore (decode_responses=True)


def _get_bytes_redis() -> redis.Redis:
    """Binary client — required by LangGraph RedisSaver."""
    global _bytes_client
    if _bytes_client is None:
        client = redis.from_url(REDIS_URL, decode_responses=False, socket_connect_timeout=3)
        # Validate connection eagerly so callers get a clear error immediately.
        client.ping()
        _bytes_client = client
    return _bytes_client


def _get_str_redis() -> redis.Redis:
    """String client — used by SessionStore for JSON messages."""
    global _str_client
    if _str_client is None:
        client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=3)
        client.ping()
        _str_client = client
    return _str_client


# ── 1. LangGraph checkpointer ──────────────────────────────────────────────────

def create_checkpointer() -> RedisSaver:
    """
    Return a RedisSaver instance for use as a LangGraph checkpointer.
    Uses decode_responses=False client as required by RedisSaver.
    Calls .setup() to create the required Redis search indexes if they
    don't exist yet (safe to call multiple times — it's a no-op after first run).

    Raises redis.exceptions.ConnectionError if Redis is unreachable.
    core.py catches this and falls back to InMemorySaver automatically.
    """
    r = _get_bytes_redis()   # raises ConnectionError if Redis is down
    checkpointer = RedisSaver(redis_client=r)
    checkpointer.setup()
    logger.info("RedisSaver checkpointer ready (URL: %s)", REDIS_URL)
    return checkpointer


# ── 2. Streamlit UI message store ──────────────────────────────────────────────

_MSG_PREFIX = "jetbot:ui:messages:"   # key pattern: jetbot:ui:messages:<session_id>
_MSG_TTL    = 60 * 60 * 24 * 7        # keep UI history for 7 days


class SessionStore:
    """
    Persist and retrieve the list of chat messages shown in the Streamlit UI.

    Messages are stored as a JSON array under the key:
        jetbot:ui:messages:<session_id>

    Falls back gracefully to an empty list when Redis is unavailable so the
    Streamlit UI never hard-crashes due to a store error.

    Usage:
        store = SessionStore()
        msgs  = store.load(session_id)          # → list[dict]
        store.append(session_id, {"role": "user", "content": "..."})
        store.save(session_id, messages_list)   # bulk overwrite
        store.clear(session_id)                 # reset on "New Flight"
    """

    def __init__(self):
        try:
            self._r = _get_str_redis()
            self._available = True
        except Exception as e:
            logger.warning("SessionStore: Redis unavailable (%s). Messages won't persist.", e)
            self._r = None
            self._available = False

    def _key(self, session_id: str) -> str:
        return f"{_MSG_PREFIX}{session_id}"

    def load(self, session_id: str) -> list[dict]:
        """Load all messages for a session. Returns [] if not found or Redis is down."""
        if not self._available:
            return []
        try:
            raw = self._r.get(self._key(session_id))
            if not raw:
                return []
            return json.loads(raw)
        except Exception as e:
            logger.warning("SessionStore.load error: %s", e)
            return []

    def save(self, session_id: str, messages: list[dict]) -> None:
        """Overwrite stored messages (used after bulk changes)."""
        if not self._available:
            return
        try:
            self._r.set(self._key(session_id), json.dumps(messages), ex=_MSG_TTL)
        except Exception as e:
            logger.warning("SessionStore.save error: %s", e)

    def append(self, session_id: str, message: dict) -> None:
        """Append one message and refresh TTL."""
        msgs = self.load(session_id)
        msgs.append(message)
        self.save(session_id, msgs)

    def clear(self, session_id: str) -> None:
        """Delete stored messages for a session."""
        if not self._available:
            return
        try:
            self._r.delete(self._key(session_id))
        except Exception as e:
            logger.warning("SessionStore.clear error: %s", e) 