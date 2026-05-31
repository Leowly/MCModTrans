"""Encode .lang files with fallback chain.

1.12.2 mod .lang files can use various encodings (UTF-8, Windows-1252,
GBK, etc.). This module detects the correct encoding and decodes the bytes.
"""

from __future__ import annotations

import codecs
from typing import Optional

# Third-party
try:
    import chardet
except ImportError:  # pragma: no cover
    chardet = None  # type: ignore[assignment]

# Encoding fallback chain (tried in order)
_ENCODING_FALLBACKS: list[str] = [
    "utf-8",
    "windows-1252",  # Most common legacy English encoding
    "gbk",           # Common for Chinese-sourced mods
    "latin-1",       # Wide compatibility, never fails to decode
]

# Legacy BOMs that indicate encoding
_BOM_MAP: dict[bytes, str] = {
    codecs.BOM_UTF8: "utf-8",
    b"\xff\xfe": "utf-16-le",
    b"\xfe\xff": "utf-16-be",
}


def detect_encoding(raw_bytes: bytes) -> str:
    """Detect the most likely encoding for a byte buffer.

    Strategy (tried in order):
    1. BOM detection (UTF-8 BOM, UTF-16 LE/BE)
    2. chardet library detection
    3. Try fallback encodings, pick first that decodes without errors
    4. Ultimate fallback: utf-8 with replace

    Returns:
        Encoding name suitable for bytes.decode().
    """
    # Strategy 1: BOM detection
    for bom, encoding in _BOM_MAP.items():
        if raw_bytes.startswith(bom):
            return encoding

    # Strategy 2: chardet
    if chardet is not None:
        result = chardet.detect(raw_bytes)
        encoding = result.get("encoding")
        confidence = result.get("confidence", 0)
        if encoding and confidence > 0.7:
            # Normalize chardet output
            encoding = encoding.lower().replace("-", "_")
            if encoding in ("gb2312", "gb18030"):
                encoding = "gbk"
            try:
                raw_bytes.decode(encoding)
                return encoding
            except (UnicodeDecodeError, LookupError):
                pass

    # Strategy 3: Try fallbacks, pick first that works
    for encoding in _ENCODING_FALLBACKS:
        try:
            raw_bytes.decode(encoding)
            # Quick sanity check for .lang/.json content
            return encoding
        except (UnicodeDecodeError, LookupError):
            continue

    # Strategy 4: Ultimate fallback
    return "utf-8"


def decode_lang(raw_bytes: bytes, *, hint_encoding: Optional[str] = None) -> tuple[str, str]:
    """Decode .lang file bytes to string, auto-detecting encoding.

    Args:
        raw_bytes: Raw file content from JAR.
        hint_encoding: Optional encoding hint (e.g. from MANIFEST.MF).

    Returns:
        Tuple of (decoded_text, used_encoding).
        Never raises — always returns something decodable.
    """
    # If hint provided and works, use it
    if hint_encoding:
        try:
            return raw_bytes.decode(hint_encoding), hint_encoding
        except (UnicodeDecodeError, LookupError):
            pass

    # Auto-detect
    encoding = detect_encoding(raw_bytes)

    try:
        return raw_bytes.decode(encoding), encoding
    except (UnicodeDecodeError, LookupError):
        # Last resort — force UTF-8 with replacement characters
        return raw_bytes.decode("utf-8", errors="replace"), "utf-8-replace"
