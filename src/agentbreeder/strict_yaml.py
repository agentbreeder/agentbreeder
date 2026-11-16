"""Bounded, strict YAML reading shared by authoring files (agent.yaml, registry manifests).

One UTF-8 document, a size limit, unique string keys, and no anchors or aliases — the same rules
as the deploy manifest (manifest.py).
"""

from __future__ import annotations

import os
import re
import stat
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
    """Only a regular file is read: it is opened non-blocking, so a FIFO (in a shared registry
    root, say) is refused instead of blocking the open forever (#989)."""
    return load_bytes(read_file(path, limit), path.name)


def load_bytes(raw: bytes, name: str) -> dict:
    """Parse bytes already read (a caller that hashes what it parsed reads once, #1024)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StrictYamlError(f"{name} is not UTF-8") from error
    return load_text(text)


def read_file(path: Path, limit: int = 64 * 1024) -> bytes:
    """The bytes of a regular file of at most `limit` bytes (see `load_file`)."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as error:
        raise StrictYamlError(f"Cannot read {path}") from error
    try:
        regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
    except OSError as error:
        os.close(descriptor)
        raise StrictYamlError(f"Cannot read {path}") from error
    if not regular:  # checked before fdopen, which raises (and leaks it) on a directory
        os.close(descriptor)
        raise StrictYamlError(f"{path.name} is not a regular file")
    try:
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as error:
        raise StrictYamlError(f"Cannot read {path}") from error
    if len(raw) > limit:
        raise StrictYamlError(f"{path.name} exceeds the {limit // 1024} KiB limit")
    return raw
