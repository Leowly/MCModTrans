"""Core JAR parser — extracts language files and metadata from mod JARs.

Orchestrates the full parsing pipeline:
1. Open JAR as ZIP
2. Detect game version (LEGACY .lang vs MODERN .json)
3. Extract metadata (mcmod.info, MANIFEST.MF, pack.mcmeta)
4. Parse en_us.* and zh_cn.* language files
5. Return ModAssets with all data
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from typing import Optional

from ..models import (
    GameVersion,
    ModAssets,
    ModMetadata,
    MODERN_PACK_FORMAT_THRESHOLD,
    LEGACY_PACK_FORMAT_MAX,
)
from .encoding import decode_lang
from .lang_parser import parse_lang
from .json_parser import parse_json

logger = logging.getLogger(__name__)


class JarParseError(Exception):
    """Non-fatal error parsing a single JAR file."""


class JarParser:
    """Parses Minecraft mod JAR files to extract language assets.

    Usage::

        parser = JarParser()
        assets = parser.parse_jar(Path("mods/example.jar"))
        print(f"Found {len(assets.english_entries)} English entries")
    """

    def __init__(self, generate_untagged: bool = False) -> None:
        self.generate_untagged = generate_untagged

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_jar(self, jar_path: Path) -> ModAssets:
        """解析单个 mod JAR 并返回其语言资产。

        Args:
            jar_path: mods 文件夹中的 .jar 文件路径。

        Returns:
            包含 english_entries 和可选 existing_chinese 的 ModAssets。

        Raises:
            JarParseError: JAR 损坏、无语言文件或编码不可读时抛出。
        """
        if not jar_path.is_file():
            raise JarParseError(f"JAR 文件不存在: {jar_path}")

        try:
            with ZipFile(jar_path, "r") as zf:
                return self._parse_zip(jar_path, zf)
        except BadZipFile as e:
            raise JarParseError(f"JAR 文件损坏: {jar_path.name}: {e}") from e
        except OSError as e:
            raise JarParseError(f"无法读取 JAR: {jar_path.name}: {e}") from e

    # ------------------------------------------------------------------
    # Internal: ZIP traversal
    # ------------------------------------------------------------------

    def _parse_zip(self, jar_path: Path, zf: ZipFile) -> ModAssets:
        """Main parsing logic on an opened ZipFile."""
        # 1. Detect game version
        game_version = self.detect_game_version(zf)

        # 2. Extract metadata
        metadata = self.extract_metadata(zf)

        # 3. Collect language file entries
        lang_entries = _collect_lang_entries(zf)

        if not lang_entries:
            if self.generate_untagged:
                # 没有语言文件，从模型文件生成伪 en_us 条目
                logger.info(
                    "%s: 无语言文件，从模型文件名推断英文名", jar_path.name
                )
                return self._generate_from_models(jar_path, zf, metadata)
            raise JarParseError(
                f"{jar_path.name} 中未找到语言文件 "
                f"(无 assets/<modid>/lang/ 目录)"
            )

        # 4. Find en_us and zh_cn files
        modid = self._infer_modid(lang_entries, metadata)
        en_us_paths, zh_cn_paths, other_lang_paths = _classify_lang_entries(lang_entries)

        if not en_us_paths:
            # Some mods might use en_US or en_us — try case-insensitive
            for path in other_lang_paths:
                if path.lower().startswith("assets/") and "en_us" in path.lower():
                    en_us_paths.append(path)

        if not en_us_paths:
            if self.generate_untagged:
                # 有其他语言文件但没有 en_us，也尝试生成
                logger.info(
                    "%s: 无 en_us 语言文件，从模型文件名推断英文名", jar_path.name
                )
                return self._generate_from_models(jar_path, zf, metadata)
            raise JarParseError(
                f"{jar_path.name} 中未找到 en_us 语言文件"
            )

        # 5. Parse English entries
        english_entries = self._parse_lang_files(zf, en_us_paths, game_version)

        # 6. Parse existing Chinese entries (if present)
        existing_chinese = {}
        if zh_cn_paths:
            existing_chinese = self._parse_lang_files(zf, zh_cn_paths, game_version)

        # 7. Detect encoding
        source_encoding = "utf-8"
        if game_version == GameVersion.LEGACY:
            # Try to detect encoding from first en_us file
            first_path = en_us_paths[0]
            raw = zf.read(first_path)
            _, source_encoding = decode_lang(raw)

        if not metadata.modid:
            metadata.modid = modid

        return ModAssets(
            modid=modid,
            game_version=game_version,
            english_entries=english_entries,
            existing_chinese=existing_chinese,
            metadata=metadata,
            jar_path=jar_path,
            source_encoding=source_encoding,
        )

    # ------------------------------------------------------------------
    # 模型文件条目生成（无语言文件时的回退方案）
    # ------------------------------------------------------------------

    def _generate_from_models(
        self, jar_path: Path, zf: ZipFile, metadata: ModMetadata
    ) -> ModAssets:
        """从模型文件推断英文名称，生成伪 en_us 条目。

        用于完全没有 en_us 语言文件的 mod。从 models/item/ 和
        blockstates/ 的文件名推断标准 Minecraft lang key 和英文名。

        例如:
        - models/item/redstone_sword.json → item.<modid>.redstone_sword.name = "Redstone Sword"
        - blockstates/copper_furnace.json → tile.<modid>.copper_furnace.name = "Copper Furnace"
        """
        import re
        from pathlib import Path as _Path

        names = zf.namelist()

        # 推断 modid
        modid = metadata.modid or self._infer_modid_from_any(names)

        # 收集模型物品和方块
        model_items: set[str] = set()
        model_blocks: set[str] = set()

        for name in names:
            if "models/item/" in name and name.endswith(".json"):
                model_items.add(_Path(name).stem)
            elif "blockstates/" in name and name.endswith(".json"):
                model_blocks.add(_Path(name).stem)

        if not model_items and not model_blocks:
            raise JarParseError(
                f"{jar_path.name} 中既无语言文件也无模型文件，无法生成翻译"
            )

        # 检测游戏版本
        game_version = self.detect_game_version(zf)

        # 生成伪英文条目
        english_entries: dict[str, str] = {}
        _cleanup = re.compile(r"[^a-zA-Z0-9_]+")

        for item_name in sorted(model_items):
            key = f"item.{modid}.{item_name}.name"
            readable = _cleanup.sub(" ", item_name)
            readable = " ".join(w.capitalize() for w in readable.split())
            english_entries[key] = readable

        for block_name in sorted(model_blocks):
            key = f"tile.{modid}.{block_name}.name"
            readable = _cleanup.sub(" ", block_name)
            readable = " ".join(w.capitalize() for w in readable.split())
            english_entries[key] = readable

        # 检查是否有现有的 zh_cn
        existing_chinese = {}
        zh_paths = [
            n for n in names
            if "/lang/" in n and n.endswith((".lang", ".json"))
            and ("zh_cn" in n.lower() or "zh-cn" in n.lower())
        ]
        if zh_paths:
            existing_chinese = self._parse_lang_files(zf, zh_paths, game_version)

        logger.info(
            "%s: 从模型文件生成 %d 条 en_us 条目 (%d 物品, %d 方块)",
            jar_path.name,
            len(english_entries),
            len(model_items),
            len(model_blocks),
        )

        return ModAssets(
            modid=modid,
            game_version=game_version,
            english_entries=english_entries,
            existing_chinese=existing_chinese,
            metadata=metadata,
            jar_path=jar_path,
            source_encoding="generated",
        )

    @staticmethod
    def _infer_modid_from_any(names: list[str]) -> str:
        """从 ZIP 文件列表中的任意路径推断 modid。"""
        for name in names:
            parts = name.split("/")
            if len(parts) >= 2 and parts[0] == "assets":
                candidate = parts[1]
                if candidate and candidate != "lang":
                    return candidate
        return "unknown"

    # ------------------------------------------------------------------
    # Game version detection
    # ------------------------------------------------------------------

    @staticmethod
    def detect_game_version(zf: ZipFile) -> GameVersion:
        """Detect whether a mod targets 1.12.2- (LEGACY) or 1.13+ (MODERN).

        Strategy:
        1. Read pack.mcmeta → pack_format. 3 → LEGACY, >=4 → MODERN.
        2. Count .lang vs .json in assets/**/lang/. Majority wins.
        3. If no language files at all → UNKNOWN.
        """
        # Strategy 1: pack.mcmeta pack_format
        try:
            mcmeta_bytes = zf.read("pack.mcmeta")
            mcmeta = json.loads(mcmeta_bytes.decode("utf-8-sig"), strict=False)
            pack_format = mcmeta.get("pack", {}).get("pack_format")
            if pack_format is not None:
                if pack_format <= LEGACY_PACK_FORMAT_MAX:
                    return GameVersion.LEGACY
                elif pack_format >= MODERN_PACK_FORMAT_THRESHOLD:
                    return GameVersion.MODERN
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
            pass

        # Strategy 2: Count .lang vs .json in lang directories
        lang_count = 0
        json_count = 0
        for name in zf.namelist():
            if "/lang/" in name:
                if name.endswith(".lang"):
                    lang_count += 1
                elif name.endswith(".json"):
                    json_count += 1

        if lang_count > 0 and json_count == 0:
            return GameVersion.LEGACY
        elif json_count > 0 and lang_count == 0:
            return GameVersion.MODERN
        elif lang_count > 0 or json_count > 0:
            # Mixed — use majority
            return GameVersion.LEGACY if lang_count >= json_count else GameVersion.MODERN

        return GameVersion.UNKNOWN

    # ------------------------------------------------------------------
    # Metadata extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_metadata(zf: ZipFile) -> ModMetadata:
        """Extract mod metadata from best available source.

        Priority: mcmod.info > MANIFEST.MF > pack.mcmeta.
        Never raises — returns best-effort ModMetadata.
        """
        # Priority 1: mcmod.info (most complete metadata)
        try:
            info_bytes = zf.read("mcmod.info")
            info = json.loads(info_bytes.decode("utf-8-sig"), strict=False)
            # mcmod.info is usually a list of mod objects
            if isinstance(info, list) and len(info) > 0:
                # Some mods list their dependencies first in mcmod.info,
                # so taking info[0] can return the wrong modid.
                # Instead, prefer the entry with the most complete metadata.
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
                mod = {}

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
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
            pass

        # Priority 2: META-INF/MANIFEST.MF
        metadata = ModMetadata(modid="")
        try:
            manifest = zf.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")
            for line in manifest.splitlines():
                if line.startswith("Implementation-Title:"):
                    name = line.split(":", 1)[1].strip()
                    if name:
                        metadata.name = name
                elif line.startswith("Implementation-Version:"):
                    metadata.version = line.split(":", 1)[1].strip()
        except (KeyError, UnicodeDecodeError):
            pass

        # Priority 3: pack.mcmeta (description only)
        try:
            mcmeta_bytes = zf.read("pack.mcmeta")
            mcmeta = json.loads(mcmeta_bytes.decode("utf-8-sig"), strict=False)
            desc = mcmeta.get("pack", {}).get("description", "")
            if desc and not metadata.description:
                metadata.description = desc
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
            pass

        return metadata

    # ------------------------------------------------------------------
    # Language file parsing
    # ------------------------------------------------------------------

    def _parse_lang_files(
        self,
        zf: ZipFile,
        paths: list[str],
        game_version: GameVersion,
    ) -> dict[str, str]:
        """Parse multiple language files and merge into one dict.

        For .lang files: detect encoding, decode, parse_lang().
        For .json files: parse_json() directly.
        Later files in the list overwrite earlier ones on key conflict.
        """
        merged: dict[str, str] = {}

        for path in paths:
            try:
                raw = zf.read(path)
                if game_version == GameVersion.LEGACY and path.endswith(".lang"):
                    text, _ = decode_lang(raw)
                    entries = parse_lang(text)
                elif path.endswith(".json"):
                    entries = parse_json(raw)
                else:
                    # Fallback: try .lang first, then .json
                    try:
                        text, _ = decode_lang(raw)
                        entries = parse_lang(text)
                    except Exception:
                        entries = parse_json(raw)

                merged.update(entries)
            except Exception as e:
                logger.warning("解析语言文件失败 %s (来自 %s): %s",
                               path, zf.filename if hasattr(zf, 'filename') else "?", e)

        return merged

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_modid(lang_entries: list[str], metadata: ModMetadata) -> str:
        """Infer the modid from language file paths or metadata.

        Language files are at assets/<modid>/lang/<file>, so the modid
        is the segment between 'assets/' and '/lang/'.
        """
        if metadata.modid:
            return metadata.modid

        for path in lang_entries:
            parts = path.split("/")
            # Look for path pattern: assets/<modid>/lang/<file>
            for i, part in enumerate(parts):
                if part == "assets" and i + 2 < len(parts) and parts[i + 2] == "lang":
                    return parts[i + 1]

        # Last resort: use first path segment after 'assets/'
        for path in lang_entries:
            if path.startswith("assets/"):
                rest = path[len("assets/"):]
                modid = rest.split("/")[0]
                if modid and modid != "lang":
                    return modid

        return "unknown"

    @staticmethod
    def extract_modid_from_path(path: str) -> str:
        """Extract modid from an asset path like 'assets/thaumcraft/lang/en_us.lang'.

        Returns 'thaumcraft'.
        """
        parts = path.split("/")
        for i, part in enumerate(parts):
            if part == "assets" and i + 2 < len(parts) and parts[i + 2] == "lang":
                return parts[i + 1]
        return "unknown"


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _collect_lang_entries(zf: ZipFile) -> list[str]:
    """Collect all file paths inside assets/<modid>/lang/ directories."""
    lang_paths: list[str] = []
    for name in zf.namelist():
        # Match assets/<anything>/lang/<anything>
        parts = name.split("/")
        if len(parts) >= 4 and parts[0] == "assets" and parts[2] == "lang":
            if parts[3]:  # Not an empty directory entry
                lang_paths.append(name)
    return lang_paths


def _classify_lang_entries(
    paths: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Classify language file paths into en_us, zh_cn, and others.

    Matching rules:
    - en_us: matches en_us, en_US, en-us in filename
    - zh_cn: matches zh_cn, zh_CN, zh-cn in filename
    """
    en_us: list[str] = []
    zh_cn: list[str] = []
    other: list[str] = []

    for path in paths:
        filename = path.split("/")[-1].lower()
        if "en_us" in filename or "en-us" in filename:
            en_us.append(path)
        elif "zh_cn" in filename or "zh-cn" in filename:
            zh_cn.append(path)
        else:
            other.append(path)

    return en_us, zh_cn, other

