#!/app/.venv/bin/python3
"""Start LiteLLM with its secrets read from mounted files (never from the container config).

LiteLLM's config refers to keys as `os.environ/NAME`, so the files under /run/secrets are copied
into this process's environment (a fixed allow-list of names), then LiteLLM replaces this
process. Values are never printed. A provider key that wasn't imported is simply absent.

1.2.0 (#1025 §4.3): LiteLLM gets an allow-list of variables, not the container's environment,
so ambient cloud credentials, metadata endpoints and proxies never reach it; the metadata and
credential-file variables below are forced; and the reviewed config is parsed before `exec`,
failing closed, so an unset gateway-only credential it names stops the start instead of falling
through to a provider SDK's default credential chain.
"""

import os
import sys

SECRETS = "/run/secrets"
CONFIG = "/run/params/config"
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
CONFIG_LIMIT = 262144  # the host-side review reads at most 256 KiB too

# The variables this version's service.yaml sets (a test checks the two lists are equal), plus
# AgentBreeder's own service name.
SERVICE_ENV = (
    "HOME",
    "LITELLM_LOG",
    "PRESIDIO_ANALYZER_API_BASE",
    "PRESIDIO_ANONYMIZER_API_BASE",
    "LITELLM_TELEMETRY",
    "DISABLE_ADMIN_UI",
    "NO_DOCS",
    "STORE_MODEL_IN_DB",
    "AWS_EC2_METADATA_DISABLED",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "NO_GCE_CHECK",
    "GCE_METADATA_HOST",
    "GCE_METADATA_IP",
    "GCE_METADATA_ROOT",
    "AGENTBREEDER_SERVICE",
)
# The pinned image's own ENV names (`docker image inspect` of the service.yaml digest, recorded
# in #1025 O1), by name: no wildcard.
IMAGE_ENV = (
    "PATH",
    "SSL_CERT_FILE",
    "LITELLM_RELEASE_TAG",
    "PRISMA_BINARY_CACHE_DIR",
    "PRISMA_CLI_PATH",
    "PRISMA_CLI_QUERY_ENGINE_TYPE",
    "PRISMA_OFFLINE_MODE",
)
# Forced whatever the container says. NO_GCE_CHECK's casing and value are what the pinned
# google-auth (2.52.0) reads; botocore reads AWS_EC2_METADATA_DISABLED. A name that never
# resolves sends no packet to a metadata address.
FORCED = {
    "AWS_EC2_METADATA_DISABLED": "true",
    "AWS_CONFIG_FILE": "/dev/null",
    "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
    "NO_GCE_CHECK": "true",
    "GCE_METADATA_HOST": "metadata.invalid",
    "GCE_METADATA_IP": "metadata.invalid",
    "GCE_METADATA_ROOT": "metadata.invalid",
}
# gateway_providers.GATEWAY_ONLY (a test checks the copy): the cloud credentials only the
# built-in gateway may import. This version imports none of them, so a config naming one stops.
GATEWAY_ONLY = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "BEDROCK_ACCESS_KEY_ID",
    "BEDROCK_SECRET_ACCESS_KEY",
    "VERTEX_SERVICE_ACCOUNT_JSON",
)
ENV_REF = "os.environ/"
LOCAL_PREFIXES = ("ollama/", "ollama_chat/")  # providers that need no key (Ollama)


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
    try:
        return data.rstrip(b"\n").decode("utf-8", "strict")
    except UnicodeDecodeError:  # never the traceback: it would quote the bytes
        sys.exit(f"litellm launcher: secret {name} is not UTF-8")


def strings(value):
    """Every string in a parsed YAML document, keys included, recursively."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def load_config(path=CONFIG):
    """The reviewed config, parsed, and the variables it names; any failure stops the start
    (fail closed), without a traceback that could quote it."""
    try:
        import yaml

        with open(path, "rb") as handle:
            raw = handle.read(CONFIG_LIMIT + 1)
        if len(raw) > CONFIG_LIMIT:
            raise ValueError("too large")
        document = yaml.safe_load(raw.decode("utf-8", "strict"))
        if not isinstance(document, dict):
            raise ValueError("not a mapping")
        names = referenced(document)
    except (Exception, RecursionError, MemoryError):  # noqa: BLE001 - fail closed, print no value
        sys.exit("litellm launcher: the gateway config can't be read or parsed")
    return document, names


def referenced(document):
    """The variable names the config reads through `os.environ/NAME`."""
    return {text[len(ENV_REF) :] for text in strings(document) if text.startswith(ENV_REF)}


def local_models(document):
    """Whether a model_list entry routes to a provider that needs no key (Ollama)."""
    for entry in document.get("model_list") or []:
        params = entry.get("litellm_params") if isinstance(entry, dict) else None
        model = params.get("model") if isinstance(params, dict) else None
        if isinstance(model, str) and model.startswith(LOCAL_PREFIXES):
            return True
    return False


def environment_for(source, secrets):
    """LiteLLM's environment: the allow-listed names from `source`, the forced variables, and
    the secrets read from files."""
    environment = {name: source[name] for name in SERVICE_ENV + IMAGE_ENV if name in source}
    environment.update(FORCED)
    environment.update(secrets)
    return environment


def main():
    secrets = {}
    for name, variable in NAMES.items():
        value = read(name)
        if value is None:
            if name in REQUIRED:
                sys.exit(f"litellm launcher: secret {name} is missing")
            continue
        secrets[variable] = value
    environment = environment_for(os.environ, secrets)
    document, names = load_config()
    missing = sorted(n for n in names if n in GATEWAY_ONLY and n not in environment)
    if missing:
        sys.exit(
            "litellm launcher: the config needs "
            + ", ".join(missing)
            + ", which this gateway did not import"
        )
    providers = [variable for name, variable in NAMES.items() if name not in REQUIRED]
    if not any(variable in environment for variable in providers) and not local_models(document):
        print("litellm launcher: no provider key imported; model calls will fail", file=sys.stderr)
    argv = ["litellm", "--config", CONFIG, "--port", "4000"]
    os.execve("/app/.venv/bin/litellm", argv, environment)


if __name__ == "__main__":
    main()
