"""FastAPI app: REST commands + a Server-Sent Events stream per debate."""

import asyncio
import json
import os
from datetime import datetime, timezone
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import coder, db, diagnostics, export, firecrawl, inventory, monitor, packs, setup
from .config import (
    APP_NAME,
    DEFAULT_AUTOPILOT,
    DEFAULT_CRITERIA,
    DEFAULT_MAX_ROUNDS,
    DEFAULT_NUM_CTX,
    HANDLES,
    MAX_SEATS,
    MIN_ROUNDS_FOR_CONSENSUS,
    MIN_SEATS,
    PORT,
    PROVIDER_PRESETS,
    RESEARCHER_NAME,
    SEAT_COLORS,
)
from .hardware import estimate_model_bytes, fit_label
from .engine import APP_BUS, drop_engine, get_engine, is_advice, is_code_review, recover_after_restart
from .providers import ProviderError, delete_model, lookup_model, pull_model


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    recover_after_restart()
    monitor.start()
    yield
    await monitor.stop()


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- schemas


class SeatIn(BaseModel):
    endpoint_id: int
    model: str
    thinking: bool = True


class ModelRef(BaseModel):
    endpoint_id: int
    model: str


class CreateDebate(BaseModel):
    """Only the question is required; everything else is picked automatically."""

    question: str = Field(min_length=1)
    seats: Optional[List[SeatIn]] = None  # None: auto council
    chair: Optional[ModelRef] = None  # None: the largest member picks the chair
    title: Optional[str] = None
    max_rounds: int = Field(DEFAULT_MAX_ROUNDS, ge=1, le=20)
    autopilot: bool = DEFAULT_AUTOPILOT
    criteria: List[str] = []
    custom_rubric: str = ""
    num_ctx: int = Field(DEFAULT_NUM_CTX, ge=2048, le=262144)
    research_enabled: Optional[bool] = None  # None: on when Firecrawl is available
    researcher: Optional[ModelRef] = None  # None: picked with the chair
    pack: Optional[str] = None  # topic pack id (see /api/packs)
    repo_path: Optional[str] = None  # a local repository the Coder (Claude Code or Codex) can read


class UpdateDebate(BaseModel):
    title: Optional[str] = None
    autopilot: Optional[bool] = None
    research_enabled: Optional[bool] = None
    max_rounds: Optional[int] = Field(None, ge=1, le=20)


class PostMessage(BaseModel):
    content: str = Field(min_length=1)


class PlanIn(BaseModel):
    models: List[ModelRef]
    num_ctx: int = DEFAULT_NUM_CTX


class EndpointIn(BaseModel):
    name: str
    base_url: str
    kind: str = Field("openai_compat", pattern="^(ollama|openai_compat)$")
    api_key: Optional[str] = None


class EndpointPatch(BaseModel):
    enabled: Optional[bool] = None
    api_key: Optional[str] = None  # "" clears it


class SettingsIn(BaseModel):
    firecrawl_mode: Optional[str] = Field(None, pattern="^(self|cloud)$")
    firecrawl_url: Optional[str] = None
    firecrawl_api_key: Optional[str] = None  # "" clears it


class LevelIn(BaseModel):
    level: str = Field(pattern="^(simple|expert)$")


class WhyIn(BaseModel):
    passage: str = Field(min_length=3, max_length=2000)


class ResearchTestIn(BaseModel):
    query: str = Field("latest stable Python release", min_length=2)


class PullIn(BaseModel):
    endpoint_id: int
    model: str


# ---------------------------------------------------------------- helpers


def _require_debate(debate_id: str) -> Dict[str, Any]:
    d = db.query_one("SELECT * FROM debates WHERE id = ?", [debate_id])
    if not d:
        raise HTTPException(404, "Debate not found")
    return d


# ---------------------------------------------------------------- system / models


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/config")
async def config():
    return {
        "app_name": APP_NAME,
        "default_criteria": DEFAULT_CRITERIA,
        "max_rounds": DEFAULT_MAX_ROUNDS,
        "autopilot": DEFAULT_AUTOPILOT,
        "num_ctx": DEFAULT_NUM_CTX,
        "min_seats": MIN_SEATS,
        "max_seats": MAX_SEATS,
        "min_rounds_for_consensus": MIN_ROUNDS_FOR_CONSENSUS,
        "handles": HANDLES,
        "researcher_name": RESEARCHER_NAME,
    }


