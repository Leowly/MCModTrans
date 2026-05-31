"""Parse .json language format (1.13+).

Modern Minecraft uses JSON files for language definitions:
  {"item.minecraft.sword": "Iron Sword", ...}

Some mods use nested JSON objects, which we flatten with dot-notation:
  {"item": {"sword": "Iron Sword"}} → {"item.sword": "Iron Sword"}
"""

from __future__ import annotations

import json
from typing import Any


def parse_json(raw_bytes: bytes) -> dict[str, str]:
    """Parse a .json language file into a flat key→value dictionary.

    Handles both flat and nested JSON structures. Nested objects are
    flattened with '.' as separator.

    Args:
        raw_bytes: Raw JSON bytes (UTF-8 encoded per Minecraft spec).

    Returns:
        Flat dictionary of translation key → display text.

    Raises:
        json.JSONDecodeError: If the JSON is malformed.
    """
    text = raw_bytes.decode("utf-8-sig")  # UTF-8 with optional BOM
    data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    return _flatten(data)


def _flatten(obj: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Recursively flatten a nested JSON object.

    Args:
        obj: The JSON dictionary to flatten.
        prefix: Key prefix from parent nesting level.

    Returns:
        Flat string→string dictionary.
    """
    result: dict[str, str] = {}

    for key, value in obj.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, str):
            result[full_key] = value
        elif isinstance(value, (int, float, bool)):
            # Coerce non-string values
            result[full_key] = str(value)
        elif isinstance(value, dict):
            result.update(_flatten(value, full_key))
        elif value is None:
            result[full_key] = ""
        else:
            # Lists or other types — coerce to string with warning potential
            result[full_key] = str(value)

    return result


def format_json(entries: dict[str, str], indent: int = 2) -> str:
    """Serialize a dictionary to .json language format.

    The Minecraft game loads language JSON as a flat object, so we output
    flat JSON. Keys are sorted for diff-friendliness.

    Args:
        entries: Translation key → display text.
        indent: JSON indentation level.

    Returns:
        Pretty-printed JSON string.
    """
    return json.dumps(
        dict(sorted(entries.items())),
        ensure_ascii=False,
        indent=indent,
    ) + "\n"
