"""Resource pack output writer.

Creates a standard Minecraft resource pack directory structure with
translated language files, ready to drop into resourcepacks/ folder.

Output structure::

    output/
    ├── pack.mcmeta
    └── assets/
        └── <modid>/
            └── lang/
                ├── zh_cn.lang   (for LEGACY: 1.12.2-)
                └── zh_cn.json   (for MODERN: 1.13+)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models import (
    GameVersion,
    ModAssets,
    MODERN_PACK_FORMAT_THRESHOLD,
    LEGACY_PACK_FORMAT_MAX,
    PACK_FORMAT_MAP,
)
from ..parser.lang_parser import format_lang
from ..parser.json_parser import format_json

logger = logging.getLogger(__name__)

# Default pack.mcmeta description
_DEFAULT_DESCRIPTION = (
    "机器翻译的 Minecraft Mod 简体中文汉化资源包"
)


class ResourcePack:
    """Writes translated mod assets as a Minecraft resource pack.

    Usage::

        pack = ResourcePack("My Chinese Pack", pack_format=3)
        pack.write(translated_mods, Path("./output"))
    """

    def __init__(
        self,
        name: str = "ModTrans 自动汉化",
        description: str = "",
        pack_format: int | None = None,
    ) -> None:
        self.name = name
        self.description = description or _DEFAULT_DESCRIPTION
        self.pack_format = pack_format  # None = auto-detect

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(
        self,
        translated_mods: list[ModAssets],
        output_dir: Path,
        *,
        mc_version: str = "",
    ) -> Path:
        """将资源包写入 output_dir。

        流程:
        1. 在 output_dir 下创建临时目录写入资源包结构
        2. 打包为 .zip
        3. 删除临时目录
        4. 最终 output_dir 中只有 .zip 文件

        Args:
            translated_mods: chinese_entries 已填充的 ModAssets。
            output_dir: 输出目录（只保留最终的 ZIP）。

        Returns:
            ZIP 文件路径。
        """
        import shutil
        import zipfile
        import os

        output_dir.mkdir(parents=True, exist_ok=True)

        # 确定 pack_format
        pack_format = self._determine_pack_format(translated_mods)

        # 在 output_dir 下创建临时工作目录
        work_dir = output_dir / ".pack_tmp"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir()

        try:
            # 写入 pack.mcmeta
            self._write_mcmeta(work_dir, pack_format)

            # 写入语言文件
            written_count = 0
            skipped_count = 0

            for mod in translated_mods:
                if not mod.chinese_entries:
                    skipped_count += 1
                    continue

                lang_dir = work_dir / "assets" / mod.modid / "lang"
                lang_dir.mkdir(parents=True, exist_ok=True)

                if mod.game_version == GameVersion.LEGACY:
                    lang_dir.joinpath("zh_cn.lang").write_text(
                        format_lang(mod.chinese_entries), encoding="utf-8"
                    )
                elif mod.game_version == GameVersion.MODERN:
                    lang_dir.joinpath("zh_cn.json").write_text(
                        format_json(mod.chinese_entries), encoding="utf-8"
                    )
                else:
                    lang_dir.joinpath("zh_cn.lang").write_text(
                        format_lang(mod.chinese_entries), encoding="utf-8"
                    )
                    lang_dir.joinpath("zh_cn.json").write_text(
                        format_json(mod.chinese_entries), encoding="utf-8"
                    )

                written_count += 1

            # 写入合并文件
            self._write_merged(work_dir, translated_mods, pack_format)

            # 打包 ZIP
            if mc_version:
                zip_name = f"ModTrans-汉化资源包-MC{mc_version}.zip"
            else:
                zip_name = f"ModTrans-汉化资源包-pack{pack_format}.zip"
            zip_path = output_dir / zip_name

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in sorted(work_dir.rglob("*")):
                    if file_path.is_file():
                        arcname = str(file_path.relative_to(work_dir)).replace(os.sep, "/")
                        zf.write(file_path, arcname)

            logger.info(
                "资源包已打包: %d 个 mod, %d 个跳过 → %s",
                written_count,
                skipped_count,
                zip_path,
            )

        finally:
            # 清理临时目录
            if work_dir.exists():
                shutil.rmtree(work_dir)

        return zip_path

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _determine_pack_format(self, mods: list[ModAssets]) -> int:
        """根据 mod 多数版本确定最佳 pack_format。

        用户显式设置优先，否则按多数：
        - 多数 LEGACY → pack_format 3 (1.12.2)
        - 多数 MODERN → pack_format 4 (1.13+)
        """
        if self.pack_format is not None:
            return self.pack_format

        legacy_count = sum(
            1 for m in mods if m.game_version == GameVersion.LEGACY
        )
        modern_count = sum(
            1 for m in mods if m.game_version == GameVersion.MODERN
        )

        if legacy_count > modern_count:
            return LEGACY_PACK_FORMAT_MAX  # 3
        return MODERN_PACK_FORMAT_THRESHOLD  # 4

    def _write_mcmeta(self, output_dir: Path, pack_format: int) -> None:
        """写入符合 Minecraft Wiki 规范的 pack.mcmeta。

        - pack_format: 核心格式号
        - supported_formats: 1.20.3+ (pack_format >= 15) 支持的格式范围
        - description: 资源包描述（纯文本或 JSON 对象）
        """
        pack: dict = {
            "pack_format": pack_format,
            "description": self.description,
        }

        # 1.20.3+ (pack_format >= 15) 需要 supported_formats
        if pack_format >= 15:
            max_known = max(PACK_FORMAT_MAP.keys()) if PACK_FORMAT_MAP else pack_format
            # max 至少等于 min
            if max_known < pack_format:
                max_known = pack_format
            pack["supported_formats"] = [pack_format, max_known]

        mcmeta = {"pack": pack}

        output_dir.joinpath("pack.mcmeta").write_text(
            json.dumps(mcmeta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_merged(
        self,
        output_dir: Path,
        mods: list[ModAssets],
        pack_format: int,
    ) -> None:
        """写入合并的 zh_cn_merged.json。"""
        merged: dict[str, str] = {}
        for mod in mods:
            if mod.chinese_entries:
                merged.update(mod.chinese_entries)
        if not merged:
            return

        merged_dir = output_dir / "assets" / "minecraft" / "lang"
        merged_dir.mkdir(parents=True, exist_ok=True)
        merged_dir.joinpath("zh_cn_merged.json").write_text(
            format_json(merged), encoding="utf-8"
        )
