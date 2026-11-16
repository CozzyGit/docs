"""Minimal, working Python example against the optional Northstar mock.

Run `python3 mock_api.py` first. Requires `pip install requests`.
"""
import os
import requests

BASE_URL = os.getenv("NORTHSTAR_BASE_URL", "http://localhost:8787/v1")
TOKEN = os.getenv("NORTHSTAR_API_KEY", "ns_demo_key")
SESSION = requests.Session()
SESSION.headers.update({"Authorization": f"Bearer {TOKEN}"})


def create_customer(name, email):
    response = SESSION.post(
        f"{BASE_URL}/customers",
        json={"name": name, "email": email, "plan": "growth"},
        headers={"Idempotency-Key": "example-python-client-001"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def track_event(customer_id):
    response = SESSION.post(
        f"{BASE_URL}/events",
        json={
            "customer_id": customer_id,
            "name": "feature.used",
            "properties": {"feature": "dashboard"},
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    customer = create_customer("Acme Robotics", "admin@acme.example")
    print("Created customer:", customer["id"])
    event = track_event(customer["id"])
    print("Tracked event:", event["id"])
