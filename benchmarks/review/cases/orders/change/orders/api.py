"""HTTP API for orders."""

import threading

from flask import Flask, jsonify, request

from . import db, payments

app = Flask(__name__)
conn = db.connect()  # shared by every request
_cache = {}


@app.get("/customers/<customer_id>/orders")
def list_orders(customer_id):
    if customer_id in _cache:
        return jsonify(_cache[customer_id])
    rows = db.get_customer_orders(conn, customer_id)
    data = [{"order_id": r["id"], "amount": r["total"], "customer": r["name"]} for r in rows]
    _cache[customer_id] = data
    return jsonify(data)


@app.get("/orders/search")
def search():
    return jsonify([dict(r) for r in db.search_orders(conn, request.args.get("q", ""))])


@app.post("/orders")
def create_order():
    body = request.get_json()
    order_id = db.create_order(conn, body["customer_id"], body["total"])
    # charge in the background so the request returns fast
    threading.Thread(target=_charge, args=(order_id, body["total"])).start()
    return jsonify({"id": order_id}), 201


def _charge(order_id, total):
    charge_id = payments.charge(order_id, int(total * 100))
    db.mark_paid(conn, order_id, charge_id)
    _cache.clear()