@app.get("/api/auto-council")
async def get_auto_council(num_ctx: int = DEFAULT_NUM_CTX, pack: Optional[str] = None):
    chosen = packs.get(pack) if pack else None
    if pack and not chosen:
        raise HTTPException(404, f"Unknown topic pack: {pack}")
    council = await inventory.auto_council(num_ctx, pack=chosen)
    council["research"] = await firecrawl.status()
    return council


@app.get("/api/inventory")
async def get_inventory(num_ctx: int = DEFAULT_NUM_CTX, refresh: bool = False):
    return await inventory.inventory(num_ctx, force=refresh)


@app.get("/api/catalog")
async def get_catalog(num_ctx: int = DEFAULT_NUM_CTX):
    inv = await inventory.inventory(num_ctx)
    installed = {m["model"] for m in inv["models"]}
    # Ollama reports "llama3.2:3b" for a pulled "llama3.2:3b"; bare names get ":latest"
    return [
        {**e, "installed": e["model"] in installed or f"{e['model']}:latest" in installed}
        for e in inventory.catalog(num_ctx)
    ]


@app.post("/api/plan")
async def post_plan(body: PlanIn):
    return await inventory.plan([m.model_dump() for m in body.models], body.num_ctx)


@app.post("/api/models/pull")
async def post_pull(body: PullIn):
    ep = inventory.endpoint(body.endpoint_id)
    if not ep:
        raise HTTPException(404, "Endpoint not found")

    async def gen() -> AsyncIterator[bytes]:
        try:
            async for event in pull_model(ep, body.model):
                yield (json.dumps(event) + "\n").encode()
        except (ProviderError, Exception) as e:
            yield (json.dumps({"error": str(e)}) + "\n").encode()
        inventory.invalidate()

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/api/models/lookup")
async def get_model_lookup(name: str, num_ctx: int = DEFAULT_NUM_CTX):
    """Any model in Ollama's public library by name: its download size and whether it fits this machine."""
    try:
        size = await lookup_model(name)
    except ProviderError as e:
        raise HTTPException(502, str(e))
    if size is None:
        return {"model": name.strip(), "found": False}
    est = estimate_model_bytes(size, {}, num_ctx)
    usable = inventory.system_info()["usable_bytes"]
    return {"model": name.strip(), "found": True, "size_bytes": size, "est_bytes": est, "fit": fit_label(est, usable)}


@app.post("/api/models/delete")
async def post_model_delete(body: PullIn):
    ep = inventory.endpoint(body.endpoint_id)
    if not ep:
        raise HTTPException(404, "Endpoint not found")
    try:
        await delete_model(ep, body.model)
    except ProviderError as e:
        raise HTTPException(400, str(e))
    inventory.invalidate()
    return {"ok": True}


@app.get("/api/providers")
async def get_providers():
    return PROVIDER_PRESETS


@app.get("/api/setup/status")
async def setup_status():
    return await setup.status()


@app.post("/api/setup/firecrawl")
async def setup_firecrawl():
    return setup.start_firecrawl()


@app.get("/api/system/series")
async def system_series():
    return monitor.series()


@app.get("/api/endpoints")
async def get_endpoints():
    return (await inventory.inventory())["endpoints"]


@app.post("/api/endpoints")
async def post_endpoint(body: EndpointIn):
    try:
        eid = db.execute(
            "INSERT INTO endpoints (name, base_url, kind, api_key) VALUES (?, ?, ?, ?)",
            [body.name.strip(), body.base_url.strip().rstrip("/"), body.kind, (body.api_key or "").strip() or None],
        )
    except Exception:
        raise HTTPException(400, "An endpoint with that URL already exists")
    inventory.invalidate()
    return {"id": eid}


@app.patch("/api/endpoints/{endpoint_id}")
async def patch_endpoint(endpoint_id: int, body: EndpointPatch):
    fields: Dict[str, Any] = {}
    if body.enabled is not None:
        fields["enabled"] = int(body.enabled)
    if body.api_key is not None:
        fields["api_key"] = body.api_key.strip() or None
    db.update("endpoints", endpoint_id, **fields)
    inventory.invalidate()
    return {"id": endpoint_id}


