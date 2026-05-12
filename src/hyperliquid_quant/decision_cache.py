"""Decision cache — eliminates the LLM-forgery hole in place_order.

PATCH NOTES (severe fixes #6 + #9):
====================================
The original tool flow accepted a free-form `decision_json` from the
LLM and only re-ran the risk check. That meant the agent could
hand-construct entry/SL/TP values and the server would happily execute
as long as the *numbers* didn't trip the L4 guards. That violates the
"formulaic, no LLM discretion" promise in the README.

This module gives the server an opaque-token API:

  evaluate_strategy()  →  (decision, token)
  place_order(token)   →  server pops the decision out of the cache,
                          executes it; refuses if the token is unknown
                          or has expired.

Tokens live ``decision_ttl_seconds`` (default 30s). Combined with the
per-trade single-use pop semantics, this also closes the "decision price
is stale by the time we hit place_order" hole — if the agent thinks for
too long the token expires and they have to re-evaluate.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .strategy import TradeDecision


@dataclass
class _Entry:
    decision: TradeDecision
    created_at: float


class DecisionCache:
    """In-memory, single-use, TTL-bounded decision store.

    Thread-safe so the MCP server can use it from multiple async handlers.
    """

    def __init__(self, ttl_seconds: int = 30):
        self.ttl = ttl_seconds
        self._store: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def issue(self, decision: TradeDecision) -> str:
        """Insert a decision into the cache and return a single-use token."""
        token = secrets.token_urlsafe(16)
        with self._lock:
            self._store[token] = _Entry(decision=decision, created_at=time.time())
            self._gc_locked()
        return token

    def peek(self, token: str) -> Optional[TradeDecision]:
        """Inspect a token without consuming it. Returns None if missing/expired."""
        with self._lock:
            self._gc_locked()
            entry = self._store.get(token)
        if entry is None:
            return None
        if time.time() - entry.created_at > self.ttl:
            return None
        return entry.decision

    def redeem(self, token: str) -> Optional[TradeDecision]:
        """Pop the decision for `token`. Returns None if missing/expired.

        The pop is intentional — a token cannot be reused. This protects
        against replay attacks where the LLM caches an old decision and
        resubmits after the underlying signals have changed.
        """
        with self._lock:
            self._gc_locked()
            entry = self._store.pop(token, None)
        if entry is None:
            return None
        if time.time() - entry.created_at > self.ttl:
            return None
        return entry.decision

    def peek_count(self) -> int:
        with self._lock:
            self._gc_locked()
            return len(self._store)

    def _gc_locked(self) -> None:
        now = time.time()
        expired = [k for k, e in self._store.items() if now - e.created_at > self.ttl]
        for k in expired:
            del self._store[k]
