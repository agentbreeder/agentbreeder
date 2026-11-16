"""The model providers a stack's LiteLLM config may route to, and how (#1025 slice P1).

`gateway_keys.config_review` reviews a repo's gateway config before any key reaches it. This
module holds the per-provider half of that review: which `litellm_params.model` strings each
provider accepts, which credential params it needs and from which import, which plain params
it requires (each with its own pattern, since they are printed in the grant), and which
`model_info` keys any entry may set.

The four key-only providers (Gemini, OpenRouter, OpenAI, Anthropic) behave as before, labels
included, so their grant digests do not change. Ollama, Vertex AI, Bedrock and Azure OpenAI
are in the table, but a provider is accepted only when the stack's gateway imports every
credential it needs (`GatewayCaps`) and, for Ollama, can reach the host port. Each provider
slice adds its own `service/litellm` version after a real run (spec §6, D12): 1.2.0 reaches
host port 11434, so it serves Ollama (#1025 O1); no version imports the Vertex, Bedrock or
Azure credentials yet, so those are refused with `GATEWAY_PROVIDER_UNSUPPORTED`.

Every check is literal (`re.fullmatch` with `\\Z`-anchored patterns, no DNS) and no config value
is echoed in an error: messages name the location only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ENV = "os.environ/"

# The import names only the built-in gateway may read (spec §3.5, D3). `env_import.denied()`
# refuses them for every other caller; `services/lock.py` refuses any other service declaring
# them.
GATEWAY_ONLY = frozenset(
    {
        "VERTEX_SERVICE_ACCOUNT_JSON",
        "BEDROCK_ACCESS_KEY_ID",
        "BEDROCK_SECRET_ACCESS_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
    }
)

OLLAMA_PORT = 11434
OLLAMA_BASE = f"http://host.docker.internal:{OLLAMA_PORT}"  # exactly (D4)

# Params every provider may set: plain scalars, none printed in the grant.
COMMON_PARAMS = frozenset(
    {
        "timeout",
        "stream_timeout",
        "max_retries",
        "dimensions",
        "rpm",
        "tpm",
        "max_tokens",
        "temperature",
        "top_p",
        "drop_params",
        "reasoning_effort",
    }
)


def _pattern(text: str) -> re.Pattern:
    if not text.endswith(r"\Z"):  # `$` would accept a trailing newline
        raise ValueError("printed-field patterns end in \\Z")
    return re.compile(text)


# Printed fields (spec §4.1): `str` values only, each with its own pattern, so no control
# character, newline or non-ASCII text reaches the grant.
VERTEX_PROJECT = _pattern(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
VERTEX_LOCATION = _pattern(r"(global|[a-z]+-[a-z]+[0-9]{1,2})\Z")
AWS_REGION = _pattern(r"[a-z]{2}(-gov)?-[a-z]+-[0-9]\Z")
AZURE_API_VERSION = _pattern(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(-preview)?\Z")
AZURE_BASE_MODEL = _pattern(r"azure/[A-Za-z0-9._-]{1,64}\Z")
OLLAMA_KEEP_ALIVE = _pattern(r"[0-9]{1,4}[smh]\Z")
# The Azure endpoint comes from the operator (AZURE_OPENAI_ENDPOINT), never the repo. Shared
# with the slice that validates the value (A1): `services up` and the launcher.
AZURE_ENDPOINT = _pattern(
    r"https://(?!xn--)[a-z0-9](?:[a-z0-9]|-(?!-)){1,62}[a-z0-9]\.openai\.azure\.com/?\Z"
)
MODEL_MODES = frozenset({"chat", "embedding"})


class EntryRefused(ValueError):
    """A model_list entry the review refuses. `where` names a location, never a value; with
    `sentence` it is already a whole sentence (no "is not allowed" is appended)."""

    def __init__(self, code: str, where: str, sentence: bool = False):
        super().__init__(where)
        self.code = code
        self.where = where
        self.sentence = sentence


@dataclass(frozen=True)
class GatewayCaps:
    """What the stack's gateway can do, from its resolved `service.yaml` (spec §5.2): the
    variables it imports and the host ports it may reach. `ref` names it in errors."""

    ref: str
    imports: frozenset[str]
    reaches_host: frozenset[int] = frozenset()


def caps_from_spec(spec, ref: str) -> GatewayCaps:
    """The caps of a resolved gateway `ServiceSpec`: its imports and its `reaches_host`
    ports (`service/litellm@1.2.0` declares 11434, for Ollama)."""
    imports = frozenset(s.import_ for s in spec.secrets.values() if s.import_)
    return GatewayCaps(ref, imports, frozenset(spec.reaches_host))


def builtin_caps(ref: str) -> GatewayCaps | None:
    """The caps of a built-in `service/litellm` version, or None when `ref` is not one (a
    project version, or one this release does not ship). Read from the installed package's
    own tree: the question is what this release ships."""
    from pathlib import Path

    from agentbreeder import registry
    from agentbreeder.services.schema import ServiceError, load_service

    try:
        parsed = registry.Ref.parse(ref)
    except (registry.RegistryError, ValueError):
        return None
    if parsed.kind != "service" or parsed.name != "litellm":
        return None
    shipped = Path(registry.__file__).resolve().parent / "builtin_registry"
    path = shipped / "service" / parsed.name / parsed.version / "service.yaml"
    if not path.is_file():
        return None
    try:
        spec = load_service(path)
    except ServiceError:
        return None
    if spec.provides != "gateway/litellm":
        return None
    return caps_from_spec(spec, ref)


@dataclass(frozen=True)
class Plain:
    """A plain param a provider requires or allows, with its value check."""

    pattern: re.Pattern | None = None  # a `str` value must fullmatch it
    exact: str | None = None  # or the value must equal this string
    minus_one: bool = False  # the int -1 is also accepted (Ollama keep_alive)

    def accepts(self, value) -> bool:
        if self.minus_one and type(value) is int and value == -1:
            return True
        if not isinstance(value, str):
            return False
        if self.exact is not None:
            return value == self.exact
        return self.pattern is not None and self.pattern.fullmatch(value) is not None


@dataclass(frozen=True)
class Provider:
    name: str  # printed in the grant
    prefixes: tuple[str, ...]  # the text before the model string's first `/`, matched exactly
    model: re.Pattern  # the whole model string must fullmatch it
    credentials: dict[str, str]  # litellm param -> the import it must name (os.environ/<X>)
    credentials_required: bool  # False: the four key-only providers may omit their key
    required: dict[str, Plain] = field(default_factory=dict)
    optional: dict[str, Plain] = field(default_factory=dict)
    info_required: dict[str, Plain] = field(default_factory=dict)  # model_info keys
    host_port: int | None = None  # a host port the gateway must be able to reach
    key_only: bool = False  # label and grant exactly as before #1025

    @property
    def params(self) -> frozenset[str]:
        return (
            COMMON_PARAMS
            | {"model"}
            | set(self.credentials)
            | set(self.required)
            | set(self.optional)
        )


def _key_only(name: str, key: str) -> Provider:
    # Printable ASCII only: the four existing providers keep every config they accepted.
    return Provider(
        name,
        (name,),
        re.compile(re.escape(name) + r"/[!-~]{1,256}\Z"),
        {"api_key": key},
        credentials_required=False,
        key_only=True,
    )


PROVIDERS: dict[str, Provider] = {
    p.name: p
    for p in (
        _key_only("gemini", "GEMINI_API_KEY"),
        _key_only("openrouter", "OPENROUTER_API_KEY"),
        _key_only("openai", "OPENAI_API_KEY"),
        _key_only("anthropic", "ANTHROPIC_API_KEY"),
        Provider(
            "ollama",
            ("ollama", "ollama_chat"),
            re.compile(r"ollama(_chat)?/[A-Za-z0-9._:-]{1,128}\Z"),
            {},
            credentials_required=False,
            required={"api_base": Plain(exact=OLLAMA_BASE)},
            optional={"keep_alive": Plain(OLLAMA_KEEP_ALIVE, minus_one=True)},
            host_port=OLLAMA_PORT,
        ),
        Provider(
            "vertex",
            ("vertex_ai",),
            re.compile(
                r"vertex_ai/(gemini-|gemini-embedding-|text-embedding-"
                r"|text-multilingual-embedding-)[a-z0-9.-]{1,96}\Z"
            ),
            {"vertex_credentials": "VERTEX_SERVICE_ACCOUNT_JSON"},
            credentials_required=True,
            required={
                "vertex_project": Plain(VERTEX_PROJECT),
                "vertex_location": Plain(VERTEX_LOCATION),
            },
        ),
        Provider(
            "bedrock",
            ("bedrock",),
            re.compile(r"bedrock/(?!arn:)[a-z0-9][a-z0-9._:-]{0,127}\Z"),
            {
                "aws_access_key_id": "BEDROCK_ACCESS_KEY_ID",
                "aws_secret_access_key": "BEDROCK_SECRET_ACCESS_KEY",
            },
            credentials_required=True,
            required={"aws_region_name": Plain(AWS_REGION)},
        ),
        Provider(
            "azure",
            ("azure",),
            re.compile(r"azure/[A-Za-z0-9._-]{1,64}\Z"),
            {"api_key": "AZURE_OPENAI_API_KEY", "api_base": "AZURE_OPENAI_ENDPOINT"},
            credentials_required=True,
            required={"api_version": Plain(AZURE_API_VERSION)},
            info_required={"base_model": Plain(AZURE_BASE_MODEL)},
        ),
    )
}
_BY_PREFIX = {prefix: p for p in PROVIDERS.values() for prefix in p.prefixes}
PREFIX_LIST = ", ".join(sorted(_BY_PREFIX))
# The key-only providers' import names: what operator_config.PROVIDER_KEY_NAMES mirrors.
KEY_ONLY_KEYS = {p.name: p.credentials["api_key"] for p in PROVIDERS.values() if p.key_only}


@dataclass(frozen=True)
class Route:
    """One reviewed model_list entry."""

    provider: str
    label: str  # printed in the grant, e.g. "gemini (GEMINI_API_KEY)"
    required_env: tuple[str, ...]  # the variables the alias needs at `up`
    route: dict  # provider plus the printed route fields (project, location, region, ...)
    env_params: tuple[str, ...]  # litellm_params that may hold os.environ/ references
    key_only: bool


def provider_for(model) -> Provider | None:
    """The provider whose prefix the model string starts with (exact, case-sensitive)."""
    if not isinstance(model, str) or "/" not in model:
        return None
    return _BY_PREFIX.get(model.partition("/")[0])


def review_entry(where: str, params: dict, info: dict, caps: GatewayCaps) -> Route:
    """Check one entry's `litellm_params` and `model_info` against its provider. Raises
    EntryRefused naming the location; `params` and `info` are dicts of plain scalars."""
    model = params.get("model")
    provider = provider_for(model)
    if provider is None or not provider.model.fullmatch(model):
        # Unknown prefixes and bare names fall back to the provider SDK's default credential
        # chain, which on a cloud host can reach the machine's own identity (#1031).
        raise EntryRefused(
            "GATEWAY_CONFIG_UNSAFE",
            f"{where}.litellm_params.model (use PROVIDER/MODEL, PROVIDER one of: {PREFIX_LIST})",
        )
    extra = set(params) - provider.params
    if extra:
        raise EntryRefused(
            "GATEWAY_CONFIG_UNSAFE",
            f"{where}.litellm_params key for {provider.name} (custom endpoints are refused)",
        )
    _model_info(where, info, provider)
    env_params = []
    for param, name in provider.credentials.items():
        value = params.get(param)
        if value is None:
            if provider.credentials_required:
                raise EntryRefused(
                    "GATEWAY_CONFIG_UNSAFE",
                    f"{where}.litellm_params.{param} is required for {provider.name} "
                    f"(use {ENV}{name})",
                    sentence=True,
                )
            continue
        if value != ENV + name:
            raise EntryRefused(
                "GATEWAY_CONFIG_UNSAFE",
                f"{where}.litellm_params.{param} (use the provider's own key, {ENV}{name})",
            )
        env_params.append(param)
    for param, check in provider.required.items():
        if param not in params or not check.accepts(params[param]):
            raise EntryRefused(
                "GATEWAY_CONFIG_UNSAFE",
                f"{where}.litellm_params.{param} is required for {provider.name} "
                "and must match its pattern",
                sentence=True,
            )
    for param, check in provider.optional.items():
        if param in params and not check.accepts(params[param]):
            raise EntryRefused(
                "GATEWAY_CONFIG_UNSAFE",
                f"{where}.litellm_params.{param} must match its pattern",
                sentence=True,
            )
    required_env = tuple(params[p][len(ENV) :] for p in env_params)
    if provider.credentials_required or provider.host_port is not None:
        missing = sorted(set(provider.credentials.values()) - caps.imports)
        if missing or (
            provider.host_port is not None and provider.host_port not in caps.reaches_host
        ):
            what = (
                f"import {', '.join(missing)}"
                if missing
                else f"reach host port {provider.host_port}"
            )
            raise EntryRefused(
                "GATEWAY_PROVIDER_UNSUPPORTED",
                f"{where}: {provider.name} needs a gateway that can {what}; {caps.ref} cannot",
                sentence=True,
            )
    return Route(
        provider.name,
        _label(provider, params, info, required_env),
        required_env,
        _route(provider, params, info),
        tuple(env_params),
        provider.key_only,
    )


def _model_info(where: str, info: dict, provider: Provider) -> None:
    """`model_info` allow-list (D13): `mode` for every provider, the provider's own required
    keys (Azure `base_model`). Price keys, `id`, `access_groups` and the rest are refused: a
    repo must not set the price that budgets are enforced against."""
    allowed = {"mode"} | set(provider.info_required)
    if set(info) - allowed:
        raise EntryRefused(
            "GATEWAY_CONFIG_UNSAFE",
            f"{where}.model_info key (only mode"
            + (", base_model" if provider.info_required else "")
            + f" for {provider.name}; prices come from LiteLLM's model map)",
        )
    if "mode" in info and info["mode"] not in MODEL_MODES:
        raise EntryRefused("GATEWAY_CONFIG_UNSAFE", f"{where}.model_info.mode (chat or embedding)")
    for key, check in provider.info_required.items():
        if key not in info or not check.accepts(info[key]):
            raise EntryRefused(
                "GATEWAY_CONFIG_UNSAFE",
                f"{where}.model_info.{key} is required for {provider.name} "
                "and must match its pattern",
                sentence=True,
            )


def _label(provider: Provider, params: dict, info: dict, required_env: tuple[str, ...]) -> str:
    """The grant line per alias (spec §5.3). The key-only providers' labels are unchanged
    byte for byte (F6); every value printed here passed its own pattern."""
    if provider.key_only:
        return f"{provider.name} ({required_env[0] if required_env else 'no key'})"
    if provider.name == "ollama":
        return f"ollama (no key → host port {provider.host_port})"
    if provider.name == "vertex":
        return (
            f"vertex ({' + '.join(required_env)}, project {params['vertex_project']}, "
            f"{params['vertex_location']})"
        )
    if provider.name == "bedrock":
        return f"bedrock ({' + '.join(required_env)}, {params['aws_region_name']})"
    # azure: the resource name joins the label when the endpoint is resolved at plan time (A1).
    return (
        f"azure (AZURE_OPENAI_API_KEY → endpoint from AZURE_OPENAI_ENDPOINT, "
        f"{params['api_version']}, priced as {info['base_model']})"
    )


def _route(provider: Provider, params: dict, info: dict) -> dict:
    route: dict = {"provider": provider.name}
    if provider.name == "ollama":
        route["host_port"] = provider.host_port
    elif provider.name == "vertex":
        route |= {"project": params["vertex_project"], "location": params["vertex_location"]}
    elif provider.name == "bedrock":
        route["region"] = params["aws_region_name"]
    elif provider.name == "azure":
        route |= {
            "endpoint": "from AZURE_OPENAI_ENDPOINT",
            "api_version": params["api_version"],
            "base_model": info["base_model"],
        }
    return route


def grant_routes(routes: dict[str, dict], key_only: set[str]) -> dict[str, dict]:
    """The routes the services grant shows: every alias except the key-only providers', whose
    label already says everything, so a stack using none of the new providers keeps its grant
    digest (spec §5.3)."""
    return {alias: route for alias, route in sorted(routes.items()) if alias not in key_only}
