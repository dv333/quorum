import sqlite3

import pytest

from users import search_users


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO users (name) VALUES (?)", [("Ada",), ("adam",), ("100%_real",), ("Bob",)])
    return conn


def test_matches_case_insensitively(conn):
    assert [r[1] for r in search_users(conn, "ad")] == ["Ada", "adam"]


def test_wildcards_match_literally(conn):
    assert [r[1] for r in search_users(conn, "%_")] == ["100%_real"]
    assert search_users(conn, "_") == [(3, "100%_real")]


def test_limit_is_clamped(conn):
    assert len(search_users(conn, "", limit=0)) == 1
    assert len(search_users(conn, "", limit=10_000)) == 4


def test_quotes_are_just_text(conn):
    assert search_users(conn, "' OR 1=1 --") == []
