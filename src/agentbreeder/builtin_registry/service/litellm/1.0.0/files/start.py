#!/app/.venv/bin/python3
"""Start LiteLLM with its secrets read from mounted files (never from the container config).

LiteLLM's config refers to keys as `os.environ/NAME`, so the files under /run/secrets are copied
into this process's environment (a fixed allow-list of names), then LiteLLM replaces this
process. Values are never printed. A provider key that wasn't imported is simply absent.
"""

import os
import sys

SECRETS = "/run/secrets"
NAMES = {
    "master-key": "LITELLM_MASTER_KEY",
    "database-url": "DATABASE_URL",
    "gemini-api-key": "GEMINI_API_KEY",
    "openrouter-api-key": "OPENROUTER_API_KEY",
    "openai-api-key": "OPENAI_API_KEY",
    "anthropic-api-key": "ANTHROPIC_API_KEY",
}
REQUIRED = {"master-key", "database-url"}
LIMIT = 16384


def read(name):
    path = os.path.join(SECRETS, name)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:  # a symlink (O_NOFOLLOW) or anything else unexpected
        sys.exit(f"litellm launcher: secret {name} can't be read")
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(LIMIT + 1)
    if not data or len(data) > LIMIT or b"\0" in data or b"\n" in data.rstrip(b"\n"):
        sys.exit(f"litellm launcher: secret {name} is empty or malformed")
    return data.rstrip(b"\n").decode("utf-8", "strict")


def main():
    environment = dict(os.environ)
    for name, variable in NAMES.items():
        value = read(name)
        if value is None:
            if name in REQUIRED:
                sys.exit(f"litellm launcher: secret {name} is missing")
            environment.pop(variable, None)
            continue
        environment[variable] = value
    providers = [variable for name, variable in NAMES.items() if name not in REQUIRED]
    if not any(variable in environment for variable in providers):
        print("litellm launcher: no provider key imported; model calls will fail", file=sys.stderr)
    argv = ["litellm", "--config", "/run/params/config", "--port", "4000"]
    os.execvpe(argv[0], argv, environment)


if __name__ == "__main__":
    main()
