"""Replay recordings leave out local paths and key-shaped strings."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("record_replay", "scripts/record_replay.py")
rec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rec)


# Built from parts, so the test file itself doesn't look like it holds a key
FAKE_KEY = "sk_" + "live_" + "51Hc9kQ2eZvKYlo2C8tQw7yN"


def test_paths_and_keys_are_scrubbed():
    home = str(Path.home())
    text = (
        f"Read {home}/code/shop/orders/db.py:15 and {home}/notes.txt; "
        "bench /private/var/folders/gv/x_1/T/quorum-bench-orders-abc123/orders/api.py; "
        f'key = "{FAKE_KEY}", prefix `sk_live_` alone stays'
    )
    out = rec.scrub(text, f"{home}/code/shop")
    assert "repo/orders/db.py:15" in out and "~/notes.txt" in out
    assert "repo/orders/api.py" in out
    assert "sk_live_51" not in out and "live_secret_" in out and "`sk_live_` alone" in out
    assert home not in out
