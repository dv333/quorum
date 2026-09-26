"""Charges cards through the payments provider."""

import os

import requests

PAYMENTS_URL = "https://payments.example.com/v1/charges"


def charge(order_id, amount_cents):
    resp = requests.post(
        PAYMENTS_URL,
        json={"order_id": order_id, "amount": amount_cents},
        headers={"Authorization": f"Bearer {os.environ['PAYMENTS_API_KEY']}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["charge_id"]
