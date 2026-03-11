"""
Sebol Memory — SQLite FTS5 long-term memory.

Every DM message (user + bot) is stored.
Before responding, a relevance search returns the most useful historical context.
This lets the bot remember client decisions, campaign details, etc. from months ago
without sending everything to Claude on every request.
"""

import os
import re
import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "memory.db")

# ── init ──────────────────────────────────────────────────────────────────────

def init_memory():
    """Create DB and FTS table if they don't exist. Call once at startup."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        # FTS5 virtual table — all fields searchable, ts/user_id not indexed in FTS
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory USING fts5(
                user_id,
                channel,
                ts,
                role,
                content,
                created_at,
                tokenize = "unicode61 remove_diacritics 2"
            )
        """)
        conn.commit()
    logger.info("✅ Memory DB initialized: %s", DB_PATH)


# ── write ─────────────────────────────────────────────────────────────────────

def remember(user_id: str, channel: str, ts: str, role: str, content: str):
    """Persist a single message to memory. Silently skips empty content."""
    content = (content or "").strip()
    if not content:
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO memory(user_id, channel, ts, role, content, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, channel, ts, role, content, datetime.now().isoformat()),
            )
            conn.commit()
    except Exception as e:
        logger.warning("memory.remember error: %s", e)


# ── search ────────────────────────────────────────────────────────────────────

def _fts_query(text: str) -> str:
    """
    Build a safe FTS5 query from free text.
    Splits into tokens, escapes special chars, joins with OR so partial matches work.
    Filters out very short / stop words.
    """
    STOP = {"a", "i", "w", "z", "na", "do", "to", "że", "ze", "się", "jak",
            "co", "nie", "są", "o", "po", "dla", "by", "czy", "te", "ta",
            "ten", "się", "bo", "ale", "też", "już", "go", "mi", "mu", "że"}
    tokens = re.findall(r'\w+', text.lower())
    tokens = [t for t in tokens if len(t) >= 3 and t not in STOP]
    if not tokens:
        return '""'   # no-op query
    # FTS5 needs quotes around each token to treat it as a phrase prefix
    escaped = [f'"{t}"' for t in tokens[:12]]  # max 12 terms
    return " OR ".join(escaped)


def recall(query: str, user_id: str = None, limit: int = 12) -> list[dict]:
    """
    Search memory for messages relevant to *query*.
    Returns list of {role, content, created_at} dicts ordered by relevance.
    """
    fts_q = _fts_query(query)
    if fts_q == '""':
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            if user_id:
                rows = conn.execute(
                    """
                    SELECT role, content, created_at
                    FROM memory
                    WHERE memory MATCH ? AND user_id = ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_q, user_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT role, content, created_at
                    FROM memory
                    WHERE memory MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_q, limit),
                ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("memory.recall error: %s", e)
        return []


def recall_as_context(query: str, user_id: str = None, limit: int = 12) -> str:
    """
    Returns a formatted string ready to inject into Claude's system prompt.
    Empty string if nothing found.
    """
    hits = recall(query, user_id=user_id, limit=limit)
    if not hits:
        return ""
    lines = []
    for h in hits:
        date = h["created_at"][:10] if h.get("created_at") else "?"
        who = "Sebol" if h["role"] == "assistant" else "User"
        lines.append(f"[{date}] {who}: {h['content'][:300]}")
    return (
        "\n[PAMIĘĆ — relevantne fragmenty z historii rozmów:]\n"
        + "\n".join(lines)
        + "\n[koniec pamięci]\n"
    )