@app.delete("/api/endpoints/{endpoint_id}")
async def delete_endpoint(endpoint_id: int):
    in_use = db.query_one("SELECT COUNT(*) AS n FROM seats WHERE endpoint_id = ?", [endpoint_id])["n"]
    if in_use:
        raise HTTPException(400, "This endpoint is used by existing debates; disable it instead")
    db.execute("DELETE FROM endpoints WHERE id = ?", [endpoint_id])
    inventory.invalidate()
    return {"ok": True}


# ---------------------------------------------------------------- debates


def _utc(stamp: Optional[str]) -> Optional[str]:
    """A client timestamp ("2026-09-26T07:00:00.000Z") in the form debates store, so text comparison works."""
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        raise HTTPException(400, f"Not a timestamp: {stamp}")


def _debate_filter(
    before: Optional[str],
    after: Optional[str],
    q: Optional[str],
    ids: Optional[str],
    status: Optional[str],
    exclude: Optional[str] = None,
) -> Tuple[str, List[Any]]:
    """WHERE clause for the conundrum list: a time range (newest first, `before` is exclusive), words to find in the
    title or question, specific ids, or statuses."""
    where: List[str] = []
    params: List[Any] = []
    if before:
        where.append("d.created_at < ?")
        params.append(_utc(before))
    if after:
        where.append("d.created_at >= ?")
        params.append(_utc(after))
    if ids:
        wanted = [i for i in ids.split(",") if i][:200]
        where.append(f"d.id IN ({','.join('?' * len(wanted))})" if wanted else "0")
        params += wanted
    if exclude:
        skip = [i for i in exclude.split(",") if i][:200]
        if skip:
            where.append(f"d.id NOT IN ({','.join('?' * len(skip))})")
            params += skip
    if status:
        wanted = [s for s in status.split(",") if s][:20]
        where.append(f"d.status IN ({','.join('?' * len(wanted))})" if wanted else "0")
        params += wanted
    for word in (q or "").split()[:8]:
        like = f"%{word}%"
        where.append(
            "(d.title LIKE ? OR EXISTS (SELECT 1 FROM messages m WHERE m.debate_id = d.id AND m.author_kind = 'user' "
            "AND m.round = 0 AND m.content LIKE ?))"
        )
        params += [like, like]
    return (" WHERE " + " AND ".join(where)) if where else "", params


@app.get("/api/debates")
async def list_debates(
    limit: Optional[int] = Query(None, ge=1, le=500),
    before: Optional[str] = None,
    after: Optional[str] = None,
    q: Optional[str] = None,
    ids: Optional[str] = None,
    status: Optional[str] = None,
    exclude: Optional[str] = None,
):
    """Conundrums, newest first. Without parameters, all of them; the sidebar pages through them with `limit` and
    `before` (the last item's created_at) within a day group's range."""
    where, params = _debate_filter(before, after, q, ids, status, exclude)
    return _debate_rows(where, params, limit)


def _debate_rows(where: str, params: List[Any], limit: Optional[int]) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT d.id, d.title, d.created_at, d.status, d.round, d.topic, d.interrupted, "
        "(SELECT COUNT(*) FROM seats s WHERE s.debate_id = d.id) AS seat_count, "
        "(SELECT m.created_at FROM messages m WHERE m.debate_id = d.id AND m.topic = d.topic AND m.author_kind = 'user' "
        " ORDER BY m.id LIMIT 1) AS asked_at, "
        "(SELECT v.created_at FROM verdicts v WHERE v.debate_id = d.id AND v.topic = d.topic "
        " ORDER BY v.id DESC LIMIT 1) AS answered_at, "
        "(SELECT content FROM messages m WHERE m.debate_id = d.id AND m.author_kind = 'user' AND m.round = 0 "
        " ORDER BY m.id LIMIT 1) AS question "
        f"FROM debates d{where} ORDER BY d.created_at DESC, d.id DESC" + (" LIMIT ?" if limit else ""),
        params + ([limit] if limit else []),
    )
    for r in rows:  # whole seconds from the current question to its answer, as the conundrum's header shows it
        asked, answered = r.pop("asked_at"), r.pop("answered_at")
        r["took_seconds"] = (
            int((datetime.fromisoformat(answered) - datetime.fromisoformat(asked)).total_seconds())
            if asked and answered
            else None
        )
    return rows


