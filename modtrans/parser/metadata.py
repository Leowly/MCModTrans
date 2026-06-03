"""Mod metadata extraction from JAR files.

Supports multiple mod loaders:
- Forge: mcmod.info
- Fabric: fabric.mod.json
- NeoForge / FML 4+: META-INF/mods.toml / neoforge.mods.toml
- Fallback: MANIFEST.MF, pack.mcmeta

Usage (internal)::

    from .metadata import extract_metadata
    metadata = extract_metadata(zf)  # zf is a ZipFile
"""

from __future__ import annotations

import json
import logging
from zipfile import ZipFile

from ..models import ModMetadata

logger = logging.getLogger(__name__)


def extract_metadata(zf: ZipFile) -> ModMetadata:
    """Extract mod metadata from best available source.

    Tries each loader-specific extractor in priority order.
    Never raises — returns best-effort ModMetadata.
    """
    for extractor in (
        _try_mcmod_info,        # Forge
        _try_fabric_mod_json,   # Fabric
        _try_mods_toml_neo,     # NeoForge (mods.toml)
        _try_mods_toml_neoforge,# NeoForge (neoforge.mods.toml)
        _try_manifest_mf,       # Generic fallback
    ):
        metadata = extractor(zf)
        if metadata is not None:
            return metadata

    # Last resort: pack.mcmeta description only
    result = ModMetadata(modid="")
    _fill_from_pack_mcmeta(zf, result)
    return result


# ------------------------------------------------------------------
# Loader-specific extractors
# ------------------------------------------------------------------


def _try_mcmod_info(zf: ZipFile) -> ModMetadata | None:
    """Forge: mcmod.info (1.12.2 ~ 1.16)."""
    try:
        info_bytes = zf.read("mcmod.info")
        info = json.loads(info_bytes.decode("utf-8-sig"), strict=False)
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    if isinstance(info, list) and len(info) > 0:
        _SKIP_MODIDS = {"minecraft", "forge", "mcp", "fml"}
        best = None
        for entry in info:
            modid = entry.get("modid", "")
            if modid in _SKIP_MODIDS:
                continue
            has_name = bool(entry.get("name"))
            has_version = bool(entry.get("version"))
            has_author = bool(entry.get("authorList") or entry.get("authors"))
            if has_name and has_version and has_author:
                best = entry
                break
            if best is None and has_name and has_version:
                best = entry
        mod = best if best is not None else info[0]
    elif isinstance(info, dict):
        mod = info
    else:
        return None

    return _forge_entry_to_metadata(mod)


def _try_fabric_mod_json(zf: ZipFile) -> ModMetadata | None:
    """Fabric: fabric.mod.json."""
    try:
        fab_bytes = zf.read("fabric.mod.json")
        fab = json.loads(fab_bytes.decode("utf-8-sig"), strict=False)
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    author_list = fab.get("authors", [])
    if isinstance(author_list, list):
        author_str = ", ".join(str(a) for a in author_list)
    else:
        author_str = str(author_list) if author_list else ""

    contact = fab.get("contact", {})
    url = contact.get("homepage", "") if isinstance(contact, dict) else ""

    return ModMetadata(
        modid=fab.get("id", ""),
        name=fab.get("name", ""),
        version=fab.get("version", ""),
        author=author_str,
        description=fab.get("description", ""),
        game_version="",
        credits="",
        url=url,
    )


def _try_mods_toml_neo(zf: ZipFile) -> ModMetadata | None:
    """NeoForge / FML 4+: META-INF/mods.toml."""
    return _try_mods_toml(zf, "META-INF/mods.toml")


def _try_mods_toml_neoforge(zf: ZipFile) -> ModMetadata | None:
    """NeoForge: META-INF/neoforge.mods.toml."""
    return _try_mods_toml(zf, "META-INF/neoforge.mods.toml")


def _try_manifest_mf(zf: ZipFile) -> ModMetadata | None:
    """Generic fallback: META-INF/MANIFEST.MF."""
    try:
        manifest = zf.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")
    except (KeyError, UnicodeDecodeError):
        return None

    result = ModMetadata(modid="")
    for line in manifest.splitlines():
        if line.startswith("Implementation-Title:"):
            name = line.split(":", 1)[1].strip()
            if name:
                result.name = name
        elif line.startswith("Implementation-Version:"):
            result.version = line.split(":", 1)[1].strip()

    if result.name or result.version:
        _fill_from_pack_mcmeta(zf, result)
        return result
    return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _try_mods_toml(zf: ZipFile, path: str) -> ModMetadata | None:
    """Shared logic for mods.toml / neoforge.mods.toml."""
    try:
        import tomllib
    except ImportError:
        return None

    try:
        toml_bytes = zf.read(path)
        toml = tomllib.loads(toml_bytes.decode("utf-8"))
    except (KeyError, tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
        return None

    mods = toml.get("mods", [])
    if not mods or not isinstance(mods, list):
        return None

    mod = mods[0]
    return ModMetadata(
        modid=mod.get("modId", ""),
        name=mod.get("displayName", ""),
        version=mod.get("version", ""),
        author=mod.get("authors", ""),
        description=mod.get("description", ""),
        game_version="",
        credits=mod.get("credits", ""),
        url=mod.get("displayURL", ""),
    )


def _forge_entry_to_metadata(mod: dict) -> ModMetadata:
    """Convert a Forge mcmod.info entry to ModMetadata."""
    author = mod.get("authorList", mod.get("authors", []))
    if isinstance(author, list):
        author_str = ", ".join(author)
    else:
        author_str = str(author) if author else ""

    return ModMetadata(
        modid=mod.get("modid", ""),
        name=mod.get("name", ""),
        version=mod.get("version", ""),
        author=author_str,
        description=mod.get("description", ""),
        game_version=mod.get("mcversion", ""),
        credits=mod.get("credits", ""),
        url=mod.get("url", ""),
    )


def _fill_from_pack_mcmeta(zf: ZipFile, metadata: ModMetadata) -> None:
    """Fill missing description from pack.mcmeta."""
    if metadata.description:
        return
    try:
        mcmeta_bytes = zf.read("pack.mcmeta")
        mcmeta = json.loads(mcmeta_bytes.decode("utf-8-sig"), strict=False)
        desc = mcmeta.get("pack", {}).get("description", "")
        if desc:
            metadata.description = desc
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
        pass
