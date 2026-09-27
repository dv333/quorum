"""One queue for local model calls, shared by every debate.

A Mac (or one GPU) runs one model well at a time. Two debates that call their models together push each other's models
out of memory, reload them over and over, and wait on each other inside Ollama, where a turn's time limit keeps running.
So calls to a local server take turns here: one at a time per server, and when several are waiting the one that wants
the model already loaded goes first (no reload), unless someone has waited too long. Remote servers don't queue.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

# A waiter this old goes next even when another wants the loaded model
MAX_SKIP_SECONDS = 90.0


@dataclass
class Caller:
    """Who holds or wants the model: shown to other debates while they wait."""

    debate_id: str
    actor: str
    model: str
    title: str = ""


@dataclass
class _Waiter:
    caller: Caller
    since: float
    granted: asyncio.Future
    on_wait: Optional[Callable[[Caller], None]] = field(default=None)


class ModelQueue:
    def __init__(self, max_skip_seconds: float = MAX_SKIP_SECONDS) -> None:
        self.max_skip_seconds = max_skip_seconds
        self._holders: Dict[str, Caller] = {}
        self._waiters: Dict[str, List[_Waiter]] = {}
        self._loaded: Dict[str, str] = {}  # server -> model that ran last (still in memory)

    def holder(self, server: str) -> Optional[Caller]:
        return self._holders.get(server)

    def waiting(self, server: str) -> List[Caller]:
        return [w.caller for w in self._waiters.get(server, [])]

    def wanted_next(self, server: str, model: str) -> bool:
        """Someone is waiting for this model: keep it loaded after this call."""
        return any(w.caller.model == model for w in self._waiters.get(server, []))

    @asynccontextmanager
    async def turn(
        self, server: Optional[str], caller: Caller, on_wait: Optional[Callable[[Caller], None]] = None
    ) -> AsyncIterator[bool]:
        """Hold the server for one call. Yields whether the call had to wait. server None: no queue (remote)."""
        if server is None:
            yield False
            return
        waited = False
        if server in self._holders or self._waiters.get(server):
            waited = True
            w = _Waiter(caller, time.monotonic(), asyncio.get_running_loop().create_future(), on_wait)
            self._waiters.setdefault(server, []).append(w)
            self._notify(w, self._holders.get(server))
            try:
                await w.granted
            except asyncio.CancelledError:
                if w.granted.done() and not w.granted.cancelled():
                    self._release(server)  # granted just as it was cancelled: pass the turn on
                elif w in self._waiters.get(server, []):
                    self._waiters[server].remove(w)
                raise
        else:
            self._holders[server] = caller
        try:
            yield waited
        finally:
            self._release(server)

    def _release(self, server: str) -> None:
        done = self._holders.pop(server, None)
        if done:
            self._loaded[server] = done.model
        queue = self._waiters.get(server) or []
        queue[:] = [w for w in queue if not w.granted.done()]  # a cancelled waiter leaves its future cancelled
        if not queue:
            return
        nxt = self._pick(server, queue)
        queue.remove(nxt)
        self._holders[server] = nxt.caller
        nxt.granted.set_result(True)
        for w in queue:  # the rest now wait on someone else
            self._notify(w, nxt.caller)

    def _pick(self, server: str, queue: List[_Waiter]) -> _Waiter:
        oldest = queue[0]
        if time.monotonic() - oldest.since >= self.max_skip_seconds:
            return oldest
        loaded = self._loaded.get(server)
        return next((w for w in queue if w.caller.model == loaded), oldest)

    @staticmethod
    def _notify(w: _Waiter, holder: Optional[Caller]) -> None:
        if w.on_wait and holder:
            try:
                w.on_wait(holder)
            except Exception:
                pass


def server_key(endpoint: Any) -> Optional[str]:
    """Local servers share this machine's memory and GPU, so their calls queue; remote ones run as they come."""
    return endpoint.base_url if endpoint.is_local else None


QUEUE = ModelQueue()