def _debate_count(where: str, params: List[Any]) -> int:
    return db.query_one(f"SELECT COUNT(*) AS n FROM debates d{where}", params)["n"]


@app.get("/api/sidebar")
async def sidebar(
    groups: str = "[]", pins: Optional[str] = None, q: Optional[str] = None, limit: int = Query(20, ge=1, le=200)
):
    """Everything the sidebar shows, in one request: for each day group (a time range from the client, in its own
    time zone) the count and, for open groups, the first `limit` items; the pinned conundrums; or search results.
    Pinned conundrums are left out of the groups."""
    try:
        specs = json.loads(groups)
        assert isinstance(specs, list)
    except (ValueError, AssertionError):
        raise HTTPException(400, "groups must be a JSON list")
    pin_ids = ",".join(i for i in (pins or "").split(",") if i)
    pinned = _debate_rows(*_debate_filter(None, None, None, pin_ids, None), None) if pin_ids else []
    if q and q.strip():
        where, params = _debate_filter(None, None, q, None, None)
        return {
            "results": {"items": _debate_rows(where, params, limit), "count": _debate_count(where, params)},
            "pinned": pinned,
        }
    out: Dict[str, Any] = {}
    for spec in specs[:8]:
        if not isinstance(spec, dict) or not spec.get("key"):
            continue
        where, params = _debate_filter(spec.get("before"), spec.get("after"), None, None, None, pin_ids or None)
        size = max(0, min(int(spec.get("limit") or 0), 200))
        out[str(spec["key"])] = {
            "count": _debate_count(where, params),
            "items": _debate_rows(where, params, size) if size else [],
        }
    return {"groups": out, "pinned": pinned}


@app.get("/api/debates/count")
async def count_debates(
    before: Optional[str] = None,
    after: Optional[str] = None,
    q: Optional[str] = None,
    status: Optional[str] = None,
    exclude: Optional[str] = None,
):
    where, params = _debate_filter(before, after, q, None, status, exclude)
    return {"count": _debate_count(where, params)}


@app.post("/api/debates")
async def create_debate(body: CreateDebate):
    pack = None
    if body.pack:
        pack = packs.get(body.pack)
        if not pack:
            raise HTTPException(400, f"Unknown topic pack: {body.pack}")
    seats = body.seats
    researcher = body.researcher
    if seats is None:
        advice = is_advice(body.question) and not is_code_review(body.question, pack, body.repo_path)
        auto = await inventory.auto_council(body.num_ctx, pack=pack, advice=advice)
        seats = [SeatIn(endpoint_id=m["endpoint_id"], model=m["model"], thinking=True) for m in auto["seats"]]
        if researcher is None and auto["researcher"]:
            researcher = ModelRef(endpoint_id=auto["researcher"]["endpoint_id"], model=auto["researcher"]["model"])
        if not seats:
            raise HTTPException(400, "No models found. Install one with Ollama (for example: ollama pull qwen3:8b).")
    repo = coder.valid_repo(body.repo_path) if body.repo_path else None
    if body.repo_path and not repo:
        raise HTTPException(400, f"Not a folder: {body.repo_path}")
    if not MIN_SEATS <= len(seats) <= MAX_SEATS:
        raise HTTPException(400, f"Pick between {MIN_SEATS} and {MAX_SEATS} council members")
    refs = [*seats] + [r for r in (body.chair, researcher) if r]
    for ref in refs:
        if not inventory.endpoint(ref.endpoint_id):
            raise HTTPException(400, f"Unknown endpoint {ref.endpoint_id}")

    research = body.research_enabled
    if research is None:
        research = bool((await firecrawl.status())["ready"])
    # Until the chair is picked, the first (largest) member stands in
    chair = body.chair or ModelRef(endpoint_id=seats[0].endpoint_id, model=seats[0].model)
    debate_id = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO debates (id, title, created_at, chair_endpoint_id, chair_model, max_rounds, autopilot, "
        "criteria_json, custom_rubric, num_ctx, research_enabled, researcher_endpoint_id, researcher_model, "
        "chair_mode, pack_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            debate_id,
            (body.title or "").strip(),
            db.now(),
            chair.endpoint_id,
            chair.model,
            body.max_rounds,
            int(body.autopilot),
            json.dumps(body.criteria),
            # A pack's focus stands in when you didn't write your own rubric
            body.custom_rubric or (pack or {}).get("focus", ""),
            body.num_ctx,
            int(research),
            researcher.endpoint_id if researcher else None,
            researcher.model if researcher else None,
            "manual" if body.chair else "auto",
            json.dumps(pack) if pack else None,
        ],
    )
    if body.repo_path:
        db.update("debates", debate_id, repo_path=repo)
    if "max_rounds" in body.model_fields_set:  # asked for explicitly (MCP quick or deep, the CLI): keep it
        db.update("debates", debate_id, rounds_fixed=1)
    for i, seat in enumerate(seats):
        db.execute(
            "INSERT INTO seats (debate_id, handle, endpoint_id, model, color, thinking_enabled, position) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [debate_id, HANDLES[i], seat.endpoint_id, seat.model, SEAT_COLORS[i], int(seat.thinking), i],
        )

    await get_engine(debate_id).post_user_message(body.question)
    return get_engine(debate_id).snapshot()


