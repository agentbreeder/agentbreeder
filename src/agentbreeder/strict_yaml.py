"""Bounded, strict YAML reading shared by authoring files (agent.yaml, registry manifests).

One UTF-8 document, a size limit, unique string keys, and no anchors or aliases — the same rules
as the deploy manifest (manifest.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


class StrictYamlError(ValueError):
    pass


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise StrictYamlError("YAML mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


MAX_FLOW_DEPTH = 64
_BRACKETS = re.compile(r"[\[\]{}]")


def _flow_depth(text: str) -> int:
    """The deepest `[`/`{` nesting, counted over the whole text (quotes ignored, so it can only
    over-count). PyYAML's scanner is quadratic in flow nesting: 64 KiB of `[` takes minutes."""
    depth = deepest = 0
    for match in _BRACKETS.finditer(text):
        depth = depth + 1 if match.group() in "[{" else max(depth - 1, 0)
        deepest = max(deepest, depth)
    return deepest


def load_text(text: str) -> dict:
    if _flow_depth(text) > MAX_FLOW_DEPTH:
        raise StrictYamlError(f"YAML nested more than {MAX_FLOW_DEPTH} levels of [ ] or {{ }}")
    try:
        if any(isinstance(t, (yaml.AliasToken, yaml.AnchorToken)) for t in yaml.scan(text)):
            raise StrictYamlError("YAML anchors and aliases are not supported")
        data = yaml.load(text, Loader=_UniqueLoader)
    except StrictYamlError:
        raise
    except (ValueError, yaml.YAMLError, RecursionError) as error:
        raise StrictYamlError("Invalid YAML; use one document of simple fields") from error
    if not isinstance(data, dict):
        raise StrictYamlError("The document must be a YAML mapping")
    return data


def load_file(path: Path, limit: int = 64 * 1024) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as error:
        raise StrictYamlError(f"Cannot read {path}") from error
    if len(raw) > limit:
        raise StrictYamlError(f"{path.name} exceeds the {limit // 1024} KiB limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StrictYamlError(f"{path.name} is not UTF-8") from error
    return load_text(text)
