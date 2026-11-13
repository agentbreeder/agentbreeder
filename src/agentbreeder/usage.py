"""Resource usage for one stack (`agentbreeder services usage`, #821 M5 S-10, M6; console §4.2,
§8.1 #6).

The pure half: which running containers are this stack's, and how Docker's text becomes numbers.

- **Containers** (`resolve`) match as `watch` matches them (R5): stack services by the
  `io.agentbreeder.stack` label (one-shot `init` and `bootstrap` steps, which carry
  `io.agentbreeder.step`, are skipped) and agent containers (agent, sidecar, egress) by
  `io.agentbreeder.deployment` against this stack's deployment IDs. Labels are not
  authenticated, so a container on the same engine can claim them: usage is best effort.
- **Samples** (`Frames`, `sample_row`): streaming `docker stats --format '{{json .}}'` writes one
  JSON line per container and starts each refresh with the cursor-home sequence `ESC [ H`
  (with `ESC [ K` and `ESC [ J` around lines, even into a pipe; Docker 27.5, 2026-11-12). A
  frame is complete when every expected container is in it or the next one starts.
- **Disk** (`disk`): this stack's volumes (named `agentbreeder-<stack-id>-…` and labelled with the
  stack) and the writable layers of its containers, from `docker system df -v`.

Only validated values leave this module: container IDs, service names, roles, volume names,
numbers, and deployment names from local state (one line, at most 64 characters). Docker's
`Name`, `Labels` and `Mounts` (which hold home paths) never do.
"""

from __future__ import annotations

import json
import re

from agentbreeder import safe_text
from agentbreeder.watch import (
    _CONTAINER,
    _DEPLOYMENT,
    _ROLE,
    _SERVICE,
    LABEL_DEPLOYMENT,
    LABEL_ROLE,
    LABEL_SERVICE,
    LABEL_STACK,
    LABEL_STEP,
)

SCHEMA_VERSION = 1
MAX_CONTAINERS = 256  # rows kept per frame and per resolution
_SHORT_ID = re.compile(r"[0-9a-f]{12}")
_VOLUME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_CSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
_HOME = b"\x1b[H"
_SIZE = re.compile(r"(\d{1,15}(?:\.\d{1,6})?)\s?([kKMGTP]?i?B)", re.ASCII)
_PERCENT = re.compile(r"(\d{1,7}(?:\.\d{1,6})?)%", re.ASCII)
_COUNT = re.compile(r"\d{1,9}", re.ASCII)
# The only fields of a stats record that are read (the rest, `Name` included, is dropped
# as it arrives, so a queued frame stays small).
STATS_FIELDS = ("CPUPerc", "MemUsage", "MemPerc", "NetIO", "BlockIO", "PIDs")
NAME_LIMIT = 64
UNITS = {
    "B": 1,
    "kB": 1000,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "PB": 1000**5,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
    "PiB": 1024**5,
}


def size(text: object) -> int | None:
    """Bytes from Docker's `120.4MiB`, `48.18MB`, `0B`; None for `--`, `N/A` or anything else."""
    if not isinstance(text, str):
        return None
    match = _SIZE.fullmatch(text.strip())
    if match is None or match.group(2) not in UNITS:
        return None
    return round(float(match.group(1)) * UNITS[match.group(2)])


def percent(text: object) -> float | None:
    if not isinstance(text, str):
        return None
    match = _PERCENT.fullmatch(text.strip())
    return float(match.group(1)) if match else None


def _pair(text: object) -> tuple[int | None, int | None]:
    if not isinstance(text, str) or text.count(" / ") != 1:
        return None, None
    first, second = text.split(" / ")
    return size(first), size(second)


def resolve(service_rows: list[list], agent_rows: list[list], stack_id: str, deployments: dict):
    """short container ID -> {"source", "name", "deployment"[, "role"]} for this stack.

    `service_rows` are `[id, compose service, step label]` for containers labelled with this
    stack; `agent_rows` are `[id, deployment label, role label]` for every container with a
    deployment label; `deployments` is this stack's rows (id -> {name, ...})."""
    found: dict[str, dict] = {}
    for row in service_rows:
        identifier, service, step = row
        if not (isinstance(identifier, str) and _SHORT_ID.fullmatch(identifier)):
            continue
        if step != "" or not (isinstance(service, str) and _SERVICE.fullmatch(service)):
            continue  # a one-shot step, or a name we will not print
        found[identifier] = {"source": "service", "name": service, "deployment": None}
    for row in agent_rows:
        identifier, deployment, role = row
        if not (isinstance(identifier, str) and _SHORT_ID.fullmatch(identifier)):
            continue
        if not (isinstance(deployment, str) and _DEPLOYMENT.fullmatch(deployment)):
            continue
        record = deployments.get(deployment)
        if record is None:
            continue  # another stack's
        # From local state (deploy validated it); still one bounded line, never a newline.
        name = safe_text.excerpt(record.get("name") or "", NAME_LIMIT)
        if not name:
            continue
        source = {"source": "agent", "name": name, "deployment": deployment}
        if isinstance(role, str) and _ROLE.fullmatch(role) and role != "agent":
            source["role"] = role
        found[identifier] = source
    return dict(list(sorted(found.items()))[:MAX_CONTAINERS])