@app.get("/api/diagnostics")
async def get_diagnostics(last: int = 20, text: bool = False):
    """Health report from your recent conundrums: outcomes, speed per model, stance and failure rates, findings."""
    r = diagnostics.report(last)
    return PlainTextResponse(diagnostics.as_text(r)) if text else r


@app.get("/api/packs")
async def list_packs():
    return packs.load_all()


@app.get("/api/debates/{debate_id}/export")
async def export_debate(debate_id: str, level: str = "standard", debate: bool = False, download: bool = False):
    """The conundrum as Markdown: the answer at a reading level, its sources and, with debate=true, the debate."""
    _require_debate(debate_id)
    snap = get_engine(debate_id).snapshot()
    try:
        text = export.to_markdown(snap, level=level, include_debate=debate)
    except ValueError as e:
        raise HTTPException(400, str(e))
    headers = {"Content-Disposition": f'attachment; filename="{export.filename(snap)}"'} if download else {}
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8", headers=headers)


@app.get("/api/debates/{debate_id}/trace")
async def trace_debate(debate_id: str):
    """Every model call of the conundrum in order: who, which model, what for, when it was asked for, how long it
    waited for the model server, how long until its first token, how long it ran, and how it ended."""
    _require_debate(debate_id)
    return db.query(
        "SELECT topic, actor, model, kind, started_at, queued_ms, first_token_ms, duration_ms, prompt_tokens, "
        "output_tokens, searches, pages, outcome, error, created_at FROM usage WHERE debate_id = ? ORDER BY id",
        [debate_id],
    )


@app.get("/api/debates/{debate_id}")
async def get_debate(debate_id: str):
    _require_debate(debate_id)
    return get_engine(debate_id).snapshot()


@app.patch("/api/debates/{debate_id}")
async def patch_debate(debate_id: str, body: UpdateDebate):
    _require_debate(debate_id)
    eng = get_engine(debate_id)
    if body.title is not None and body.title.strip():
        eng._set(title=body.title.strip())
    if body.max_rounds is not None:
        eng._set(max_rounds=body.max_rounds, rounds_fixed=1)
    if body.research_enabled is not None:
        eng._set(research_enabled=int(body.research_enabled))
    if body.autopilot is not None:
        await eng.set_autopilot(body.autopilot)
    return eng.debate()


@app.delete("/api/debates/{debate_id}")
async def delete_debate(debate_id: str):
    _require_debate(debate_id)
    await drop_engine(debate_id)
    db.execute("DELETE FROM debates WHERE id = ?", [debate_id])
    return {"ok": True}


@app.post("/api/debates/{debate_id}/messages")
async def post_message(debate_id: str, body: PostMessage):
    _require_debate(debate_id)
    await get_engine(debate_id).post_user_message(body.content)
    return {"ok": True}


@app.post("/api/debates/{debate_id}/verdicts/{verdict_id}/level")
async def post_level(debate_id: str, verdict_id: int, body: LevelIn):
    _require_debate(debate_id)
    try:
        return {"level": body.level, "content": await get_engine(debate_id).rewrite_level(verdict_id, body.level)}
    except KeyError:
        raise HTTPException(404, "Answer not found")
    except Exception as e:
        raise HTTPException(502, f"Couldn't rewrite the answer: {e}")


