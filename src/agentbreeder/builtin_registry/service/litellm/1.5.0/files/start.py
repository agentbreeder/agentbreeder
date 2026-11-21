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

1.3.0 (#1105): LiteLLM runs a copy of the reviewed config whose only change is
`litellm_settings.callbacks: [agentbreeder_request_filter.handler]` (files/
agentbreeder_request_filter.py, on PYTHONPATH), which refuses provider-routing parameters and
every call type but chat completions and embeddings. The copy is written to a new private
directory under /tmp; the reviewed config may not name callbacks itself.

1.4.0 (#1089) keeps this launcher; its filter also serves Anthropic messages.

1.5.0 (#1025 V1): 1.4.0's filter, plus Vertex AI. The optional secret
`vertex-service-account-json` must be a `service_account` key (never `authorized_user` or
`external_account`) with Google's own token endpoint and universe. LiteLLM receives a
re-serialised copy holding seven known fields, so the two parsers cannot disagree on duplicate
keys or extra fields. Every `vertex_ai/` entry must read that key and name the key's own
project, or the start stops.
"""

import json
import os
import re
import sys
import tempfile

SECRETS = "/run/secrets"
CONFIG = "/run/params/config"
NAMES = {
    "master-key": "LITELLM_MASTER_KEY",
    "database-url": "DATABASE_URL",
    "gemini-api-key": "GEMINI_API_KEY",
    "openrouter-api-key": "OPENROUTER_API_KEY",
    "openai-api-key": "OPENAI_API_KEY",
    "anthropic-api-key": "ANTHROPIC_API_KEY",
    "vertex-service-account-json": "VERTEX_SERVICE_ACCOUNT_JSON",
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
    "PYTHONPATH",
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
    "PYTHONPATH": "/files",  # the request filter, and nothing from the container
}
# gateway_providers.GATEWAY_ONLY (a test checks the copy): the cloud credentials only the
# built-in gateway may import. This version imports the Vertex key only, so a config naming
# another one, or the Vertex key when it was not imported, stops.
GATEWAY_ONLY = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "BEDROCK_ACCESS_KEY_ID",
    "BEDROCK_SECRET_ACCESS_KEY",
    "VERTEX_SERVICE_ACCOUNT_JSON",
)
ENV_REF = "os.environ/"
LOCAL_PREFIXES = ("ollama/", "ollama_chat/")  # providers that need no key (Ollama)
FILTER = "agentbreeder_request_filter.handler"  # files/agentbreeder_request_filter.py
RUN_DIR = "/tmp"

# The Vertex service-account key rules: copies of gateway_providers.VERTEX_* (a test checks
# them, and that both sides accept and refuse the same values).
VERTEX_SECRET = "vertex-service-account-json"
VERTEX_KEY_NAME = "VERTEX_SERVICE_ACCOUNT_JSON"
VERTEX_PROVIDER = "vertex_ai"  # the model string's prefix
VERTEX_KEY_FIELDS = (
    "type",
    "project_id",
    "private_key_id",
    "private_key",
    "client_email",
    "client_id",
    "token_uri",
)
VERTEX_KEY_TYPE = "service_account"
VERTEX_TOKEN_URI = "https://oauth2.googleapis.com/token"
VERTEX_UNIVERSE = "googleapis.com"
VERTEX_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
VERTEX_LOCATION = re.compile(r"(global|[a-z]+-[a-z]+[0-9]{1,2})\Z")
# The only litellm_params a Vertex entry may set: the host review's set for Vertex (a test checks
# them). Anything else, such as api_base, api_key or custom_llm_provider, could redirect the call.
VERTEX_PARAMS = frozenset(
    {
        "dimensions",
        "drop_params",
        "max_retries",
        "max_tokens",
        "model",
        "reasoning_effort",
        "rpm",
        "stream_timeout",
        "temperature",
        "timeout",
        "top_p",
        "tpm",
        "vertex_credentials",
        "vertex_location",
        "vertex_project",
    }
)


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


def no_duplicates(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate key")
    return result


def vertex_key(text):
    """The canonical fields of a service-account key, or None. Never raises, never prints."""
    try:
        key = json.loads(text, object_pairs_hook=no_duplicates)
    except (ValueError, RecursionError, MemoryError):
        return None
    if not isinstance(key, dict):
        return None
    if key.get("type") != VERTEX_KEY_TYPE or key.get("token_uri") != VERTEX_TOKEN_URI:
        return None
    if key.get("universe_domain", VERTEX_UNIVERSE) != VERTEX_UNIVERSE:
        return None
    if any(not isinstance(key.get(name), str) or not key[name] for name in VERTEX_KEY_FIELDS):
        return None
    if not VERTEX_PROJECT.fullmatch(key["project_id"]):
        return None
    return {name: key[name] for name in VERTEX_KEY_FIELDS}


def vertex_canonical(key):
    """One line of JSON with the canonical fields only: what LiteLLM receives."""
    return json.dumps(key, separators=(",", ":"), ensure_ascii=True)


def vertex_entries(document):
    """The litellm_params of every model_list entry routed to Vertex AI."""
    found = []
    for entry in document.get("model_list") or []:
        params = entry.get("litellm_params") if isinstance(entry, dict) else None
        model = params.get("model") if isinstance(params, dict) else None
        if isinstance(model, str) and model.partition("/")[0].startswith(VERTEX_PROVIDER):
            found.append(params)
    return found


def check_vertex(document, key):
    """Stop unless every Vertex entry reads the imported key and names the key's own project,
    and nothing else in the config reads the key (the host-side review and `services up` check
    the same; this is the file LiteLLM runs)."""
    entries = vertex_entries(document)
    reference = ENV_REF + VERTEX_KEY_NAME
    if sum(text.startswith(reference) for text in strings(document)) != len(entries):
        sys.exit(
            "litellm launcher: only a vertex_ai entry's vertex_credentials may read "
            + VERTEX_KEY_NAME
        )
    for params in entries:
        if params.get("vertex_credentials") != ENV_REF + VERTEX_KEY_NAME:
            sys.exit(
                "litellm launcher: a vertex_ai entry must read vertex_credentials from "
                + ENV_REF
                + VERTEX_KEY_NAME
            )
        location = params.get("vertex_location")
        if not isinstance(location, str) or not VERTEX_LOCATION.fullmatch(location):
            sys.exit("litellm launcher: a vertex_ai entry's vertex_location is not a location")
        if set(params) - VERTEX_PARAMS:  # names the rule, never the parameter or its value
            sys.exit(
                "litellm launcher: a vertex_ai entry sets a parameter the review does not allow"
            )
        if key is None or params.get("vertex_project") != key["project_id"]:
            sys.exit(
                "litellm launcher: a vertex_ai entry's vertex_project is not the "
                "service-account key's project"
            )


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


def with_filter(document):
    """The reviewed config with the request filter as LiteLLM's only callback. A config that
    names callbacks itself stops the start (the host-side review refuses it too)."""
    settings = document.get("litellm_settings")
    if settings is None:
        settings = {}
    if not isinstance(settings, dict) or "callbacks" in settings:
        sys.exit("litellm launcher: the gateway config may not set litellm_settings.callbacks")
    return {**document, "litellm_settings": {**settings, "callbacks": [FILTER]}}


def write_config(document, run_dir=RUN_DIR):
    """Write the filtered config to a new private directory; returns its path."""
    try:
        import yaml

        directory = tempfile.mkdtemp(prefix="agentbreeder-litellm-", dir=run_dir)
        path = os.path.join(directory, "config.yaml")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, sort_keys=False)
    except Exception:  # noqa: BLE001 - fail closed, print no value
        sys.exit("litellm launcher: the filtered gateway config can't be written")
    return path


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
    document, names = load_config()
    key = None
    if VERTEX_KEY_NAME in secrets:
        key = vertex_key(secrets.pop(VERTEX_KEY_NAME))
        used = VERTEX_KEY_NAME in names or bool(vertex_entries(document))
        if key is None and used:  # never the value
            sys.exit(f"litellm launcher: secret {VERTEX_SECRET} is not a service-account key")
        if key is not None and used:
            secrets[VERTEX_KEY_NAME] = vertex_canonical(key)
        # A config that does not use the key: LiteLLM never sees it, key or not.
    environment = environment_for(os.environ, secrets)
    missing = sorted(n for n in names if n in GATEWAY_ONLY and n not in environment)
    if missing:
        sys.exit(
            "litellm launcher: the config needs "
            + ", ".join(missing)
            + ", which this gateway did not import"
        )
    check_vertex(document, key)
    providers = [variable for name, variable in NAMES.items() if name not in REQUIRED]
    if not any(variable in environment for variable in providers) and not local_models(document):
        print("litellm launcher: no provider key imported; model calls will fail", file=sys.stderr)
    config = write_config(with_filter(document))
    argv = ["litellm", "--config", config, "--port", "4000"]
    os.execve("/app/.venv/bin/litellm", argv, environment)


if __name__ == "__main__":
    main()
