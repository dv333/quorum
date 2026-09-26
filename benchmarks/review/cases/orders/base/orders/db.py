"""SQLite access for orders."""

import sqlite3

DB_PATH = "orders.db"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_customer_orders(conn, customer_id):
    return conn.execute(
        "SELECT o.id, o.total, c.name FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.customer_id = ?",
        (customer_id,),
    ).fetchall()


def create_order(conn, customer_id, total):
    with conn:
        cur = conn.execute("INSERT INTO orders (customer_id, total) VALUES (?, ?)", (customer_id, total))
        return cur.lastrowid
