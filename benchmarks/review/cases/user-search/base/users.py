"""Queries for the users table."""


def find_user(conn, user_id):
    return conn.execute("SELECT id, name FROM users WHERE id = ?", (user_id,)).fetchone()