SERVICE_KEYS = (LABEL_SERVICE, LABEL_STEP)
AGENT_KEYS = (LABEL_DEPLOYMENT, LABEL_ROLE)


def service_label(stack_id: str) -> str:
    return f"{LABEL_STACK}={stack_id}"


class Frames:
    """Feed streaming `docker stats` lines in order; each returns the frames it completed, as
    {short id: record}."""

    def __init__(self) -> None:
        self.current: dict[str, dict] = {}

    def feed(self, raw: bytes, expected: frozenset[str] | set[str]) -> list[dict[str, dict]]:
        done = []
        if _HOME in raw and self.current:
            done.append(self.current)
            self.current = {}
        text = _CSI.sub(b"", raw).strip()
        if not text:
            return done
        try:
            record = json.loads(text)
        except (ValueError, RecursionError):
            return done
        identifier = record.get("Container") if isinstance(record, dict) else None
        if not (isinstance(identifier, str) and _SHORT_ID.fullmatch(identifier)):
            return done
        if identifier in self.current:  # a frame started without the home sequence
            done.append(self.current)
            self.current = {}
        if len(self.current) < MAX_CONTAINERS:
            self.current[identifier] = {key: record.get(key) for key in STATS_FIELDS}
        if expected and expected <= self.current.keys():
            done.append(self.current)
            self.current = {}
        return done


def sample_row(record: dict, source: dict) -> dict:
    """One container's numbers (null where Docker printed `--` or something unexpected).
    CPU and memory are of the Docker VM on Docker Desktop (console §8.1 #6)."""
    used, limit = _pair(record.get("MemUsage"))
    received, sent = _pair(record.get("NetIO"))
    read, written = _pair(record.get("BlockIO"))
    pids = record.get("PIDs")
    return {
        **source,
        "cpu_percent": percent(record.get("CPUPerc")),
        "memory_used": used,
        "memory_limit": limit,
        "memory_percent": percent(record.get("MemPerc")),
        "network_rx": received,
        "network_tx": sent,
        "block_read": read,
        "block_write": written,
        "pids": int(pids) if isinstance(pids, str) and _COUNT.fullmatch(pids) else None,
    }


def _labels(value: object) -> dict[str, str]:
    from agentbreeder.services.compose import _labels

    return _labels(value) if isinstance(value, (str, dict)) else {}


def disk(document: dict, project: str, stack_id: str, containers: dict[str, dict]) -> dict:
    """{"volumes": [{"name", "size"}], "containers": [{source..., "size"}]} for this stack, from
    a `docker system df -v` document. `containers` is the current resolution (short id ->
    source); a container's size is its writable layer."""
    volumes = []
    for volume in document.get("Volumes") or []:
        if not isinstance(volume, dict):
            continue
        name = volume.get("Name")
        if not (isinstance(name, str) and _VOLUME.fullmatch(name)):
            continue
        if not name.startswith(f"{project}-"):
            continue
        if _labels(volume.get("Labels")).get(LABEL_STACK) != stack_id:
            continue
        volumes.append({"name": name, "size": size(volume.get("Size"))})
    layers = []
    for container in document.get("Containers") or []:
        if not isinstance(container, dict):
            continue
        identifier = container.get("ID")
        if not (isinstance(identifier, str) and _CONTAINER.fullmatch(identifier)):
            continue
        source = containers.get(identifier[:12])
        if source is None:
            continue
        written = container.get("Size")
        written = written.split(" (")[0] if isinstance(written, str) else None
        layers.append({**source, "size": size(written)})
    volumes.sort(key=lambda v: v["name"])
    layers.sort(key=lambda c: (c["source"], c["name"], c.get("role") or ""))
    return {"volumes": volumes[:MAX_CONTAINERS], "containers": layers[:MAX_CONTAINERS]}


def human(value: int | None) -> str:
    if value is None:
        return "-"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return "-"  # pragma: no cover - the loop always returns


def _what(row: dict) -> str:
    return row["name"] + (f" ({row['role']})" if row.get("role") else "")


def sample_text(sample: dict) -> str:
    """Lines for a terminal (every value is validated or a number)."""
    if not sample["containers"]:
        return f"{sample['time']} usage: nothing running"
    lines = [f"{sample['time']} usage:"]
    for row in sample["containers"]:
        cpu = "-" if row["cpu_percent"] is None else f"{row['cpu_percent']:.1f}%"
        lines.append(
            f"  {_what(row):<28} cpu {cpu:>7}  mem {human(row['memory_used'])}"
            f" / {human(row['memory_limit'])}  net {human(row['network_rx'])}"
            f" / {human(row['network_tx'])}  block {human(row['block_read'])}"
            f" / {human(row['block_write'])}"
        )
    return "\n".join(lines)


def disk_text(record: dict) -> str:
    when = record["as_of"] or "never"
    stale = " (stale: the last reading failed)" if record["stale"] else ""
    parts = [f"{v['name']} {human(v['size'])}" for v in record["volumes"]]
    parts += [f"{_what(c)} {human(c['size'])}" for c in record["containers"]]
    return f"disk as of {when}{stale}: " + (", ".join(parts) or "nothing")
