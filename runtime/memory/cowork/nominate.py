"""Two team behaviours: suggest who to pull in, and let agents self-select.

- **Competence memory**: record each agent's outcomes per topic tag so the team
  learns "ask @db-agent about queries" — then ``suggest`` proposes who to invite
  for a new message (keyword match × past success).
- **Self-nomination gate**: in a busy swarm, not everyone should pile on. ``gate``
  keeps only the participant agents relevant to the message, so multi-agent stays
  economical and natural instead of N walls of text.

Scoring is pure; only the competence tally is persisted (a tiny sqlite table).
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "you",
        "are",
        "can",
        "with",
        "this",
        "that",
        "how",
        "what",
        "please",
        "help",
        "need",
        "want",
        "我",
        "的",
        "了",
        "吗",
        "你",
        "帮",
    }
)


def _expand_cjk(token: str) -> set[str]:
    """CJK has no spaces, so a run like 算法实现 is one token that won't match
    算法工程师. Emit overlapping bigrams (算法, 法实, 实现) so compound terms
    partially match. Latin tokens pass through unchanged."""
    if re.fullmatch(r"[一-鿿]+", token) and len(token) > 2:
        return {token[i : i + 2] for i in range(len(token) - 1)}
    return {token}


def tokenize(text: str) -> set[str]:
    """Lowercase alnum/CJK tokens worth matching on (CJK expanded to bigrams,
    short stopwords dropped)."""
    raw = re.findall(r"[a-zA-Z0-9]{3,}|[一-鿿]{2,}", (text or "").lower())
    out: set[str] = set()
    for t in raw:
        if t not in _STOPWORDS:
            out |= _expand_cjk(t)
    return out


def domain_tokens(agent_id: str, family: str = "") -> set[str]:
    """An agent's topic surface from its id + family (split on separators; CJK
    runs expanded to bigrams so 算法工程师 matches 算法… goals)."""
    parts = re.split(r"[-_./\s]+", f"{agent_id} {family}".lower())
    out: set[str] = set()
    for p in parts:
        if len(p) >= 2:
            out |= _expand_cjk(p)
    return out


def relevance(text: str, agent_id: str, family: str = "") -> float:
    """How relevant an agent is to a message: token overlap, 0..1."""
    text_toks = tokenize(text)
    if not text_toks:
        return 0.0
    dom = domain_tokens(agent_id, family)
    hits = sum(1 for d in dom if any(d in t or t in d for t in text_toks))
    return min(1.0, hits / 2.0)  # 2 domain hits → fully relevant


class CompetenceStore:
    """Per-agent, per-tag success tally — the team's memory of who's good at what."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        from runtime.platform.process.paths import app_paths

        d = Path(base_dir) if base_dir else app_paths().data_dir / "cowork"
        d.mkdir(parents=True, exist_ok=True)
        self._db = d / "competence.db"
        self._lock = threading.Lock()
        with self._lock, sqlite3.connect(str(self._db)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS competence ("
                "agent_id TEXT NOT NULL, tag TEXT NOT NULL, wins INTEGER DEFAULT 0, "
                "total INTEGER DEFAULT 0, PRIMARY KEY (agent_id, tag))"
            )

    def record(self, agent_id: str, tag: str, success: bool) -> None:
        with self._lock, sqlite3.connect(str(self._db)) as conn:
            conn.execute(
                "INSERT INTO competence(agent_id, tag, wins, total) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(agent_id, tag) DO UPDATE SET wins = wins + ?, total = total + 1",
                (agent_id, tag, 1 if success else 0, 1 if success else 0),
            )

    def competence(self, agent_id: str, tag: str) -> float:
        """Success rate for (agent, tag), 0.5 prior when unseen (no strong signal)."""
        with self._lock, sqlite3.connect(str(self._db)) as conn:
            row = conn.execute(
                "SELECT wins, total FROM competence WHERE agent_id=? AND tag=?",
                (agent_id, tag),
            ).fetchone()
        if not row or not row[1]:
            return 0.5
        return row[0] / row[1]

    def best_tag_score(self, agent_id: str, tags: set[str]) -> float:
        return max((self.competence(agent_id, t) for t in tags), default=0.5)


def suggest(
    text: str,
    candidates: list[tuple[str, str]],
    store: CompetenceStore | None = None,
) -> list[dict]:
    """Rank candidate agents to pull in for ``text``.

    ``candidates``: (agent_id, family) of agents NOT yet in the thread. Score =
    relevance, boosted by past competence on the message's tags. Returns those
    with any relevance, best first."""
    tags = tokenize(text)
    out: list[dict] = []
    for agent_id, family in candidates:
        rel = relevance(text, agent_id, family)
        if rel <= 0:
            continue
        comp = store.best_tag_score(agent_id, tags) if store else 0.5
        out.append(
            {
                "agent_id": agent_id,
                "relevance": round(rel, 2),
                "competence": round(comp, 2),
                "score": round(rel * 0.7 + comp * 0.3, 3),
            }
        )
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def gate(
    participant_agents: list[tuple[str, str]],
    text: str,
    *,
    threshold: float = 0.5,
) -> list[str]:
    """Self-nomination: of the agents already participating, who is relevant
    enough to speak this turn. Empty text or all-irrelevant → everyone passes
    (don't silence a genuinely open prompt)."""
    if not tokenize(text):
        return [a for a, _ in participant_agents]
    scored = [(a, relevance(text, a, fam)) for a, fam in participant_agents]
    relevant = [a for a, r in scored if r >= threshold]
    return relevant or [a for a, _ in participant_agents]
