# ─────────────────────────────────────────────
# Shared results cache for pagination
# Imported by both core.py and main.py
# to avoid circular imports
# ─────────────────────────────────────────────

_results_cache: dict = {}


def store_results(session_id: str, aircraft: list, total: int):
    """Store full aircraft list after a search."""
    _results_cache[session_id] = {
        "aircraft": aircraft,
        "page":     1,
        "total":    total,
    }


def get_cache(session_id: str) -> dict | None:
    """Get cached results for a session."""
    return _results_cache.get(session_id)


def delete_cache(session_id: str):
    """Clear cache for a session."""
    _results_cache.pop(session_id, None)


def has_cache(session_id: str) -> bool:
    """Check if a session has cached results."""
    return session_id in _results_cache