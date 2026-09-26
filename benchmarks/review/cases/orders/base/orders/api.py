"""HTTP API for orders."""

from flask import Flask, jsonify, request

from . import db, payments

app = Flask(__name__)


@app.get("/customers/<int:customer_id>/orders")
def list_orders(customer_id):
    conn = db.connect()
    rows = db.get_customer_orders(conn, customer_id)
    return jsonify([{"id": r["id"], "total": r["total"], "customer": r["name"]} for r in rows])


@app.post("/orders")
def create_order():
    body = request.get_json()
    conn = db.connect()
    order_id = db.create_order(conn, body["customer_id"], body["total"])
    charge_id = payments.charge(order_id, int(body["total"] * 100))
    return jsonify({"id": order_id, "charge_id": charge_id}), 201
