import asyncio

import pytest

from backend.model_queue import Caller, ModelQueue, server_key
from backend.providers import Endpoint

SERVER = "http://localhost:11434"


def caller(model, debate="d1", actor="Otter"):
    return Caller(debate, actor, model, f"Debate {debate}")


async def hold(queue, c, log, release, server=SERVER):
    async with queue.turn(server, c):
        log.append(("start", c.debate_id, c.model))
        await release.wait()
        log.append(("end", c.debate_id, c.model))


async def test_calls_to_one_local_server_take_turns():
    q = ModelQueue()
    log, first, second = [], asyncio.Event(), asyncio.Event()
    a = asyncio.create_task(hold(q, caller("m1", "a"), log, first))
    await asyncio.sleep(0)
    b = asyncio.create_task(hold(q, caller("m2", "b"), log, second))
    await asyncio.sleep(0.01)
    assert log == [("start", "a", "m1")]  # b waits while a holds the server
    assert q.holder(SERVER).debate_id == "a" and [c.debate_id for c in q.waiting(SERVER)] == ["b"]
    first.set()
    await a
    await asyncio.sleep(0)
    assert log[-1] == ("start", "b", "m2")
    second.set()
    await b
    assert q.holder(SERVER) is None


async def test_remote_servers_do_not_queue():
    q = ModelQueue()
    log, release = [], asyncio.Event()
    tasks = [asyncio.create_task(hold(q, caller("m", d), log, release, server=None)) for d in ("a", "b")]
    await asyncio.sleep(0.01)
    assert [e for e in log if e[0] == "start"] == [("start", "a", "m"), ("start", "b", "m")]
    release.set()
    await asyncio.gather(*tasks)


async def test_the_loaded_model_goes_next_to_avoid_a_reload():
    q = ModelQueue()
    log, gates = [], {d: asyncio.Event() for d in "abc"}
    a = asyncio.create_task(hold(q, caller("gpt-oss", "a"), log, gates["a"]))
    await asyncio.sleep(0)
    b = asyncio.create_task(hold(q, caller("qwen3", "b"), log, gates["b"]))  # waited longer, other model
    await asyncio.sleep(0)
    c = asyncio.create_task(hold(q, caller("gpt-oss", "c"), log, gates["c"]))  # same model as the one loaded
    await asyncio.sleep(0.01)
    gates["a"].set()
    await a
    await asyncio.sleep(0)
    assert log[-1] == ("start", "c", "gpt-oss")
    gates["c"].set()
    await c
    await asyncio.sleep(0)
    assert log[-1] == ("start", "b", "qwen3")
    gates["b"].set()
    await b


async def test_a_long_wait_beats_the_loaded_model():
    q = ModelQueue(max_skip_seconds=0)
    log, gates = [], {d: asyncio.Event() for d in "abc"}
    a = asyncio.create_task(hold(q, caller("gpt-oss", "a"), log, gates["a"]))
    await asyncio.sleep(0)
    b = asyncio.create_task(hold(q, caller("qwen3", "b"), log, gates["b"]))
    await asyncio.sleep(0)
    c = asyncio.create_task(hold(q, caller("gpt-oss", "c"), log, gates["c"]))
    await asyncio.sleep(0.01)
    gates["a"].set()
    await a
    await asyncio.sleep(0)
    assert log[-1] == ("start", "b", "qwen3")  # oldest first once it has waited too long
    for g in gates.values():
        g.set()
    await asyncio.gather(b, c)


async def test_waiters_hear_who_holds_the_server_and_when_that_changes():
    q = ModelQueue()
    heard, gates = [], {d: asyncio.Event() for d in "abc"}

    async def wait_and_listen(c):
        async with q.turn(SERVER, c, on_wait=lambda h: heard.append((c.debate_id, h.debate_id))):
            await gates[c.debate_id].wait()

    a = asyncio.create_task(hold(q, caller("m1", "a"), [], gates["a"]))
    await asyncio.sleep(0)
    b = asyncio.create_task(wait_and_listen(caller("m2", "b")))
    await asyncio.sleep(0)
    c = asyncio.create_task(wait_and_listen(caller("m3", "c")))
    await asyncio.sleep(0.01)
    assert heard == [("b", "a"), ("c", "a")]
    gates["a"].set()
    await a
    await asyncio.sleep(0)
    assert heard[-1] == ("c", "b")  # c now waits on b
    gates["b"].set()
    gates["c"].set()
    await asyncio.gather(b, c)


async def test_a_cancelled_waiter_leaves_the_queue():
    q = ModelQueue()
    log, gates = [], {d: asyncio.Event() for d in "abc"}
    a = asyncio.create_task(hold(q, caller("m1", "a"), log, gates["a"]))
    await asyncio.sleep(0)
    b = asyncio.create_task(hold(q, caller("m2", "b"), log, gates["b"]))
    await asyncio.sleep(0)
    c = asyncio.create_task(hold(q, caller("m3", "c"), log, gates["c"]))
    await asyncio.sleep(0.01)
    b.cancel()  # the user stopped debate b while it waited
    with pytest.raises(asyncio.CancelledError):
        await b
    gates["a"].set()
    await a
    await asyncio.sleep(0)
    assert log[-1] == ("start", "c", "m3") and all(e[1] != "b" for e in log)
    gates["c"].set()
    await c
    assert q.holder(SERVER) is None and q.waiting(SERVER) == []


async def test_a_waiter_cancelled_as_its_turn_comes_passes_it_on():
    q = ModelQueue()
    log, gates = [], {d: asyncio.Event() for d in "abc"}
    a = asyncio.create_task(hold(q, caller("m1", "a"), log, gates["a"]))
    await asyncio.sleep(0)
    b = asyncio.create_task(hold(q, caller("m2", "b"), log, gates["b"]))
    await asyncio.sleep(0)
    c = asyncio.create_task(hold(q, caller("m3", "c"), log, gates["c"]))
    await asyncio.sleep(0.01)
    gates["a"].set()
    await a  # b is granted the server but hasn't run yet
    b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await b
    await asyncio.sleep(0)
    assert log[-1] == ("start", "c", "m3")
    gates["c"].set()
    await c
    assert q.holder(SERVER) is None


async def test_wanted_next_says_whether_to_keep_a_model_loaded():
    q = ModelQueue()
    release = asyncio.Event()
    a = asyncio.create_task(hold(q, caller("gpt-oss", "a"), [], release))
    await asyncio.sleep(0)
    b = asyncio.create_task(hold(q, caller("gpt-oss", "b"), [], release))
    await asyncio.sleep(0.01)
    assert q.wanted_next(SERVER, "gpt-oss") and not q.wanted_next(SERVER, "qwen3")
    release.set()
    await asyncio.gather(a, b)


def test_only_local_servers_queue():
    local = Endpoint(1, "Ollama", "http://localhost:11434", "ollama")
    remote = Endpoint(2, "Box", "http://10.0.0.5:11434", "ollama")
    assert server_key(local) == "http://localhost:11434"
    assert server_key(remote) is None
