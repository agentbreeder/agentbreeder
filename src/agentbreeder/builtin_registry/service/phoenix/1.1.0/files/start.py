#!/usr/bin/python3.13
"""Start Phoenix with its secrets read from mounted files (never from the container config).

Phoenix reads its signing secrets, initial admin password and database URL only from environment
variables, so the files under /run/secrets are copied into this process's environment (a fixed
allow-list), then Phoenix replaces this process. Values are never printed.
"""

import os
import sys

SECRETS = "/run/secrets"
NAMES = {
    "secret": "PHOENIX_SECRET",
    "admin-secret": "PHOENIX_ADMIN_SECRET",
    "admin-password": "PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD",
    "database-url": "PHOENIX_SQL_DATABASE_URL",
}
LIMIT = 16384


def read(name):
    path = os.path.join(SECRETS, name)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        sys.exit(f"phoenix launcher: secret {name} is missing")
    except OSError:  # a symlink (O_NOFOLLOW) or anything else unexpected
        sys.exit(f"phoenix launcher: secret {name} can't be read")
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(LIMIT + 1)
    if not data or len(data) > LIMIT or b"\0" in data or b"\n" in data.rstrip(b"\n"):
        sys.exit(f"phoenix launcher: secret {name} is empty or malformed")
    return data.rstrip(b"\n").decode("utf-8", "strict")


def main():
    environment = dict(os.environ)
    for name, variable in NAMES.items():
        environment[variable] = read(name)
    # Only the URL above selects the database: never a PHOENIX_POSTGRES_* fallback.
    for variable in [v for v in environment if v.startswith("PHOENIX_POSTGRES_")]:
        del environment[variable]
    argv = [sys.executable, "-m", "phoenix.server.main", "serve"]
    os.execve(argv[0], argv, environment)


if __name__ == "__main__":
    main()
