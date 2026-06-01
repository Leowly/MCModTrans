"""Shared model scanner — detect untagged items/blocks from model files.

Consolidates model-scanning logic that was previously duplicated across
three separate code paths (translate pipeline, analyze command,
find-untagged command). All three now use this module so their results
are guaranteed consistent.

Strategy:
1. Scan JAR for models/item/*.json and blockstates/*.json
2. Cross-reference against known lang keys using multiple candidate patterns
3. Report items/blocks with no matching lang key as "untagged"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import ZipFile

if TYPE_CHECKING:
    from ..models import ModAssets

logger = logging.getLogger(__name__)

_ITEM_NAME_CLEANUP = re.compile(r"[^a-zA-Z0-9_]+")


@dataclass
class ModelScannerConfig:
    """Configuration for model scanning behavior.

    Attributes:
        colon_format: Emit keys with colon format ``item.{modid}:{name}.name``.
            True by default (Forge 1.12.2+ standard).
        detect_no_dot_name: Check for keys without ``.name`` suffix.
        fuzzy_match: Enable trailing-``.{name}.name`` suffix matching
            against known key roots.
    """
    colon_format: bool = True
    detect_no_dot_name: bool = True
    fuzzy_match: bool = True


@dataclass
class ScanResult:
    """Result of scanning a single mod for untagged items/blocks."""

    modid: str
    model_items: list[str] = field(default_factory=list)
    model_blocks: list[str] = field(default_factory=list)
    untagged_items: list[str] = field(default_factory=list)
    untagged_blocks: list[str] = field(default_factory=list)
    matched_keys: dict[str, list[str]] = field(default_factory=dict)
    known_keys: set[str] = field(default_factory=set)

    @property
    def has_untagged(self) -> bool:
        return bool(self.untagged_items or self.untagged_blocks)

    @property
    def total_untagged(self) -> int:
        return len(self.untagged_items) + len(self.untagged_blocks)

    def gap_dicts(self) -> list[dict]:
        gaps: list[dict] = []
        for name in self.untagged_items:
            key = f"item.{self.modid}:{name}.name"
            gaps.append({
                "source_modid": self.modid,
                "key": key,
                "suggested_en": _name_to_english(name),
                "item_name": name,
                "item_type": "item",
            })
        for name in self.untagged_blocks:
            key = f"tile.{self.modid}:{name}.name"
            gaps.append({
                "source_modid": self.modid,
                "key": key,
                "suggested_en": _name_to_english(name),
                "item_name": name,
                "item_type": "block",
            })
        return gaps


def scan_mod(
    mod: ModAssets,
    config: ModelScannerConfig | None = None,
) -> ScanResult:
    """Scan a parsed mod's JAR for untagged items/blocks.

    Re-opens the JAR to enumerate model files, then cross-references
    against ``mod.english_entries``.

    Args:
        mod: Parsed ModAssets from JarParser (must have jar_path).
        config: Scanner configuration. Uses defaults if None.

    Returns:
        ScanResult with matched and untagged items/blocks.
    """
    if config is None:
        config = ModelScannerConfig()

    if not mod.jar_path or not mod.jar_path.is_file():
        logger.debug("scan_mod: %s no JAR path, skipping", mod.modid)
        return ScanResult(modid=mod.modid, known_keys=set(mod.english_entries.keys()))

    with ZipFile(mod.jar_path, "r") as zf:
        names = zf.namelist()
        return _scan_inner(names, mod.modid, set(mod.english_entries.keys()), config)


def scan_jar_direct(
    jar_path: Path,
    modid: str,
    known_keys: set[str],
    config: ModelScannerConfig | None = None,
) -> ScanResult:
    """Scan a JAR directly with externally-provided modid and known keys.

    Used when JarParser cannot fully parse a mod (e.g. no language files),
    but we still need to enumerate model files for untagged detection.

    Args:
        jar_path: Path to the .jar file.
        modid: Mod identifier to use for key generation.
        known_keys: Set of known lang keys to check against.
        config: Scanner configuration. Uses defaults if None.

    Returns:
        ScanResult with matched and untagged items/blocks.
    """
    if config is None:
        config = ModelScannerConfig()

    if not jar_path.is_file():
        logger.debug("scan_jar_direct: file not found %s", jar_path)
        return ScanResult(modid=modid, known_keys=known_keys)

    with ZipFile(jar_path, "r") as zf:
        names = zf.namelist()
        return _scan_inner(names, modid, known_keys, config)


def generate_gaps(
    mods: list[ModAssets],
    config: ModelScannerConfig | None = None,
) -> list[dict]:
    """Convenience: scan all mods and return gaps as gap dicts.

    Args:
        mods: List of parsed mod assets.
        config: Scanner configuration. Uses defaults if None.

    Returns:
        Flat list of gap dicts (see ``ScanResult.gap_dicts()``).
    """
    if config is None:
        config = ModelScannerConfig()

    all_gaps: list[dict] = []
    for mod in mods:
        try:
            result = scan_mod(mod, config)
            all_gaps.extend(result.gap_dicts())
        except Exception as e:
            logger.warning("scan_mod failed %s: %s", mod.modid, e)
    return all_gaps


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scan_inner(
    names: list[str],
    modid: str,
    known_keys: set[str],
    config: ModelScannerConfig,
) -> ScanResult:
    """Core scanning logic shared by both entry points."""
    model_items, model_blocks = _collect_model_files(names)

    if not model_items and not model_blocks:
        return ScanResult(modid=modid, known_keys=known_keys)

    has_colon = False
    has_no_dot_name = False
    if config.colon_format:
        has_colon = _detect_colon_format(known_keys, modid)
    if config.detect_no_dot_name:
        has_no_dot_name = _detect_no_dot_name_keys(known_keys, modid)

    model_roots = _build_model_roots(known_keys) if config.fuzzy_match else set()

    matched_keys: dict[str, list[str]] = {}
    untagged_items: list[str] = []
    untagged_blocks: list[str] = []

    for name in sorted(model_items):
        matched = _find_matching_keys(
            name, modid, known_keys, model_roots, has_colon, has_no_dot_name,
        )
        matched_keys[name] = matched
        if not matched:
            untagged_items.append(name)

    for name in sorted(model_blocks):
        matched = _find_matching_keys(
            name, modid, known_keys, model_roots, has_colon, has_no_dot_name,
        )
        matched_keys[name] = matched
        if not matched:
            untagged_blocks.append(name)

    return ScanResult(
        modid=modid,
        model_items=sorted(model_items),
        model_blocks=sorted(model_blocks),
        untagged_items=untagged_items,
        untagged_blocks=untagged_blocks,
        matched_keys=matched_keys,
        known_keys=known_keys,
    )


def _collect_model_files(names: list[str]) -> tuple[set[str], set[str]]:
    """Collect item/block names from model file paths in a JAR.

    Returns:
        ``(model_items, model_blocks)`` — sets of file stems.
    """
    model_items: set[str] = set()
    model_blocks: set[str] = set()
    for name in names:
        if "models/item/" in name and name.endswith(".json"):
            model_items.add(Path(name).stem)
        elif "blockstates/" in name and name.endswith(".json"):
            model_blocks.add(Path(name).stem)
    return model_items, model_blocks


def _detect_colon_format(known_keys: set[str], modid: str) -> bool:
    """Check whether this mod uses colon-format lang keys.

    Colon format: ``item.modid:itemname.name`` (Forge 1.12.2+ standard).
    """
    prefixes = (f"item.{modid}:", f"tile.{modid}:", f"block.{modid}:")
    return any(k.startswith(prefixes) for k in known_keys)


def _detect_no_dot_name_keys(known_keys: set[str], modid: str) -> bool:
    """Check whether any known keys use ``item.modid:name`` without ``.name``."""
    prefixes = (f"item.{modid}:", f"tile.{modid}:", f"block.{modid}:")
    return any(
        k for k in known_keys
        if k.startswith(prefixes) and not k.endswith(".name")
    )


def _build_model_roots(known_keys: set[str]) -> set[str]:
    """Build a set of key roots for fuzzy matching.

    The "root" of ``item.modid:sword.name`` is ``item.modid:sword``.
    """
    roots: set[str] = set()
    for k in known_keys:
        parts = k.rsplit(".", 1)
        if len(parts) == 2:
            roots.add(parts[0])
    return roots


def _find_matching_keys(
    name: str,
    modid: str,
    known_keys: set[str],
    model_roots: set[str],
    use_colon: bool,
    use_no_dot: bool,
) -> list[str]:
    """Check a model name against known lang keys using all candidate patterns.

    Tries every known naming convention so no format is missed.

    Args:
        name: Model file stem (e.g. ``redstone_sword``).
        modid: Mod identifier.
        known_keys: Set of known lang keys.
        model_roots: Set of model roots for fuzzy matching.
        use_colon: Whether to include colon-format candidates.
        use_no_dot: Whether to include candidates without ``.name`` suffix.

    Returns:
        List of matching lang keys (empty = untagged).
    """
    matched: list[str] = []

    # Dot format (always tried)
    dot_candidates = [
        f"item.{modid}.{name}.name",
        f"tile.{modid}.{name}.name",
        f"block.{modid}.{name}.name",
        f"{modid}.{name}.name",
        f"item.{name}.name",
        f"tile.{name}.name",
    ]
    matched.extend(c for c in dot_candidates if c in known_keys)

    # Colon format (Forge 1.12.2+ standard)
    if use_colon:
        colon_candidates = [
            f"item.{modid}:{name}.name",
            f"tile.{modid}:{name}.name",
            f"block.{modid}:{name}.name",
        ]
        matched.extend(c for c in colon_candidates if c in known_keys)

    # No-.name suffix format
    if use_no_dot:
        no_dot_candidates = [
            f"item.{modid}:{name}",
            f"tile.{modid}:{name}",
            f"block.{modid}:{name}",
        ]
        matched.extend(c for c in no_dot_candidates if c in known_keys)

    # Fuzzy match via model roots
    if not matched and model_roots:
        for root in model_roots:
            if root.endswith(f":{name}") or root.endswith(f".{name}"):
                matched.append(root)
                break

    return matched


def _name_to_english(name: str) -> str:
    """Convert internal registry name to readable English.

    ``redstone_sword`` -> "Redstone Sword"
    ``copper_furnace`` -> "Copper Furnace"
    """
    cleaned = _ITEM_NAME_CLEANUP.sub(" ", name)
    return " ".join(w.capitalize() for w in cleaned.split())
