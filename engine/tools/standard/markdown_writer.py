"""Generic markdown-to-file writer.

Persists arbitrary model-produced markdown to a slug-named file under the
configured output directory. The agent supplies the title + content; this tool
only handles I/O — it does NOT generate or transform content.

Configurable via env: ``DOCUMENT_OUTPUT_DIR`` (default: ``./output``).
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Document title (slugified to derive the filename).",
        },
        "content": {
            "type": "string",
            "description": "Full markdown body to persist.",
        },
        "subdir": {
            "type": "string",
            "description": "Optional sub-directory under DOCUMENT_OUTPUT_DIR.",
            "default": "",
        },
    },
    "required": ["title", "content"],
}


def markdown_writer(title: str, content: str, subdir: str = "") -> dict[str, Any]:
    """Save markdown to disk and return the resolved file path.

    Args:
        title: Used to derive a kebab-cased filename. Always paired with a
            UTC timestamp suffix so multiple writes don't collide.
        content: Full markdown body.
        subdir: Optional sub-directory under ``DOCUMENT_OUTPUT_DIR``.

    Returns:
        A dict with keys:
            path: absolute path to the saved file.
            byte_size: size of the saved file in bytes.
            title: the title that was rendered.
    """
    base_dir = Path(os.getenv("DOCUMENT_OUTPUT_DIR", "./output"))
    out_dir = base_dir / subdir if subdir else base_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "document"
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"{slug}-{timestamp}.md"
    out_path = out_dir / filename

    out_path.write_text(content, encoding="utf-8")

    return {
        "path": str(out_path.resolve()),
        "byte_size": out_path.stat().st_size,
        "title": title,
    }
