"""Open a ticket for the human support team for the current customer (rt.customer)."""

from __future__ import annotations

PRIORITIES = ("low", "normal", "urgent")


def escalate_to_human(rt, priority: str, summary: str) -> str:
    if priority not in PRIORITIES:
        raise ValueError("priority must be low, normal or urgent")
    ticket = rt.memory.open_ticket(rt.customer, rt.session, priority, summary[:2000])
    rt.record("tickets", ticket)
    return f"Ticket {ticket} opened with priority {priority}."
