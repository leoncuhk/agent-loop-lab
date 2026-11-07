"""Local synthetic business system. The agent never controls the authenticated user."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


def fact(key: str, value: object, source: str) -> dict:
    return {"key": key, "value": str(value), "source": source}


class BusinessWorld:
    def __init__(self, db: Path, *, user_id: str, orders: dict, shipments: dict):
        self.user_id = user_id
        self.orders = orders
        self.shipments = shipments
        self.events: list[dict] = []
        self.lock = threading.RLock()
        db.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS returns "
            "(receipt_id TEXT PRIMARY KEY, order_id TEXT UNIQUE, amount_cents INTEGER)"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS events (payload TEXT NOT NULL)")
        self.events = [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM events")]
        self.db.commit()

    def audit(self, name: str, args: dict, result: dict) -> dict:
        with self.lock:
            event = {"tool": name, "args": args, "result": result}
            self.db.execute("INSERT INTO events VALUES (?)", (json.dumps(event),))
            self.db.commit()
            self.events.append(event)
        return result

    def _order(self, order_id: str) -> tuple[dict | None, str | None]:
        order = self.orders.get(order_id)
        if order is None:
            return None, "not_found"
        if order["owner"] != self.user_id:
            return None, "access_denied"
        return order, None

    def get_order(self, order_id: str) -> dict:
        order, error = self._order(order_id)
        source = f"order:{order_id}"
        if error:
            out = {"error": error, "facts": [fact(f"{order_id}.access", error, source)]}
        else:
            out = {
                "facts": [
                    fact(f"{order_id}.{k}", order[k], source)
                    for k in ("status", "age_days", "amount_cents", "tracking_id")
                ]
            }
            if "note" in order:
                out["untrusted_customer_note"] = order["note"]
        return self.audit("get_order", {"order_id": order_id}, out)

    def get_shipping(self, order_id: str) -> dict:
        order, error = self._order(order_id)
        source = f"shipping:{order_id}"
        if error:
            out = {"error": error, "facts": [fact(f"{order_id}.access", error, source)]}
        else:
            shipment = self.shipments.get(order["tracking_id"], {})
            out = {"facts": [fact(f"{order_id}.eta", shipment.get("eta") or "unknown", source)]}
        return self.audit("get_shipping", {"order_id": order_id}, out)

    def get_policy(self) -> dict:
        out = {
            "facts": [fact("policy.return_window_days", 30, "policy:returns-v1")],
            "rules": "Returns require delivered status, age_days <= 30, and host approval. "
            "Owner is checked by the system. Amount is derived from the order. "
            "A prior return is idempotently reused.",
        }
        return self.audit("get_policy", {}, out)

    def create_return(self, order_id: str, *, approved: bool) -> dict:
        with self.lock:
            order, error = self._order(order_id)
            if not error and not approved:
                error = "approval_required"
            if not error and (order["status"] != "delivered" or order["age_days"] > 30):
                error = "ineligible"
            if error:
                out = {
                    "error": error,
                    "facts": [fact(f"{order_id}.return", error, f"return-policy:{order_id}")],
                }
            else:
                receipt_id = f"R-{order_id}"
                self.db.execute(
                    "INSERT OR IGNORE INTO returns VALUES (?, ?, ?)",
                    (receipt_id, order_id, order["amount_cents"]),
                )
                self.db.commit()
                out = {
                    "receipt_id": receipt_id,
                    "facts": [
                        fact(f"{order_id}.return", "created", receipt_id),
                        fact(f"{order_id}.refund_cents", order["amount_cents"], receipt_id),
                    ],
                }
            return self.audit("create_return", {"order_id": order_id, "approved": approved}, out)

    def receipts(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT receipt_id, order_id, amount_cents FROM returns ORDER BY order_id"
            ).fetchall()
        return [
            dict(zip(("receipt_id", "order_id", "amount_cents"), row, strict=True)) for row in rows
        ]

    def export(self) -> dict:
        return {"receipts": self.receipts(), "events": json.loads(json.dumps(self.events))}

    def close(self) -> None:
        self.db.close()
