"""Map the PyPI version to the npm launcher's version (spec §3.4).

PEP 440 pre-releases (`3.0.0a1`) are not valid semver, so the npm package `agentbreeder` uses
`3.0.0-alpha.1`. Only the forms this project releases are accepted; anything else is refused
rather than guessed.
"""

import re

_PEP440 = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")
_NPM_PRE = {"a": "alpha", "b": "beta", "rc": "rc"}


def npm_version(pypi: str) -> str:
    match = _PEP440.match(pypi)
    if not match:
        raise ValueError(f"cannot map {pypi!r} to an npm version (expected X.Y.Z[a|b|rcN])")
    major, minor, patch, pre, number = match.groups()
    base = f"{int(major)}.{int(minor)}.{int(patch)}"
    return f"{base}-{_NPM_PRE[pre]}.{int(number)}" if pre else base
