"""Long-term memory: one durable, non-secret note about the current customer (rt.customer)."""

from __future__ import annotations


def save_customer_note(rt, note: str) -> str:
    note = note.strip()[:500]
    if not note:
        raise ValueError("note is empty")
    rt.memory.add_note(rt.customer, note)
    rt.record("memory_saved", note)
    return "Saved."
