"""Queries for the users table."""

MAX_LIMIT = 200


def find_user(conn, user_id):
    return conn.execute("SELECT id, name FROM users WHERE id = ?", (user_id,)).fetchone()


def search_users(conn, term, limit=50):
    """Users whose name contains `term` (case-insensitive for ASCII); % and _ in `term` match literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    limit = max(1, min(int(limit), MAX_LIMIT))
    return conn.execute(
        "SELECT id, name FROM users WHERE name LIKE ? ESCAPE '\\' ORDER BY name LIMIT ?",
        (f"%{escaped}%", limit),
    ).fetchall()
