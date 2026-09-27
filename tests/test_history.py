"""The conundrum list pages from the server: time ranges, a cursor, search, ids, statuses and counts."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from backend import db, main


@pytest.fixture(autouse=True)
def history():
    db.connect(":memory:")
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    for i in range(25):
        when = (now - timedelta(hours=i)).isoformat()
        db.execute(
            "INSERT INTO debates (id, title, created_at, chair_endpoint_id, chair_model, max_rounds, num_ctx, status) "
            "VALUES (?, ?, ?, 1, 'm', 2, 8192, ?)",
            [
                f"d{i:02d}",
                f"Heat pump question {i}" if i % 5 == 0 else f"Other question {i}",
                when,
                "running" if i == 3 else "concluded",
            ],
        )
        db.execute(
            "INSERT INTO messages (debate_id, topic, round, author_kind, content, created_at) VALUES (?, 1, 0, 'user', ?, ?)",
            [f"d{i:02d}", f"Should I buy thing {i}?", when],
        )


async def test_pages_through_a_range_newest_first_with_a_cursor():
    first = await main.list_debates(limit=10, before=None, after=None, q=None, ids=None, status=None, exclude=None)
    assert [d["id"] for d in first] == [f"d{i:02d}" for i in range(10)]
    second = await main.list_debates(
        limit=10, before=first[-1]["created_at"], after=None, q=None, ids=None, status=None, exclude=None
    )
    assert [d["id"] for d in second] == [f"d{i:02d}" for i in range(10, 20)]
    assert second[0]["question"] == "Should I buy thing 10?"


async def test_ranges_search_ids_statuses_and_counts():
    # Client timestamps in "Z" form work against stored "+00:00" ones
    since = await main.count_debates(before=None, after="2026-09-26T03:00:00.000Z", q=None, status=None, exclude=None)
    assert since == {"count": 10}
    assert (await main.count_debates(before=None, after=None, q="heat pump", status=None, exclude=None))["count"] == 5
    found = await main.list_debates(
        limit=None, before=None, after=None, q="thing 12", ids=None, status=None, exclude=None
    )
    assert [d["id"] for d in found] == ["d12"]  # the question's text is searched too
    picked = await main.list_debates(
        limit=None, before=None, after=None, q=None, ids="d04,d01", status=None, exclude=None
    )
    assert [d["id"] for d in picked] == ["d01", "d04"]
    live = await main.list_debates(
        limit=None, before=None, after=None, q=None, ids=None, status="running", exclude=None
    )
    assert [d["id"] for d in live] == ["d03"]
    assert (await main.count_debates(before=None, after=None, q=None, status=None, exclude="d00,d01"))["count"] == 23


async def test_a_bad_timestamp_is_a_client_error():
    with pytest.raises(HTTPException):
        await main.count_debates(before="yesterday", after=None, q=None, status=None, exclude=None)


async def test_the_sidebar_loads_in_one_request():
    groups = '[{"key": "recent", "after": "2026-09-26T03:00:00Z", "limit": 3}, {"key": "older", "before": "2026-09-26T03:00:00Z", "limit": 0}]'
    data = await main.sidebar(groups=groups, pins="d00", q=None, limit=20)
    assert data["groups"]["recent"]["count"] == 9  # d00 is pinned, so it's left out
    assert [d["id"] for d in data["groups"]["recent"]["items"]] == ["d01", "d02", "d03"]
    assert data["groups"]["older"] == {"count": 15, "items": []}
    assert [d["id"] for d in data["pinned"]] == ["d00"]
    found = await main.sidebar(groups="[]", pins=None, q="heat pump", limit=2)
    assert found["results"]["count"] == 5 and len(found["results"]["items"]) == 2
    with pytest.raises(HTTPException):
        await main.sidebar(groups="not json", pins=None, q=None, limit=20)


async def test_answered_conundrums_say_how_long_the_answer_took():
    db.execute("UPDATE debates SET topic = 1 WHERE id = 'd00'")
    asked = db.query_one("SELECT created_at FROM messages WHERE debate_id = 'd00'")["created_at"]
    answered = (datetime.fromisoformat(asked) + timedelta(minutes=11, seconds=12)).isoformat()
    db.execute(
        "INSERT INTO verdicts (debate_id, topic, reason, rounds, message_id, created_at) VALUES ('d00', 1, 'max_rounds', 3, 1, ?)",
        [answered],
    )
    rows = await main.list_debates(limit=2, before=None, after=None, q=None, ids=None, status=None, exclude=None)
    assert rows[0]["took_seconds"] == 672 and rows[1]["took_seconds"] is None


async def test_a_restart_marks_live_conundrums_until_resumed_or_dismissed():
    from backend import engine

    engine.recover_after_restart()
    rows = await main.list_debates(limit=None, before=None, after=None, q=None, ids="d03", status=None, exclude=None)
    assert rows[0]["status"] == "paused" and rows[0]["interrupted"] == 1
    await main.dismiss_interrupted()
    rows = await main.list_debates(limit=None, before=None, after=None, q=None, ids="d03", status=None, exclude=None)
    assert rows[0]["status"] == "paused" and rows[0]["interrupted"] == 0
    engine._engines.clear()
