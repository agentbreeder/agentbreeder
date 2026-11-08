"""Read-only commerce tools: the current customer's orders and the product catalog.

Access rule: the customer is `rt.customer`, which the runtime sets from the server-side session,
never from model input. A model asked (or prompt-injected) to look up another customer's order
gets "not found", exactly as for an order that does not exist.
"""

from __future__ import annotations

STATUSES = ["pending", "paid", "shipped", "delivered", "cancelled", "returned", "refunded"]


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def lookup_orders(rt, order_id: str = "", status: str = "") -> str:
    db = rt.db
    sql = (
        "SELECT id, status, ship_to_country, carrier, tracking_number, created, shipped_at, "
        "delivered_at, total_cents FROM orders WHERE customer_id = %s"
    )
    params: list = [rt.customer]
    if order_id:
        sql += " AND upper(id) = upper(%s)"
        params.append(order_id.strip()[:32])
    if status:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        sql += " AND status = %s"
        params.append(status)
    orders = db.execute(sql + " ORDER BY created DESC LIMIT 10", params).fetchall()
    if not orders:
        result = (
            f"No order {order_id} on this customer's account."
            if order_id
            else "This customer has no matching orders."
        )
        rt.evidence("orders", "Order lookup", result)
        return result
    blocks = []
    for oid, state, country, carrier, tracking, created, shipped, delivered, total in orders:
        items = db.execute(
            "SELECT p.name, i.quantity, i.unit_price_cents FROM order_items i "
            "JOIN products p ON p.sku = i.sku WHERE i.order_id = %s ORDER BY p.name",
            (oid,),
        ).fetchall()
        lines = [f"Order {oid}: status {state}, placed {created:%Y-%m-%d}, total {money(total)}"]
        lines += [f"  - {qty} x {name} @ {money(price)}" for name, qty, price in items]
        if country:
            lines.append(f"  ship to: {country}")
        if carrier:
            lines.append(f"  carrier: {carrier}, tracking: {tracking}")
        if shipped:
            lines.append(f"  shipped: {shipped:%Y-%m-%d}")
        if delivered:
            lines.append(f"  delivered: {delivered:%Y-%m-%d}")
        for rid, rstatus, note in db.execute(
            "SELECT id, status, decision_note FROM cancellation_requests WHERE order_id = %s "
            "ORDER BY requested_at DESC LIMIT 1",
            (oid,),
        ).fetchall():
            label = {
                "pending": "pending staff approval (not cancelled yet)",
                "approved": "approved",
                "rejected": "rejected",
            }[rstatus]
            lines.append(
                f"  cancellation request {rid}: {label}"
                + (f" ({note})" if note and rstatus == "rejected" else "")
            )
        blocks.append("\n".join(lines))
    result = "\n\n".join(blocks)
    rt.evidence("orders", "Order lookup", result)
    return result


def get_product_catalog(rt, query: str = "") -> str:
    sql = "SELECT sku, name, category, description, price_cents, unit FROM products WHERE active"
    params: list = []
    if query and query.strip():
        sql += " AND (name ILIKE %s OR description ILIKE %s OR category ILIKE %s OR sku ILIKE %s)"
        like = f"%{query.strip()[:60]}%"
        params = [like] * 4
    rows = rt.db.execute(sql + " ORDER BY category, price_cents", params).fetchall()
    result = (
        "\n".join(
            f"- {name} ({sku}, {category}): {money(price)} {unit}. {desc}"
            for sku, name, category, desc, price, unit in rows
        )
        if rows
        else "No matching products."
    )
    rt.evidence("catalog", "Product catalog", result)
    return result
