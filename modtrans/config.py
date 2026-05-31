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
    enable_i18n: bool = True
    enable_cross_mod_fill: bool = True
    enable_untagged_fill: bool = True


@dataclass
class AIConfig:
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    max_tokens: int = 65536
    temperature: float = 0.3
    max_retries: int = 3
    retry_base_delay: float = 2.0
    requests_per_minute: int = 50
    max_keys_per_call: int = 100


@dataclass
class PromptConfig:
    custom_prefix: str = ""
    system_prompt_file: str = ""
    glossary_file: str = ""
    include_builtin_references: bool = True


@dataclass
class PackagerConfig:
    pack_name: str = "ModTrans 自动汉化"
    pack_description: str = "机器翻译的 Minecraft Mod 简体中文汉化资源包"
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
    """将 TOML 字典转为 AppConfig，缺失字段使用 dataclass 定义的默认值。"""
    # 用 dataclass 字段默认值初始化，TOML 有则覆盖
    config = AppConfig()

    if "general" in data:
        g = data["general"]
        config.general = GeneralConfig(
            mods_dir=Path(g.get("mods_dir", config.general.mods_dir)),
            output_dir=Path(g.get("output_dir", config.general.output_dir)),
            cache_dir=Path(g.get("cache_dir", config.general.cache_dir)),
            game_version=g.get("game_version", config.general.game_version),
            log_level=g.get("log_level", config.general.log_level),
            enable_i18n=bool(g.get("enable_i18n", config.general.enable_i18n)),
            enable_cross_mod_fill=bool(g.get("enable_cross_mod_fill", config.general.enable_cross_mod_fill)),
            enable_untagged_fill=bool(g.get("enable_untagged_fill", config.general.enable_untagged_fill)),
        )

    if "ai" in data:
        a = data["ai"]
        d = config.ai
        config.ai = AIConfig(
            api_base=a.get("api_base", d.api_base),
            api_key=a.get("api_key", d.api_key),
            model=a.get("model", d.model),
            max_tokens=int(a.get("max_tokens", d.max_tokens)),
            temperature=float(a.get("temperature", d.temperature)),
            max_retries=int(a.get("max_retries", d.max_retries)),
            retry_base_delay=float(a.get("retry_base_delay", d.retry_base_delay)),
            requests_per_minute=int(a.get("requests_per_minute", d.requests_per_minute)),
            max_keys_per_call=int(a.get("max_keys_per_call", d.max_keys_per_call)),
        )

    if "prompt" in data:
        p = data["prompt"]
        d = config.prompt
        config.prompt = PromptConfig(
            custom_prefix=p.get("custom_prefix", d.custom_prefix),
            system_prompt_file=p.get("system_prompt_file", d.system_prompt_file),
            glossary_file=p.get("glossary_file", d.glossary_file),
            include_builtin_references=bool(p.get("include_builtin_references", d.include_builtin_references)),
        )

    if "packager" in data:
        pk = data["packager"]
        d = config.packager
        config.packager = PackagerConfig(
            pack_name=pk.get("pack_name", d.pack_name),
            pack_description=pk.get("pack_description", d.pack_description),
            pack_format=str(pk.get("pack_format", d.pack_format)),
        )

    if "cache" in data:
        c = data["cache"]
        d = config.cache
        config.cache = CacheConfig(
            enabled=bool(c.get("enabled", d.enabled)),
            max_age_days=int(c.get("max_age_days", d.max_age_days)),
            max_size_mb=int(c.get("max_size_mb", d.max_size_mb)),
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
