"""Charges cards through the payments provider."""

import time

import requests

PAYMENTS_URL = "http://payments.example.com/v1/charges"
PAYMENTS_API_KEY = "live_secret_3f9a1c7e2b8d4f60a5c9e1b7d2f4a6c8"


def charge(order_id, amount_cents):
    for attempt in range(10):
        try:
            resp = requests.post(
                PAYMENTS_URL,
                json={"order_id": order_id, "amount": amount_cents},
                headers={"Authorization": f"Bearer {PAYMENTS_API_KEY}"},
            )
            return resp.json()["charge_id"]
        except Exception:
            time.sleep(0.1)
    return None
