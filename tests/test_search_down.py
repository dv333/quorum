"""Web search that's down stays marked down across conundrums: until the settings change, or for a while."""

import time

import pytest

from backend import db, firecrawl


@pytest.fixture(autouse=True)
def memory_db():
    db.connect(":memory:")


def test_no_credits_lasts_until_the_settings_change():
    firecrawl.mark_down("Firecrawl error (HTTP 402): Insufficient credits")
    assert "402" in firecrawl.down()
    db.set_setting("firecrawl_api_key", "fc-new-key-123")
    assert firecrawl.down() is None


def test_an_unreachable_server_is_tried_again_after_a_while(monkeypatch):
    firecrawl.mark_down("Can't reach Firecrawl at http://localhost:3002")
    assert firecrawl.down()
    later = time.time() + firecrawl.DOWN_RETRY_SECONDS + 1
    monkeypatch.setattr(firecrawl.time, "time", lambda: later)
    assert firecrawl.down() is None


def test_a_search_that_works_clears_it():
    firecrawl.mark_down("Can't reach Firecrawl at http://localhost:3002")
    firecrawl.mark_up()
    assert firecrawl.down() is None


async def test_settings_show_why_search_is_down():
    firecrawl.mark_down("Firecrawl error (HTTP 402): Insufficient credits")
    st = await firecrawl.status()
    assert st["ready"] is False and "402" in st["error"]
