"""Order cancellation REQUESTS. This never cancels anything: it files a row that a staff member
approves or rejects in the operator's ops surface. The customer is `rt.customer` (server-side).
"""

from __future__ import annotations

import uuid

CANCELLABLE = ("pending", "paid")


def cancel_order(rt, order_id: str, reason: str) -> str:
    db = rt.db
    order_id = order_id.strip().upper()[:32]
    request = None
    with db.transaction():
        row = db.execute(
            "SELECT status FROM orders WHERE upper(id) = %s AND customer_id = %s FOR UPDATE",
            (order_id, rt.customer),
        ).fetchone()
        if not row:
            message = f"No order {order_id} on this customer's account; nothing was requested."
        elif row[0] not in CANCELLABLE:
            hint = (
                " It has shipped, so it can't be cancelled; an unused pen can be returned "
                "within 30 days instead."
                if row[0] in ("shipped", "delivered")
                else ""
            )
            message = f"Order {order_id} is {row[0]} and cannot be cancelled.{hint}"
        else:
            existing = db.execute(
                "SELECT id FROM cancellation_requests WHERE order_id = %s AND status = 'pending'",
                (order_id,),
            ).fetchone()
            if existing:
                request = existing[0]
                message = (
                    f"Cancellation request {request} for {order_id} is already waiting for staff "
                    "approval. The order is NOT cancelled until a team member approves it."
                )
            else:
                request = "CR-" + uuid.uuid4().hex[:8].upper()
                db.execute(
                    "INSERT INTO cancellation_requests (id, order_id, customer_id, reason, "
                    "session, turn_id) VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        request,
                        order_id,
                        rt.customer,
                        reason.strip()[:500] or "not given",
                        rt.session,
                        rt.turn,
                    ),
                )
                message = (
                    f"Cancellation request {request} for {order_id} was submitted for staff "
                    "approval. The order is NOT cancelled yet: a team member must approve it "
                    "first. If approved, any payment is refunded to the original payment method "
                    "per the refund policy. The customer can ask about the order to see the "
                    "request status."
                )
    if request:
        rt.record("cancellation_requests", request)
    rt.evidence("orders", "Cancellation request", message)
    return message
