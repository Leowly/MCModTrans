"""Mod-specific compatibility workarounds.

Some mods have known bugs that are triggered by Chinese translations.
This module provides centralized filtering to avoid those bugs.

To add a new workaround, add an entry to SKIP_TRANSLATION_PATTERNS
with the modid and a description of why the translation causes issues.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Track which mods have already been logged (to avoid duplicate messages)
# _untranslated_keys() is called multiple times per mod during batching.
_logged_compat: set[str] = set()

# ---------------------------------------------------------------------------
# Mods to skip entirely — no translation needed
# ---------------------------------------------------------------------------
# These are infrastructure / API mods whose English text is technical and
# does not benefit from in-game Chinese translation.

SKIP_TRANSLATION_MODS: set[str] = {           # Minecraft Forge — 技术性 API 文本，不需要翻译
    "mixinextras",      # MixinExtras — 运行时 mixin 库
    "architectury",     # Architectury API — 跨平台 API
}

# ---------------------------------------------------------------------------
# Known-bug patterns: keys that must stay in English
# ---------------------------------------------------------------------------
# Each entry: modid → (key_prefixes, reason)
# Keys matching any prefix will NOT be translated — they keep English text.

SKIP_TRANSLATION_PATTERNS: dict[str, tuple[tuple[str, ...], str]] = {
    "projecte": (
        ("pe.manual.",),
        # ProjectE 的 ManualFontRenderer 换行算法依赖英文空格分词。
        # 中文文本无空格 → wrapFormStringToWidth() 找不到切分点 →
        # 无限递归 → StackOverflowError 崩溃。
        # 详见 ManualFontRenderer.java:44
        "手册文本无空格导致换行算法无限递归 (StackOverflowError)",
    ),
}


def should_keep_english(modid: str, key: str) -> bool:
    """Check if a translation key should remain in English for a given mod.

    Returns True if the key matches a known bug pattern and should NOT
    be translated.
    """
    entry = SKIP_TRANSLATION_PATTERNS.get(modid)
    if entry is None:
        return False
    prefixes, _reason = entry
    return any(key.startswith(prefix) for prefix in prefixes)


def get_skip_reason(modid: str) -> str | None:
    """Return a human-readable reason why keys are skipped for this mod."""
    entry = SKIP_TRANSLATION_PATTERNS.get(modid)
    if entry is None:
        return None
    _prefixes, reason = entry
    return reason


def filter_compat_keys(modid: str, keys: set[str]) -> tuple[set[str], set[str]]:
    """Split keys into (safe_to_translate, must_keep_english).

    Args:
        modid: The mod identifier.
        keys: Set of translation keys to check.

    Returns:
        Tuple of (translate_keys, keep_english_keys).
    """
    entry = SKIP_TRANSLATION_PATTERNS.get(modid)
    if entry is None:
        return keys, set()

    prefixes, reason = entry
    keep: set[str] = set()
    translate: set[str] = set()

    for key in keys:
        if any(key.startswith(prefix) for prefix in prefixes):
            keep.add(key)
        else:
            translate.add(key)

    if keep and modid not in _logged_compat:
        _logged_compat.add(modid)
        logger.info(
            "%s: 跳过 %d 个键（%s）",
            modid, len(keep), reason,
        )

    return translate, keep
