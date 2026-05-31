"""Parse .lang format (1.12.2 and below).

.lang files use a simple key=value format, one entry per line.
Lines starting with # are comments. Empty lines are ignored.

Special directives:
  #PARSE_ESCAPES — tells the game to interpret escape sequences like \n

Format codes that must be preserved:
  §[0-9a-fk-or] — Minecraft color/formatting codes
  %s, %d, %f, %n$s — Java printf-style placeholders
"""

from __future__ import annotations


def parse_lang(text: str) -> dict[str, str]:
    """Parse .lang content into a key→value dictionary.

    Args:
        text: Decoded .lang file content (one key=value per line).

    Returns:
        Dictionary of translation key → display text.
        Comments and empty lines are excluded.

    Raises:
        ValueError: If a line cannot be parsed (logged but skipped).
    """
    entries: dict[str, str] = {}
    continuation_key: str | None = None
    continuation_value: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")

        # Handle continuation from previous line (trailing backslash — rare)
        if continuation_key is not None:
            continuation_value.append(line)
            if line.endswith("\\"):
                # Still continuing
                continuation_value[-1] = line.rstrip("\\")
                continue
            else:
                # End continuation
                entries[continuation_key] = "".join(continuation_value)
                continuation_key = None
                continuation_value = []
                continue

        # Skip empty lines and comments
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Split on first =
        if "=" not in stripped:
            # Line with no = — malformed, skip with warning potential
            continue

        eq_pos = stripped.index("=")
        key = stripped[:eq_pos].strip()
        value = stripped[eq_pos + 1:]

        # Handle backslash continuation
        if value.rstrip().endswith("\\"):
            continuation_key = key
            continuation_value = [value.rstrip("\\")]
            continue

        entries[key] = value

    # If file ends with a continuation (shouldn't happen), store it
    if continuation_key is not None:
        entries[continuation_key] = "".join(continuation_value)

    return entries


def format_lang(entries: dict[str, str]) -> str:
    """Serialize a dictionary back to .lang format.

    Args:
        entries: Translation key → display text.

    Returns:
        .lang-format string, sorted by key for diff-friendliness.
    """
    lines: list[str] = []
    for key in sorted(entries):
        lines.append(f"{key}={entries[key]}")
    return "\n".join(lines) + "\n"
