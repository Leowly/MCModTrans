"""TOML configuration loading.

Config is searched in this order:
1. Explicit path argument
2. ./modtrans.toml (current directory)
3. ~/.config/modtrans/config.toml
4. Built-in defaults
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from tomllib import load as toml_load
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class GeneralConfig:
    mods_dir: Path = Path("./mods")
    output_dir: Path = Path("./output_resource_pack")
    cache_dir: Path = Path("./.cache/modtrans")
    game_version: str = "auto"
    log_level: str = "INFO"


@dataclass
class AIConfig:
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    max_tokens: int = 4096
    temperature: float = 0.3
    max_retries: int = 3
    retry_base_delay: float = 2.0
    requests_per_minute: int = 50
    max_keys_per_call: int = 200


@dataclass
class PromptConfig:
    custom_prefix: str = ""
    system_prompt_file: str = ""
    glossary_file: str = ""
    include_builtin_references: bool = True


@dataclass
class BatcherConfig:
    strategy: str = "author"
    max_batch_keys: int = 500
    min_keys_for_solo: int = 50


@dataclass
class PackagerConfig:
    pack_name: str = "ModTrans 自动汉化"
    pack_description: str = (
        "机器翻译的 Minecraft Mod 简体中文汉化资源包"
    )
    pack_format: str = "auto"


@dataclass
class CacheConfig:
    enabled: bool = True
    max_age_days: int = 30
    max_size_mb: int = 500


@dataclass
class AppConfig:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    batcher: BatcherConfig = field(default_factory=BatcherConfig)
    packager: PackagerConfig = field(default_factory=PackagerConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

_CONFIG_SEARCH_PATHS = [
    Path("./modtrans.toml"),
    Path.home() / ".config" / "modtrans" / "config.toml",
]


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML config file directly."""
    with open(path, "rb") as f:
        return toml_load(f)


def _dict_to_config(data: dict[str, Any]) -> AppConfig:
    """Convert a raw TOML dict to an AppConfig dataclass."""
    config = AppConfig()

    if "general" in data:
        g = data["general"]
        config.general = GeneralConfig(
            mods_dir=Path(g.get("mods_dir", "./mods")),
            output_dir=Path(g.get("output_dir", "./output_resource_pack")),
            cache_dir=Path(g.get("cache_dir", "./.cache/modtrans")),
            game_version=g.get("game_version", "auto"),
            log_level=g.get("log_level", "INFO"),
        )

    if "ai" in data:
        a = data["ai"]
        config.ai = AIConfig(
            api_base=a.get("api_base", "https://api.openai.com/v1"),
            api_key=a.get("api_key", ""),
            model=a.get("model", "gpt-4o"),
            max_tokens=int(a.get("max_tokens", 4096)),
            temperature=float(a.get("temperature", 0.3)),
            max_retries=int(a.get("max_retries", 3)),
            retry_base_delay=float(a.get("retry_base_delay", 2.0)),
            requests_per_minute=int(a.get("requests_per_minute", 50)),
            max_keys_per_call=int(a.get("max_keys_per_call", 200)),
        )

    if "prompt" in data:
        p = data["prompt"]
        config.prompt = PromptConfig(
            custom_prefix=p.get("custom_prefix", ""),
            system_prompt_file=p.get("system_prompt_file", ""),
            glossary_file=p.get("glossary_file", ""),
            include_builtin_references=bool(
                p.get("include_builtin_references", True)
            ),
        )

    if "batcher" in data:
        b = data["batcher"]
        config.batcher = BatcherConfig(
            strategy=b.get("strategy", "author"),
            max_batch_keys=int(b.get("max_batch_keys", 500)),
            min_keys_for_solo=int(b.get("min_keys_for_solo", 50)),
        )

    if "packager" in data:
        pk = data["packager"]
        config.packager = PackagerConfig(
            pack_name=pk.get("pack_name", "Auto Translated Chinese"),
            pack_description=pk.get(
                "pack_description",
                "Machine-translated Simplified Chinese localization",
            ),
            pack_format=str(pk.get("pack_format", "auto")),
        )

    if "cache" in data:
        c = data["cache"]
        config.cache = CacheConfig(
            enabled=bool(c.get("enabled", True)),
            max_age_days=int(c.get("max_age_days", 30)),
            max_size_mb=int(c.get("max_size_mb", 500)),
        )

    return config


def load_config(path: Path | None = None) -> AppConfig:
    """Load the application configuration.

    Args:
        path: Explicit config file path. If None, searches default locations.

    Returns:
        AppConfig with all values from config or defaults.

    Raises:
        FileNotFoundError: If an explicit path is given but doesn't exist.
    """
    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(f"配置文件未找到: {path}")
        raw = _load_toml(path)
        logger.info("已加载配置文件: %s", path)
        return _dict_to_config(raw)

    # Search default locations
    for search_path in _CONFIG_SEARCH_PATHS:
        if search_path.is_file():
            raw = _load_toml(search_path)
            logger.info("已加载配置文件: %s", search_path)
            return _dict_to_config(raw)

    logger.info("未找到配置文件，使用默认设置")
    return AppConfig()


def generate_example_config() -> str:
    """生成示例 TOML 配置文件内容。"""
    return """\
# ModTrans 配置文件

[ai]
# API 接口地址 — 使用第三方 API 时修改此处 (DeepSeek、通义千问等)
api_base = "https://api.openai.com/v1"
# API 密钥 — 直接粘贴到这里
api_key = "sk-your-api-key-here"
# 模型名称
model = "gpt-4o"
"""