@app.post("/api/debates/{debate_id}/verdicts/{verdict_id}/why")
async def post_why(debate_id: str, verdict_id: int, body: WhyIn):
    """Who argued for (and against) a passage of the answer, and which sources back it."""
    _require_debate(debate_id)
    try:
        return await get_engine(debate_id).why(verdict_id, body.passage)
    except KeyError:
        raise HTTPException(404, "Answer not found")
    except Exception as e:
        raise HTTPException(502, f"Couldn't trace that passage: {e}")


@app.post("/api/debates/{debate_id}/intake/confirm")
async def post_intake_confirm(debate_id: str):
    """Accept the chair's summary, or skip the clarifying questions, and start the debate."""
    _require_debate(debate_id)
    await get_engine(debate_id).confirm_intake()
    return {"ok": True}


@app.post("/api/debates/{debate_id}/continue")
async def post_continue(debate_id: str):
    _require_debate(debate_id)
    await get_engine(debate_id).continue_()
    return {"ok": True}


@app.post("/api/debates/interrupted/dismiss")
async def dismiss_interrupted():
    """Stop offering to resume the conundrums a restart interrupted; they stay paused."""
    db.execute("UPDATE debates SET interrupted = 0 WHERE interrupted = 1")
    return {"ok": True}


@app.post("/api/debates/{debate_id}/stop")
async def post_stop(debate_id: str):
    _require_debate(debate_id)
    await get_engine(debate_id).stop()
    return {"ok": True}


@app.post("/api/debates/{debate_id}/cancel")
async def post_cancel(debate_id: str):
    _require_debate(debate_id)
    await get_engine(debate_id).cancel()
    return {"ok": True}


@app.post("/api/debates/{debate_id}/conclude")
async def post_conclude(debate_id: str):
    _require_debate(debate_id)
    await get_engine(debate_id).conclude()
    return {"ok": True}


@app.get("/api/events")
async def app_events():
    """Which conundrums changed status, round or title, as it happens, so the sidebar doesn't poll."""
    return StreamingResponse(
        _stream(APP_BUS, None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream(bus: Any, first: Optional[Callable[[], Dict[str, Any]]]) -> AsyncIterator[str]:
    q = bus.subscribe()
    try:
        if first is not None:  # taken after subscribing, so no event falls between it and the stream
            yield f"data: {json.dumps(first())}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15)
                if event is None:  # fell too far behind: end the stream, and the app reconnects
                    return
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        bus.unsubscribe(q)


@app.get("/api/debates/{debate_id}/events")
async def debate_events(debate_id: str):
    _require_debate(debate_id)
    eng = get_engine(debate_id)
    return StreamingResponse(
        _stream(eng.bus, lambda: {"type": "snapshot", "state": eng.snapshot()}),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- research / settings


@app.get("/api/settings")
async def get_settings():
    return {"firecrawl": firecrawl.public_settings()}


@app.put("/api/settings")
async def put_settings(body: SettingsIn):
    if body.firecrawl_mode is not None:
        db.set_setting("firecrawl_mode", body.firecrawl_mode)
    if body.firecrawl_url is not None and body.firecrawl_url.strip():
        db.set_setting("firecrawl_url", body.firecrawl_url.strip().rstrip("/"))
    if body.firecrawl_api_key is not None:
        db.set_setting("firecrawl_api_key", body.firecrawl_api_key.strip())
    return {"firecrawl": firecrawl.public_settings()}


@app.get("/api/research/status")
async def research_status():
    return await firecrawl.status()


@app.post("/api/research/test")
async def research_test(body: ResearchTestIn):
    try:
        results = await firecrawl.search(body.query, 3)
    except firecrawl.SearchError as e:
        raise HTTPException(502, str(e))
    return [{"title": r["title"], "url": r["url"], "chars": len(r["content"])} for r in results]


def main():
    import uvicorn

    # Live-update streams never end on their own; don't wait on them at shutdown
    # The app polls a few endpoints every couple of seconds; logging each request buries warnings and errors.
    # QUORUM_ACCESS_LOG=1 turns the per-request log back on.
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=PORT,
        reload=False,
        timeout_graceful_shutdown=2,
        access_log=os.getenv("QUORUM_ACCESS_LOG", "") == "1",
    )


if __name__ == "__main__":
    main()
