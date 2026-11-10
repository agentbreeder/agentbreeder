"""Order cancellation REQUESTS. This never cancels anything: it files a row that a staff member
approves or rejects in the operator's ops surface. The customer is `rt.customer` (server-side).

1.0.1 needs only SELECT on orders and cancellation_requests and INSERT on the request columns
below: no row lock (a lock needs UPDATE rights). The one-pending-request-per-order unique index
settles a race between two conversations; staff decide against the order's status at approval.
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
            "SELECT status FROM orders WHERE upper(id) = %s AND customer_id = %s",
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
            filed = db.execute(
                "INSERT INTO cancellation_requests (id, order_id, customer_id, reason, "
                "session, turn_id) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING RETURNING id",
                (
                    "CR-" + uuid.uuid4().hex[:8].upper(),
                    order_id,
                    rt.customer,
                    reason.strip()[:500] or "not given",
                    rt.session,
                    rt.turn,
                ),
            ).fetchone()
            if filed is None:  # an open request for this order already exists
                existing = db.execute(
                    "SELECT id FROM cancellation_requests "
                    "WHERE order_id = %s AND status = 'pending'",
                    (order_id,),
                ).fetchone()
                request = existing[0] if existing else None
                message = (
                    f"Cancellation request {request} for {order_id} is already waiting for staff "
                    "approval. The order is NOT cancelled until a team member approves it."
                    if request
                    else f"A request for {order_id} was just decided; ask about the order to "
                    "see its status."
                )
            else:
                request = filed[0]
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
