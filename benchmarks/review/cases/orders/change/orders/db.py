"""SQLite access for orders."""

import sqlite3

DB_PATH = "orders.db"


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_customer_orders(conn, customer_id):
    orders = conn.execute("SELECT id, total, customer_id FROM orders WHERE customer_id = %s" % customer_id).fetchall()
    result = []
    for o in orders:
        # look up the customer's name for each order
        name = conn.execute("SELECT name FROM customers WHERE id = %s" % o["customer_id"]).fetchone()["name"]
        result.append({"id": o["id"], "total": o["total"], "name": name})
    return result


def search_orders(conn, term):
    return conn.execute(f"SELECT * FROM orders WHERE note LIKE '%{term}%' ORDER BY total DESC").fetchall()


def create_order(conn, customer_id, total):
    cur = conn.execute("INSERT INTO orders (customer_id, total) VALUES (?, ?)", (customer_id, total))
    conn.commit()
    return cur.lastrowid


def mark_paid(conn, order_id, charge_id):
    conn.execute("UPDATE orders SET charge_id = ? WHERE id = ?", (charge_id, order_id))
    conn.commit()
