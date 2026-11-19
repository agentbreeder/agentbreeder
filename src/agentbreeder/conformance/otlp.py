"""Read spans out of an OTLP/HTTP protobuf payload (`ExportTraceServiceRequest`).

A small wire-format reader, so the conformance kit needs no protobuf package. It reads only what
the checks use: each span's ids, name and attributes, and the resource's attributes. Anything it
does not understand is skipped; a payload that is not protobuf gives no spans.
"""

from __future__ import annotations

import struct

MAX_DEPTH = 8
MAX_SPANS = 20000


class _Malformed(ValueError):
    pass


def _varint(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if at >= len(data) or shift > 63:
            raise _Malformed
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, at
        shift += 7


def _fields(data: bytes):
    """(field number, wire type, value) for each field of one message."""
    at = 0
    while at < len(data):
        key, at = _varint(data, at)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, at = _varint(data, at)
        elif wire == 1:
            value, at = data[at : at + 8], at + 8
        elif wire == 2:
            size, at = _varint(data, at)
            value, at = data[at : at + size], at + size
        elif wire == 5:
            value, at = data[at : at + 4], at + 4
        else:
            raise _Malformed
        if at > len(data):
            raise _Malformed
        yield number, wire, value


def _text(value: bytes) -> str:
    return value.decode("utf-8", "replace")


def _any(data: bytes, depth: int = 0):
    """An AnyValue: a string, bool, int, float or a list of those; None for anything else."""
    if depth > MAX_DEPTH:
        return None
    for number, wire, value in _fields(data):
        if number == 1 and wire == 2:
            return _text(value)
        if number == 2 and wire == 0:
            return bool(value)
        if number == 3 and wire == 0:
            return value - (1 << 64) if value >= 1 << 63 else value
        if number == 4 and wire == 1 and len(value) == 8:
            return struct.unpack("<d", value)[0]
        if number == 5 and wire == 2:
            return [_any(item, depth + 1) for n, w, item in _fields(value) if n == 1 and w == 2]
    return None


def _attributes(pairs: list[bytes]) -> dict:
    result = {}
    for pair in pairs:
        key, value = None, None
        for number, wire, item in _fields(pair):
            if number == 1 and wire == 2:
                key = _text(item)
            elif number == 2 and wire == 2:
                value = _any(item)
        if key is not None:
            result[key] = value
    return result


def _span(data: bytes, resource: dict) -> dict:
    span = {"trace_id": "", "span_id": "", "parent_span_id": "", "name": "", "resource": resource}
    pairs = []
    for number, wire, value in _fields(data):
        if wire != 2:
            continue
        if number == 1:
            span["trace_id"] = value.hex()
        elif number == 2:
            span["span_id"] = value.hex()
        elif number == 4:
            span["parent_span_id"] = value.hex()
        elif number == 5:
            span["name"] = _text(value)
        elif number == 9:
            pairs.append(value)
    span["attributes"] = _attributes(pairs)
    return span


def spans(payload: bytes) -> list[dict]:
    """Every span in one payload as {trace_id, span_id, parent_span_id, name, attributes,
    resource}; ids are lower-case hex. A malformed payload gives the spans read before the
    damage."""
    found: list[dict] = []
    try:
        for number, wire, resource_spans in _fields(payload):
            if number != 1 or wire != 2:
                continue
            resource: dict = {}
            scopes = []
            for n, w, value in _fields(resource_spans):
                if n == 1 and w == 2:
                    resource = _attributes(
                        [v for fn, fw, v in _fields(value) if (fn, fw) == (1, 2)]
                    )
                elif n == 2 and w == 2:
                    scopes.append(value)
            for scope in scopes:
                for n, w, value in _fields(scope):
                    if n == 2 and w == 2 and len(found) < MAX_SPANS:
                        found.append(_span(value, resource))
    except _Malformed:
        pass
    return found
