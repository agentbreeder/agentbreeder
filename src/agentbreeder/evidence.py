"""Release evidence: did `release` put this exact image into production through the eval gate?

Neutral ground for `release` (which must not call a hand-deployed production "already released",
guided G-F3), `promote` (which only pushes released images) and, later, `next`. It reads the
audit log of a store it is given and changes nothing. `ReleaseError` lives here so this module
needs nothing from `release` or `promote`.
"""

from __future__ import annotations

from pathlib import Path

from agentbreeder import registry
from agentbreeder.lock import Lock


class ReleaseError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


def released(
    store,
    agent,
    project: Path,
    current: dict,
    image: str,
    *,
    threshold: float | None = None,
    require_package: bool = False,
) -> dict:
    """The reasons of the audited `release/promoted` row for this image and production
    manifest, or a `ReleaseError` saying what is missing:

    - `NO_RELEASE_EVIDENCE`: no release promoted it (for example a plain `deploy`);
    - `RELEASE_THRESHOLD_LOWERED`: that release ran below the threshold `agent.yaml` asks now;
    - `RELEASE_EVALSET_CHANGED`: that release used another eval set than `agent.lock` pins.

    For `release` all three mean "not released: evaluate again". `threshold` is the one in
    force now (`release --threshold`); by default the one in `agent.yaml`.

    `require_package` (set by `release`, M13 spec §5.5) skips rows without `RELEASE_PACKAGE`,
    written before M13, and keeps looking for a packaged row. `promote` leaves it unset and
    keeps accepting old rows.
    """
    from agentbreeder import hints

    needed = agent.evals.threshold if threshold is None else threshold
    ref = registry.Ref.parse(agent.evals.ref)
    locked = Lock.load(project / "agent.lock").entries.get(str(ref)) or {}
    for row in store.audit_matching("release", "promoted"):
        reasons = {item["code"]: item["message"] for item in row.get("reasons") or []}
        if require_package and "RELEASE_PACKAGE" not in reasons:
            continue
        if (
            reasons.get("RELEASE_IMAGE") != image
            or reasons.get("RELEASE_MANIFEST") != current["digest"]
        ):
            continue
        try:
            recorded = float(reasons.get("RELEASE_THRESHOLD", -1))
        except (TypeError, ValueError) as error:
            raise ReleaseError(
                "RELEASE_EVIDENCE_INVALID", "That release's audit row is malformed; release again"
            ) from error
        if recorded < needed:
            raise ReleaseError(
                "RELEASE_THRESHOLD_LOWERED",
                "That release ran below the threshold in agent.yaml; release again",
            )
        if reasons.get("RELEASE_EVALSET") != locked.get("sha256"):
            raise ReleaseError(
                "RELEASE_EVALSET_CHANGED", "That release used another eval set; release again"
            )
        return reasons
    raise ReleaseError(
        "NO_RELEASE_EVIDENCE",
        f"No audited release promoted this image; run `{hints.command('release')}`",
    )
